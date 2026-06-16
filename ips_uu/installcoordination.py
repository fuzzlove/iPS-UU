"""InstallCoordination host test runner diagnostics for iPS-UU."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .banner import print_intro

DEFAULT_BINARY = Path("installcoordination_host_test_runner")

INSTALL_ERROR_RULES = [
    ("This app could not be installed because it does not work on this device.", "Device/platform compatibility check failed."),
    ("minimum required capabilities", "The target device does not satisfy the app capability requirements."),
    ("minimum OS version requirement", "The target OS is too old for the app bundle."),
    ("insufficient storage", "The target device does not have enough free space for the app install."),
    ("integrity could not be verified", "Code signature or package integrity verification failed."),
    ("code signature version is no longer supported", "The app uses an unsupported code signature format."),
    ("installableType must be kIXRemoteInstallableTypeEmbedded", "Embedded-device installs require the embedded installable type."),
    ("IXRemoteInstallOptions must have either an install target directory set, or an install target path set, not both", "Specify exactly one install target location."),
    ("Missing pairing ID for gizmo app install", "Watch/gizmo app install is missing the paired device identifier."),
    ("Failed to materialize placeholder", "InstallCoordination could not create the placeholder app record."),
    ("Coordinated install exists with different intent", "An existing coordinated install conflicts with the requested operation."),
    ("Failed to locate coordinated install", "The expected coordinated install record was not found."),
    ("Uninstall prohibited", "The target app cannot be uninstalled by policy."),
    ("Uninstall operation had both bundleID and path specified", "Specify bundle ID for embedded targets or path for macOS targets, not both."),
    ("Missing bundleID while performing uninstall when targeting an embedded platform", "Embedded uninstall requires a bundle ID."),
    ("Missing uninstall path while performing uninstall when targeting macOS", "macOS uninstall requires an app path."),
]

REMOTE_INSTALL_CONFIGURATION_FIELDS = [
    "bundleID",
    "localizedName",
    "installMode",
    "installableType",
    "importance",
    "stashMode",
    "pairedAutoInstallOverride",
    "provisioningProfileDatas",
    "provisioningProfileInstallFailureIsFatal",
    "sinfData",
    "storeMetadata",
    "deltaDirectoryURL",
    "remoteInstallTargetURL",
    "remoteInstallTargetDirectoryURL",
    "iconData",
    "iconType",
]


class InstallCoordinationError(RuntimeError):
    pass


def binary_path(value: str | None) -> Path:
    return Path(value) if value else DEFAULT_BINARY


def require_binary(path: Path) -> None:
    if not path.exists():
        raise InstallCoordinationError(f"binary not found: {path}")


def analyze_binary(path: Path) -> dict[str, Any]:
    require_binary(path)
    return {
        "binary": str(path),
        "program": "installcoordination_host_test_runner",
        "project": "InstallCoordination-1",
        "purpose": "Host-side tests for remote app install, revert stash, and uninstall flows.",
        "restore_relevance": {
            "firmware_restore_logic_found": False,
            "ipsw_or_buildmanifest_logic_found": False,
            "tss_shsh_ap_ticket_logic_found": False,
            "mobile_restore_api_found": False,
            "useful_to_ips_uu": "Post-restore app install diagnostics and remote install configuration auditing only.",
        },
        "linked_private_frameworks": [
            "MobileDevice.framework",
            "RemoteServiceDiscovery.framework",
            "RemoteXPC.framework",
        ],
        "mobiledevice_imports": [
            "AMDeviceCopyRemoteDevice",
            "AMDeviceCreateWithRemoteDevice",
        ],
        "remote_service": "com.apple.remote.installcoordination_proxy",
        "test_classes": ["IXRemoteInstallTests"],
        "operations": [
            "IXRemotePerformInstallation",
            "IXRemotePerformInstallationAsync",
            "IXRemoteRevertStash",
            "IXRemoteRevertStashAsync",
            "IXRemotePerformUninstallation",
            "IXRemotePerformUninstallationAsync",
            "IXRemotePerformUninstallationByPath",
            "IXRemotePerformUninstallationByPathAsync",
        ],
        "configuration_fields": REMOTE_INSTALL_CONFIGURATION_FIELDS,
        "notes": [
            "This tool targets app installation coordination, not firmware restore.",
            "iPS-UU does not execute remote install, uninstall, or stash operations.",
        ],
    }


def diagnose_message(message: str) -> list[dict[str, str]]:
    return [
        {"matched": needle, "guidance": guidance}
        for needle, guidance in INSTALL_ERROR_RULES
        if needle in message
    ]


def configuration_template() -> dict[str, Any]:
    return {
        "bundleID": "com.example.app",
        "localizedName": "Example",
        "installMode": "application",
        "installableType": "embedded-or-macos",
        "remoteInstallTargetURL": None,
        "remoteInstallTargetDirectoryURL": None,
        "deltaDirectoryURL": None,
        "provisioningProfileDatas": [],
        "provisioningProfileInstallFailureIsFatal": False,
        "sinfData": None,
        "storeMetadata": {},
        "safe_notes": [
            "Set either remoteInstallTargetURL or remoteInstallTargetDirectoryURL, not both.",
            "Embedded-device flows require bundleID and embedded installable type.",
            "This template is for audit/planning only; iPS-UU does not perform app installs.",
        ],
    }


def analyze_command(args: argparse.Namespace) -> int:
    print(json.dumps(analyze_binary(binary_path(args.binary)), indent=2, sort_keys=True))
    return 0


def fields_command(args: argparse.Namespace) -> int:
    print(json.dumps({"configuration_fields": REMOTE_INSTALL_CONFIGURATION_FIELDS, "template": configuration_template()}, indent=2, sort_keys=True))
    return 0


def diagnose_command(args: argparse.Namespace) -> int:
    matches = diagnose_message(args.message)
    print(json.dumps({"message": args.message, "matches": matches}, indent=2, sort_keys=True))
    return 0 if matches else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze InstallCoordination host test runner logic")
    parser.add_argument("--binary", help="Path to installcoordination_host_test_runner; defaults to ./installcoordination_host_test_runner")
    subcommands = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subcommands.add_parser("analyze", help="Print installcoordination_host_test_runner findings")
    analyze_parser.set_defaults(func=analyze_command)

    fields_parser = subcommands.add_parser("fields", help="Print remote install configuration fields")
    fields_parser.set_defaults(func=fields_command)

    diagnose_parser = subcommands.add_parser("diagnose", help="Diagnose InstallCoordination install/uninstall messages")
    diagnose_parser.add_argument("message", help="InstallCoordination message")
    diagnose_parser.set_defaults(func=diagnose_command)
    return parser


def main(argv: list[str] | None = None, show_intro: bool = False) -> int:
    if show_intro:
        print_intro()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except InstallCoordinationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(show_intro=True))
