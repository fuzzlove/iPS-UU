"""Research wrapper for lawful Apple-signed restore workflows.

This module intentionally avoids private MobileDevice/AuthInstall execution.
It inventories local restore surfaces, parses IPSW metadata, and can hand off
only to documented/public command-line behavior with explicit wipe consent.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .banner import print_intro
from .planner import PlannerError, choose_identity, downgrade_assessment, load_build_manifest, supported_product_types
from .restorectl import DeviceSnapshot, compatibility_report, detect_device, manifest_summary
from .services.contents_research_service import contents_requirements, signed_firmware_lookup
from .services.dependency_setup_service import dependency_setup
from .services.shsh_blob_service import inspect_blob
from .services.tool_resolver import cfgutil_available, resolve_cfgutil, idevicerestore_available, resolve_idevicerestore


CFGUTIL = Path("/Applications/Apple Configurator.app/Contents/MacOS/cfgutil")
CFGUTIL_MANPAGE = Path("/Applications/Apple Configurator.app/Contents/Resources/cfgutil.1")
APPLE_CONFIGURATOR = Path("/Applications/Apple Configurator.app")
MOBILEDEVICE = Path("/System/Library/PrivateFrameworks/MobileDevice.framework/MobileDevice")
DEVICERECOVERYD_PLIST = Path("/System/Library/LaunchDaemons/com.apple.devicerecoveryd.plist")
MOBILE_SOFTWAREUPDATED_PLIST = Path("/System/Library/LaunchDaemons/com.apple.mobile.softwareupdated.plist")
CONTENTS_ROOT = Path("Contents")
CONTENTS_LIBIDEVICERESTORE = CONTENTS_ROOT / "Frameworks/libidevicerestore.dylib"
CONTENTS_ITUNES_FLASH = CONTENTS_ROOT / "MacOS/iTunesFlash"


class RestoreResearchError(RuntimeError):
    pass


CONTENTS_RESTORE_METHODS: list[dict[str, Any]] = [
    {
        "id": "3utools_bundled_libidevicerestore_online_restore",
        "name": "Bundled libidevicerestore normal restore",
        "source": "Contents/Frameworks/libidevicerestore.dylib",
        "transport_or_api": "libidevicerestore API",
        "online_or_offline": "online",
        "purpose": "Normal IPSW restore using BuildManifest/Restore.plist parsing, device nonce collection, Apple TSS requests, and restore-mode component streaming.",
        "evidence": [
            "_idevicerestore_client_new",
            "_idevicerestore_set_ipsw",
            "_idevicerestore_set_progress_callback",
            "_idevicerestore_start",
            "_tss_request_send",
            "https://gs.apple.com/TSS/controller?action=2",
            "ERROR: Unable to proceed without a TSS record.",
        ],
        "safe_to_execute_in_ips_uu": True,
        "status": "implemented_via_safe_idevicerestore_backend",
        "refusal_or_guardrail": "Usable through the local/PATH idevicerestore CLI backend, not by loading the old embedded dylib directly.",
        "ips_uu_action": "method-run --method-id 3utools_bundled_libidevicerestore_online_restore --ipsw <firmware.ipsw> --dry-run",
    },
    {
        "id": "3utools_signed_firmware_query",
        "name": "Signed firmware discovery",
        "source": "Contents/Frameworks/libidevicerestore.dylib",
        "transport_or_api": "ipsw.me API plus Apple restore validation later",
        "online_or_offline": "online",
        "purpose": "Query currently signed firmware metadata and firmware URLs before selecting an IPSW.",
        "evidence": [
            "_ipsw_get_signed_firmwares",
            "_ipsw_download_latest_fw",
            "https://api.ipsw.me/v4/device/%s",
            "ERROR: Could not fetch list of signed firmwares.",
        ],
        "safe_to_execute_in_ips_uu": True,
        "status": "implemented_as_metadata_lookup",
        "refusal_or_guardrail": "Usable as public firmware metadata only. It does not replace Apple TSS validation during restore.",
        "ips_uu_action": "method-run --method-id 3utools_signed_firmware_query --product-type <ProductType>",
    },
    {
        "id": "3utools_custom_ipsw_import",
        "name": "Custom IPSW import and validation",
        "source": "Contents/MacOS/3uTools and Contents/Frameworks/libidevicerestore.dylib",
        "transport_or_api": "IPSW zip/plist parsing",
        "online_or_offline": "offline_preflight_only",
        "purpose": "Import a local IPSW, inspect BuildManifest.plist and Restore.plist, select firmware rows, and check ProductType compatibility.",
        "evidence": [
            "slotAddIpswFromFlashModule",
            "on_download_center_imprted_ipsw",
            "_ipsw_extract_build_manifest",
            "_ipsw_extract_restore_plist",
            "_build_manifest_check_compatibility",
        ],
        "safe_to_execute_in_ips_uu": True,
        "status": "implemented_as_dry_run_metadata",
        "refusal_or_guardrail": "Usable for offline metadata parsing and compatibility checks. It is not an offline restore.",
        "ips_uu_action": "method-run --method-id 3utools_custom_ipsw_import --ipsw <firmware.ipsw> --dry-run",
    },
    {
        "id": "3utools_shsh_query_download",
        "name": "SHSH query/download UI",
        "source": "Contents/MacOS/3uTools",
        "transport_or_api": "3uTools UI/tasks plus TSS-related restore library behavior",
        "online_or_offline": "online_or_cached_artifact",
        "purpose": "Query or save SHSH-related data for a device/firmware selection.",
        "evidence": [
            "QDialogDownloadShsh",
            "QDownloadShshModel",
            "task_get_shsh_datas",
            "task_save_shsh_datas",
            "SHSH saved to '%s'",
            "Using cached SHSH",
        ],
        "safe_to_execute_in_ips_uu": True,
        "status": "implemented_as_blob_inspector",
        "refusal_or_guardrail": "Usable as local SHSH/APTicket blob inspection only. iPS-UU does not fetch, replay, select, or abuse blobs for unsigned or offline restores.",
        "ips_uu_action": "method-run --method-id 3utools_shsh_query_download --shsh-blob <blob.shsh2>",
    },
    {
        "id": "3utools_dfu_recovery_flash",
        "name": "DFU/recovery restore flow",
        "source": "Contents/MacOS/3uTools and Contents/Frameworks/libidevicerestore.dylib",
        "transport_or_api": "irecovery/libidevicerestore DFU and recovery clients",
        "online_or_offline": "online_for_modern_signed_restore",
        "purpose": "Enter recovery/DFU, send iBSS/iBEC/RestoreRamDisk/KernelCache, and transition to restore mode.",
        "evidence": [
            "task_dfu_base",
            "task_dfu_flash",
            "task_dfu_exit_recovery_mode",
            "_dfu_send_component",
            "_recovery_send_ibec",
            "_recovery_send_ramdisk",
            "_recovery_enter_restore",
        ],
        "safe_to_execute_in_ips_uu": True,
        "status": "implemented_via_signed_restore_backend",
        "refusal_or_guardrail": "Usable only as normal signed restore handoff through idevicerestore/cfgutil. No pwned DFU, custom component sending, or exploit flow is implemented.",
        "ips_uu_action": "method-run --method-id 3utools_dfu_recovery_flash --ipsw <firmware.ipsw> --dry-run",
    },
    {
        "id": "3utools_itunesflash_mobiledevice_private_restore",
        "name": "iTunesFlash private MobileDevice restore",
        "source": "Contents/MacOS/iTunesFlash",
        "transport_or_api": "Private MobileDevice.framework AMRestorableDeviceRestore",
        "online_or_offline": "online_or_system_managed_by_private_api",
        "purpose": "Small helper that builds RestoreBundlePath/AuthInstallRestoreBehavior options and calls Apple's private MobileDevice restore API.",
        "evidence": [
            "AMRestorableDeviceRestore",
            "AMRestoreCreateDefaultOptions",
            "AMRestorableDeviceGetECID",
            "RestoreBundlePath",
            "AuthInstallRestoreBehavior",
            "AMRestoreEnableFileLogging",
        ],
        "safe_to_execute_in_ips_uu": False,
        "status": "blocked_private_api",
        "refusal_or_guardrail": "Private MobileDevice/AuthInstall execution is unstable and not a documented public backend.",
    },
    {
        "id": "3utools_super_restore_apps_backup",
        "name": "Super restore / iTunes backup and app restore",
        "source": "Contents/MacOS/3uTools and Contents/Frameworks/libidm.1.0.0.dylib",
        "transport_or_api": "MobileBackup2, installation_proxy, AFC, app restore tasks",
        "online_or_offline": "mostly_offline_device_data_restore",
        "purpose": "Restore user data, app lists, media, contacts, messages, and backup contents after or separate from firmware restore.",
        "evidence": [
            "QTaskSuperRestore",
            "QTaskResotreItunes",
            "CItunesRestoreWithAppDialog",
            "mobilebackup_request_restore",
            "RestoreApplications.plist",
            "com.apple.mobilebackup2",
        ],
        "safe_to_execute_in_ips_uu": True,
        "status": "implemented_as_backup_data_restore",
        "refusal_or_guardrail": "Usable as backup/data restore only. It is separate from firmware flashing and does not bypass activation or signing.",
        "ips_uu_action": "method-run --method-id 3utools_super_restore_apps_backup --backup-dir <backup-directory> --dry-run",
    },
]


ACTIONABLE_METHOD_IDS = {
    "3utools_bundled_libidevicerestore_online_restore",
    "3utools_signed_firmware_query",
    "3utools_custom_ipsw_import",
    "3utools_shsh_query_download",
    "3utools_dfu_recovery_flash",
    "3utools_super_restore_apps_backup",
}

BLOCKED_METHOD_REASONS = {
    "3utools_itunesflash_mobiledevice_private_restore": "Private MobileDevice/AuthInstall restore execution is blocked.",
}


def exists(path: Path) -> bool:
    return path.exists()


def tool_path(name: str) -> str | None:
    return shutil.which(name)


def cfgutil_installed() -> bool:
    return cfgutil_available()


def cfgutil_command(ipsw: Path, action: str, device: DeviceSnapshot, selector: str) -> list[str]:
    command = [resolve_cfgutil() or str(CFGUTIL), "--format", "JSON", "--progress"]
    if selector == "ecid" and device.ecid:
        command.extend(["--ecid", str(device.ecid)])
    command.extend([action, "-I", str(ipsw)])
    return command


def idevicerestore_command(ipsw: Path, action: str, device: DeviceSnapshot) -> list[str]:
    binary = resolve_idevicerestore() or "idevicerestore"
    command = [binary]
    if action == "restore":
        command.append("-e")
    if device.udid:
        command.extend(["-u", device.udid])
    command.append(str(ipsw))
    return command


def inventory() -> dict[str, Any]:
    return {
        "generated_by": "restore-research",
        "policy": {
            "supports_unsigned_firmware": False,
            "supports_security_bypass": False,
            "private_api_execution": False,
            "execution_backends": ["apple_configurator_cfgutil", "idevicerestore"],
        },
        "candidates": [
            {
                "id": "apple_configurator_cfgutil",
                "path": resolve_cfgutil() or str(CFGUTIL),
                "installed": cfgutil_installed(),
                "purpose": "Apple Configurator command-line device management, including documented restore/update/revive commands.",
                "callable_from_userland": True,
                "permissions_or_entitlements_required": [
                    "Runs as a userland CLI.",
                    "Requires a connected/trusted or restorable device and normal Apple restore eligibility.",
                    "Activation Lock, device lock state, supervision, and internet/signing requirements still apply.",
                ],
                "stability_risk": "medium",
                "execution_supported_by_restore_research": True,
                "evidence_source": [
                    str(CFGUTIL_MANPAGE),
                    "Bundled cfgutil.1 documents restore/update -I IPSW, --ecid selection, JSON/plist output, and --progress.",
                    "Binary strings include restore/update/revive, Restore System results, custom IPSW path, and firmware build lookup.",
                ],
            },
            {
                "id": "apple_configurator_app",
                "path": str(APPLE_CONFIGURATOR),
                "installed": exists(APPLE_CONFIGURATOR),
                "purpose": "GUI restore/update workflow and Automator actions around Configurator restore operations.",
                "callable_from_userland": "GUI only; not a stable automation backend for this tool.",
                "permissions_or_entitlements_required": [
                    "Apple-signed application with bundled/private frameworks.",
                    "May use app-internal restore assistants and XPC services not exposed as stable CLI API.",
                ],
                "stability_risk": "medium",
                "execution_supported_by_restore_research": False,
                "evidence_source": [
                    "Info.plist imports com.apple.iTunes.ipsw.",
                    "Bundle contains Restore Devices Automator actions.",
                    "Strings include restoreDevice:, installFirmwareWithURLs:targetedItems:, RestoreOptionsAssistant, and AuthInstallRestoreBehavior.",
                ],
            },
            {
                "id": "mobiledevice_framework",
                "path": str(MOBILEDEVICE),
                "installed": exists(MOBILEDEVICE),
                "purpose": "Private MobileDevice/AuthInstall library containing device discovery, TSS, personalization, DFU, and restore symbols.",
                "callable_from_userland": "Technically loadable but private and not treated as a supported backend.",
                "permissions_or_entitlements_required": [
                    "Private framework and unstable ABI.",
                    "Some flows appear to require privileged USB support, XPC services, or Apple-internal clients.",
                ],
                "stability_risk": "high",
                "execution_supported_by_restore_research": False,
                "evidence_source": [
                    "nm/strings show AMAuthInstall* TSS request APIs, AMRestore*/Restorable symbols, APNonce/SepNonce strings, and MobileRestore service names.",
                    "Direct use would be private API execution and is intentionally not implemented.",
                ],
            },
            {
                "id": "devicerecoveryd",
                "path": str(DEVICERECOVERYD_PLIST),
                "installed": exists(DEVICERECOVERYD_PLIST),
                "purpose": "LaunchDaemon exposing DeviceRecovery service Mach names.",
                "callable_from_userland": "Service endpoint only; no documented restore CLI contract found locally.",
                "permissions_or_entitlements_required": [
                    "Launchd-managed service.",
                    "Likely expects approved clients/protocols rather than ad hoc restore callers.",
                ],
                "stability_risk": "high",
                "execution_supported_by_restore_research": False,
                "evidence_source": [
                    "LaunchDaemon has MachServices com.apple.DeviceRecoveryEnvironmentService, com.apple.DeviceRecoveryOverrideService, and com.apple.DeviceRecoveryService.",
                ],
            },
            {
                "id": "mobile_softwareupdated",
                "path": str(MOBILE_SOFTWAREUPDATED_PLIST),
                "installed": exists(MOBILE_SOFTWAREUPDATED_PLIST),
                "purpose": "Mobile software update daemon for system-managed update services.",
                "callable_from_userland": "No documented direct restore CLI contract found locally.",
                "permissions_or_entitlements_required": ["Runs as _softwareupdate via launchd."],
                "stability_risk": "high",
                "execution_supported_by_restore_research": False,
                "evidence_source": [
                    "LaunchDaemon exposes com.apple.mobile.softwareupdated and runs MobileSoftwareUpdate.framework support binary.",
                ],
            },
            {
                "id": "libimobiledevice_comparison_tools",
                "path": {
                    "ideviceinfo": tool_path("ideviceinfo"),
                    "irecovery": tool_path("irecovery"),
                    "idevicerestore": resolve_idevicerestore(),
                },
                "installed": any((tool_path("ideviceinfo"), tool_path("irecovery"), resolve_idevicerestore())),
                "purpose": "Public/open-source equivalents for detection and restore comparison.",
                "callable_from_userland": True,
                "permissions_or_entitlements_required": ["Normal USB/device access."],
                "stability_risk": "low",
                "execution_supported_by_restore_research": "Supported as an explicit backend through the local tools/cfgutil wrapper when Apple Configurator is installed.",
                "evidence_source": [
                    "Used for normal-mode lockdown metadata and recovery/DFU metadata in dry-run preflight.",
                ],
            },
            {
                "id": "local_internal_restore_tools",
                "path": {
                    name: tool_path(name)
                    for name in (
                        "mobile_restore",
                        "prestore",
                        "factory_purple_restore",
                        "factory_demo_restore",
                        "goldrestore",
                        "goldrestore2",
                    )
                },
                "installed": any(
                    tool_path(name)
                    for name in (
                        "mobile_restore",
                        "prestore",
                        "factory_purple_restore",
                        "factory_demo_restore",
                        "goldrestore",
                        "goldrestore2",
                    )
                ),
                "purpose": "Factory/internal restore-style tools found locally.",
                "callable_from_userland": "Not supported here; behavior and policy are private/factory-specific.",
                "permissions_or_entitlements_required": ["Unknown; may require internal environment, credentials, entitlements, or device states."],
                "stability_risk": "critical",
                "execution_supported_by_restore_research": False,
                "evidence_source": ["PATH inventory only. Not reversed into an executor."],
            },
            {
                "id": "contents_3utools_bundle",
                "path": {
                    "root": str(CONTENTS_ROOT),
                    "libidevicerestore": str(CONTENTS_LIBIDEVICERESTORE),
                    "itunes_flash": str(CONTENTS_ITUNES_FLASH),
                },
                "installed": CONTENTS_ROOT.exists(),
                "purpose": "3uTools-style restore/flash app bundle with bundled libidevicerestore and a private MobileDevice iTunesFlash helper.",
                "callable_from_userland": "Inventory only in iPS-UU.",
                "permissions_or_entitlements_required": [
                    "Bundled app is ad-hoc signed locally.",
                    "iTunesFlash uses private MobileDevice.framework APIs.",
                    "libidevicerestore still requires normal Apple signing/TSS for modern restores.",
                ],
                "stability_risk": "high",
                "execution_supported_by_restore_research": False,
                "evidence_source": [
                    "CONTENTS_3UTOOLS_RESTORE_AUDIT.md",
                    "Bundled symbols include libidevicerestore restore/TSS callbacks and iTunesFlash AMRestorableDeviceRestore strings.",
                ],
            },
        ],
        "contents_restore_methods": CONTENTS_RESTORE_METHODS,
        "workflow_map": safe_workflow_map(),
    }


def safe_workflow_map() -> dict[str, Any]:
    return {
        "device_detection": [
            "Use ideviceinfo -x for normal-mode metadata when available.",
            "Use irecovery -q for recovery/DFU-style metadata when available.",
            "Use cfgutil list as the Apple Configurator equivalent for ECID/device selection; dry-run reports the command but does not require it.",
        ],
        "ipsw_validation": [
            "Open IPSW as a zip archive.",
            "Read BuildManifest.plist.",
            "Report ProductVersion, ProductBuildVersion, SupportedProductTypes, and selected BuildIdentity.",
            "Refuse execution if detected or requested ProductType is not supported by the IPSW.",
        ],
        "signing_tss_check": [
            "No offline TSS forge or replay is implemented.",
            "Preflight reports signing as not locally verified unless an Apple-supported backend performs live validation.",
            "Execution delegates signing/APTicket/nonce/SEP/baseband validation to cfgutil or idevicerestore and stops on nonzero failure.",
        ],
        "erase_update_decision": [
            "restore maps to an erase install.",
            "update maps to a data-preserving update where the backend supports it.",
            "cfgutil documentation states recovery-mode restore/update erases the device.",
        ],
        "restore_handoff": [
            "Apple Configurator backend: tools/cfgutil --format JSON --progress restore|update -I <ipsw>.",
            "Comparison fallback backend: idevicerestore -e <ipsw> for erase or idevicerestore <ipsw> for non-erase update-style handoff.",
            "No direct MobileDevice/AuthInstall private API calls.",
        ],
        "progress_error_reporting": [
            "cfgutil --progress with JSON output is the preferred observable progress channel.",
            "idevicerestore stdout/stderr is used only for fallback execution.",
            "Any signing, APTicket, APNonce, SEP/baseband, or validation failure is terminal.",
        ],
    }


def signing_status(downgrade_attempt: bool) -> dict[str, Any]:
    return {
        "status": "not_verified_in_local_dry_run",
        "apple_tss_required": True,
        "downgrade_requires_current_apple_signing": bool(downgrade_attempt),
        "offline_restore_supported": False,
        "notes": [
            "No local Apple signing-status oracle was found.",
            "cfgutil/Apple Configurator and idevicerestore perform live Apple signing and validation during normal restore.",
            "Dry-run cannot prove APTicket validity because the ticket is device, nonce, build, board, and component specific.",
        ],
    }


def choose_backend(name: str, action: str) -> str:
    if name != "auto":
        return name
    if idevicerestore_available():
        return "idevicerestore"
    if cfgutil_installed():
        return "cfgutil"
    return "none"


def backend_command(backend: str, ipsw: Path, action: str, device: DeviceSnapshot, selector: str) -> list[str]:
    if backend == "cfgutil":
        return cfgutil_command(ipsw, action, device, selector)
    if backend == "idevicerestore":
        return idevicerestore_command(ipsw, action, device)
    return []


def hard_refusals() -> list[str]:
    return [
        "Unsigned firmware or missing Apple signing validation.",
        "Downgrade attempts unless a supported Apple backend confirms current signing during restore.",
        "SEP/baseband component overrides or mismatches.",
        "Missing or invalid APTicket, APNonce, nonce, or personalization validation.",
        "Any request to patch, fake, skip, ignore, or continue past restore validation.",
        "Custom signing servers, offline signing overrides, exploit chains, pwned DFU, or private entitlement abuse.",
    ]


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    ipsw = Path(args.ipsw)
    try:
        manifest = load_build_manifest(ipsw)
        device = detect_device(args.device)
        product_type = args.product_type or device.product_type
        identity = choose_identity(manifest, product_type, args.device_class, args.variant)
    except PlannerError as exc:
        raise RestoreResearchError(str(exc)) from exc

    current_build = args.current_build or device.build_version
    compatibility = compatibility_report(device, manifest, identity, current_build, product_type)
    backend = choose_backend(args.backend, args.action)
    command = backend_command(backend, ipsw, args.action, device, args.device_selector)

    warnings: list[str] = []
    if device.error:
        warnings.append(f"Device detection warning: {device.error}")
    if compatibility["product_type_match"] is False:
        warnings.append("Detected/requested ProductType is not listed in the IPSW SupportedProductTypes.")
    if compatibility["downgrade_attempt"]:
        warnings.append("Target build is older than current build. Execution is refused unless Apple signing is validated by the backend.")
    if backend == "cfgutil" and not cfgutil_installed():
        warnings.append("cfgutil was selected but Apple Configurator's cfgutil was not found.")
    if backend == "idevicerestore" and not idevicerestore_available():
        warnings.append("idevicerestore was selected but no usable PATH or local compiled binary was found.")
    if backend == "none":
        warnings.append("No supported restore backend was found. Install Apple Configurator or idevicerestore.")
    if args.action == "update" and device.current_mode not in {"normal", "unknown", "not_detected"}:
        warnings.append("cfgutil documentation indicates recovery-mode update/restore erases the device.")

    return {
        "dry_run": not args.execute,
        "will_execute": bool(args.execute),
        "device": asdict(device),
        "ipsw": {
            "path": str(ipsw.resolve()),
            **manifest_summary(manifest),
        },
        "selected_identity": identity.__dict__,
        "compatibility": compatibility,
        "signing_status": signing_status(bool(compatibility["downgrade_attempt"])),
        "candidate_restore_backend": {
            "selected": backend,
            "reason": "Auto mode prefers a usable local/PATH idevicerestore, then Apple Configurator cfgutil.",
            "command": command,
            "erases_device": args.action == "restore",
        },
        "exact_handshake_plan": [
            "Detect device mode and identifiers using ideviceinfo/irecovery when available.",
            "Parse IPSW BuildManifest.plist and select a matching BuildIdentity.",
            "Confirm ProductType compatibility and report downgrade risk.",
            "Do not create, modify, replay, or bypass TSS/APTicket/nonce material.",
            f"If execution is explicitly confirmed, run: {' '.join(command) if command else '<no backend available>'}",
            "Stop immediately on any backend validation, signing, nonce, SEP/baseband, or restore failure.",
        ],
        "downgrade_assessment": downgrade_assessment(identity.build_version, current_build, args.action),
        "hard_refusals": hard_refusals(),
        "warnings": warnings,
        "limitations": [
            "Dry-run does not verify Apple signing status before execution.",
            "No private MobileDevice/AuthInstall APIs are called.",
            "No offline restore path is implemented because normal restores require Apple personalization for the exact device and nonce state.",
        ],
    }


def enforce_execution_guardrails(args: argparse.Namespace, plan: dict[str, Any]) -> None:
    if not args.execute:
        return
    if not args.erase_device or not args.i_understand_this_may_wipe_data:
        raise RestoreResearchError("--execute requires --erase-device and --i-understand-this-may-wipe-data")
    if plan["compatibility"]["product_type_match"] is False:
        raise RestoreResearchError("refusing restore: detected/requested ProductType does not match the IPSW")
    if plan["compatibility"]["downgrade_attempt"]:
        raise RestoreResearchError("refusing restore: downgrade cannot be preflight-confirmed as currently signed by Apple")
    backend = plan["candidate_restore_backend"]["selected"]
    if backend == "cfgutil" and not cfgutil_installed():
        raise RestoreResearchError("cfgutil was not found. Install Apple Configurator or use dry-run only.")
    if backend == "idevicerestore" and not idevicerestore_available():
        raise RestoreResearchError("idevicerestore was not found. Install or build a supported restore executor, or use cfgutil.")
    if backend not in {"cfgutil", "idevicerestore"}:
        raise RestoreResearchError("no supported restore backend is available")


def restore_command(args: argparse.Namespace) -> int:
    plan = build_plan(args)
    print(json.dumps(plan, indent=2, sort_keys=True))
    enforce_execution_guardrails(args, plan)
    if not args.execute:
        return 0
    completed = subprocess.run(plan["candidate_restore_backend"]["command"], check=False)
    return completed.returncode


def inventory_command(_args: argparse.Namespace) -> int:
    print(json.dumps(inventory(), indent=2, sort_keys=True))
    return 0


def methods_command(_args: argparse.Namespace) -> int:
    payload = {
        "generated_by": "restore-research",
        "source": "Contents 3uTools bundle audit",
        "policy": {
            "offline_restore_execution_supported": False,
            "unsigned_restore_supported": False,
            "private_mobiledevice_execution_supported": False,
            "safe_offline_scope": "IPSW metadata parsing and compatibility preflight only",
        },
        "methods": CONTENTS_RESTORE_METHODS,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _method_restore_namespace(args: argparse.Namespace, method_id: str) -> argparse.Namespace:
    return argparse.Namespace(
        ipsw=args.ipsw,
        device=args.device,
        device_selector=args.device_selector,
        product_type=args.product_type,
        device_class=args.device_class,
        variant=args.variant,
        current_build=args.current_build,
        action=args.action,
        backend=args.backend,
        dry_run=not args.execute,
        execute=args.execute,
        erase_device=args.erase_device,
        i_understand_this_may_wipe_data=args.i_understand_this_may_wipe_data,
        method_id=method_id,
    )


def backup_restore_plan(args: argparse.Namespace) -> dict[str, Any]:
    backend = args.backup_backend
    if backend == "auto":
        backend = "idevicebackup2" if shutil.which("idevicebackup2") else "cfgutil"
    device = detect_device(args.device)
    command: list[str] = []
    warnings: list[str] = []
    if device.error:
        warnings.append(f"Device detection warning: {device.error}")
    if backend == "idevicebackup2":
        binary = shutil.which("idevicebackup2")
        if not binary:
            warnings.append("idevicebackup2 was not found on PATH.")
        command = [binary or "idevicebackup2"]
        if device.udid:
            command.extend(["--udid", device.udid])
        command.append("restore")
        if args.restore_system:
            command.append("--system")
        if args.restore_settings:
            command.append("--settings")
        if args.skip_apps:
            command.append("--skip-apps")
        if args.no_reboot:
            command.append("--no-reboot")
        command.append(str(Path(args.backup_dir).expanduser()))
    elif backend == "cfgutil":
        binary = resolve_cfgutil()
        if not binary:
            warnings.append("cfgutil was not found. Install Apple Configurator.")
        command = [binary or "cfgutil", "--format", "JSON", "--progress"]
        if device.ecid:
            command.extend(["--ecid", str(device.ecid)])
        command.append("restore-backup")
        if args.backup_source:
            command.extend(["--source", args.backup_source])
    else:
        raise RestoreResearchError(f"unsupported backup backend: {backend}")

    backup_dir = Path(args.backup_dir).expanduser() if args.backup_dir else None
    if backend == "idevicebackup2" and (not backup_dir or not backup_dir.exists()):
        warnings.append("Backup directory was not found.")
    if backend == "cfgutil" and not args.backup_source:
        warnings.append("cfgutil restore-backup will look for a MobileSync backup matching the device UDID unless --backup-source is provided.")

    return {
        "dry_run": not args.execute,
        "will_execute": bool(args.execute),
        "method_adapter": {
            "method_id": "3utools_super_restore_apps_backup",
            "execution_model": "safe MobileBackup/cfgutil data restore adapter",
            "firmware_flash": False,
            "notes": [
                "This restores backup/app/data content, not iOS firmware.",
                "It does not bypass activation, signing, passcode, pairing, supervision, or device trust requirements.",
                "Encrypted backup passwords should be supplied through backend-supported interactive or environment flows, not stored in iPS-UU logs.",
            ],
        },
        "device": asdict(device),
        "backup_restore": {
            "backend": backend,
            "backup_dir": str(backup_dir) if backup_dir else None,
            "backup_source": args.backup_source,
            "command": command,
            "may_overwrite_device_data": True,
        },
        "guardrails": [
            "Requires explicit --execute plus --i-understand-this-may-wipe-data for execution.",
            "Does not patch backups, firmware, manifests, tickets, or trust checks.",
            "Stops on backend errors.",
        ],
        "warnings": warnings,
    }


def run_backup_restore_method(args: argparse.Namespace) -> int:
    if not args.backup_dir and args.backup_backend != "cfgutil":
        raise RestoreResearchError("--backup-dir is required for idevicebackup2 backup restore")
    plan = backup_restore_plan(args)
    print(json.dumps(plan, indent=2, sort_keys=True))
    if not args.execute:
        return 0
    if not args.i_understand_this_may_wipe_data:
        raise RestoreResearchError("backup restore execution requires --i-understand-this-may-wipe-data")
    if plan["backup_restore"]["backend"] == "idevicebackup2" and not shutil.which("idevicebackup2"):
        raise RestoreResearchError("idevicebackup2 was not found on PATH")
    if plan["backup_restore"]["backend"] == "cfgutil" and not resolve_cfgutil():
        raise RestoreResearchError("cfgutil was not found")
    if any(warning == "Backup directory was not found." for warning in plan["warnings"]):
        raise RestoreResearchError("backup directory was not found")
    completed = subprocess.run(plan["backup_restore"]["command"], check=False)
    return completed.returncode


def method_run_command(args: argparse.Namespace) -> int:
    method_id = args.method_id
    if method_id in BLOCKED_METHOD_REASONS:
        raise RestoreResearchError(BLOCKED_METHOD_REASONS[method_id])
    if method_id not in ACTIONABLE_METHOD_IDS:
        raise RestoreResearchError(f"unknown or unsupported method id: {method_id}")

    if method_id == "3utools_signed_firmware_query":
        if not args.product_type:
            raise RestoreResearchError("--product-type is required for signed firmware discovery")
        print(json.dumps(signed_firmware_lookup(args.product_type, args.timeout), indent=2, sort_keys=True))
        return 0
    if method_id == "3utools_shsh_query_download":
        if not args.shsh_blob:
            raise RestoreResearchError("--shsh-blob is required for SHSH/APTicket blob inspection")
        try:
            payload = inspect_blob(args.shsh_blob, args.product_type, args.expected_ecid, args.expected_apnonce)
        except Exception as exc:
            raise RestoreResearchError(str(exc)) from exc
        payload["method_adapter"] = {
            "method_id": method_id,
            "execution_model": "safe local blob metadata inspector",
            "firmware_flash": False,
            "blocked_original_behaviors": [
                "No SHSH query/download from third-party services.",
                "No blob replay, ticket submission, custom TSS server, nonce manipulation, or offline restore.",
                "No private MobileDevice/AuthInstall calls.",
            ],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if method_id == "3utools_super_restore_apps_backup":
        return run_backup_restore_method(args)

    if not args.ipsw:
        raise RestoreResearchError("--ipsw is required for this method")
    restore_args = _method_restore_namespace(args, method_id)
    plan = build_plan(restore_args)
    plan["method_adapter"] = {
        "method_id": method_id,
        "source_status": next((m.get("status") for m in CONTENTS_RESTORE_METHODS if m.get("id") == method_id), None),
        "execution_model": "safe public CLI/backend adapter",
        "blocked_original_behaviors": [
            "No embedded 3uTools dylib execution.",
            "No private MobileDevice/AuthInstall calls.",
            "No SHSH/APTicket replay, custom signing server, or offline signing bypass.",
            "No pwned DFU, exploit chain, custom firmware, or component patching.",
        ],
    }
    print(json.dumps(plan, indent=2, sort_keys=True))
    enforce_execution_guardrails(restore_args, plan)
    if not args.execute:
        return 0
    completed = subprocess.run(plan["candidate_restore_backend"]["command"], check=False)
    return completed.returncode


def requirements_command(_args: argparse.Namespace) -> int:
    print(json.dumps(contents_requirements(CONTENTS_ROOT, CONTENTS_RESTORE_METHODS), indent=2, sort_keys=True))
    return 0


def signed_firmwares_command(args: argparse.Namespace) -> int:
    print(json.dumps(signed_firmware_lookup(args.product_type, args.timeout), indent=2, sort_keys=True))
    return 0


def setup_deps_command(args: argparse.Namespace) -> int:
    print(json.dumps(dependency_setup(write_settings=args.write_settings), indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="restore-research",
        description="Research lawful Apple-signed restore backends without bypassing device security policy.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  Dry-run with automatic backend selection:
    restore-research restore --ipsw ./firmware.ipsw --dry-run

  Dry-run with explicit ProductType:
    restore-research restore --ipsw ./firmware.ipsw --product-type iPhone13,2 --dry-run

  Execute a normal Apple-supported erase restore through the local/PATH idevicerestore:
    restore-research restore --ipsw ./firmware.ipsw --backend idevicerestore --execute --erase-device --i-understand-this-may-wipe-data

notes:
  This command does not bypass Apple signing, SEP/baseband checks, APTicket validation,
  APNonce validation, activation, or device security policy.
""",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    inv = subcommands.add_parser("inventory", help="Print local candidate restore tools, frameworks, and services")
    inv.set_defaults(func=inventory_command)

    methods = subcommands.add_parser("methods", help="Print restore/flash methods observed in the Contents bundle")
    methods.set_defaults(func=methods_command)

    method_run = subcommands.add_parser("method-run", help="Run a safe adapter for an observed restore method")
    method_run.add_argument("--method-id", required=True, choices=tuple(method["id"] for method in CONTENTS_RESTORE_METHODS))
    method_run.add_argument("--ipsw", help="Path to an IPSW file for restore/preflight methods")
    method_run.add_argument("--device", default="auto", help="auto, normal-mode UDID, or recovery ECID")
    method_run.add_argument("--device-selector", choices=("auto", "ecid"), default="ecid", help="cfgutil selector strategy")
    method_run.add_argument("--product-type", help="ProductType such as iPhone13,2")
    method_run.add_argument("--device-class", help="BuildManifest Info.DeviceClass/board config")
    method_run.add_argument("--variant", help="AuthInstall variant to select")
    method_run.add_argument("--current-build", help="Override detected current build for downgrade assessment")
    method_run.add_argument("--action", choices=("restore", "update"), default="restore")
    method_run.add_argument("--backend", choices=("auto", "cfgutil", "idevicerestore"), default="auto")
    method_run.add_argument("--backup-backend", choices=("auto", "idevicebackup2", "cfgutil"), default="auto", help="Backend for backup/data restore methods")
    method_run.add_argument("--backup-dir", help="Backup directory for idevicebackup2 restore")
    method_run.add_argument("--backup-source", help="cfgutil MobileSync backup source name")
    method_run.add_argument("--shsh-blob", help="Local .shsh/.shsh2/.bshsh2 file to inspect without using it for restore")
    method_run.add_argument("--expected-ecid", help="Optional ECID to compare against a local blob")
    method_run.add_argument("--expected-apnonce", help="Optional APNonce to compare against a local blob")
    method_run.add_argument("--restore-system", action="store_true", help="Restore system files with idevicebackup2")
    method_run.add_argument("--restore-settings", action="store_true", help="Restore settings with idevicebackup2")
    method_run.add_argument("--skip-apps", action="store_true", help="Skip app reinstall trigger with idevicebackup2")
    method_run.add_argument("--no-reboot", action="store_true", help="Do not reboot after idevicebackup2 restore")
    method_run.add_argument("--timeout", type=int, default=10, help="Network timeout for metadata lookup methods")
    method_mode = method_run.add_mutually_exclusive_group()
    method_mode.add_argument("--dry-run", action="store_true", help="Print plan only; default")
    method_mode.add_argument("--execute", action="store_true", help="Execute only after all explicit guardrail flags are provided")
    method_run.add_argument("--erase-device", action="store_true", help="Required with --execute")
    method_run.add_argument("--i-understand-this-may-wipe-data", action="store_true", help="Required with --execute")
    method_run.set_defaults(func=method_run_command)

    reqs = subcommands.add_parser("requirements", help="Print safe implementation requirements derived from Contents")
    reqs.set_defaults(func=requirements_command)

    signed = subcommands.add_parser("signed-firmwares", help="Query public signed firmware metadata for a ProductType")
    signed.add_argument("--product-type", required=True, help="ProductType such as iPhone13,2")
    signed.add_argument("--timeout", type=int, default=10, help="Network timeout in seconds")
    signed.set_defaults(func=signed_firmwares_command)

    setup = subcommands.add_parser("setup-deps", help="Detect supported local restore tools and optionally save settings")
    setup.add_argument("--write-settings", action="store_true", help="Save detected supported backend paths to iPS-UU settings")
    setup.set_defaults(func=setup_deps_command)

    restore = subcommands.add_parser("restore", help="Print or execute a guarded signed restore plan")
    restore.add_argument("--ipsw", required=True, help="Path to an IPSW file")
    restore.add_argument("--device", default="auto", help="auto, normal-mode UDID, or recovery ECID")
    restore.add_argument("--device-selector", choices=("auto", "ecid"), default="ecid", help="cfgutil selector strategy")
    restore.add_argument("--product-type", help="Override detected ProductType, for example iPhone13,2")
    restore.add_argument("--device-class", help="BuildManifest Info.DeviceClass/board config")
    restore.add_argument("--variant", help="AuthInstall variant to select")
    restore.add_argument("--current-build", help="Override detected current build for downgrade assessment")
    restore.add_argument("--action", choices=("restore", "update"), default="restore", help="restore erases; update preserves when backend/device state supports it")
    restore.add_argument("--backend", choices=("auto", "cfgutil", "idevicerestore"), default="auto")
    mode = restore.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Print plan only; default")
    mode.add_argument("--execute", action="store_true", help="Execute only after all explicit guardrail flags are provided")
    restore.add_argument("--erase-device", action="store_true", help="Required with --execute")
    restore.add_argument("--i-understand-this-may-wipe-data", action="store_true", help="Required with --execute")
    restore.set_defaults(func=restore_command)
    return parser


def main(argv: list[str] | None = None, show_intro: bool = False) -> int:
    if show_intro:
        print_intro()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RestoreResearchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(show_intro=True))
