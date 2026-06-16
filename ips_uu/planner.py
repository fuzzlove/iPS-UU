#!/usr/bin/env python3
"""
IPSW downgrade/restore planner reconstructed from the PurpleRestore bundle.

This is not a MobileRestore/AuthInstall replacement and it does not bypass
Apple firmware signing. It mirrors the app's reproducible selection logic:
read BuildManifest.plist, enumerate supported devices and build identities,
select an AuthInstall variant, and compose restore options for an erase or
update install.
"""

from __future__ import annotations

import argparse
import json
import plistlib
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .banner import print_intro
from .purple_rabbit import (
    DEFAULT_CONTENTS_PR,
    PurpleRabbitError,
    load_config as load_purple_rabbit_config,
    load_translation_rules,
    restore_settings_template,
    translate_message,
)
from .services.tool_resolver import resolve_idevicerestore


DEFAULT_SETTINGS = Path("Contents/Resources/Default Settings.pr")
ERASE_TEMPLATE = Path("Contents/Resources/Erase Install.plist")
UPDATE_TEMPLATE = Path("Contents/Resources/Update Install.plist")


class PlannerError(RuntimeError):
    pass


@dataclass(frozen=True)
class BuildIdentity:
    index: int
    variant: str
    device_class: str | None
    restore_behavior: str | None
    product_version: str | None
    build_version: str | None
    manifest_keys: tuple[str, ...]

    @classmethod
    def from_dict(cls, index: int, identity: dict[str, Any], manifest: dict[str, Any]) -> "BuildIdentity":
        info = identity.get("Info") or {}
        return cls(
            index=index,
            variant=str(info.get("Variant") or "Unknown"),
            device_class=string_or_none(info.get("DeviceClass") or info.get("DeviceClassName")),
            restore_behavior=string_or_none(info.get("RestoreBehavior")),
            product_version=string_or_none(
                info.get("ProductVersion")
                or manifest.get("ProductVersion")
                or manifest.get("ProductVersionExtra")
            ),
            build_version=string_or_none(info.get("BuildNumber") or manifest.get("ProductBuildVersion")),
            manifest_keys=tuple(sorted((identity.get("Manifest") or {}).keys())),
        )


def string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def load_plist(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as f:
            data = plistlib.load(f)
    except FileNotFoundError as exc:
        raise PlannerError(f"File not found: {path}") from exc
    except Exception as exc:
        raise PlannerError(f"Could not parse plist {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PlannerError(f"Expected plist dictionary in {path}")
    return data


def load_build_manifest(ipsw: Path) -> dict[str, Any]:
    if not ipsw.exists():
        raise PlannerError(f"IPSW not found: {ipsw}")
    try:
        with zipfile.ZipFile(ipsw) as archive:
            names = archive.namelist()
            candidates = [name for name in names if name.endswith("BuildManifest.plist")]
            if not candidates:
                raise PlannerError("BuildManifest.plist was not found inside the IPSW")
            # PurpleRestore looks for BuildManifest.plist by name. Prefer the root entry.
            manifest_name = "BuildManifest.plist" if "BuildManifest.plist" in candidates else candidates[0]
            with archive.open(manifest_name) as f:
                manifest = plistlib.load(f)
    except zipfile.BadZipFile as exc:
        raise PlannerError(f"Not a valid IPSW/zip archive: {ipsw}") from exc
    if not isinstance(manifest, dict):
        raise PlannerError("BuildManifest.plist is not a dictionary")
    if not isinstance(manifest.get("BuildIdentities"), list):
        raise PlannerError("BuildManifest.plist has no BuildIdentities array")
    return manifest


def identities(manifest: dict[str, Any]) -> list[BuildIdentity]:
    return [
        BuildIdentity.from_dict(index, identity, manifest)
        for index, identity in enumerate(manifest.get("BuildIdentities") or [])
        if isinstance(identity, dict)
    ]


def supported_product_types(manifest: dict[str, Any]) -> list[str]:
    values = manifest.get("SupportedProductTypes") or []
    if not isinstance(values, list):
        return []
    return sorted({str(value) for value in values})


def identity_matches(identity: BuildIdentity, product_type: str | None, device_class: str | None) -> bool:
    if device_class and identity.device_class and identity.device_class.lower() == device_class.lower():
        return True
    if device_class and identity.device_class and identity.device_class.lower().replace("-", "") == device_class.lower().replace("-", ""):
        return True
    # ProductType is usually top-level in IPSWs, not per identity. If the caller
    # supplied only a ProductType, keep all identities and let variant selection
    # disambiguate.
    return product_type is not None and device_class is None


def filtered_identities(
    manifest: dict[str, Any], product_type: str | None, device_class: str | None
) -> list[BuildIdentity]:
    all_identities = identities(manifest)
    if not product_type and not device_class:
        return all_identities
    if product_type and product_type not in supported_product_types(manifest):
        return []
    matches = [identity for identity in all_identities if identity_matches(identity, product_type, device_class)]
    return matches or (all_identities if product_type and not device_class else [])


def choose_identity(
    manifest: dict[str, Any],
    product_type: str | None,
    device_class: str | None,
    variant: str | None,
) -> BuildIdentity:
    candidates = filtered_identities(manifest, product_type, device_class)
    if variant:
        candidates = [i for i in candidates if i.variant.lower() == variant.lower()]
    if not candidates:
        raise PlannerError("No BuildIdentity matched the requested device/variant")
    if len(candidates) == 1:
        return candidates[0]
    erase = [i for i in candidates if "erase" in i.variant.lower()]
    if erase:
        return erase[0]
    return candidates[0]


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def default_restore_options(install_mode: str) -> dict[str, Any]:
    settings: dict[str, Any] = {"RestoreOptions": {}}
    if DEFAULT_SETTINGS.exists():
        settings = load_plist(DEFAULT_SETTINGS)
    purple_restore_options = purple_rabbit_restore_options()
    if purple_restore_options:
        settings = deep_merge(settings, {"RestoreOptions": purple_restore_options})
    template_path = ERASE_TEMPLATE if install_mode == "erase" else UPDATE_TEMPLATE
    if template_path.exists():
        settings = deep_merge(settings, load_plist(template_path))
    restore_options = dict(settings.get("RestoreOptions") or {})
    restore_options["CreateFilesystemPartitions"] = install_mode == "erase"
    restore_options["AuthInstallRestoreBehavior"] = "Erase" if install_mode == "erase" else "Update"
    restore_options.setdefault("ShouldRestoreSystemImage", True)
    restore_options.setdefault("UpdateBaseband", True)
    restore_options.setdefault("AutoPersonalizeBaseband", True)
    restore_options.setdefault("AuthInstallSigningServerURL", "https://gs.apple.com:443")
    return restore_options


def purple_rabbit_restore_options(contents_pr: Path = DEFAULT_CONTENTS_PR) -> dict[str, Any]:
    if not contents_pr.exists():
        return {}
    try:
        template = restore_settings_template(load_purple_rabbit_config(contents_pr))
    except PurpleRabbitError:
        return {}
    restore_manager = template.get("RestoreManager") or {}
    restore_options = restore_manager.get("RestoreOptions") or {}
    return dict(restore_options) if isinstance(restore_options, dict) else {}


def build_restore_options(
    ipsw: Path,
    identity: BuildIdentity,
    install_mode: str,
    allow_unsigned: bool,
    signing_server_url: str | None = None,
    signing_server_host: str | None = None,
    signing_server_port: int | None = None,
) -> dict[str, Any]:
    options = default_restore_options(install_mode)
    options["RestoreBundlePath"] = str(ipsw.resolve())
    options["AuthInstallVariant"] = identity.variant
    options["PrepareVariant"] = identity.variant
    if signing_server_url:
        options["AuthInstallSigningServerURL"] = signing_server_url
    if signing_server_host:
        options["AuthInstallSigningServerHost"] = signing_server_host
    if signing_server_port:
        options["AuthInstallSigningServerPort"] = signing_server_port
    if identity.product_version:
        options["ProductVersion"] = identity.product_version
    if identity.build_version:
        options["BuildVersion"] = identity.build_version
    if allow_unsigned:
        options["AllowUntetheredRestore"] = True
    return options


def offline_feasibility(manifest: dict[str, Any], ipsw: Path, options: dict[str, Any]) -> dict[str, Any]:
    has_manifest = bool(manifest.get("BuildIdentities"))
    signing_server = options.get("AuthInstallSigningServerURL")
    is_local_signing_server = signing_server_is_local(str(signing_server or ""))
    return {
        "local_ipsw_usable": ipsw.exists() and has_manifest,
        "offline_manifest_selection": has_manifest,
        "offline_personalization_supported_by_this_app": False,
        "offline_signing_server_override": is_local_signing_server,
        "signing_server": signing_server,
        "requires_online_signing_for_normal_restore": True,
        "local_server_requirements": [
            "Accept the AuthInstall/TSS request generated from the selected BuildIdentity.",
            "Return a valid TSS response/APTicket for the exact ECID, APNonce/generator, board/chip IDs, variant, and manifest measurements.",
            "Provide any required baseband or coprocessor signing responses when the restore options request those components.",
            "Use tickets that are already valid for the device/build; this planner does not create or bypass Apple signatures.",
        ],
        "reason": (
            "The recovered PurpleRestore validation checks network reachability "
            "against both Apple's connectivity URL and AuthInstallSigningServerURL. "
            "RestoreManager creates a PersonalizedRestoreBundlePath during restore, "
            "which indicates AuthInstall/MobileRestore personalization is still part "
            "of the flow. No local SHSH/APTicket replay path was recovered."
        ),
    }


def signing_server_is_local(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return host in {"127.0.0.1", "::1", "localhost", "spidercab"} or host.endswith(".local")


def validate_signing_server_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PlannerError(f"Invalid signing server URL: {value}")
    return value


def signing_server_from_args(args: argparse.Namespace) -> tuple[str | None, str | None, int | None]:
    url = getattr(args, "signing_server_url", None)
    host = getattr(args, "signing_server_host", None)
    port = getattr(args, "signing_server_port", None)
    offline_mode = bool(getattr(args, "offline_mode", False))

    if url and (host or port):
        raise PlannerError("Use either --signing-server-url or --signing-server-host/--signing-server-port, not both")
    if port is not None and not host:
        raise PlannerError("--signing-server-port requires --signing-server-host")
    if host and port is None:
        port = 80
    if host:
        url = f"http://{host}:{port}"
    if offline_mode and not url:
        url = "http://127.0.0.1:8080"
    if url:
        url = validate_signing_server_url(url)
    return url, host, port


def unsafe_restore_option_requested(args: argparse.Namespace) -> str | None:
    if bool(getattr(args, "allow_unsigned", False)):
        return "--allow-unsigned cannot be used for executable restores"
    if bool(getattr(args, "offline_mode", False)):
        return "--offline-mode cannot be used for executable restores"
    if getattr(args, "signing_server_url", None):
        return "--signing-server-url cannot be used for executable restores"
    if getattr(args, "signing_server_host", None) or getattr(args, "signing_server_port", None):
        return "--signing-server-host/--signing-server-port cannot be used for executable restores"
    return None


def idevicerestore_command(binary: str, install_mode: str, ipsw: Path) -> list[str]:
    return [binary, "-e" if install_mode == "erase" else "-u", str(ipsw)]


def build_tuple(build: str) -> tuple[int, int, int, str]:
    match = re.fullmatch(r"(\d+)([A-Za-z]+)(\d+)([A-Za-z0-9]*)", build.strip())
    if not match:
        return (0, 0, 0, build)
    train_num, train_letters, build_num, suffix = match.groups()
    letter_value = 0
    for char in train_letters.upper():
        letter_value = letter_value * 26 + (ord(char) - ord("A") + 1)
    return (int(train_num), letter_value, int(build_num), suffix)


def downgrade_assessment(target_build: str | None, current_build: str | None, install_mode: str) -> list[str]:
    notes: list[str] = []
    if not target_build:
        notes.append("Target build was not present in the manifest, so downgrade comparison was skipped.")
        return notes
    if not current_build:
        notes.append("No current build was provided; compatibility was assessed from the IPSW manifest only.")
        return notes
    target = build_tuple(target_build)
    current = build_tuple(current_build)
    if target < current:
        notes.append(f"Target build {target_build} is older than current build {current_build}: this is a downgrade.")
        if install_mode == "update":
            notes.append("Update-install downgrade is likely to fail; use erase install unless you know the target permits it.")
    elif target == current:
        notes.append(f"Target build {target_build} equals current build {current_build}.")
    else:
        notes.append(f"Target build {target_build} is newer than current build {current_build}.")
    return notes


def find_ipsw_bundles(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if root.name.lower().endswith(".ipsw") else []
    if not root.exists():
        raise PlannerError(f"Bundle scan root does not exist: {root}")
    return sorted(path for path in root.rglob("*") if path.is_file() and path.name.lower().endswith(".ipsw"))


def bundle_scan_entry(
    ipsw: Path,
    product_type: str | None,
    device_class: str | None,
    variant: str | None,
) -> dict[str, Any]:
    manifest = load_build_manifest(ipsw)
    selected: dict[str, Any] | None = None
    match_error: str | None = None
    try:
        selected = choose_identity(manifest, product_type, device_class, variant).__dict__
    except PlannerError as exc:
        match_error = str(exc)
    product_build = string_or_none(manifest.get("ProductBuildVersion"))
    product_version = string_or_none(manifest.get("ProductVersion"))
    all_identities = filtered_identities(manifest, product_type, device_class)
    return {
        "path": str(ipsw.resolve()),
        "product_version": product_version,
        "product_build_version": product_build,
        "supported_product_types": supported_product_types(manifest),
        "variants": sorted({identity.variant for identity in all_identities}),
        "selected_identity": selected,
        "match_error": match_error,
        "sort_key": build_tuple(product_build or (selected.get("build_version") if selected else "")),
    }


def newest_first(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(entries, key=lambda item: item["sort_key"], reverse=True)


def public_bundle_entry(entry: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(entry)
    cleaned.pop("sort_key", None)
    return cleaned


def inspect_command(args: argparse.Namespace) -> int:
    manifest = load_build_manifest(Path(args.ipsw))
    all_identities = filtered_identities(manifest, args.product_type, args.device_class)
    payload = {
        "ipsw": str(Path(args.ipsw).resolve()),
        "product_version": manifest.get("ProductVersion"),
        "product_build_version": manifest.get("ProductBuildVersion"),
        "supported_product_types": supported_product_types(manifest),
        "variants": sorted({identity.variant for identity in all_identities}),
        "identities": [identity.__dict__ for identity in all_identities],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def scan_bundles_command(args: argparse.Namespace) -> int:
    root = Path(args.root)
    entries: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for ipsw in find_ipsw_bundles(root):
        try:
            entries.append(bundle_scan_entry(ipsw, args.product_type, args.device_class, args.variant))
        except PlannerError as exc:
            errors.append({"path": str(ipsw), "error": str(exc)})
    sorted_entries = newest_first(entries)
    if args.latest and sorted_entries:
        sorted_entries = sorted_entries[:1]
    payload = {
        "root": str(root.resolve()),
        "count": len(sorted_entries),
        "bundles": [public_bundle_entry(entry) for entry in sorted_entries],
        "errors": errors,
        "notes": [
            "Mirrors PurpleRabbit's ScanForBundles idea for local IPSW discovery.",
            "Newest selection is based on ProductBuildVersion parsing when available.",
        ],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def diagnose_error_command(args: argparse.Namespace) -> int:
    try:
        rules = load_translation_rules(Path(args.contents_pr))
    except PurpleRabbitError as exc:
        raise PlannerError(str(exc)) from exc
    matches = translate_message(args.message, rules)
    print(json.dumps({"message": args.message, "matches": matches}, indent=2, sort_keys=True))
    return 0 if matches else 1


def plan_command(args: argparse.Namespace) -> int:
    ipsw = Path(args.ipsw)
    manifest = load_build_manifest(ipsw)
    identity = choose_identity(manifest, args.product_type, args.device_class, args.variant)
    notes = downgrade_assessment(identity.build_version, args.current_build, args.install_mode)
    signing_url, signing_host, signing_port = signing_server_from_args(args)
    options = build_restore_options(
        ipsw,
        identity,
        args.install_mode,
        args.allow_unsigned,
        signing_url,
        signing_host,
        signing_port,
    )
    plan = {
        "selected_identity": identity.__dict__,
        "restore_options": options,
        "offline": offline_feasibility(manifest, ipsw, options),
        "purple_rabbit_defaults_imported": bool(purple_rabbit_restore_options()),
        "notes": notes
        + [
            "PurpleRabbit restore defaults are imported when ContentsPR/Resources/Config.plist is present.",
            "Apple signing is still required for normal restores; this tool does not bypass TSS/AuthInstall.",
            "Use a trusted restore tool such as idevicerestore or Apple's MobileRestore stack to execute the plan.",
        ],
        "suggested_command": [
            "idevicerestore",
            "-e" if args.install_mode == "erase" else "-u",
            str(ipsw),
        ],
    }
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


def export_options_command(args: argparse.Namespace) -> int:
    ipsw = Path(args.ipsw)
    manifest = load_build_manifest(ipsw)
    identity = choose_identity(manifest, args.product_type, args.device_class, args.variant)
    signing_url, signing_host, signing_port = signing_server_from_args(args)
    options = {
        "RestoreOptions": build_restore_options(
            ipsw,
            identity,
            args.install_mode,
            args.allow_unsigned,
            signing_url,
            signing_host,
            signing_port,
        )
    }
    output = Path(args.output)
    with output.open("wb") as f:
        plistlib.dump(options, f, sort_keys=True)
    print(f"Wrote {output}")
    return 0


def offline_command(args: argparse.Namespace) -> int:
    ipsw = Path(args.ipsw)
    manifest = load_build_manifest(ipsw)
    identity = choose_identity(manifest, args.product_type, args.device_class, args.variant)
    signing_url, signing_host, signing_port = signing_server_from_args(args)
    options = build_restore_options(
        ipsw,
        identity,
        args.install_mode,
        args.allow_unsigned,
        signing_url,
        signing_host,
        signing_port,
    )
    payload = {
        "selected_identity": identity.__dict__,
        "restore_options_overrides": {
            key: options[key]
            for key in (
                "AuthInstallSigningServerURL",
                "AuthInstallSigningServerHost",
                "AuthInstallSigningServerPort",
            )
            if key in options
        },
        "offline": offline_feasibility(manifest, ipsw, options),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def restore_command(args: argparse.Namespace) -> int:
    unsafe_reason = unsafe_restore_option_requested(args)
    if unsafe_reason:
        raise PlannerError(
            f"{unsafe_reason}. This command only performs normal restores that rely on Apple's signing service."
        )

    ipsw = Path(args.ipsw)
    manifest = load_build_manifest(ipsw)
    identity = choose_identity(manifest, args.product_type, args.device_class, args.variant)
    binary = resolve_idevicerestore(args.idevicerestore)
    if not binary:
        raise PlannerError("idevicerestore was not found. Install or build libimobiledevice/idevicerestore and retry.")

    command = idevicerestore_command(binary, args.install_mode, ipsw)
    notes = downgrade_assessment(identity.build_version, args.current_build, args.install_mode)
    preflight = {
        "selected_identity": identity.__dict__,
        "install_mode": args.install_mode,
        "command": command,
        "will_execute": bool(args.execute),
        "notes": notes
        + [
            "This path performs a standard restore and depends on Apple's normal firmware signing.",
            "It does not use offline signing-server overrides, forged tickets, or unsigned restore options.",
        ],
    }

    if not args.execute:
        print(json.dumps(preflight, indent=2, sort_keys=True))
        return 0
    if args.install_mode == "erase" and not args.confirm_erase:
        raise PlannerError("erase restore requires --confirm-erase because it will wipe the target device")

    print(json.dumps(preflight, indent=2, sort_keys=True))
    completed = subprocess.run(command, check=False)
    return completed.returncode


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("ipsw", help="Path to an IPSW file")
    parser.add_argument("--product-type", help="Device ProductType, for example iPhone10,6")
    parser.add_argument("--device-class", help="BuildManifest Info.DeviceClass/board config, for example d22ap")


def add_signing_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--offline-mode", action="store_true", help="Point AuthInstallSigningServerURL at a local signing server")
    parser.add_argument("--signing-server-url", help="Override AuthInstallSigningServerURL, for example http://127.0.0.1:8080")
    parser.add_argument("--signing-server-host", help="Override AuthInstallSigningServerHost and synthesize an http URL")
    parser.add_argument("--signing-server-port", type=int, help="Override AuthInstallSigningServerPort, default 80 with --signing-server-host")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m ips_uu planner",
        description="Inspect IPSWs and prepare downgrade/restore plans.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  Online full restore dry-run:
    python3 -m ips_uu planner restore ./firmware.ipsw --product-type iPhone10,6 --install-mode erase

  Online full restore execution, Apple-signed only:
    python3 -m ips_uu planner restore ./firmware.ipsw --product-type iPhone10,6 --install-mode erase --execute --confirm-erase

  Offline full restore feasibility check, no signing bypass:
    python3 -m ips_uu planner offline-check ./firmware.ipsw --product-type iPhone10,6 --install-mode erase --offline-mode

  Offline listener for replaying an already-valid captured TSS response:
    python3 -m ips_uu listener --response ./valid-tss-response.plist --host 127.0.0.1 --port 8080

notes:
  The restore command performs only normal Apple-signed restores.
  Offline mode prepares/checks local signing-server settings only; it does not create,
  forge, or bypass SHSH/APTicket/TSS signatures.
""",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subcommands.add_parser("inspect", help="Print manifest devices, variants, and identities")
    add_common_args(inspect_parser)
    inspect_parser.set_defaults(func=inspect_command)

    scan_parser = subcommands.add_parser("scan-bundles", help="Recursively find local IPSW restore bundles")
    scan_parser.add_argument("root", help="Directory or IPSW path to scan")
    scan_parser.add_argument("--product-type", help="Device ProductType, for example iPhone10,6")
    scan_parser.add_argument("--device-class", help="BuildManifest Info.DeviceClass/board config, for example d22ap")
    scan_parser.add_argument("--variant", help="AuthInstall variant to select")
    scan_parser.add_argument("--latest", action="store_true", help="Only print the newest matching bundle")
    scan_parser.set_defaults(func=scan_bundles_command)

    diagnose_parser = subcommands.add_parser("diagnose-error", help="Translate common restore errors using PurpleRabbit rules")
    diagnose_parser.add_argument("message", help="Restore log or error message")
    diagnose_parser.add_argument("--contents-pr", default=str(DEFAULT_CONTENTS_PR), help="Path to ContentsPR; defaults to ./ContentsPR")
    diagnose_parser.set_defaults(func=diagnose_error_command)

    plan_parser = subcommands.add_parser("plan", help="Select an identity and print a restore plan")
    add_common_args(plan_parser)
    plan_parser.add_argument("--variant", help="AuthInstall variant to select")
    plan_parser.add_argument("--current-build", help="Currently installed build, for downgrade comparison")
    plan_parser.add_argument("--install-mode", choices=("erase", "update"), default="erase")
    plan_parser.add_argument("--allow-unsigned", action="store_true", help="Set AllowUntetheredRestore in the exported options only")
    add_signing_args(plan_parser)
    plan_parser.set_defaults(func=plan_command)

    export_parser = subcommands.add_parser("export-options", help="Write PurpleRestore-style restore options plist")
    add_common_args(export_parser)
    export_parser.add_argument("--variant", help="AuthInstall variant to select")
    export_parser.add_argument("--install-mode", choices=("erase", "update"), default="erase")
    export_parser.add_argument("--allow-unsigned", action="store_true", help="Set AllowUntetheredRestore in the exported options only")
    add_signing_args(export_parser)
    export_parser.add_argument("-o", "--output", required=True, help="Output plist path")
    export_parser.set_defaults(func=export_options_command)

    offline_parser = subcommands.add_parser(
        "offline-check",
        help="Assess whether a restore can be prepared or run offline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  Offline full restore feasibility check, using the default local URL:
    python3 -m ips_uu planner offline-check ./firmware.ipsw --product-type iPhone10,6 --install-mode erase --offline-mode

  Offline feasibility check with an explicit local signing-server URL:
    python3 -m ips_uu planner offline-check ./firmware.ipsw --product-type iPhone10,6 --install-mode erase --signing-server-url http://127.0.0.1:8080

  Listener for replaying an already-valid captured TSS response:
    python3 -m ips_uu listener --response ./valid-tss-response.plist --host 127.0.0.1 --port 8080

notes:
  This command does not execute a restore.
  A local listener must return a valid response for the exact device/build request;
  iPS-UU does not create or bypass Apple signing.
""",
    )
    add_common_args(offline_parser)
    offline_parser.add_argument("--variant", help="AuthInstall variant to select")
    offline_parser.add_argument("--install-mode", choices=("erase", "update"), default="erase")
    offline_parser.add_argument("--allow-unsigned", action="store_true", help="Set AllowUntetheredRestore in the computed options only")
    add_signing_args(offline_parser)
    offline_parser.set_defaults(func=offline_command)

    restore_parser = subcommands.add_parser(
        "restore",
        help="Run a standard Apple-signed restore through idevicerestore",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  Online full restore dry-run:
    python3 -m ips_uu planner restore ./firmware.ipsw --product-type iPhone10,6 --install-mode erase

  Online full restore execution, Apple-signed only:
    python3 -m ips_uu planner restore ./firmware.ipsw --product-type iPhone10,6 --install-mode erase --execute --confirm-erase

notes:
  --execute runs idevicerestore.
  --confirm-erase is required for erase installs because the target device is wiped.
  Offline signing-server overrides and unsigned restore options are intentionally rejected here.
""",
    )
    add_common_args(restore_parser)
    restore_parser.add_argument("--variant", help="AuthInstall variant to select for preflight reporting")
    restore_parser.add_argument("--current-build", help="Currently installed build, for downgrade comparison")
    restore_parser.add_argument("--install-mode", choices=("erase", "update"), default="erase")
    restore_parser.add_argument("--idevicerestore", help="Path to idevicerestore binary; defaults to PATH lookup")
    restore_parser.add_argument("--execute", action="store_true", help="Actually run idevicerestore; default is dry-run")
    restore_parser.add_argument("--confirm-erase", action="store_true", help="Required with --execute when --install-mode erase")
    restore_parser.add_argument("--allow-unsigned", action="store_true", help=argparse.SUPPRESS)
    restore_parser.add_argument("--offline-mode", action="store_true", help=argparse.SUPPRESS)
    restore_parser.add_argument("--signing-server-url", help=argparse.SUPPRESS)
    restore_parser.add_argument("--signing-server-host", help=argparse.SUPPRESS)
    restore_parser.add_argument("--signing-server-port", type=int, help=argparse.SUPPRESS)
    restore_parser.set_defaults(func=restore_command)
    return parser


def main(argv: list[str] | None = None, show_intro: bool = False) -> int:
    if show_intro:
        print_intro()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except PlannerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(show_intro=True))
