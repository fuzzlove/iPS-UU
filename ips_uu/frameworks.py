"""Framework restore-resource diagnostics for iPS-UU."""

from __future__ import annotations

import argparse
import json
import plistlib
import sys
import zipfile
from pathlib import Path
from typing import Any

from .banner import print_intro

DEFAULT_FRAMEWORKS = Path("Frameworks")

RESTORE_TEMPLATE_PATHS = [
    Path("ATKImaging.framework/Versions/A/Resources/bridgeOSRestoreOptions.plist"),
    Path("ATKImaging.framework/Versions/A/Resources/prdocGood.pr"),
    Path("ATKImaging.framework/Versions/A/Resources/bridgeOSRestore.plist"),
    Path("ATKImaging.framework/Versions/A/Resources/bridgeOSRestore2.plist"),
    Path("ATKImaging.framework/Versions/A/Resources/bridgeOSRestore3.plist"),
    Path("ATKImaging.framework/Versions/A/Resources/bridgeOSRestore4.plist"),
    Path("ATKImaging.framework/Versions/A/Resources/bridgeOSRestore5.plist"),
    Path("ATKImagingService.framework/Versions/A/Resources/defaultMacOSRestoreOptions.plist"),
]

RESTORE_ERROR_RULES = [
    ("RestoreBundlePath points to afile that doesn't exist", "RestoreBundlePath validation failed; check the local DMG/IPSW path."),
    ("MissingRestoreBundlePath", "Restore task is missing a RestoreBundlePath."),
    ("PRKitMissing", "PRKit package was not present or could not be resolved."),
    ("PRKitUncompression", "PRKit package could not be uncompressed before reading RestoreOptions."),
    ("Failed to transition to DFU", "Device state transition to DFU failed."),
    ("Cannot find attached primate cable", "Factory cable automation was unavailable for DFU transition."),
    ("DFU", "Restore path is operating on or waiting for a DFU device."),
    ("Recovery", "Restore path is operating on or waiting for a recovery-mode device."),
]

SENSITIVE_RESTORE_KEYS = {
    "AuthInstallSigningServerURL",
    "AuthInstallVariant",
    "BasebandUpdater",
    "CreateFilesystemPartitions",
    "InstallDiags",
    "InstallRecoveryOS",
    "NVRAMVariableModifications",
    "PersistantBootArgsModifications",
    "PostRestoreAction",
    "RecoveryOSOnly",
    "RestoreBootArgs",
    "RestoreBundlePath",
    "RestoreNVRAMVariables",
    "SystemPartitionSize",
    "UpdateBaseband",
}


class FrameworksError(RuntimeError):
    pass


def frameworks_path(value: str | None) -> Path:
    return Path(value) if value else DEFAULT_FRAMEWORKS


def load_plist(path: Path) -> Any:
    try:
        with path.open("rb") as f:
            return plistlib.load(f)
    except FileNotFoundError as exc:
        raise FrameworksError(f"file not found: {path}") from exc
    except Exception as exc:
        raise FrameworksError(f"could not parse plist {path}: {exc}") from exc


def restore_options_from_plist(path: Path) -> dict[str, Any]:
    value = load_plist(path)
    if isinstance(value, dict) and isinstance(value.get("RestoreOptions"), dict):
        return dict(value["RestoreOptions"])
    if isinstance(value, dict):
        return dict(value)
    raise FrameworksError(f"{path} does not contain a restore options dictionary")


def summarize_restore_options(options: dict[str, Any]) -> dict[str, Any]:
    return {
        "key_count": len(options),
        "sensitive_keys": {key: options.get(key) for key in sorted(SENSITIVE_RESTORE_KEYS) if key in options},
        "signing_server": options.get("AuthInstallSigningServerURL"),
        "restore_bundle_path": options.get("RestoreBundlePath"),
        "auth_install_variant": options.get("AuthInstallVariant"),
        "create_filesystem_partitions": options.get("CreateFilesystemPartitions"),
        "post_restore_action": options.get("PostRestoreAction"),
        "updates_baseband": options.get("UpdateBaseband"),
        "installs_recovery_os": options.get("InstallRecoveryOS"),
        "modifies_nvram": bool(options.get("NVRAMVariableModifications") or options.get("RestoreNVRAMVariables")),
        "modifies_boot_args": bool(options.get("PersistantBootArgsModifications") or options.get("RestoreBootArgs")),
    }


def template_summaries(root: Path) -> list[dict[str, Any]]:
    rows = []
    for relative in RESTORE_TEMPLATE_PATHS:
        path = root / relative
        if not path.exists():
            continue
        options = restore_options_from_plist(path)
        rows.append({"path": str(path), "summary": summarize_restore_options(options)})
    return rows


def find_plists_in_prkit(path: Path) -> list[tuple[str, bytes]]:
    if path.is_dir():
        rows = []
        for child in path.rglob("*"):
            if child.is_file() and child.suffix.lower() in {".plist", ".pr"}:
                rows.append((str(child.relative_to(path)), child.read_bytes()))
        return rows
    if zipfile.is_zipfile(path):
        rows = []
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                lowered = name.lower()
                if lowered.endswith(".plist") or lowered.endswith(".pr"):
                    rows.append((name, archive.read(name)))
        return rows
    return [(path.name, path.read_bytes())]


def restore_options_from_prkit(path: Path) -> list[dict[str, Any]]:
    rows = []
    try:
        plist_rows = find_plists_in_prkit(path)
    except FileNotFoundError as exc:
        raise FrameworksError(f"path not found: {path}") from exc
    for name, data in plist_rows:
        try:
            value = plistlib.loads(data)
        except Exception:
            continue
        if isinstance(value, dict) and isinstance(value.get("RestoreOptions"), dict):
            options = dict(value["RestoreOptions"])
        elif isinstance(value, dict) and {"RestoreBundlePath", "AuthInstallVariant"} & set(value):
            options = dict(value)
        else:
            continue
        rows.append({"member": name, "summary": summarize_restore_options(options), "restore_options": options})
    return rows


def diagnose_message(message: str) -> list[dict[str, str]]:
    return [
        {"matched": needle, "guidance": guidance}
        for needle, guidance in RESTORE_ERROR_RULES
        if needle in message
    ]


def analyze_command(args: argparse.Namespace) -> int:
    root = frameworks_path(args.frameworks)
    payload = {
        "frameworks": str(root),
        "restore_frameworks": [
            {
                "name": "ATKImaging",
                "evidence": [
                    "ATKImagingRestorableMobileDevice restoreWithOptions",
                    "AMRestorableDeviceRestore import",
                    "ECID-based restorable device discovery",
                ],
            },
            {
                "name": "ATKImagingService",
                "evidence": [
                    "ATKImager restoreWithOptions/restoreWithImagerTask",
                    "PRKit RestoreOptions extraction",
                    "restore bundle path provider for SWE/NFA/Knox flows",
                    "DFU/recovery troubleshooting strings",
                ],
            },
            {
                "name": "FFDCSD",
                "evidence": [
                    "AMRestorableDeviceRestoreWithError import",
                    "BuildManifest board/chip matching helpers",
                    "DCSD RestoreOptions wrapper",
                ],
            },
            {
                "name": "CoreFactorySupport",
                "evidence": [
                    "AMRestorePerformDFURestore/AMRestorePerformRecoveryModeRestore imports",
                    "PersonalizedRestoreBundlePath handling",
                    "APTicket copy and RestoreSEP references",
                ],
            },
        ],
        "templates": template_summaries(root),
        "notes": [
            "iPS-UU imports restore template inspection and PRKit parsing only.",
            "Private MobileRestore, AuthInstall, DFU, recovery, APTicket, and factory security paths are not executed.",
        ],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def templates_command(args: argparse.Namespace) -> int:
    print(json.dumps(template_summaries(frameworks_path(args.frameworks)), indent=2, sort_keys=True))
    return 0


def inspect_prkit_command(args: argparse.Namespace) -> int:
    rows = restore_options_from_prkit(Path(args.path))
    payload: dict[str, Any] = {"path": str(Path(args.path).resolve()), "matches": rows}
    if not args.include_options:
        for row in payload["matches"]:
            row.pop("restore_options", None)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if rows else 1


def diagnose_command(args: argparse.Namespace) -> int:
    matches = diagnose_message(args.message)
    print(json.dumps({"message": args.message, "matches": matches}, indent=2, sort_keys=True))
    return 0 if matches else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze Frameworks restore resources")
    parser.add_argument("--frameworks", help="Path to Frameworks root; defaults to ./Frameworks")
    subcommands = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subcommands.add_parser("analyze", help="Print restore-related framework findings")
    analyze_parser.set_defaults(func=analyze_command)

    templates_parser = subcommands.add_parser("templates", help="Summarize bundled restore option templates")
    templates_parser.set_defaults(func=templates_command)

    prkit_parser = subcommands.add_parser("inspect-prkit", help="Extract RestoreOptions summaries from a PRKit/plist/PR document")
    prkit_parser.add_argument("path", help="Path to a PRKit directory/archive or restore options plist")
    prkit_parser.add_argument("--include-options", action="store_true", help="Include full RestoreOptions dictionaries")
    prkit_parser.set_defaults(func=inspect_prkit_command)

    diagnose_parser = subcommands.add_parser("diagnose", help="Diagnose ATK imaging restore messages")
    diagnose_parser.add_argument("message", help="ATK imaging message")
    diagnose_parser.set_defaults(func=diagnose_command)
    return parser


def main(argv: list[str] | None = None, show_intro: bool = False) -> int:
    if show_intro:
        print_intro()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FrameworksError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(show_intro=True))
