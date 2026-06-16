"""PurpleSNIFF diagnostics and device metadata helpers for iPS-UU."""

from __future__ import annotations

import argparse
import json
import plistlib
import sys
from pathlib import Path
from typing import Any

from .banner import print_intro

DEFAULT_SNIFF = Path("PurpleSNIFF.app")

SNIFF_ERROR_RULES = [
    ("AMRestoreRegisterForDeviceNotifications FAILED", "Restore/DFU/recovery notification registration failed."),
    ("Connecting to restore mode device failed", "Restore-mode device was detected but could not be queried."),
    ("Cannot start AFC service", "AFC service startup failed while collecting device-side files."),
    ("Connecting to AFC failed", "AFC connection failed after service startup."),
    ("Cannot establish AFC connection", "AFC socket/context setup failed."),
    ("Cannot open Factory Restore Marker", "Factory restore marker file could not be read from the device."),
    ("Looking for Factory Restore Marker failed", "Factory restore marker probe failed."),
    ("Failed request IOReg info using diagnosticsRelayHelper", "Diagnostics relay IORegistry request failed."),
    ("File relay failed", "Mobile file relay request failed."),
    ("Could not relay files from the device", "Mobile file relay could not collect requested sources."),
    ("Unknown lockdown property", "Requested lockdown key was not available on the device."),
    ("Are you sure you want to completely obliterate your device", "Diagnostics relay action can erase or DFU the device; keep this as manual review only."),
]

PLUGIN_FINDINGS = [
    {
        "plugin": "Download Logs",
        "principal_class": "SNIFFDownloadLogsPlugin",
        "services": ["com.apple.mobile.file_relay"],
        "mobiledevice_imports": ["AMDeviceRelayFile", "AMDeviceStartService"],
        "safe_use": "Plan or diagnose file relay log collection.",
    },
    {
        "plugin": "Recovery Mode",
        "principal_class": "SNIFFRecoveryModePlugin",
        "services": [],
        "mobiledevice_imports": [
            "AMRecoveryModeDeviceReboot",
            "AMRecoveryModeDeviceSendCommandToDevice",
            "AMRecoveryModeDeviceSetAutoBoot",
        ],
        "safe_use": "Document recovery-mode actions; iPS-UU does not execute them.",
    },
    {
        "plugin": "Diagnostics Relay",
        "principal_class": "SNIFFDiagnosticsRelayPlugin",
        "services": ["com.apple.mobile.diagnostics_relay"],
        "mobiledevice_imports": ["AMDeviceSecureStartService", "AMDServiceConnectionSendMessage", "AMDServiceConnectionReceive"],
        "safe_use": "Diagnose diagnostics relay and IORegistry request failures.",
    },
    {
        "plugin": "AppleTV & Lockdown",
        "principal_class": "SNIFFLockdownPlugin",
        "services": ["lockdown"],
        "mobiledevice_imports": ["AMDeviceCopyValue", "AMDeviceSetValue"],
        "safe_use": "Summarize lockdown property lookup behavior.",
    },
    {
        "plugin": "PDCA",
        "principal_class": "SNIFFPDCAPlugin",
        "services": ["PDCA web lookup"],
        "mobiledevice_imports": [],
        "safe_use": "Build serial/ECID lookup URLs for operator review.",
    },
]


class SNIFFError(RuntimeError):
    pass


def sniff_path(value: str | None) -> Path:
    return Path(value) if value else DEFAULT_SNIFF


def resource_path(root: Path, name: str) -> Path:
    return root / "Contents" / "Resources" / name


def load_plist(path: Path) -> Any:
    try:
        with path.open("rb") as f:
            return plistlib.load(f)
    except FileNotFoundError as exc:
        raise SNIFFError(f"file not found: {path}") from exc
    except Exception as exc:
        raise SNIFFError(f"could not parse plist {path}: {exc}") from exc


def flatten_key_labels(value: Any, prefix: str = "") -> dict[str, str]:
    rows: dict[str, str] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}/{key}" if prefix else str(key)
            if isinstance(child, dict):
                rows.update(flatten_key_labels(child, child_prefix))
            else:
                rows[child_prefix] = str(child)
    return rows


def key_template_summary(root: Path) -> dict[str, Any]:
    value = load_plist(resource_path(root, "KeysTemplate.plist"))
    labels = flatten_key_labels(value)
    return {
        "path": str(resource_path(root, "KeysTemplate.plist")),
        "key_count": len(labels),
        "restore_relevant_labels": {
            key: label
            for key, label in labels.items()
            if any(needle in key.lower() for needle in ("restore", "buildversion", "serial", "baseband", "fdr", "secure"))
        },
    }


def device_map_summary(root: Path, limit: int = 12) -> dict[str, Any]:
    path = resource_path(root, "device_map.plist")
    value = load_plist(path)
    if not isinstance(value, dict):
        raise SNIFFError("device_map.plist is not a dictionary")
    product_types = {}
    restore_rule_count = 0
    component_names: set[str] = set()
    for board, info in value.items():
        if not isinstance(info, dict):
            continue
        product_type = info.get("ProductType")
        if product_type:
            product_types[str(product_type)] = str(board)
        manifest = info.get("Manifest")
        if isinstance(manifest, dict):
            for variant in manifest.values():
                if not isinstance(variant, dict):
                    continue
                component_names.update(str(name) for name in variant)
                for component in variant.values():
                    if isinstance(component, dict) and component.get("RestoreRequestRules"):
                        restore_rule_count += len(component.get("RestoreRequestRules") or [])
    return {
        "path": str(path),
        "board_count": len(value),
        "product_type_count": len(product_types),
        "sample_product_types": dict(sorted(product_types.items())[:limit]),
        "component_name_count": len(component_names),
        "sample_components": sorted(component_names)[:limit],
        "restore_request_rule_count": restore_rule_count,
        "notes": [
            "device_map.plist is metadata for device identification and firmware component labeling.",
            "iPS-UU summarizes it only; it does not apply RestoreRequestRules or generate tickets.",
        ],
    }


def lookup_product(root: Path, product_type: str) -> dict[str, Any]:
    value = load_plist(resource_path(root, "device_map.plist"))
    if not isinstance(value, dict):
        raise SNIFFError("device_map.plist is not a dictionary")
    matches = []
    for board, info in value.items():
        if not isinstance(info, dict) or info.get("ProductType") != product_type:
            continue
        manifest = info.get("Manifest") if isinstance(info.get("Manifest"), dict) else {}
        variants = sorted(str(name) for name in manifest)
        components = sorted({str(component) for variant in manifest.values() if isinstance(variant, dict) for component in variant})[:40]
        matches.append(
            {
                "board": str(board),
                "product_type": product_type,
                "board_id": info.get("BoardID"),
                "chip_id": info.get("ChipID"),
                "platform": info.get("Platform"),
                "platform_name": info.get("PlatformName"),
                "sdk_platform": info.get("SDKPlatform"),
                "image_format": info.get("ImageFormat"),
                "variants": variants,
                "sample_components": components,
            }
        )
    return {"product_type": product_type, "matches": matches}


def diagnose_message(message: str) -> list[dict[str, str]]:
    return [
        {"matched": needle, "guidance": guidance}
        for needle, guidance in SNIFF_ERROR_RULES
        if needle in message
    ]


def analyze_command(args: argparse.Namespace) -> int:
    root = sniff_path(args.sniff)
    payload = {
        "sniff": str(root),
        "bundle": {
            "identifier": "com.apple.factory.SNIFF",
            "name": "PurpleSNIFF",
            "version": "2.0",
        },
        "restore_relevance": {
            "firmware_restore_execution_found": False,
            "mobile_restore_observer_found": True,
            "imports_restore_observation": [
                "AMRestoreRegisterForDeviceNotifications",
                "AMRestoreModeDeviceCopyRestoreLog",
                "AMRestoreModeDeviceGetProgress",
                "AMRestoreModeDeviceCopyEcid",
                "AMRestoreModeDeviceCopyBoardConfig",
            ],
            "unsigned_downgrade_or_offline_signing_found": False,
        },
        "plugins": PLUGIN_FINDINGS,
        "key_template": key_template_summary(root),
        "device_map": device_map_summary(root, limit=args.limit),
        "notes": [
            "PurpleSNIFF is useful for restore-state observation and diagnostics, not restore execution.",
            "iPS-UU imports metadata summaries and error guidance only.",
        ],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def keys_command(args: argparse.Namespace) -> int:
    print(json.dumps(key_template_summary(sniff_path(args.sniff)), indent=2, sort_keys=True))
    return 0


def device_map_command(args: argparse.Namespace) -> int:
    print(json.dumps(device_map_summary(sniff_path(args.sniff), limit=args.limit), indent=2, sort_keys=True))
    return 0


def lookup_command(args: argparse.Namespace) -> int:
    payload = lookup_product(sniff_path(args.sniff), args.product_type)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["matches"] else 1


def diagnose_command(args: argparse.Namespace) -> int:
    matches = diagnose_message(args.message)
    print(json.dumps({"message": args.message, "matches": matches}, indent=2, sort_keys=True))
    return 0 if matches else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze PurpleSNIFF restore-state diagnostics")
    parser.add_argument("--sniff", help="Path to PurpleSNIFF.app; defaults to ./PurpleSNIFF.app")
    parser.add_argument("--limit", type=int, default=12, help="Number of sample device-map entries to print")
    subcommands = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subcommands.add_parser("analyze", help="Print PurpleSNIFF findings")
    analyze_parser.add_argument("--limit", type=int, default=12, help="Number of sample device-map entries to print")
    analyze_parser.set_defaults(func=analyze_command)

    keys_parser = subcommands.add_parser("keys", help="Print restore-relevant key labels from KeysTemplate.plist")
    keys_parser.set_defaults(func=keys_command)

    map_parser = subcommands.add_parser("device-map", help="Summarize PurpleSNIFF device_map.plist")
    map_parser.add_argument("--limit", type=int, default=12, help="Number of sample device-map entries to print")
    map_parser.set_defaults(func=device_map_command)

    lookup_parser = subcommands.add_parser("lookup-product", help="Look up a ProductType in device_map.plist")
    lookup_parser.add_argument("product_type", help="ProductType, for example iPhone10,6")
    lookup_parser.set_defaults(func=lookup_command)

    diagnose_parser = subcommands.add_parser("diagnose", help="Diagnose PurpleSNIFF/MobileDevice messages")
    diagnose_parser.add_argument("message", help="PurpleSNIFF message")
    diagnose_parser.set_defaults(func=diagnose_command)
    return parser


def main(argv: list[str] | None = None, show_intro: bool = False) -> int:
    if show_intro:
        print_intro()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SNIFFError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(show_intro=True))
