"""AirSwitch resource analysis and OTA planning helpers for iPS-UU."""

from __future__ import annotations

import argparse
import json
import plistlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .banner import print_intro

DEFAULT_CONTENTS_AS = Path("ContentsAS")
MOBILE_ASSET_TYPES = {
    "documentation": "com.apple.MobileAsset.SoftwareUpdateDocumentation",
    "brain": "com.apple.MobileAsset.MobileSoftwareUpdate.UpdateBrain",
    "software_update": "com.apple.MobileAsset.SoftwareUpdate",
}
BASEJUMPER = "https://basejumper.apple.com"
PURPLERESTORE_ALL_BUILDS_INDEX = "https://purplerestore.apple.com/index/v5_all_builds.plist"
PURPLERESTORE_IOS_RESTORE_INDEX = "https://purplerestore.apple.com/index/v5_iosrestoreimages.plist"


class AirSwitchError(RuntimeError):
    pass


@dataclass(frozen=True)
class OTAErrorRule:
    needle: str
    guidance: str

    def matches(self, message: str) -> bool:
        return self.needle in message


OTA_ERROR_RULES = [
    OTAErrorRule(
        'MobileSoftwareUpdateErrorDomain Code=2 "could not obtain device identity information',
        "Could not obtain device identity information; ensure the device is on a supported internal build.",
    ),
    OTAErrorRule(
        "TATSU server declined update image",
        "TATSU declined the OTA image. Check AppleConnect OTA settings and build eligibility.",
    ),
    OTAErrorRule(
        "Domain=MobileSoftwareUpdateErrorDomain Code=3",
        "MobileSoftwareUpdate returned code 3; inspect device update logs for the underlying failure.",
    ),
    OTAErrorRule(
        'Domain=MobileSoftwareUpdateErrorDomain Code=7 "Received XPC error Connection interrupted',
        "The update brain/service connection was interrupted; retry after rebooting or restarting update services.",
    ),
    OTAErrorRule(
        "Software update installer (Brain) is not available for download",
        "UpdateBrain is unavailable. Check AppleConnect, lock state, and the selected target build.",
    ),
    OTAErrorRule(
        'Code=26 "no stashbag - stashbag is required',
        "A stashbag is required for this OTA path. Unlock/authenticate the device before preparing the update.",
    ),
    OTAErrorRule(
        "Can't find trust cache for this build",
        "Trust cache is missing for this build. Check AppleConnect or choose a different build.",
    ),
    OTAErrorRule(
        "test_software_update: unrecognized option `-ssoToken'",
        "Device test_software_update does not support -ssoToken; run without SSO token support.",
    ),
    OTAErrorRule(
        "Updating to an earlier seed is not supported in OTA",
        "OTA cannot move to an earlier seed; use a normal signed restore path instead.",
    ),
]


def contents_as_path(value: str | None) -> Path:
    return Path(value) if value else DEFAULT_CONTENTS_AS


def resource_path(contents_as: Path, name: str) -> Path:
    return contents_as / "Resources" / name


def load_failure_guidance(contents_as: Path) -> dict[str, str]:
    path = resource_path(contents_as, "failureGuidance.plist")
    try:
        with path.open("rb") as f:
            value = plistlib.load(f)
    except FileNotFoundError as exc:
        raise AirSwitchError(f"AirSwitch failureGuidance.plist not found at {path}") from exc
    if not isinstance(value, dict):
        raise AirSwitchError(f"Expected plist dictionary in {path}")
    return {str(key): str(item) for key, item in value.items()}


def mobile_asset_urls(train: str, build: str | None, build_path_type: str, audience_uuid: str | None) -> dict[str, Any]:
    mode = build_path_type.lower()
    if mode == "audience":
        if not audience_uuid:
            raise AirSwitchError("audience UUID is required when build path type is Audience")
        return {
            "mode": "Audience",
            "pallas_enabled": True,
            "defaults": {
                "EnableLiveAssetServerV2": "on",
                "MobileAssetAssetAudience": audience_uuid,
            },
            "asset_urls": {},
        }

    if mode == "tot":
        base = f"{BASEJUMPER}/assets/{train}"
    elif mode == "livability":
        base = f"{BASEJUMPER}/livability/{train}"
    else:
        if not build:
            raise AirSwitchError("build is required for Specific build path type")
        base = f"{BASEJUMPER}/assets/{train}/{train}{build}"

    return {
        "mode": "ToT" if mode == "tot" else "Livability" if mode == "livability" else "Specific",
        "pallas_enabled": False,
        "defaults": {"EnableLiveAssetServerV2": "off"},
        "asset_urls": {
            key: {
                "asset_type": asset_type,
                "url": base,
            }
            for key, asset_type in MOBILE_ASSET_TYPES.items()
        },
        "brain_fallback_note": (
            "AirSwitch falls back to the train-level brain URL if the specific build brain URL returns 404."
            if mode not in {"tot", "livability"}
            else None
        ),
    }


def ota_manifest_paths(asset_root: str) -> dict[str, str]:
    asset_data = f"{asset_root.rstrip('/')}/AssetData"
    return {
        "asset_data": asset_data,
        "stashbag_build_manifest": f"{asset_data}/boot/BuildManifest.plist",
    }


def parse_update_scan_output(text: str) -> dict[str, Any]:
    values: dict[str, int] = {}
    for key in ("totalRequiredFreeSpace", "msuPrepareSize"):
        match = re.search(rf"{re.escape(key)}\D+([0-9]+)", text)
        if match:
            values[key] = int(match.group(1))
    result: dict[str, Any] = {"raw_values": values}
    if "totalRequiredFreeSpace" in values and "msuPrepareSize" in values:
        result["minimalRequiredFreeSpace"] = values["totalRequiredFreeSpace"] - values["msuPrepareSize"]
        result["delete_command"] = (
            "test_software_update -space "
            f"{values['totalRequiredFreeSpace']} -minimal {result['minimalRequiredFreeSpace']} -delete"
        )
    return result


def diagnose_message(message: str, guidance: dict[str, str]) -> dict[str, Any]:
    phase_matches = [
        {"phase": key, "guidance": value}
        for key, value in sorted(guidance.items())
        if key in message
    ]
    error_matches = [
        {"matched": rule.needle, "guidance": rule.guidance}
        for rule in OTA_ERROR_RULES
        if rule.matches(message)
    ]
    return {"message": message, "phase_matches": phase_matches, "error_matches": error_matches}


def analyze_command(args: argparse.Namespace) -> int:
    contents_as = contents_as_path(args.contents_as)
    payload = {
        "contents_as": str(contents_as),
        "failure_guidance": load_failure_guidance(contents_as),
        "known_indexes": {
            "all_builds": PURPLERESTORE_ALL_BUILDS_INDEX,
            "ios_restore_images": PURPLERESTORE_IOS_RESTORE_INDEX,
        },
        "mobile_asset_types": MOBILE_ASSET_TYPES,
        "notes": [
            "AirSwitch is OTA/MobileAsset oriented, not an IPSW restore executor.",
            "It imports MobileDevice device/session APIs but not AMRestorableDeviceRestore or AMRestorePerform* restore APIs.",
            "AirSwitch explicitly notes that updating to an earlier seed is not supported in OTA.",
        ],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def plan_ota_command(args: argparse.Namespace) -> int:
    payload = {
        "urls": mobile_asset_urls(args.train, args.build, args.build_path_type, args.audience_uuid),
        "manifest_paths": ota_manifest_paths(args.asset_root),
        "prepare_commands": {
            "purge_assets": "test_software_update --remove all",
            "prepare": "test_software_update {asset_data}".format(
                asset_data=ota_manifest_paths(args.asset_root)["asset_data"]
            ),
            "prepare_with_extra_params": "test_software_update {asset_data} {extra_params}".format(
                asset_data=ota_manifest_paths(args.asset_root)["asset_data"],
                extra_params=args.extra_params or "",
            ).strip(),
        },
        "notes": [
            "This is an OTA MobileAsset planning path from AirSwitch logic.",
            "It is not an unsigned downgrade path and does not bypass TSS/AuthInstall.",
            "AirSwitch logic says OTA updates to an earlier seed are unsupported.",
        ],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def diagnose_command(args: argparse.Namespace) -> int:
    guidance = load_failure_guidance(contents_as_path(args.contents_as))
    payload = diagnose_message(args.message, guidance)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["phase_matches"] or payload["error_matches"] else 1


def parse_scan_command(args: argparse.Namespace) -> int:
    text = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
    print(json.dumps(parse_update_scan_output(text), indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze AirSwitch OTA/MobileAsset restore-adjacent logic")
    parser.add_argument("--contents-as", help="Path to ContentsAS; defaults to ./ContentsAS")
    subcommands = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subcommands.add_parser("analyze", help="Print AirSwitch OTA findings")
    analyze_parser.set_defaults(func=analyze_command)

    plan_parser = subcommands.add_parser("plan-ota", help="Build an AirSwitch-style OTA MobileAsset plan")
    plan_parser.add_argument("--train", required=True, help="Train name, for example 21A")
    plan_parser.add_argument("--build", help="Build suffix/number used with Specific mode")
    plan_parser.add_argument("--build-path-type", choices=("ToT", "Livability", "Specific", "Audience"), default="Specific")
    plan_parser.add_argument("--audience-uuid", help="Audience UUID used with Audience mode")
    plan_parser.add_argument("--asset-root", default="/var/tmp/AirSwitch", help="Remote asset root before /AssetData")
    plan_parser.add_argument("--extra-params", help="Extra test_software_update parameters to include in the plan")
    plan_parser.set_defaults(func=plan_ota_command)

    diagnose_parser = subcommands.add_parser("diagnose", help="Diagnose AirSwitch/OTA update messages")
    diagnose_parser.add_argument("message", help="OTA log or failure message")
    diagnose_parser.set_defaults(func=diagnose_command)

    scan_parser = subcommands.add_parser("parse-scan", help="Parse test_software_update -scan output")
    scan_parser.add_argument("--file", help="File containing scan output; defaults to stdin")
    scan_parser.set_defaults(func=parse_scan_command)
    return parser


def main(argv: list[str] | None = None, show_intro: bool = False) -> int:
    if show_intro:
        print_intro()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except AirSwitchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(show_intro=True))
