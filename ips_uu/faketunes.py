"""FakeTunes sync, backup, and backup-restore diagnostics for iPS-UU."""

from __future__ import annotations

import argparse
import json
import plistlib
import sys
from pathlib import Path
from typing import Any

from .banner import print_intro

DEFAULT_APP = Path("FakeTunes.app")

LINKED_FRAMEWORKS = [
    "DeviceLink.framework",
    "SyncServices.framework",
    "MobileDevice.framework",
]

MOBILEDEVICE_IMPORTS = [
    "AMDCopyErrorText",
    "AMDSecureListenForNotifications",
    "AMDSecureObserveNotification",
    "AMDeviceConnect",
    "AMDeviceDisconnect",
    "AMDeviceSecureStartService",
    "AMDeviceStartSession",
    "AMDeviceStopSession",
    "AMDeviceValidatePairing",
]

DEVICELINK_IMPORTS = [
    "DLDeviceCopyAMDValue",
    "DLDeviceGetAMDevice",
    "DLDeviceGetName",
    "DLDeviceGetUDID",
    "DLDeviceGetWithUDID",
    "DLDeviceListenerCreateWithCallbacks",
    "DLDeviceListenerDestroy",
    "DLDevicePair",
    "DLDeviceSetName",
    "DLDeviceValidatePairing",
]

BACKUP_RESTORE_OPTIONS = [
    "RestoreShouldReboot",
    "RestorePreserveSettings",
    "RestorePreserveCameraRoll",
    "RestoreDontCopyBackup",
    "ShouldPerformSplitRestore",
]

MOBILESYNC_REQUESTS = [
    "AMSBackupRequest",
    "AMSBackupOptionsKey",
    "AMSRestoreWithApplicationsRequest",
    "AMSRestoreRestoreOptionsKey",
    "AMSGetSourcesForRestoreRequest",
    "AMSGetCompatibleSourcesForRestoreRequest",
    "AMSGetBackupApplications",
    "AMSGetBackupInfo",
    "AMSChangeBackupPassword",
    "AMSEnableCloudBackup",
    "AMSSubmitRestoreLogRequest",
]

DATA_CLASSES = [
    "com.apple.Calendars",
    "com.apple.Contacts",
    "com.apple.Bookmarks",
    "com.apple.MailAccounts",
    "com.apple.Notes",
]

ERROR_RULES = [
    ("Purple Restore is running. Ignoring attached device.", "FakeTunes intentionally avoids device sync/backup work while Purple Restore is running."),
    ("Another instance of sync or backup/restore is currently running", "A MobileSync operation is already active."),
    ("Backup manifest from computer is invalid", "The selected backup metadata is corrupt or incompatible."),
    ("Device refused restore request", "The device rejected a backup-restore request."),
    ("Timeout waiting for device to restore", "Backup restore did not complete before timeout."),
    ("Device refused backup request", "The device rejected a backup request."),
    ("The backup does not exist", "Selected backup source is missing."),
    ("Restoring the device partially failed", "Backup restore completed with partial failure."),
    ("Invalid password", "Encrypted backup password was rejected."),
    ("No password found in keychain for backup", "Encrypted backup restore needs a password that was not available in keychain."),
    ("Could not get backup info", "MobileSync could not read backup metadata."),
    ("Couldn't start notification service", "MobileDevice notification_proxy service failed to start."),
    ("Could not pair with device", "Device pairing failed before sync/backup."),
    ("Device software is out of date", "Device sync protocol version is too old."),
    ("Host version is out of date", "Host sync tooling is too old for the device."),
]


class FakeTunesError(RuntimeError):
    pass


def app_path(value: str | None) -> Path:
    return Path(value) if value else DEFAULT_APP


def require_app(path: Path) -> None:
    if not path.exists():
        raise FakeTunesError(f"app bundle not found: {path}")


def load_plist(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as f:
            value = plistlib.load(f)
    except FileNotFoundError as exc:
        raise FakeTunesError(f"file not found: {path}") from exc
    except Exception as exc:
        raise FakeTunesError(f"could not parse plist {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FakeTunesError(f"plist is not a dictionary: {path}")
    return value


def bundle_info(path: Path) -> dict[str, Any]:
    info = load_plist(path / "Contents" / "Info.plist")
    version = load_plist(path / "Contents" / "version.plist")
    return {
        "identifier": info.get("CFBundleIdentifier"),
        "short_version": info.get("CFBundleShortVersionString"),
        "bundle_version": info.get("CFBundleVersion"),
        "project_name": version.get("ProjectName"),
        "build_alias": version.get("BuildAliasOf"),
        "build_version": version.get("BuildVersion"),
        "executable": info.get("CFBundleExecutable"),
    }


def analyze_app(path: Path) -> dict[str, Any]:
    require_app(path)
    return {
        "app": str(path),
        "bundle": bundle_info(path),
        "purpose": "Host-side DeviceLink/MobileSync test client for sync, backup, backup restore, migration, and crash-log copy flows.",
        "restore_relevance": {
            "firmware_restore_logic_found": False,
            "ipsw_or_buildmanifest_logic_found": False,
            "mobile_restore_api_found": False,
            "tss_shsh_ap_ticket_logic_found": False,
            "unsigned_downgrade_or_offline_signing_found": False,
            "backup_restore_logic_found": True,
            "purple_restore_guard_found": True,
            "useful_to_ips_uu": "Distinguish firmware restore from MobileSync backup restore and diagnose post-restore backup/sync failures.",
        },
        "linked_frameworks": LINKED_FRAMEWORKS,
        "mobiledevice_imports": MOBILEDEVICE_IMPORTS,
        "devicelink_imports": DEVICELINK_IMPORTS,
        "backup_restore_options": BACKUP_RESTORE_OPTIONS,
        "mobilesync_requests": MOBILESYNC_REQUESTS,
        "data_classes": DATA_CLASSES,
        "services_and_domains": [
            "com.apple.mobile.backup",
            "com.apple.mobile.notification_proxy",
            "com.apple.mobile.data_sync",
            "com.apple.mobile.backup.domain_changed",
            "com.apple.PurpleRestore",
        ],
        "paths": [
            "Library/Application Support/MobileSync/Backup",
            "AppleMobileDeviceHelper.app",
            "AppleMobileSync",
            "AppleMobileBackup",
            "MDCrashReportTool",
        ],
        "notes": [
            "FakeTunes restore means MobileSync backup restore, not firmware restore.",
            "iPS-UU does not execute DeviceLink, MobileDevice services, backup, restore, migration, or crash-log copy.",
        ],
    }


def restore_template() -> dict[str, Any]:
    return {
        "request": "AMSRestoreWithApplicationsRequest",
        "backup_source": {
            "root": "Library/Application Support/MobileSync/Backup",
            "source_target_identifier": None,
            "device_identifier": None,
        },
        "options": {
            "RestoreShouldReboot": True,
            "RestorePreserveSettings": True,
            "RestorePreserveCameraRoll": True,
            "RestoreDontCopyBackup": False,
            "ShouldPerformSplitRestore": False,
        },
        "encrypted_backup": {
            "is_encrypted": None,
            "password_source": "keychain-or-operator-provided",
        },
        "safe_notes": [
            "This describes MobileSync backup restore options only.",
            "It does not restore firmware, personalize images, contact TSS, or alter SEP/baseband state.",
            "Use it to classify logs and avoid confusing backup restore failures with IPSW restore failures.",
        ],
    }


def diagnose_message(message: str) -> list[dict[str, str]]:
    return [
        {"matched": needle, "guidance": guidance}
        for needle, guidance in ERROR_RULES
        if needle in message
    ]


def analyze_command(args: argparse.Namespace) -> int:
    print(json.dumps(analyze_app(app_path(args.app)), indent=2, sort_keys=True))
    return 0


def template_command(_args: argparse.Namespace) -> int:
    print(json.dumps(restore_template(), indent=2, sort_keys=True))
    return 0


def diagnose_command(args: argparse.Namespace) -> int:
    matches = diagnose_message(args.message)
    print(json.dumps({"message": args.message, "matches": matches}, indent=2, sort_keys=True))
    return 0 if matches else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze FakeTunes MobileSync backup-restore logic")
    parser.add_argument("--app", help="Path to FakeTunes.app; defaults to ./FakeTunes.app")
    subcommands = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subcommands.add_parser("analyze", help="Print FakeTunes findings")
    analyze_parser.set_defaults(func=analyze_command)

    template_parser = subcommands.add_parser("template", help="Print safe MobileSync restore option template")
    template_parser.set_defaults(func=template_command)

    diagnose_parser = subcommands.add_parser("diagnose", help="Diagnose FakeTunes sync/backup/restore messages")
    diagnose_parser.add_argument("message", help="FakeTunes message")
    diagnose_parser.set_defaults(func=diagnose_command)
    return parser


def main(argv: list[str] | None = None, show_intro: bool = False) -> int:
    if show_intro:
        print_intro()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FakeTunesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(show_intro=True))
