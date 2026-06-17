"""Safe research inventory for the local Contents application bundle."""

from __future__ import annotations

import json
import plistlib
import shutil
import sys
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ips_uu.services.tool_resolver import resolve_cfgutil, resolve_idevicerestore


REPO_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))


def default_contents_root() -> Path:
    """Return the local reverse-engineering bundle root when present."""
    for candidate in (REPO_ROOT / "Contents", REPO_ROOT / "rengineer"):
        if candidate.exists():
            return candidate
    return REPO_ROOT / "Contents"


DEFAULT_CONTENTS_ROOT = default_contents_root()

BUNDLED_COMPONENTS: list[dict[str, str]] = [
    {"id": "main_app", "path": "MacOS/3uTools", "purpose": "Qt desktop restore/flash front end"},
    {"id": "itunes_flash", "path": "MacOS/iTunesFlash", "purpose": "Private MobileDevice restore helper"},
    {"id": "crash_report", "path": "MacOS/CrashReport", "purpose": "Crash reporter helper"},
    {"id": "rxpc", "path": "MacOS/rxpc", "purpose": "Bundled helper binary"},
    {"id": "libidevicerestore", "path": "Frameworks/libidevicerestore.dylib", "purpose": "Open-source restore engine library"},
    {"id": "libimobiledevice", "path": "Frameworks/libimobiledevice.dylib", "purpose": "Device services and lockdown protocol library"},
    {"id": "libirecovery", "path": "Frameworks/libirecovery.dylib", "purpose": "Recovery/DFU mode USB protocol library"},
    {"id": "libusbmuxd", "path": "Frameworks/libusbmuxd.dylib", "purpose": "USB mux transport library"},
    {"id": "libplist", "path": "Frameworks/libplist2.dylib", "purpose": "Property-list parsing library"},
    {"id": "libdownload", "path": "Frameworks/libdownload.dylib", "purpose": "Download/task support library"},
    {"id": "libidm", "path": "Frameworks/libidm.1.0.0.dylib", "purpose": "Device management, backup, and app restore support library"},
]

RESTORE_ENGINE_FINDINGS: list[dict[str, Any]] = [
    {
        "id": "bundled_libidevicerestore_signed_pipeline",
        "component": "Frameworks/libidevicerestore.dylib",
        "finding": "The bundled restore engine follows the normal idevicerestore pipeline: IPSW parsing, BuildManifest identity selection, nonce collection, Apple TSS request, component personalization, recovery/restore mode handoff, and filesystem streaming.",
        "evidence": [
            "_idevicerestore_start",
            "_ipsw_extract_build_manifest",
            "_build_manifest_check_compatibility",
            "_get_tss_response",
            "_tss_request_send",
            "_restore_send_root_ticket",
            "https://gs.apple.com/TSS/controller?action=2",
        ],
        "ips_uu_integration": "Use as architecture guidance and optional inventory only; execute supported signed restores through cfgutil or a known idevicerestore CLI.",
        "guardrail": "No offline unsigned restore, TSS bypass, forged ticket, SEP/baseband bypass, or continued restore after missing/invalid tickets.",
    },
    {
        "id": "itunesflash_mobiledevice_wrapper",
        "component": "MacOS/iTunesFlash",
        "finding": "The helper dynamically loads Apple's private MobileDevice.framework, waits for a matching ECID, creates default restore options, sets RestoreBundlePath and AuthInstallRestoreBehavior, then calls AMRestorableDeviceRestore.",
        "evidence": [
            "AMRestorableDeviceRegisterForNotifications",
            "AMRestorableDeviceRestore",
            "AMRestoreCreateDefaultOptions",
            "AMRestorableDeviceGetECID",
            "RestoreBundlePath",
            "AuthInstallRestoreBehavior",
            "Update",
            "Erase",
        ],
        "ips_uu_integration": "Document the call flow and mirror the user-facing distinction between update and erase, but do not call private MobileDevice restore APIs.",
        "guardrail": "Private MobileDevice/AuthInstall execution remains blocked in iPS-UU.",
    },
]

ITUNES_FLASH_HELPER_MODEL: dict[str, Any] = {
    "component": "MacOS/iTunesFlash",
    "classification": "private_mobiledevice_restore_wrapper_documented_only",
    "argc": 6,
    "arguments": [
        {"index": 1, "name": "save_user_data_flag", "parser": "atoi", "effect": "0 selects Erase; nonzero selects Update"},
        {"index": 2, "name": "ecid", "parser": "sscanf %llx", "effect": "Only restore the matching restorable device ECID; 0 behaves as wildcard in observed logic."},
        {"index": 3, "name": "restore_bundle_path", "parser": "string", "effect": "Assigned to RestoreBundlePath."},
        {"index": 4, "name": "mobiledevice_log_path", "parser": "string", "effect": "Passed to AMRestoreEnableFileLogging."},
        {"index": 5, "name": "callback_error_path", "parser": "string", "effect": "Receives restore callback failure code/description."},
    ],
    "option_dictionary": {
        "RestoreBundlePath": "argv[3]",
        "AuthInstallRestoreBehavior": "Update or Erase",
        "iTunesVersion": "iTunes 12.9.5.5",
        "UserLocale": "Zh_cn",
    },
    "private_symbols": [
        "AMRestorableDeviceRegisterForNotifications",
        "AMRestorableDeviceRestore",
        "AMRestoreCreateDefaultOptions",
        "AMRestorableDeviceGetECID",
        "AMRestoreEnableFileLogging",
        "AMDSetLogLevel",
    ],
    "ips_uu_policy": {
        "execute_helper": False,
        "private_api_execution_supported": False,
        "useful_for": ["progress model", "update-vs-erase labeling", "log/callback surface design"],
    },
}

PYTHON_IMPLEMENTATION_MAP: list[dict[str, Any]] = [
    {
        "capability": "IPSW metadata import",
        "contents_evidence": ["_ipsw_extract_build_manifest", "_ipsw_extract_restore_plist", "slotAddIpswFromFlashModule"],
        "python_module": "ips_uu.services.ipsw_service",
        "status": "implemented",
        "safe_scope": "Offline parsing of BuildManifest.plist and Restore.plist only.",
    },
    {
        "capability": "Restore method catalog",
        "contents_evidence": ["QPageFlash", "task_dfu_flash", "QDialogDownloadShsh", "AMRestorableDeviceRestore"],
        "python_module": "ips_uu.restore_research",
        "status": "implemented_as_inventory",
        "safe_scope": "Documents observed methods and guardrails without executing private or unsafe paths.",
    },
    {
        "capability": "Device discovery",
        "contents_evidence": ["libimobiledevice.dylib", "libirecovery.dylib"],
        "python_module": "ips_uu.restorectl",
        "status": "implemented_via_safe_external_tools",
        "safe_scope": "Uses ideviceinfo/irecovery/cfgutil-style metadata when available.",
    },
    {
        "capability": "Signed restore dry-run planning",
        "contents_evidence": ["_idevicerestore_set_ipsw", "_build_manifest_check_compatibility", "_tss_request_send"],
        "python_module": "ips_uu.restore_research",
        "status": "implemented_as_dry_run",
        "safe_scope": "Plans a lawful restore handoff and refuses bypass/offline signing behavior.",
    },
    {
        "capability": "Signed firmware metadata discovery",
        "contents_evidence": ["_ipsw_get_signed_firmwares", "https://api.ipsw.me/v4/device/%s"],
        "python_module": "ips_uu.services.contents_research_service.signed_firmware_lookup",
        "status": "implemented_as_optional_online_metadata",
        "safe_scope": "Queries public metadata only; does not replace Apple TSS validation.",
    },
    {
        "capability": "Private MobileDevice restore",
        "contents_evidence": ["AMRestorableDeviceRestore", "AMRestoreCreateDefaultOptions", "AuthInstallRestoreBehavior"],
        "python_module": None,
        "status": "blocked",
        "safe_scope": "Private API execution is not implemented.",
    },
    {
        "capability": "SHSH/APTicket handling",
        "contents_evidence": ["QDialogDownloadShsh", "task_get_shsh_datas", "Using cached SHSH"],
        "python_module": None,
        "status": "blocked",
        "safe_scope": "No blob creation, replay, selection, or bypass support.",
    },
]

RELEASE_REQUIREMENTS: list[dict[str, Any]] = [
    {
        "name": "Python",
        "requirement": "Python >= 3.10",
        "purpose": "Core CLI, IPSW parsing, dry-run planning, and services.",
        "required_for_release": True,
    },
    {
        "name": "PySide6",
        "requirement": "PySide6 >= 6.7",
        "purpose": "Desktop GUI.",
        "required_for_release": True,
    },
    {
        "name": "PyInstaller",
        "requirement": "pyinstaller >= 6.0",
        "purpose": "Desktop bundle packaging.",
        "required_for_release": False,
    },
    {
        "name": "Apple Configurator cfgutil",
        "requirement": "/Applications/Apple Configurator.app/Contents/MacOS/cfgutil",
        "purpose": "Preferred Apple-supported restore/update backend for optional CLI execution.",
        "required_for_release": False,
    },
    {
        "name": "libimobiledevice tools",
        "requirement": "ideviceinfo, irecovery, optional tools/idevicerestore or idevicerestore on PATH",
        "purpose": "Device metadata and fallback restore comparison.",
        "required_for_release": False,
    },
]

BLOCKED_RESEARCH_AREAS: list[dict[str, str]] = [
    {
        "area": "Unsigned or offline iOS downgrade",
        "reason": "Modern restore personalization requires Apple TSS/APTicket validation for the exact device, nonce, board, build, and components.",
    },
    {
        "area": "SHSH/APTicket replay or abuse",
        "reason": "Blob replay/manipulation would bypass normal device security policy and is outside the lawful signed-restore scope.",
    },
    {
        "area": "SEP/baseband mismatch bypass",
        "reason": "The tool must not patch or ignore firmware component compatibility and validation failures.",
    },
    {
        "area": "Private MobileDevice/AuthInstall restore execution",
        "reason": "The local helper exposes private APIs, but those are unstable and not a documented public backend.",
    },
    {
        "area": "Pwned DFU or exploit chains",
        "reason": "Exploit-based restore flows are explicitly out of scope for iPS-UU.",
    },
]


def _read_info_plist(root: Path) -> dict[str, Any]:
    info_path = root / "Info.plist"
    if not info_path.exists():
        return {"path": str(info_path), "present": False}
    try:
        with info_path.open("rb") as handle:
            info = plistlib.load(handle)
    except Exception as exc:
        return {"path": str(info_path), "present": True, "error": str(exc)}
    return {
        "path": str(info_path),
        "present": True,
        "bundle_identifier": info.get("CFBundleIdentifier"),
        "display_name": info.get("CFBundleDisplayName") or info.get("CFBundleName"),
        "version": info.get("CFBundleShortVersionString") or info.get("CFBundleVersion"),
        "url_schemes": [
            scheme
            for item in info.get("CFBundleURLTypes", [])
            for scheme in item.get("CFBundleURLSchemes", [])
            if isinstance(scheme, str)
        ],
        "allows_arbitrary_loads": bool(
            (info.get("NSAppTransportSecurity") or {}).get("NSAllowsArbitraryLoads")
        ),
    }


def _component_inventory(root: Path) -> list[dict[str, Any]]:
    return [
        {
            **component,
            "full_path": str(root / component["path"]),
            "present": (root / component["path"]).exists(),
        }
        for component in BUNDLED_COMPONENTS
    ]


def _external_tool_inventory() -> list[dict[str, Any]]:
    tools = []
    cfgutil = resolve_cfgutil()
    tools.append({"name": "cfgutil", "path": cfgutil, "present": cfgutil is not None})
    for name in ("ideviceinfo", "irecovery"):
        path = shutil.which(name)
        tools.append({"name": name, "path": path, "present": path is not None})
    idevicerestore = resolve_idevicerestore()
    tools.append({"name": "idevicerestore", "path": idevicerestore, "present": idevicerestore is not None})
    return tools


def contents_requirements(root: Path | str | None = None, methods: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return a safe requirements map derived from the local reverse-engineering audit."""
    if root is None:
        root_path = DEFAULT_CONTENTS_ROOT
    else:
        root_path = Path(root)
        if not root_path.is_absolute():
            root_path = REPO_ROOT / root_path
        if not root_path.exists() and root_path.name == "Contents" and DEFAULT_CONTENTS_ROOT.exists():
            root_path = DEFAULT_CONTENTS_ROOT
    components = _component_inventory(root_path)
    implemented = [item for item in PYTHON_IMPLEMENTATION_MAP if str(item["status"]).startswith("implemented")]
    blocked = [item for item in PYTHON_IMPLEMENTATION_MAP if item["status"] == "blocked"]
    return {
        "generated_by": "iPS-UU contents research",
        "contents_root": str(root_path),
        "contents_present": root_path.exists(),
        "research_report": str(root_path / "REVERSE_ENGINEERING_REPORT.md") if (root_path / "REVERSE_ENGINEERING_REPORT.md").exists() else None,
        "bundle_info": _read_info_plist(root_path),
        "bundled_components": components,
        "restore_engine_findings": RESTORE_ENGINE_FINDINGS,
        "itunes_flash_helper_model": ITUNES_FLASH_HELPER_MODEL,
        "release_requirements": RELEASE_REQUIREMENTS,
        "external_tools": _external_tool_inventory(),
        "python_implementation_map": PYTHON_IMPLEMENTATION_MAP,
        "implemented_safe_features": implemented,
        "blocked_research_areas": BLOCKED_RESEARCH_AREAS,
        "blocked_contents_capabilities": blocked,
        "observed_restore_methods": methods or [],
        "release_policy": {
            "offline_restore_execution_supported": False,
            "unsigned_restore_supported": False,
            "private_restore_api_execution_supported": False,
            "safe_offline_features": ["IPSW metadata parsing", "compatibility preflight", "requirements inventory"],
        },
    }


def signed_firmware_lookup(product_type: str, timeout: int = 10) -> dict[str, Any]:
    """Query public signed firmware metadata without treating it as restore authorization."""
    if not product_type:
        raise ValueError("product_type is required")
    url = f"https://api.ipsw.me/v4/device/{quote(product_type)}"
    request = urllib.request.Request(url, headers={"User-Agent": "iPS-UU/0.1 signed-restore-research"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    firmwares = []
    for firmware in payload.get("firmwares", []):
        if not isinstance(firmware, dict):
            continue
        firmwares.append(
            {
                "version": firmware.get("version"),
                "build": firmware.get("buildid"),
                "signed": bool(firmware.get("signed")),
                "url": firmware.get("url"),
            }
        )
    return {
        "source": url,
        "product_type": product_type,
        "firmwares": firmwares,
        "policy": {
            "metadata_only": True,
            "apple_tss_still_required": True,
            "does_not_enable_offline_restore": True,
        },
    }
