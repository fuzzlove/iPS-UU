"""Restore options analysis for signed iOS restore paths."""

from __future__ import annotations

import json
import os
import plistlib
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from ips_uu.frameworks import FrameworksError, find_plists_in_prkit, restore_options_from_prkit, summarize_restore_options
from ips_uu.services.backend_runner import practical_risks
from ips_uu.services.contents_research_service import contents_requirements, signed_firmware_lookup
from ips_uu.services.device_service import detect_target
from ips_uu.services.idevicerestore_service import build_restore_plan, find_tool as find_idevicerestore
from ips_uu.services.ipsw_service import compatibility_summary, parse_ipsw
from ips_uu.services.logging_service import get_log_dir
from ips_uu.services.tool_discovery import discover_tools
from ips_uu.services.turdus_merula_service import chip_class_for_product


REPO_ROOT = Path(__file__).resolve().parents[2]
PURPLE_RESTORE_ROOT = REPO_ROOT / "rengineer"
PURPLE_RESTORE_CLI = PURPLE_RESTORE_ROOT / "MacOS" / "restore-cmd-tool"
PURPLE_RESTORE_APP = PURPLE_RESTORE_ROOT / "MacOS" / "Purple Restore 4 (Beta)"
RESTORE_INTEGRATION_LAYER = PURPLE_RESTORE_ROOT / "Frameworks" / "RestoreIntegrationLayer.framework"
RESTORE_FRAMEWORK = PURPLE_RESTORE_ROOT / "Frameworks" / "RestoreFramework.framework"
SOFTWARE_BUNDLE_KIT = PURPLE_RESTORE_ROOT / "Frameworks" / "SoftwareBundleKit.framework"
SEARCH_FRAMEWORK = PURPLE_RESTORE_ROOT / "Frameworks" / "SearchFramework.framework"
DOWNLOAD_MANAGER_FRAMEWORK = PURPLE_RESTORE_ROOT / "Frameworks" / "DownloadManagerFramework.framework"
RESTORE_SD_PROTOCOL = PURPLE_RESTORE_ROOT / "Frameworks" / "RestoreSDProtocol.framework"
KNOX_PLUGIN = PURPLE_RESTORE_ROOT / "Plugins" / "KnoxPlugin.framework"
PURPLE_RESTORE_CLASSIC_APP = PURPLE_RESTORE_ROOT / "PurpleRestore Classic.app"
PURPLE_RESTORE_CLASSIC_BINARY = PURPLE_RESTORE_CLASSIC_APP / "Contents" / "MacOS" / "PurpleRestore Classic"
PURPLE_RESTORE_CLASSIC_PR2 = PURPLE_RESTORE_CLASSIC_APP / "Contents" / "Resources" / "PR2Document.plist"


PURPLE_RESTORE_PHASES = [
    "Personalizing restore bundle",
    "Downloading restore bundle",
    "Entering recovery mode",
    "Showing Unlock Device UI",
    "Hiding Unlock Device UI",
    "DFU download",
    "Setting restore boot-args",
    "Verifying restore",
    "Updating baseband",
    "Preparing for baseband update",
    "Booting the baseband",
    "Executing iBEC to bootstrap update",
    "Finalizing NAND epoch update",
    "Creating factory restore marker",
    "Sending Apple logo to device",
    "Flashing SYSCFG",
    "Checking for uncollected logs",
    "Creating Recovery OS Partition/Container/Volume",
    "Installing recovery OS files",
    "Installing recovery OS image",
]


PURPLE_FAILURE_CATEGORIES = [
    {
        "category": "authorization_declined",
        "matches": ["TATSU declined", "AuthInstall error"],
        "guidance": "Verify build nomination, ECID/device authorization, signing server, AppleConnect state, and selected AuthInstall variant.",
    },
    {
        "category": "baseband_rollback",
        "matches": ["The baseband cannot be rolled back", "Unpersonalized baseband firmware rejected"],
        "guidance": "Use a compatible baseband path or a target build whose baseband policy is accepted by this device.",
    },
    {
        "category": "older_epoch_requires_dfu",
        "matches": ["Cannot restore older epoch without using DFU"],
        "guidance": "Move the device into DFU and retry only if the target restore policy supports the older epoch.",
    },
    {
        "category": "wrong_mode",
        "matches": ["Device is not in restore mode"],
        "guidance": "Refresh the device state and enter the required normal, recovery, or DFU mode before execution.",
    },
    {
        "category": "factory_logs_required",
        "matches": ["uncollected factory logs"],
        "guidance": "Collect required device logs before retrying the restore.",
    },
]


CLASSIC_RESTORE_PHASES = [
    "Personalizing restore bundle",
    "DFU download",
    "Sending ramdisk to device",
    "Setting restore boot-args",
    "Updating baseband",
    "Executing iBEC to bootstrap update",
    "Finalizing NAND epoch update",
    "Closing modem tickets",
    "Clearing NVRAM",
]


CLASSIC_PR2_HIGH_IMPACT_KEYS = {
    "BootOptions.DFUFile",
    "BootOptions.DFUFileType",
    "BootOptions.FirmwareDirectory",
    "BootOptions.BootImageFile",
    "BootOptions.RestoreBootArgs",
    "BootOptions.SetRecoveryModeOutput",
    "RestoreOptions.AllowUntetheredRestore",
    "RestoreOptions.AuthInstallSigningServerHost",
    "RestoreOptions.AuthInstallSigningServerPort",
    "RestoreOptions.AuthInstallVariant",
    "RestoreOptions.ClearNVRAM",
    "RestoreOptions.ClearPersistentBootArgs",
    "RestoreOptions.CloseModemTickets",
    "RestoreOptions.FlashNOR",
    "RestoreOptions.ForceBasebandUpdate",
    "RestoreOptions.NORImageType",
    "RestoreOptions.UpdateBaseband",
    "RestoreOptions.WipeStorageDevice",
}


CLASSIC_DEVICE_BROWSER_COLUMNS = [
    "Device",
    "ECID",
    "USB Location",
    "Serial Number",
    "Restore Settings",
    "Bundle",
    "Root",
    "Data",
    "Progress",
]


def _session_dir() -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base = get_log_dir() / "restore-options"
    try:
        path = base / stamp
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        path = Path(tempfile.gettempdir()) / "ips-uu" / "logs" / "restore-options" / stamp
        path.mkdir(parents=True, exist_ok=True)
        return path


def internal_mode_enabled() -> bool:
    return os.environ.get("IPS_UU_INTERNAL", "").strip().lower() in {"1", "true", "yes", "apple", "internal"}


def _path_status(path: Path) -> dict[str, Any]:
    return {"path": str(path), "present": path.exists(), "executable": path.is_file() and os.access(path, os.X_OK)}


def _load_plist_bytes(data: bytes) -> Any | None:
    try:
        return plistlib.loads(data)
    except Exception:
        return None


def _load_plist_file(path: Path) -> Any | None:
    try:
        with path.open("rb") as handle:
            return plistlib.load(handle)
    except Exception:
        return None


def _collect_pr2_schema_items(node: Any, items: list[dict[str, Any]]) -> None:
    if isinstance(node, dict):
        key_path = node.get("PRKeyPath")
        key_paths = [str(item) for item in node.get("PRKeyPaths") or []]
        if key_path and str(key_path) not in key_paths:
            key_paths.insert(0, str(key_path))
        if key_paths:
            items.append(
                {
                    "label": node.get("Label"),
                    "type": node.get("Type"),
                    "key_paths": key_paths,
                    "default": node.get("Default"),
                    "choices": node.get("PopUpItems") or [],
                    "high_impact": any(key in CLASSIC_PR2_HIGH_IMPACT_KEYS for key in key_paths),
                }
            )
        for child in node.get("zChildren") or []:
            _collect_pr2_schema_items(child, items)
    elif isinstance(node, list):
        for child in node:
            _collect_pr2_schema_items(child, items)


def _classic_pr2_schema_summary(path: Path = PURPLE_RESTORE_CLASSIC_PR2) -> dict[str, Any]:
    payload = _load_plist_file(path)
    items: list[dict[str, Any]] = []
    _collect_pr2_schema_items(payload, items)
    key_paths = sorted({key for item in items for key in item.get("key_paths") or []})
    groups: dict[str, int] = {}
    for key in key_paths:
        group = key.split(".", 1)[0] if "." in key else "Root"
        groups[group] = groups.get(group, 0) + 1
    return {
        "path": str(path),
        "present": path.exists(),
        "item_count": len(items),
        "key_path_count": len(key_paths),
        "groups": groups,
        "high_impact_items": [item for item in items if item.get("high_impact")],
        "key_paths": key_paths,
    }


def _nested_get(payload: dict[str, Any], dotted_key: str) -> Any:
    current: Any = payload
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _extract_classic_settings(payload: dict[str, Any]) -> dict[str, Any]:
    restore_options = payload.get("RestoreOptions") if isinstance(payload.get("RestoreOptions"), dict) else {}
    boot_options = payload.get("BootOptions") if isinstance(payload.get("BootOptions"), dict) else {}
    browser_options = payload.get("PRDeviceBrowser") if isinstance(payload.get("PRDeviceBrowser"), dict) else {}

    flattened = {}
    for dotted_key in CLASSIC_PR2_HIGH_IMPACT_KEYS:
        value = _nested_get(payload, dotted_key)
        if value is not None:
            flattened[dotted_key] = value

    return {
        "restore_options": dict(restore_options),
        "boot_options": dict(boot_options),
        "browser_options": dict(browser_options),
        "high_impact_values": flattened,
        "auth_install_variant": restore_options.get("AuthInstallVariant") or boot_options.get("AuthInstallVariant"),
        "signing_server": restore_options.get("AuthInstallSigningServerHost") or boot_options.get("AuthInstallSigningServerHost"),
        "signing_port": restore_options.get("AuthInstallSigningServerPort") or boot_options.get("AuthInstallSigningServerPort"),
        "restore_bundle_path": restore_options.get("RestoreBundlePath") or boot_options.get("RestoreBundlePath"),
        "dfu_file": boot_options.get("DFUFile"),
        "dfu_file_type": boot_options.get("DFUFileType"),
        "restore_boot_args": boot_options.get("RestoreBootArgs"),
        "allow_untethered_restore": restore_options.get("AllowUntetheredRestore"),
        "updates_baseband": restore_options.get("UpdateBaseband"),
        "force_baseband_update": restore_options.get("ForceBasebandUpdate"),
        "clears_nvram": restore_options.get("ClearNVRAM"),
        "modifies_boot_args": bool(
            boot_options.get("RestoreBootArgs")
            or restore_options.get("ClearPersistentBootArgs") is not None
            or restore_options.get("PersistantBootArgsModifications")
        ),
        "post_restore_action": restore_options.get("PostRestoreAction"),
    }


def _classic_matches_from_value(member: str, value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    if value.get("Type") == "PezRoot" and value.get("zChildren"):
        items: list[dict[str, Any]] = []
        _collect_pr2_schema_items(value, items)
        return [
            {
                "member": member,
                "kind": "classic_pr2_schema",
                "summary": {
                    "item_count": len(items),
                    "key_path_count": len({key for item in items for key in item.get("key_paths") or []}),
                    "high_impact_key_count": len([item for item in items if item.get("high_impact")]),
                },
                "override_candidates": sorted({key for item in items for key in item.get("key_paths") or []}),
            }
        ]
    restore_options = value.get("RestoreOptions") if isinstance(value.get("RestoreOptions"), dict) else {}
    classic_specific_restore_keys = {
        "AllowUntetheredRestore",
        "ForceBasebandUpdate",
        "CloseModemTickets",
        "UpdateStaticEEPOnly",
        "VerifyStaticEEP",
        "IgnoreBadStaticEEPBackup",
        "ClearNVRAM",
        "AutoBootDelay",
    }
    is_classic_settings = (
        "BootOptions" in value
        or "PRDeviceBrowser" in value
        or bool(classic_specific_restore_keys & set(restore_options))
    )
    if is_classic_settings:
        classic = _extract_classic_settings(value)
        candidates = sorted(
            {
                *(f"RestoreOptions.{key}" for key in classic["restore_options"]),
                *(f"BootOptions.{key}" for key in classic["boot_options"]),
                *(f"PRDeviceBrowser.{key}" for key in classic["browser_options"]),
            }
        )
        return [{"member": member, "kind": "classic_pr2_settings", "summary": classic, "override_candidates": candidates}]
    return []


def _inspect_classic_restore_document(path: Path) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    try:
        members = find_plists_in_prkit(path)
    except Exception:
        members = [(path.name, path.read_bytes())]
    for member, data in members:
        value = _load_plist_bytes(data)
        matches.extend(_classic_matches_from_value(member, value))
    return matches


def purple_restore_capabilities() -> dict[str, Any]:
    components = {
        "restore_cmd_tool": _path_status(PURPLE_RESTORE_CLI),
        "purple_restore_app": _path_status(PURPLE_RESTORE_APP),
        "restore_integration_layer": _path_status(RESTORE_INTEGRATION_LAYER),
        "restore_framework": _path_status(RESTORE_FRAMEWORK),
        "software_bundle_kit": _path_status(SOFTWARE_BUNDLE_KIT),
        "search_framework": _path_status(SEARCH_FRAMEWORK),
        "download_manager_framework": _path_status(DOWNLOAD_MANAGER_FRAMEWORK),
        "restore_sd_protocol": _path_status(RESTORE_SD_PROTOCOL),
        "knox_plugin": _path_status(KNOX_PLUGIN),
    }
    cli_ready = components["restore_cmd_tool"]["present"] and components["restore_integration_layer"]["present"]
    framework_ready = components["restore_framework"]["present"] and components["restore_integration_layer"]["present"]
    search_ready = components["software_bundle_kit"]["present"] and components["search_framework"]["present"]
    knox_ready = (
        components["download_manager_framework"]["present"]
        and components["restore_sd_protocol"]["present"]
        and components["knox_plugin"]["present"]
    )
    return {
        "mode": "apple_internal" if internal_mode_enabled() else "public_guarded",
        "enabled": internal_mode_enabled(),
        "components": components,
        "executor_candidates": [
            {
                "id": "purple_restore_cli",
                "name": "Purple Restore CLI executor",
                "available": bool(cli_ready),
                "enabled": bool(cli_ready and internal_mode_enabled()),
                "requires": ["RestoreIntegrationLayer.framework", "MobileDevice.framework", "Apple-internal runtime"],
                "command_shape": "restore-cmd-tool --ecid <ECID> --bundle <path> --variant <variant> --override Key[:type]=Value",
            },
            {
                "id": "restore_framework_adapter",
                "name": "RestoreFramework dynamic adapter",
                "available": bool(framework_ready),
                "enabled": bool(framework_ready and internal_mode_enabled()),
                "requires": ["Objective-C bridge", "RestoreIntegrationLayer.framework", "matching private framework versions"],
                "risk": "Higher ABI drift than CLI wrapping.",
            },
            {
                "id": "software_bundle_provider",
                "name": "SoftwareBundleKit/SearchFramework provider",
                "available": bool(search_ready),
                "enabled": bool(search_ready and internal_mode_enabled()),
                "requires": ["AppleConnect/SSO for remote providers"],
                "value": "Build/device/variant/livability lookup.",
            },
            {
                "id": "knox_nfa_provider",
                "name": "DownloadManager/Knox/NFA provider",
                "available": bool(knox_ready),
                "enabled": bool(knox_ready and internal_mode_enabled()),
                "requires": ["AppleConnect/SSO", "Knox/NFA reachability", "DYLD framework path"],
                "value": "Authenticated internal bundle retrieval and checksum verification.",
            },
        ],
        "restore_phases": PURPLE_RESTORE_PHASES,
        "failure_categories": PURPLE_FAILURE_CATEGORIES,
        "missing_blockers": [
            name
            for name, status in components.items()
            if name in {"restore_cmd_tool", "restore_integration_layer", "restore_framework"} and not status.get("present")
        ],
    }


def purple_restore_classic_capabilities() -> dict[str, Any]:
    binary = _path_status(PURPLE_RESTORE_CLASSIC_BINARY)
    schema = _classic_pr2_schema_summary()
    return {
        "available": bool(binary["present"] and schema["present"]),
        "binary": binary,
        "pr2_schema": schema,
        "legacy_restore_stack": {
            "evidence": [
                "embedded AMRestore/libusbrestore strings",
                "DFUUSBDevice and RecoveryModeUSBDevice classes",
                "TSS/IMG3 personalization and stitching strings",
                "WTF/iBSS/iBEC/ramdisk recovery bootstrap paths",
                "Cannot restore older epoch without using DFU",
            ],
            "phases": CLASSIC_RESTORE_PHASES,
            "hard_blockers": [
                "The baseband cannot be rolled back",
                "Unable to connect to signing server",
                "variant is not published",
                "production iBoot ignores restore boot-args",
            ],
            "modern_force_downgrade_supported": False,
        },
        "device_browser_columns": CLASSIC_DEVICE_BROWSER_COLUMNS,
        "implementation_status": "modeled_non_executing",
        "guidance": "Classic PR2 and old DFU/epoch behavior are modeled for preflight and document import. Execution is not exposed as a modern downgrade backend.",
    }


def inspect_restore_document(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    doc_path = Path(path).expanduser()
    if not doc_path.exists():
        return {"path": str(doc_path), "status": "missing", "error": "Restore document path does not exist."}
    try:
        matches = restore_options_from_prkit(doc_path)
    except FrameworksError as exc:
        return {"path": str(doc_path), "status": "error", "error": str(exc)}
    summaries = []
    for row in matches:
        options = row.get("restore_options") or {}
        summary = row.get("summary") or summarize_restore_options(options)
        summaries.append(
            {
                "member": row.get("member"),
                "kind": "purple_restore_options",
                "summary": summary,
                "override_candidates": sorted(options.keys()),
            }
        )
    classic_matches = _inspect_classic_restore_document(doc_path)
    summaries.extend(classic_matches)
    techniques = ["Purple Restore .pr/PRKit-style restore option import."] if matches else []
    if classic_matches:
        techniques.append("PurpleRestore Classic PR2 schema/settings import.")
    return {
        "path": str(doc_path),
        "status": "ok" if summaries else "no_restore_options_found",
        "match_count": len(summaries),
        "matches": summaries,
        "technique": " ".join(techniques) if techniques else "No supported restore option payload found.",
    }


def _version_tuple(version: str | None) -> tuple[int, ...]:
    if not version:
        return ()
    parts: list[int] = []
    for raw in str(version).split("."):
        digits = "".join(ch for ch in raw if ch.isdigit())
        if digits:
            parts.append(int(digits))
    return tuple(parts)


def _mode(device: dict[str, Any]) -> str:
    value = str(device.get("current_mode") or "").lower()
    if value in {"normal", "recovery", "dfu"}:
        return value
    if "recovery" in value:
        return "recovery"
    if "dfu" in value:
        return "DFU"
    return value or "unknown"


def _device_status(device: dict[str, Any]) -> dict[str, Any]:
    product_type = device.get("product_type")
    return {
        "product_type": product_type,
        "model": device.get("model_name") or device.get("model") or product_type,
        "chip_family": chip_class_for_product(product_type) if product_type else "unknown",
        "current_ios_version": device.get("product_version") or device.get("firmware_version"),
        "mode": _mode(device),
        "ecid": device.get("ecid"),
        "udid": device.get("udid"),
        "serial_number": device.get("serial_number"),
        "usb_location": device.get("usb_location"),
        "board_config": device.get("board_config") or device.get("model_identifier"),
        "raw": device,
    }


def _lookup_signing(product_type: str | None, timeout: int) -> dict[str, Any]:
    if not product_type:
        return {"status": "signature_unavailable", "error": "No ProductType available for signing metadata lookup.", "firmwares": []}
    try:
        payload = signed_firmware_lookup(product_type, timeout)
    except Exception as exc:
        return {"status": "signature_unavailable", "error": str(exc), "firmwares": []}
    signed = [item for item in payload.get("firmwares") or [] if item.get("signed")]
    return {**payload, "status": "ok", "signed_firmwares": signed}


def _signed_match(ipsw: dict[str, Any], signing: dict[str, Any]) -> dict[str, Any]:
    if signing.get("status") != "ok":
        return {"appears_signed": None, "status": "signature_unavailable", "detail": signing.get("error") or "Signing metadata unavailable."}
    version = str(ipsw.get("product_version") or "")
    build = str(ipsw.get("product_build_version") or "")
    for item in signing.get("firmwares") or []:
        if str(item.get("version") or "") == version and str(item.get("build") or "") == build:
            signed = bool(item.get("signed"))
            return {
                "appears_signed": signed,
                "status": "signed" if signed else "not_signed",
                "detail": "Firmware appears signed in public metadata." if signed else "Firmware appears unsigned in public metadata.",
                "metadata": item,
            }
    return {"appears_signed": False, "status": "not_listed", "detail": "Firmware was not listed as currently signed for this ProductType."}


def _is_classic_generation(product_type: str | None, chip_family: str | None) -> bool:
    product = str(product_type or "")
    chip = str(chip_family or "").upper()
    classic_prefixes = (
        "iPhone1,",
        "iPhone2,",
        "iPhone3,",
        "iPhone4,",
        "iPad1,",
        "iPod1,",
        "iPod2,",
        "iPod3,",
        "iPod4,",
    )
    return product.startswith(classic_prefixes) or chip in {"S5L8900", "S5L8720", "S5L8920", "A4"}


def _document_baseband_flags(restore_document: dict[str, Any] | None) -> dict[str, Any]:
    flags = {
        "updates_baseband": False,
        "force_baseband_update": False,
        "close_modem_tickets": False,
        "allow_untethered_restore": None,
        "signing_server": None,
        "dfu_file": None,
    }
    for match in (restore_document or {}).get("matches") or []:
        summary = match.get("summary") or {}
        if summary.get("updates_baseband") is True:
            flags["updates_baseband"] = True
        if summary.get("force_baseband_update") is True:
            flags["force_baseband_update"] = True
        high = summary.get("high_impact_values") or {}
        if high.get("RestoreOptions.CloseModemTickets") is True:
            flags["close_modem_tickets"] = True
        for key in ("allow_untethered_restore", "signing_server", "dfu_file"):
            if summary.get(key) is not None and flags.get(key) is None:
                flags[key] = summary.get(key)
    return flags


def _downgrade_preflight(
    status: dict[str, Any],
    firmware_check: dict[str, Any] | None,
    restore_document: dict[str, Any] | None,
) -> dict[str, Any]:
    ipsw = (firmware_check or {}).get("ipsw") or {}
    target_version = ipsw.get("product_version")
    current_version = status.get("current_ios_version")
    target_older = bool(_version_tuple(target_version) and _version_tuple(current_version) and _version_tuple(target_version) < _version_tuple(current_version))
    classic_generation = _is_classic_generation(status.get("product_type"), status.get("chip_family"))
    mode = str(status.get("mode") or "unknown").lower()
    doc_flags = _document_baseband_flags(restore_document)
    blockers: list[str] = []
    warnings: list[str] = []
    required_mode = "normal/recovery"
    legacy_older_epoch_route = False

    if target_older:
        warnings.append("Target firmware is older than the detected current firmware.")
        required_mode = "dfu"
        if classic_generation:
            legacy_older_epoch_route = True
            warnings.append("Classic restore evidence says older epoch restores require DFU on supported legacy devices/builds.")
            if mode != "dfu":
                blockers.append("Move the device to DFU before attempting a legacy older-epoch restore.")
        else:
            blockers.append("Modern forced downgrade is not supported by the modeled PurpleRestore techniques.")

    signature = (firmware_check or {}).get("signature") or {}
    if target_older and signature.get("appears_signed") is not True:
        blockers.append("Target firmware is not confirmed signed/authorized for this device.")
    if doc_flags["updates_baseband"] or doc_flags["force_baseband_update"]:
        warnings.append("Restore document requests baseband update behavior; Classic still blocks baseband rollback.")
    if doc_flags["force_baseband_update"]:
        blockers.append("ForceBasebandUpdate does not bypass baseband rollback policy.")

    return {
        "target_version": target_version,
        "current_version": current_version,
        "target_older_than_current": target_older,
        "classic_generation_candidate": classic_generation,
        "legacy_older_epoch_route": legacy_older_epoch_route,
        "required_mode": required_mode,
        "current_mode": mode,
        "modern_force_downgrade_supported": False,
        "document_flags": doc_flags,
        "blockers": blockers,
        "warnings": warnings,
        "guidance": [
            "Classic-era older-epoch handling is modeled only for legacy devices/builds and requires DFU.",
            "AuthInstall/TSS authorization, manifest variant matching, and baseband policy remain authoritative.",
            "Use this as preflight intelligence before handing off to a vetted internal restore backend.",
        ],
    }


def _install_status(compatibility: dict[str, Any], signature: dict[str, Any], backend_required: bool) -> str:
    if compatibility.get("status") == "incompatible":
        return "Unsupported device"
    if signature.get("status") == "signature_unavailable":
        return "Signature unavailable"
    if signature.get("appears_signed") is True:
        return "Requires external research backend" if backend_required else "Installable"
    if backend_required:
        return "Tethered only"
    return "Not installable"


def _external_backends(tools: dict[str, Any], product_type: str | None) -> list[dict[str, Any]]:
    chip = chip_class_for_product(product_type)
    backends = []
    for tool in tools.get("tools") or []:
        workflows = tool.get("supported_workflows") or []
        tethered = any("tether" in str(item).lower() or "boot" in str(item).lower() for item in workflows)
        supports_device = True
        if tool.get("name") == "turdus merula":
            supports_device = chip in {"A9/A9X", "A10", "A10X"}
        command = [str(tool.get("path") or f"tools/{tool.get('name')}"), "<explicit backend args>"] if tool.get("detected") else []
        backends.append(
            {
                "tool_name": tool.get("name"),
                "detected": tool.get("detected"),
                "supported_devices": tool.get("supported_device_families") or [],
                "supported_ios_versions": "backend-defined; verify with bundled tool documentation/source",
                "supports_connected_device": supports_device,
                "tethered_status": "tethered/research" if tethered else "standard/untethered or backend-defined",
                "risks": practical_risks(),
                "command": command,
                "command_preview": " ".join(command),
                "dry_run": {
                    "available": bool(tool.get("detected")),
                    "executes_device_changes": False,
                    "logs": "restore-options session log",
                },
            }
        )
    return backends


def _available_paths(device: dict[str, Any], signing: dict[str, Any], tools: dict[str, Any]) -> list[dict[str, Any]]:
    current = str(device.get("product_version") or "")
    signed = signing.get("signed_firmwares") or []
    latest = signed[0] if signed else None
    same = next((item for item in signed if str(item.get("version") or "") == current), None)
    idevicerestore = find_idevicerestore()
    binary = str(idevicerestore.get("path") or "idevicerestore")
    tethered = any(item.get("tool_name") == "turdus merula" and item.get("detected") and item.get("supports_connected_device") for item in _external_backends(tools, device.get("product_type")))
    return [
        {
            "name": "Update to latest signed iOS",
            "possible": latest is not None,
            "status": "Installable" if latest else "Signature unavailable",
            "target": latest,
            "command": [binary, "--latest"] if latest else [],
            "command_preview": f"{binary} --latest" if latest else "",
        },
        {
            "name": "Restore latest signed iOS",
            "possible": latest is not None,
            "status": "Installable" if latest else "Signature unavailable",
            "target": latest,
            "command": [binary, "--erase", "--latest"] if latest else [],
            "command_preview": f"{binary} --erase --latest" if latest else "",
        },
        {
            "name": "Reinstall same version",
            "possible": same is not None,
            "status": "Installable" if same else "Not installable",
            "target": same or {"version": current},
            "guidance": "Only possible as a standard firmware reinstall if the installed iOS version is still signed.",
        },
        {
            "name": "Downgrade target firmware",
            "possible": False,
            "status": "Not installable",
            "guidance": "A downgrade is standard-installable only when the target IPSW is currently signed for this exact device.",
        },
        {
            "name": "Tethered research workflow",
            "possible": tethered,
            "status": "Requires external research backend" if tethered else "Not installable",
            "guidance": "Shown only when a bundled backend advertises tethered boot/downgrade support for this device family.",
        },
    ]


def _research_findings() -> dict[str, Any]:
    try:
        requirements = contents_requirements()
    except Exception as exc:
        return {
            "status": "unavailable",
            "error": str(exc),
            "private_mobiledevice_restore_supported": False,
            "offline_unsigned_restore_supported": False,
        }
    return {
        "status": "ok",
        "contents_root": requirements.get("contents_root"),
        "research_report": requirements.get("research_report"),
        "restore_engine_findings": requirements.get("restore_engine_findings") or [],
        "itunes_flash_helper_model": requirements.get("itunes_flash_helper_model") or {},
        "private_mobiledevice_restore_supported": False,
        "offline_unsigned_restore_supported": False,
    }


def analyze_restore_options(
    ipsw_path: str | None = None,
    device: dict[str, Any] | None = None,
    timeout: int = 10,
    restore_document_path: str | None = None,
) -> dict[str, Any]:
    device_info = device or detect_target("auto")
    status = _device_status(device_info)
    tools = discover_tools()
    signing = _lookup_signing(status.get("product_type"), timeout)
    firmware_check = None
    dry_run_plan = None
    if ipsw_path:
        ipsw = parse_ipsw(ipsw_path, status.get("product_type"))
        compatibility = compatibility_summary(device_info, ipsw)
        signature = _signed_match(ipsw, signing)
        backend_required = signature.get("appears_signed") is not True and any(
            item.get("detected") and item.get("supports_connected_device") and "tethered" in str(item.get("tethered_status")).lower()
            for item in _external_backends(tools, status.get("product_type"))
        )
        install_status = _install_status(compatibility, signature, backend_required)
        firmware_check = {
            "ipsw": ipsw,
            "compatibility": compatibility,
            "signature": signature,
            "status": install_status,
        }
        if install_status == "Installable":
            dry_run_plan = build_restore_plan(ipsw_path, erase=True)
        else:
            dry_run_plan = {
                "purpose": "Restore Options firmware dry run",
                "command": [],
                "command_preview": "No standard restore command generated because the IPSW is not confirmed installable.",
                "shell": False,
                "risks": practical_risks(),
            }
    session = _session_dir()
    purple = purple_restore_capabilities()
    classic = purple_restore_classic_capabilities()
    restore_document = inspect_restore_document(restore_document_path)
    downgrade_preflight = _downgrade_preflight(status, firmware_check, restore_document)
    warnings = [
        "This may erase data.",
        "This may update the device.",
        "Unsigned firmware is normally refused by Apple restore services.",
        "Some research workflows may be tethered.",
        "Some restores may fail activation due to SEP/baseband compatibility.",
        "Users must check local laws and understand the risk.",
        *downgrade_preflight.get("warnings", []),
        *downgrade_preflight.get("blockers", []),
    ]
    result = {
        "generated_by": "NovaCerts Restore Options",
        "device_status": status,
        "available_restore_paths": _available_paths(device_info, signing, tools),
        "firmware_check": firmware_check,
        "restore_document": restore_document,
        "purple_restore_internal": purple,
        "purple_restore_classic": classic,
        "downgrade_preflight": downgrade_preflight,
        "restore_progress_model": {
            "source": "Purple Restore 4 and PurpleRestore Classic static analysis",
            "phases": PURPLE_RESTORE_PHASES,
            "classic_phases": CLASSIC_RESTORE_PHASES,
            "failure_categories": PURPLE_FAILURE_CATEGORIES,
            "session_log_contract": {
                "device_keys": ["ECID", "UDID", "USB location", "ProductType", "board config", "serial number"],
                "artifact_keys": ["restore document", "selected bundle", "selected variant", "typed overrides", "DFU file", "boot image", "host log", "device log", "status plist"],
                "classic_device_browser_columns": CLASSIC_DEVICE_BROWSER_COLUMNS,
            },
        },
        "reverse_engineering_findings": _research_findings(),
        "restore_without_updating_guidance": [
            "If the installed iOS is still signed, reinstalling that same version may be possible.",
            "If the version is no longer signed, a standard restore will normally update to a currently signed version.",
            "User data reset may be possible through device settings, but this is not the same as reinstalling firmware.",
            "Recovery restore normally requires signed firmware.",
        ],
        "internal_restore_guidance": [
            "Set IPS_UU_INTERNAL=1 to enable Apple-internal providers after the required private frameworks and services are present.",
            "Purple Restore CLI execution still requires RestoreIntegrationLayer.framework; this extraction is incomplete without it.",
            "PurpleRestore Classic PR2 options are imported for preflight and review, not exposed as a current macOS execution backend.",
            "Use restore document summaries and typed overrides for review before handing off to an internal executor.",
            "Treat TATSU/AuthInstall, baseband rollback, older epoch, and device mode failures as terminal until the internal backend authorizes a different path.",
        ],
        "external_backends": _external_backends(tools, status.get("product_type")),
        "dry_run_command_plan": dry_run_plan,
        "warnings": warnings,
        "session_dir": str(session),
        "signing_metadata": signing,
    }
    (session / "restore_options.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    if dry_run_plan:
        (session / "command_preview.txt").write_text(str(dry_run_plan.get("command_preview") or "") + "\n", encoding="utf-8")
    return result
