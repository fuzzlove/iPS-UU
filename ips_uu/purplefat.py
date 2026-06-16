"""PurpleFAT factory activation diagnostics for iPS-UU."""

from __future__ import annotations

import argparse
import json
import plistlib
import sys
from pathlib import Path
from typing import Any

from .banner import print_intro

DEFAULT_APP = Path("PurpleFAT.app")

MOBILEDEVICE_IMPORTS = [
    "AMDeviceActivate",
    "AMDeviceConnect",
    "AMDeviceCopyValue",
    "AMDeviceCreateActivationInfoWithOptions",
    "AMDeviceDeactivate",
    "AMDeviceDisconnect",
    "AMDeviceNotificationSubscribe",
    "AMDeviceNotificationUnsubscribe",
    "AMDevicePair",
    "AMDeviceStartSession",
    "AMDeviceStopSession",
    "AMDeviceValidatePairing",
]

ACTIVATION_METHODS = [
    "gatherActivationInfoForDevice:",
    "requestActivationRecordUsingHTTPPost:fromServer:returningErrorString:",
    "requestActivationRecord:fromServer:returningErrorString:",
    "deactivateDevice:returningErrorString:",
    "activateDevice:withActivationRecord:returningErrorString:",
    "reportActivationToShopFloor:",
    "sendSFCHttpPostActivationStart:withStartTime:andStopTime:",
    "tellPuddingAboutActivation:forDevice:withErrorMessage:withStartTime:andStopTime:",
]

XPC_SERVICES = {
    "ShopFloorControlXPC": {
        "bundle_id": "com.apple.ShopFloorControlXPC",
        "protocol": "ShopFloorControlXPCProtocol",
        "methods": [
            "SFCHttpPost:forDevice:withStationName:withStationID:withSwVer:withStartTime:andStopTime:withProduct:withMacAddress:withFailingTests:withFailureMessage:withReply:",
            "IPSendReport:forDevice:withErrorMessage:withStartTime:andStopTime:withReply:",
        ],
        "safe_use": "Document shop-floor activation reporting fields.",
    },
    "securityinfofetcher": {
        "bundle_id": "com.apple.securityinfofetcher",
        "protocol": "SecurityInfoQueryProtocol",
        "methods": [
            "grabInfoFromSecurityServerCombined",
            "grabInfoFromSecurityServer:forType:forHostInfo:",
            "validatedPropertyListFromSignedDictionary:usingPublicKey:errorDescription:",
        ],
        "safe_use": "Document factory security configuration validation.",
    },
}

ENDPOINT_PATTERNS = [
    "http://%@:%@/raptor/processor",
    "http://gh/%@/",
    "http://%@/%@",
    "zhongnanhai.asia.apple.com",
]

SECURITY_ARTIFACTS = [
    "/usr/local/share/misc/factory_restore_key.pub",
    "~/Library/Logs/factory_security_xpc.log",
    "com.apple.factory.securityapp_checkconfig",
    "coreosfactory",
]

ERROR_RULES = [
    ("Cannot connect to attached device", "MobileDevice connection failed before activation."),
    ("Cannot pair with attached device", "Pairing failed before activation."),
    ("Cannot start session with attached device", "MobileDevice session startup failed."),
    ("Cannot validate activation information", "Activation info returned by the device could not be validated."),
    ("Cannot determine activation state", "ActivationState could not be read from the device."),
    ("does not need factory activation", "Device reports FactoryActivated or otherwise does not need this flow."),
    ("Request for activation record", "Activation server request failed or returned an unusable record."),
    ("Activation of device", "Device activation failed after an activation record was retrieved."),
    ("Unable to first deactivate device", "Pre-activation deactivation failed."),
    ("Unable to activate device", "AMDeviceActivate failed."),
    ("BasebandStatus", "Activation flow checks baseband readiness before continuing."),
    ("Waiting for baseband", "Activation is blocked waiting for baseband boot/readiness."),
    ("Could not send activation information to server", "Activation server upload failed."),
    ("Could not retrieve activation record from server", "Activation record download failed."),
    ("Security has been broken", "Factory security check failed; keep as manual review."),
    ("Unable to report activation", "Shop-floor/Pudding reporting failed after activation."),
]


class PurpleFATError(RuntimeError):
    pass


def app_path(value: str | None) -> Path:
    return Path(value) if value else DEFAULT_APP


def require_app(path: Path) -> None:
    if not path.exists():
        raise PurpleFATError(f"app bundle not found: {path}")


def load_plist(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as f:
            value = plistlib.load(f)
    except FileNotFoundError as exc:
        raise PurpleFATError(f"file not found: {path}") from exc
    except Exception as exc:
        raise PurpleFATError(f"could not parse plist {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PurpleFATError(f"plist is not a dictionary: {path}")
    return value


def bundle_info(path: Path) -> dict[str, Any]:
    info = load_plist(path / "Contents" / "Info.plist")
    return {
        "identifier": info.get("CFBundleIdentifier"),
        "name": info.get("CFBundleName"),
        "version": info.get("CFBundleVersion"),
        "executable": info.get("CFBundleExecutable"),
        "minimum_system_version": info.get("LSMinimumSystemVersion"),
    }


def analyze_app(path: Path) -> dict[str, Any]:
    require_app(path)
    return {
        "app": str(path),
        "bundle": bundle_info(path),
        "purpose": "Factory activation client for attached devices, with shop-floor and security-info XPC helpers.",
        "restore_relevance": {
            "firmware_restore_logic_found": False,
            "ipsw_or_buildmanifest_logic_found": False,
            "mobile_restore_api_found": False,
            "tss_shsh_ap_ticket_logic_found": False,
            "unsigned_downgrade_or_offline_signing_found": False,
            "mobiledevice_activation_logic_found": True,
            "baseband_readiness_check_found": True,
            "useful_to_ips_uu": "Activation-state/baseband readiness diagnostics and factory reporting metadata only.",
        },
        "linked_private_frameworks": ["MobileDevice.framework"],
        "mobiledevice_imports": MOBILEDEVICE_IMPORTS,
        "activation_methods": ACTIVATION_METHODS,
        "xpc_services": XPC_SERVICES,
        "endpoint_patterns": ENDPOINT_PATTERNS,
        "security_artifacts": SECURITY_ARTIFACTS,
        "device_keys": [
            "ActivationInfoXML",
            "ActivationState",
            "FactoryActivated",
            "BasebandStatus",
        ],
        "notes": [
            "PurpleFAT performs factory activation, not IPSW restore.",
            "iPS-UU does not call MobileDevice activation/deactivation or factory network services.",
        ],
    }


def activation_template() -> dict[str, Any]:
    return {
        "device_identifiers": {
            "serial_number": None,
            "imei": None,
            "ecid": None,
        },
        "device_state_checks": [
            "Confirm MobileDevice pairing/session succeeds.",
            "Read ActivationState.",
            "Skip devices that are already FactoryActivated.",
            "Wait for BasebandStatus readiness before requesting activation.",
        ],
        "server_request_shape": {
            "activation_info_xml": "ActivationInfoXML from AMDeviceCreateActivationInfoWithOptions",
            "server_pattern": "http://<host>:<port>/raptor/processor",
            "expected_response": "ActivationRecord plist/dictionary",
        },
        "reporting_fields": [
            "device",
            "stationName",
            "stationID",
            "swVer",
            "product",
            "macAddress",
            "failingTests",
            "failureMessage",
            "startTime",
            "stopTime",
        ],
        "safe_notes": [
            "This is a planning template only.",
            "Do not reuse activation records across devices.",
            "This has no SHSH/APTicket, TSS, IPSW, or restore-signing behavior.",
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
    print(json.dumps(activation_template(), indent=2, sort_keys=True))
    return 0


def diagnose_command(args: argparse.Namespace) -> int:
    matches = diagnose_message(args.message)
    print(json.dumps({"message": args.message, "matches": matches}, indent=2, sort_keys=True))
    return 0 if matches else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze PurpleFAT factory activation logic")
    parser.add_argument("--app", help="Path to PurpleFAT.app; defaults to ./PurpleFAT.app")
    subcommands = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subcommands.add_parser("analyze", help="Print PurpleFAT findings")
    analyze_parser.set_defaults(func=analyze_command)

    template_parser = subcommands.add_parser("template", help="Print safe activation planning template")
    template_parser.set_defaults(func=template_command)

    diagnose_parser = subcommands.add_parser("diagnose", help="Diagnose PurpleFAT activation messages")
    diagnose_parser.add_argument("message", help="PurpleFAT message")
    diagnose_parser.set_defaults(func=diagnose_command)
    return parser


def main(argv: list[str] | None = None, show_intro: bool = False) -> int:
    if show_intro:
        print_intro()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except PurpleFATError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(show_intro=True))
