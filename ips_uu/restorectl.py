"""Safe restore proof-of-concept wrapper for Apple-signed restore flows."""

from __future__ import annotations

import argparse
import json
import plistlib
import shutil
import subprocess
import sys
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .banner import print_intro
from .planner import (
    PlannerError,
    build_tuple,
    choose_identity,
    downgrade_assessment,
    load_build_manifest,
    supported_product_types,
)
from .services.tool_resolver import idevicerestore_available, resolve_idevicerestore


class RestoreCtlError(RuntimeError):
    pass


TOOL_CATALOG = {
    "idevicerestore": {
        "classification": "supported_restore_executor",
        "execution_supported": True,
        "notes": [
            "Supported executor for normal Apple-signed restores.",
            "Final signing/APTicket validation is delegated to idevicerestore and Apple's normal restore flow.",
        ],
    },
    "ideviceinfo": {
        "classification": "supported_detection_tool",
        "execution_supported": False,
        "notes": ["Used for normal-mode lockdown device metadata in dry-run preflight."],
    },
    "irecovery": {
        "classification": "supported_detection_tool",
        "execution_supported": False,
        "notes": ["Used for recovery/DFU-style metadata in dry-run preflight."],
    },
    "mobile_restore": {
        "classification": "unsupported_internal_restore_tool",
        "execution_supported": False,
        "notes": [
            "Internal/private restore tool found on some factory stations.",
            "Not wired into restorectl because behavior, entitlement requirements, and signing semantics are not public/stable.",
        ],
    },
    "prestore": {
        "classification": "unsupported_internal_restore_tool",
        "execution_supported": False,
        "notes": [
            "Internal/private restore-style tool.",
            "Not executed by restorectl; use documented restore tooling instead.",
        ],
    },
    "factory_purple_restore": {
        "classification": "unsupported_internal_restore_tool",
        "execution_supported": False,
        "notes": [
            "Factory/private PurpleRestore-style tool.",
            "Not executed by restorectl and not reverse-engineered into an executor.",
        ],
    },
    "goldrestore": {
        "classification": "unsupported_internal_restore_tool",
        "execution_supported": False,
        "notes": [
            "Factory/private restore tool.",
            "Not executed by restorectl because it is not a documented public restore interface.",
        ],
    },
    "goldrestore2": {
        "classification": "unsupported_internal_restore_tool",
        "execution_supported": False,
        "notes": [
            "Factory/private restore tool.",
            "Not executed by restorectl because it is not a documented public restore interface.",
        ],
    },
    "factory_demo_restore": {
        "classification": "unsupported_internal_restore_tool",
        "execution_supported": False,
        "notes": [
            "Factory/demo restore tool.",
            "Not executed by restorectl because it may depend on private factory environment behavior.",
        ],
    },
}


@dataclass
class DeviceSnapshot:
    detection_method: str
    current_mode: str
    product_type: str | None = None
    product_version: str | None = None
    build_version: str | None = None
    ecid: str | None = None
    udid: str | None = None
    device_name: str | None = None
    raw: dict[str, Any] | None = None
    error: str | None = None


def run_command(command: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise RestoreCtlError(f"required tool not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RestoreCtlError(f"timed out running {' '.join(command)}") from exc


def decimal_to_hex(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return f"0x{int(str(value), 10):X}"
    except ValueError:
        return str(value)


def detect_normal_device(device: str) -> DeviceSnapshot | None:
    binary = shutil.which("ideviceinfo")
    if not binary:
        return None
    command = [binary, "-x"]
    if device != "auto":
        command.extend(["-u", device])
    completed = run_command(command)
    if completed.returncode != 0:
        return DeviceSnapshot(
            detection_method="ideviceinfo",
            current_mode="unknown",
            error=(completed.stderr or completed.stdout).strip() or "ideviceinfo failed",
        )
    try:
        info = plistlib.loads(completed.stdout.encode())
    except Exception as exc:
        return DeviceSnapshot(
            detection_method="ideviceinfo",
            current_mode="unknown",
            error=f"could not parse ideviceinfo plist output: {exc}",
        )
    if not isinstance(info, dict):
        return DeviceSnapshot(detection_method="ideviceinfo", current_mode="unknown", error="ideviceinfo output was not a dictionary")
    return DeviceSnapshot(
        detection_method="ideviceinfo",
        current_mode="normal",
        product_type=str(info.get("ProductType")) if info.get("ProductType") else None,
        product_version=str(info.get("ProductVersion")) if info.get("ProductVersion") else None,
        build_version=str(info.get("BuildVersion")) if info.get("BuildVersion") else None,
        ecid=decimal_to_hex(str(info.get("UniqueChipID")) if info.get("UniqueChipID") else None),
        udid=str(info.get("UniqueDeviceID")) if info.get("UniqueDeviceID") else None,
        device_name=str(info.get("DeviceName")) if info.get("DeviceName") else None,
        raw={key: info.get(key) for key in ("ProductType", "ProductVersion", "BuildVersion", "UniqueChipID", "UniqueDeviceID", "DeviceName")},
    )


def parse_irecovery_query(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            values[key.strip().lower()] = value.strip()
        elif "=" in line:
            key, value = line.split("=", 1)
            values[key.strip().lower()] = value.strip()
    return values


def detect_recovery_or_dfu(device: str) -> DeviceSnapshot | None:
    binary = shutil.which("irecovery")
    if not binary:
        return None
    command = [binary, "-q"]
    if device != "auto":
        command = [binary, "-i", device, "-q"]
    completed = run_command(command)
    if completed.returncode != 0:
        return DeviceSnapshot(
            detection_method="irecovery",
            current_mode="unknown",
            error=(completed.stderr or completed.stdout).strip() or "irecovery failed",
        )
    values = parse_irecovery_query(completed.stdout)
    mode = values.get("mode") or values.get("device mode") or "recovery_or_dfu"
    return DeviceSnapshot(
        detection_method="irecovery",
        current_mode=mode.lower(),
        product_type=values.get("product") or values.get("product type"),
        ecid=values.get("ecid"),
        raw=values,
    )


def detect_device(device: str) -> DeviceSnapshot:
    normal = detect_normal_device(device)
    if normal and normal.current_mode == "normal":
        return normal
    recovery = detect_recovery_or_dfu(device)
    if recovery and not recovery.error:
        return recovery
    if normal and normal.error:
        return normal
    if recovery and recovery.error:
        return recovery
    return DeviceSnapshot(
        detection_method="none",
        current_mode="not_detected",
        error="Neither ideviceinfo nor irecovery detected a device.",
    )


def manifest_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_version": manifest.get("ProductVersion"),
        "product_build_version": manifest.get("ProductBuildVersion"),
        "supported_product_types": supported_product_types(manifest),
        "build_identity_count": len(manifest.get("BuildIdentities") or []),
    }


def load_optional_plist_from_ipsw(ipsw: Path, name: str) -> dict[str, Any] | None:
    try:
        with zipfile.ZipFile(ipsw) as archive:
            candidates = [entry for entry in archive.namelist() if entry.endswith(name)]
            if not candidates:
                return None
            preferred = name if name in candidates else candidates[0]
            with archive.open(preferred) as handle:
                value = plistlib.load(handle)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def identity_has_component(identity: Any, component: str) -> bool:
    return component in set(identity.manifest_keys)


def compatibility_report(
    device: DeviceSnapshot,
    manifest: dict[str, Any],
    identity: Any,
    current_build: str | None,
    requested_product_type: str | None,
) -> dict[str, Any]:
    ipsw_products = supported_product_types(manifest)
    product_type = device.product_type or requested_product_type
    build_version = identity.build_version or str(manifest.get("ProductBuildVersion") or "")
    downgrade = False
    if build_version and current_build:
        downgrade = build_tuple(build_version) < build_tuple(current_build)
    return {
        "product_type_match": product_type in ipsw_products if product_type else None,
        "product_type_used_for_match": product_type,
        "selected_identity_variant": identity.variant,
        "selected_identity_device_class": identity.device_class,
        "target_build": build_version or None,
        "current_build": current_build,
        "downgrade_attempt": downgrade,
        "sep_component_in_selected_identity": identity_has_component(identity, "SEP"),
        "restore_sep_component_in_selected_identity": identity_has_component(identity, "RestoreSEP"),
        "baseband_component_in_selected_identity": identity_has_component(identity, "BasebandFirmware"),
        "sep_baseband_policy": [
            "Only bundled IPSW components are considered.",
            "External SEP/baseband images are not accepted by this PoC.",
            "Final SEP/baseband and APTicket validation is left to Apple's normal restore flow.",
        ],
    }


def signing_report(args: argparse.Namespace, downgrade_attempt: bool) -> dict[str, Any]:
    checker = shutil.which("tsschecker")
    return {
        "status": "not_verified_in_preflight",
        "checker": checker,
        "apple_tss_required": True,
        "downgrade_requires_online_apple_signing": bool(downgrade_attempt),
        "execution_policy": [
            "No custom signing server is supported.",
            "No unsigned mode is supported.",
            "No manifest, ticket, APTicket, nonce, SEP, or baseband patching is supported.",
            "Actual execution delegates signature/APTicket validation to idevicerestore and Apple's normal signing flow.",
        ],
        "note": "Install a supported signing-status checker if this needs pre-execution Apple signing confirmation.",
    }


def idevicerestore_command(binary: str, ipsw: Path, install_mode: str, device: DeviceSnapshot) -> list[str]:
    command = [binary]
    if install_mode == "erase":
        command.append("-e")
    if device.udid:
        command.extend(["-u", device.udid])
    command.append(str(ipsw))
    return command


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    ipsw = Path(args.ipsw)
    try:
        manifest = load_build_manifest(ipsw)
        restore_plist = load_optional_plist_from_ipsw(ipsw, "Restore.plist")
        device = detect_device(args.device)
        product_type = args.product_type or device.product_type
        identity = choose_identity(manifest, product_type, args.device_class, args.variant)
    except PlannerError as exc:
        raise RestoreCtlError(str(exc)) from exc

    current_build = args.current_build or device.build_version
    compatibility = compatibility_report(device, manifest, identity, current_build, product_type)
    signing = signing_report(args, bool(compatibility["downgrade_attempt"]))
    binary = resolve_idevicerestore(args.idevicerestore)
    command = idevicerestore_command(binary or "idevicerestore", ipsw, args.install_mode, device)
    guardrails = [
        "Refuse custom signing servers and offline signing overrides.",
        "Refuse unsigned firmware and do not expose any unsigned restore flags.",
        "Refuse external SEP/baseband component overrides.",
        "Refuse execution without --i-understand-this-erases-device for erase installs.",
        "Never patch manifests, tickets, APTickets, nonces, SEP, baseband, or bootchain files.",
        "Never continue after idevicerestore/AuthInstall reports signing, APTicket, nonce, or validation failure.",
    ]
    warnings: list[str] = []
    if device.error:
        warnings.append(f"Device detection warning: {device.error}")
    if compatibility["product_type_match"] is False:
        warnings.append("Detected ProductType is not listed in the IPSW SupportedProductTypes.")
    if compatibility["downgrade_attempt"]:
        warnings.append("Target build is older than the current build; execution must rely on Apple's online signing validation.")
    if not binary:
        warnings.append("idevicerestore was not found on PATH or in the local compiled source tree; execution is unavailable until it is installed or built.")

    return {
        "dry_run": not args.execute,
        "will_execute": bool(args.execute),
        "device": asdict(device),
        "ipsw": {
            "path": str(ipsw.resolve()),
            **manifest_summary(manifest),
            "restore_plist_present": restore_plist is not None,
            "restore_plist_keys": sorted(str(key) for key in (restore_plist or {}).keys()),
        },
        "selected_identity": identity.__dict__,
        "compatibility": compatibility,
        "signing_status": signing,
        "restore_plan": {
            "backend": "idevicerestore",
            "install_mode": args.install_mode,
            "command": command,
            "erases_device": args.install_mode == "erase",
        },
        "downgrade_assessment": downgrade_assessment(identity.build_version, current_build, args.install_mode),
        "guardrails": guardrails,
        "warnings": warnings,
        "limitations": [
            "This PoC does not call MobileDevice private restore APIs.",
            "Preflight does not create a TSS request or prove Apple signing status.",
            "Actual signing validity is enforced by the supported restore executor during normal restore.",
        ],
    }


def enforce_execution_guardrails(args: argparse.Namespace, plan: dict[str, Any]) -> None:
    if args.install_mode == "erase" and not args.i_understand_this_erases_device:
        raise RestoreCtlError("erase restore requires --i-understand-this-erases-device")
    if plan["compatibility"]["product_type_match"] is not True:
        raise RestoreCtlError("refusing restore: detected or requested ProductType was not confirmed compatible with the IPSW")
    if plan["compatibility"]["downgrade_attempt"]:
        raise RestoreCtlError("refusing restore: downgrade attempt was not preflight-confirmed as currently signed by Apple")
    if not idevicerestore_available(args.idevicerestore):
        raise RestoreCtlError("idevicerestore was not found. Install or build a supported restore executor and retry.")


def restore_command(args: argparse.Namespace) -> int:
    plan = build_plan(args)
    print(json.dumps(plan, indent=2, sort_keys=True))
    if not args.execute:
        return 0
    enforce_execution_guardrails(args, plan)
    completed = subprocess.run(plan["restore_plan"]["command"], check=False)
    return completed.returncode


def tool_inventory() -> dict[str, Any]:
    tools: dict[str, Any] = {}
    for name, metadata in TOOL_CATALOG.items():
        path = shutil.which(name)
        tools[name] = {
            "path": path,
            "installed": bool(path),
            **metadata,
        }
    return {
        "tools": tools,
        "execution_policy": [
            "restorectl executes only supported_restore_executor tools.",
            "Internal/private restore tools are inventory-only and are not reverse-engineered or invoked.",
            "Unsigned restore, custom signing, private entitlement, and factory-only restore paths remain unsupported.",
        ],
    }


def tools_command(_args: argparse.Namespace) -> int:
    print(json.dumps(tool_inventory(), indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="restorectl",
        description="Safe Apple-signed iOS restore proof-of-concept wrapper.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  Dry-run preflight:
    restorectl restore --ipsw ./firmware.ipsw --device auto --dry-run

  Dry-run with explicit ProductType:
    restorectl restore --ipsw ./firmware.ipsw --device auto --product-type iPhone13,2 --dry-run

  Execute a normal Apple-signed erase restore through idevicerestore:
    restorectl restore --ipsw ./firmware.ipsw --device auto --execute --i-understand-this-erases-device

notes:
  This command does not bypass Apple signing and does not support unsigned downgrades.
  Actual execution only uses idevicerestore and Apple's normal restore validation.
""",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    tools_parser = subcommands.add_parser("tools", help="Inventory supported and unsupported local restore-related tools")
    tools_parser.set_defaults(func=tools_command)

    restore_parser = subcommands.add_parser("restore", help="Plan or execute a normal Apple-signed restore")
    restore_parser.add_argument("--ipsw", required=True, help="Path to an IPSW file")
    restore_parser.add_argument("--device", default="auto", help="auto, normal-mode UDID, or recovery ECID")
    restore_parser.add_argument("--product-type", help="Override detected ProductType, for example iPhone13,2")
    restore_parser.add_argument("--device-class", help="BuildManifest Info.DeviceClass/board config")
    restore_parser.add_argument("--variant", help="AuthInstall variant to select")
    restore_parser.add_argument("--current-build", help="Override detected current build for downgrade assessment")
    restore_parser.add_argument("--install-mode", choices=("erase", "update"), default="erase")
    restore_parser.add_argument("--idevicerestore", help="Path to idevicerestore; defaults to PATH lookup")
    mode = restore_parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Print preflight and restore plan without executing; default")
    mode.add_argument("--execute", action="store_true", help="Execute through idevicerestore after printing the plan")
    restore_parser.add_argument(
        "--i-understand-this-erases-device",
        action="store_true",
        help="Required for --execute with --install-mode erase",
    )
    restore_parser.set_defaults(func=restore_command)
    return parser


def main(argv: list[str] | None = None, show_intro: bool = False) -> int:
    if show_intro:
        print_intro()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RestoreCtlError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(show_intro=True))
