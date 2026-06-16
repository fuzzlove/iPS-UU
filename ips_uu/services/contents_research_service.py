"""Safe research inventory for the local Contents application bundle."""

from __future__ import annotations

import json
import plistlib
import shutil
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ips_uu.services.tool_resolver import resolve_cfgutil, resolve_idevicerestore


DEFAULT_CONTENTS_ROOT = Path("Contents")

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


def contents_requirements(root: Path | str = DEFAULT_CONTENTS_ROOT, methods: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return a safe requirements map derived from the local Contents audit."""
    root_path = Path(root)
    components = _component_inventory(root_path)
    implemented = [item for item in PYTHON_IMPLEMENTATION_MAP if str(item["status"]).startswith("implemented")]
    blocked = [item for item in PYTHON_IMPLEMENTATION_MAP if item["status"] == "blocked"]
    return {
        "generated_by": "iPS-UU contents research",
        "contents_root": str(root_path),
        "contents_present": root_path.exists(),
        "bundle_info": _read_info_plist(root_path),
        "bundled_components": components,
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
