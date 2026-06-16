"""DCSD restore diagnostics and resource analysis helpers for iPS-UU."""

from __future__ import annotations

import argparse
import json
import plistlib
import sys
from pathlib import Path
from typing import Any

from .banner import print_intro
from .planner import PlannerError, identities, load_build_manifest

DEFAULT_DCSD = Path("DCSD")


class DCSDError(RuntimeError):
    pass


DCSD_RESTORE_ERROR_RULES = [
    ("AMRestorableDeviceRestoreWithError failed", "MobileRestore returned a restore execution error."),
    ("Restore top level error", "Top-level MobileRestore error was reported."),
    ("Restore bottom level error", "Bottom-level MobileRestore error was reported."),
    ("BuildManifest.plist doesn't contain overridable diags image path", "No DCSD-overridable diags image was found for this device."),
    ("Device Is In RestoreOS", "Device is in RestoreOS; DCSD expects reboot before continuing this path."),
    ("Bundle on the station does not have connected devices information for restore", "Restore bundle metadata did not match the connected device."),
    ("Connected device's restore bundle info not found", "No matching restore bundle entry was found for the connected device."),
    ("pr selected for restoring  does not have postRestoreAction as Reboot", "PR document postRestoreAction should be Reboot for this workflow."),
    ("Fail to delete Prevent-Restores NVRAM flag", "DCSD log collection failed to remove the prevent-restores flag."),
    ("CopyList.plist is missing logpartition section", "Log collection copy list is missing LogPartition entries."),
    ("Device failed to enter diags", "Post-restore diagnostic boot sequence failed."),
    ("Failed to get key or nonce for writing control bit", "Factory/security control-bit credential flow failed."),
    ("not able to read ECID in FactorySupportRamDisk", "Device swap check could not read ECID in factory ramdisk."),
    ("Failed to read device info in FactorySupportRamDisk", "FactorySupportRamDisk device attribute query failed."),
    ("PreventRestoresIfNVRAMSet", "Restore options or checks reference prevent-restores NVRAM gating."),
]


def dcsd_path(value: str | None) -> Path:
    return Path(value) if value else DEFAULT_DCSD


def resource_path(root: Path, name: str) -> Path:
    return root / "Resources" / name


def load_plist(path: Path) -> Any:
    try:
        with path.open("rb") as f:
            return plistlib.load(f)
    except FileNotFoundError as exc:
        raise DCSDError(f"DCSD resource not found: {path}") from exc
    except Exception as exc:
        raise DCSDError(f"Could not parse plist {path}: {exc}") from exc


def post_restore_sequence(root: Path) -> list[dict[str, Any]]:
    value = load_plist(resource_path(root, "DCSD_PostRestoreSequence.plist"))
    sequence = value.get("Sequence") if isinstance(value, dict) else None
    if not isinstance(sequence, list):
        raise DCSDError("DCSD_PostRestoreSequence.plist has no Sequence array")
    return [dict(item) for item in sequence if isinstance(item, dict)]


def copy_list(root: Path) -> dict[str, Any]:
    value = load_plist(resource_path(root, "copyList.plist"))
    if not isinstance(value, dict):
        raise DCSDError("copyList.plist is not a dictionary")
    return value


def demo_table(root: Path) -> list[dict[str, Any]]:
    value = load_plist(resource_path(root, "DemoTable.plist"))
    table = value.get("DemoTable") if isinstance(value, dict) else None
    return [dict(item) for item in table or [] if isinstance(item, dict)]


def attrbextra_summary(root: Path) -> dict[str, Any]:
    value = load_plist(resource_path(root, "attrbextra.plist"))
    if not isinstance(value, dict):
        raise DCSDError("attrbextra.plist is not a dictionary")
    groups = {}
    for name, rows in value.items():
        if isinstance(rows, list):
            groups[str(name)] = {
                "count": len(rows),
                "ip_attribute_keys": sorted(
                    {
                        str(row.get("IPATTRBKEY"))
                        for row in rows
                        if isinstance(row, dict) and row.get("IPATTRBKEY")
                    }
                ),
            }
    return groups


def render_sequence(sequence: list[dict[str, Any]], values: dict[str, str]) -> list[dict[str, Any]]:
    rendered = []
    for index, step in enumerate(sequence):
        command = str(step.get("COMMAND", ""))
        scanned_key = step.get("SCANNEDKEY")
        if "%@" in command and scanned_key:
            command = command.replace("%@", values.get(str(scanned_key), f"<{scanned_key}>"))
        rendered.append(
            {
                "index": index,
                "type": step.get("TYPE"),
                "command": command,
                "expect": step.get("EXPECT"),
                "timeout": step.get("TIMEOUT"),
                "delay_before": step.get("DELAYBEFORE"),
                "delay_after": step.get("DELAYAFTER"),
                "scanned_key": scanned_key,
            }
        )
    return rendered


def find_diags_components(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for identity in identities(manifest):
        raw_identity = (manifest.get("BuildIdentities") or [])[identity.index]
        identity_manifest = raw_identity.get("Manifest") if isinstance(raw_identity, dict) else {}
        if not isinstance(identity_manifest, dict):
            continue
        components = []
        for key, value in sorted(identity_manifest.items()):
            if "diags" not in str(key).lower() and "diagnostic" not in str(key).lower():
                continue
            info = value.get("Info") if isinstance(value, dict) else {}
            components.append(
                {
                    "component": str(key),
                    "path": info.get("Path") if isinstance(info, dict) else None,
                }
            )
        if components:
            rows.append(
                {
                    "identity_index": identity.index,
                    "variant": identity.variant,
                    "device_class": identity.device_class,
                    "build_version": identity.build_version,
                    "components": components,
                }
            )
    return rows


def diagnose_message(message: str) -> list[dict[str, str]]:
    return [
        {"matched": needle, "guidance": guidance}
        for needle, guidance in DCSD_RESTORE_ERROR_RULES
        if needle in message
    ]


def analyze_command(args: argparse.Namespace) -> int:
    root = dcsd_path(args.dcsd)
    payload = {
        "dcsd": str(root),
        "post_restore_sequence": render_sequence(post_restore_sequence(root), {}),
        "copy_list": copy_list(root),
        "demo_table": demo_table(root),
        "attrbextra_summary": attrbextra_summary(root),
        "restore_error_rules": [
            {"matched": needle, "guidance": guidance}
            for needle, guidance in DCSD_RESTORE_ERROR_RULES
        ],
        "notes": [
            "DCSD includes MobileRestore execution in dcsd_worker, but iPS-UU imports only resource/config diagnostics.",
            "DCSD helper binaries can enter recovery, reboot recovery devices, copy logs, and manipulate prevent-restores; iPS-UU does not execute those actions.",
        ],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def sequence_command(args: argparse.Namespace) -> int:
    values = {}
    for item in args.value or []:
        key, _, value = item.partition("=")
        if not key or not _:
            raise DCSDError(f"Invalid --value {item!r}; expected KEY=VALUE")
        values[key] = value
    print(json.dumps(render_sequence(post_restore_sequence(dcsd_path(args.dcsd)), values), indent=2, sort_keys=True))
    return 0


def copy_plan_command(args: argparse.Namespace) -> int:
    payload = {
        "copy_list": copy_list(dcsd_path(args.dcsd)),
        "safe_notes": [
            "DCSD copyLogs supports AFC/ramdisk log collection and delete_prevent_restores, but this command only prints paths.",
            "Use this output to plan log collection; iPS-UU does not run AFC copy or NVRAM changes.",
        ],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def inspect_ipsw_command(args: argparse.Namespace) -> int:
    try:
        manifest = load_build_manifest(Path(args.ipsw))
    except PlannerError as exc:
        raise DCSDError(str(exc)) from exc
    payload = {
        "ipsw": str(Path(args.ipsw).resolve()),
        "diags_components": find_diags_components(manifest),
        "notes": [
            "Mirrors DCSD's interest in overridable diags images from BuildManifest.",
            "This does not personalize, boot, or restore a diagnostic image.",
        ],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def diagnose_command(args: argparse.Namespace) -> int:
    matches = diagnose_message(args.message)
    print(json.dumps({"message": args.message, "matches": matches}, indent=2, sort_keys=True))
    return 0 if matches else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze DCSD restore/log-collection resources")
    parser.add_argument("--dcsd", help="Path to DCSD bundle root; defaults to ./DCSD")
    subcommands = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subcommands.add_parser("analyze", help="Print DCSD restore/log resources")
    analyze_parser.set_defaults(func=analyze_command)

    sequence_parser = subcommands.add_parser("post-restore-sequence", help="Render DCSD post-restore UART sequence")
    sequence_parser.add_argument("--value", action="append", help="Substitute scanned value, for example SerialNumber=ABC")
    sequence_parser.set_defaults(func=sequence_command)

    copy_parser = subcommands.add_parser("copy-plan", help="Print DCSD log-copy source plan")
    copy_parser.set_defaults(func=copy_plan_command)

    inspect_parser = subcommands.add_parser("inspect-ipsw", help="Find DCSD-relevant diags components in an IPSW")
    inspect_parser.add_argument("ipsw", help="Path to an IPSW file")
    inspect_parser.set_defaults(func=inspect_ipsw_command)

    diagnose_parser = subcommands.add_parser("diagnose", help="Diagnose DCSD/MobileRestore messages")
    diagnose_parser.add_argument("message", help="Restore/log-collection message")
    diagnose_parser.set_defaults(func=diagnose_command)
    return parser


def main(argv: list[str] | None = None, show_intro: bool = False) -> int:
    if show_intro:
        print_intro()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except DCSDError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(show_intro=True))
