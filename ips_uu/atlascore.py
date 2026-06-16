"""AtlasCore2 metadata diagnostics for iPS-UU."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .banner import print_intro

DEFAULT_BINARY = Path("AtlasCore2")

ATLAS_PATHS = {
    "state_root": "~/Library/Atlas2",
    "system_assets": "/usr/local/Atlas/Assets",
    "user_assets": "~/Library/Atlas2/Assets",
    "user_config": "~/Library/Atlas2/Config",
    "user_sequences": "~/Library/Atlas2/Sequences",
    "user_actions": "~/Library/Atlas2/Actions",
    "system_plugins": "/usr/local/Atlas/Plugins",
    "user_plugins": "~/Library/Atlas2/Plugins",
}

ATLAS_COMPONENTS = [
    "AtlasDetectionAdaptor",
    "AtlasGroupAdaptor",
    "AtlasIPCDelegate",
    "AtlasDetectionProcess",
    "AtlasGroupProcess",
    "AtlasListener",
]

ATLAS_ERROR_RULES = [
    ("sanityCheckAtlasUIConfig", "Atlas UI configuration sanity check failed or was invoked."),
    ("startDetectionSequence", "Atlas is starting a device detection sequence."),
    ("waitDevicesDisappearedForGroup", "Atlas is waiting for detected devices to disconnect."),
    ("getConnectedSlotsForGroup", "Atlas is enumerating connected station slots."),
    ("responseAssertion", "Atlas received or evaluated a response assertion."),
    ("stopGroup", "Atlas group process stop path was invoked."),
]


class AtlasCoreError(RuntimeError):
    pass


def binary_path(value: str | None) -> Path:
    return Path(value) if value else DEFAULT_BINARY


def require_binary(path: Path) -> None:
    if not path.exists():
        raise AtlasCoreError(f"binary not found: {path}")


def analyze_binary(path: Path) -> dict[str, Any]:
    require_binary(path)
    return {
        "binary": str(path),
        "program": "AtlasCore2",
        "project": "Atlas-2.31.1.2",
        "purpose": "Atlas station coordinator process for detection, group execution, IPC, and data reporting.",
        "linked_frameworks": [
            "AtlasStationCoordinator.framework",
            "AtlasDataReporting.framework",
            "AtlasLogging.framework",
            "AtlasIPC.framework",
        ],
        "restore_relevance": {
            "firmware_restore_logic_found": False,
            "mobiledevice_imports_found": False,
            "mobile_restore_api_found": False,
            "ipsw_or_buildmanifest_logic_found": False,
            "tss_shsh_ap_ticket_logic_found": False,
            "useful_to_ips_uu": "Station/process metadata and reporting context only.",
        },
        "components": ATLAS_COMPONENTS,
        "paths": ATLAS_PATHS,
        "report_classes": [
            "ATKAttributeReport",
            "ATKBinaryReport",
            "ATKBlobReport",
            "ATKDataReport",
            "ATKParametricReport",
        ],
        "notes": [
            "AtlasCore2 does not perform iOS firmware restore.",
            "iPS-UU uses this only to document Atlas station paths and process names.",
        ],
    }


def diagnose_message(message: str) -> list[dict[str, str]]:
    return [
        {"matched": needle, "guidance": guidance}
        for needle, guidance in ATLAS_ERROR_RULES
        if needle in message
    ]


def analyze_command(args: argparse.Namespace) -> int:
    print(json.dumps(analyze_binary(binary_path(args.binary)), indent=2, sort_keys=True))
    return 0


def paths_command(args: argparse.Namespace) -> int:
    print(json.dumps({"paths": ATLAS_PATHS, "components": ATLAS_COMPONENTS}, indent=2, sort_keys=True))
    return 0


def diagnose_command(args: argparse.Namespace) -> int:
    matches = diagnose_message(args.message)
    print(json.dumps({"message": args.message, "matches": matches}, indent=2, sort_keys=True))
    return 0 if matches else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze AtlasCore2 station coordinator metadata")
    parser.add_argument("--binary", help="Path to AtlasCore2; defaults to ./AtlasCore2")
    subcommands = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subcommands.add_parser("analyze", help="Print AtlasCore2 findings")
    analyze_parser.set_defaults(func=analyze_command)

    paths_parser = subcommands.add_parser("paths", help="Print Atlas station paths and process names")
    paths_parser.set_defaults(func=paths_command)

    diagnose_parser = subcommands.add_parser("diagnose", help="Diagnose AtlasCore2 messages")
    diagnose_parser.add_argument("message", help="AtlasCore2 message")
    diagnose_parser.set_defaults(func=diagnose_command)
    return parser


def main(argv: list[str] | None = None, show_intro: bool = False) -> int:
    if show_intro:
        print_intro()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except AtlasCoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(show_intro=True))
