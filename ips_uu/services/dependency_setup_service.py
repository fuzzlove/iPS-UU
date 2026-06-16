"""Dependency detection and setup for supported restore research tooling."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ips_uu.services.settings_service import AppSettings, load_settings, save_settings
from ips_uu.services.tool_resolver import cfgutil_candidates, resolve_cfgutil, idevicerestore_candidates, resolve_idevicerestore


APPLE_CFGUTIL = Path("/Applications/Apple Configurator.app/Contents/MacOS/cfgutil")
CONTENTS_ROOT = Path("Contents")

PATH_TOOLS = ("ideviceinfo", "irecovery", "idevicerestore", "idevicebackup2")

NON_VENDORABLE_COMPONENTS = [
    {
        "name": "Apple Configurator cfgutil",
        "reason": "Apple-distributed tool. Detect and use from its installed location; do not copy into this app.",
    },
    {
        "name": "3uTools application bundle",
        "reason": "Third-party proprietary application. Use only as a local research input; do not bundle or redistribute.",
    },
    {
        "name": "iTunesFlash / private MobileDevice restore helper",
        "reason": "Private MobileDevice/AuthInstall execution is blocked and not packaged.",
    },
]

def _path_tool(name: str) -> dict[str, Any]:
    if name == "idevicerestore":
        resolved = resolve_idevicerestore()
        return {
            "name": name,
            "path": resolved,
            "present": resolved is not None,
            "usable": resolved is not None,
            "setup_action": "detected_local_or_path" if resolved else "install_or_build_externally",
            "candidates": idevicerestore_candidates(),
        }
    path = shutil.which(name)
    return {
        "name": name,
        "path": path,
        "present": path is not None,
        "usable": path is not None,
        "setup_action": "detected_on_path" if path else "install_externally",
    }


def _cfgutil_candidates() -> list[dict[str, Any]]:
    candidates = []
    for candidate in cfgutil_candidates():
        source = str(candidate.get("source") or "")
        setup_action = "install_apple_configurator"
        if candidate.get("usable"):
            setup_action = "use_wrapper" if "wrapper" in source else "use_in_place"
        candidates.append(
            {
                "name": source,
                "path": candidate.get("path"),
                "present": candidate.get("present"),
                "usable": candidate.get("usable"),
                "requires_apple_configurator": candidate.get("requires_apple_configurator"),
                "setup_action": setup_action,
            }
        )
    return candidates


def _contents_components() -> list[dict[str, Any]]:
    components = [
        ("3uTools", "MacOS/3uTools", "research_only"),
        ("iTunesFlash", "MacOS/iTunesFlash", "blocked_private_api"),
        ("libidevicerestore.dylib", "Frameworks/libidevicerestore.dylib", "research_only"),
        ("libimobiledevice.dylib", "Frameworks/libimobiledevice.dylib", "research_only"),
        ("libirecovery.dylib", "Frameworks/libirecovery.dylib", "research_only"),
        ("libusbmuxd.dylib", "Frameworks/libusbmuxd.dylib", "research_only"),
    ]
    return [
        {
            "name": name,
            "path": str(CONTENTS_ROOT / rel_path),
            "present": (CONTENTS_ROOT / rel_path).exists(),
            "usable": False,
            "setup_action": status,
        }
        for name, rel_path, status in components
    ]


def dependency_setup(write_settings: bool = False) -> dict[str, Any]:
    """Detect supported tools and optionally save GUI settings.

    This function intentionally does not copy proprietary or private binaries.
    It only records supported external tool locations.
    """
    cfgutil_candidates = _cfgutil_candidates()
    path_tools = [_path_tool(name) for name in PATH_TOOLS]
    cfgutil_path = resolve_cfgutil()
    cfgutil = {"path": cfgutil_path, "usable": True} if cfgutil_path else None
    idevicerestore = next((item for item in path_tools if item["name"] == "idevicerestore" and item["usable"]), None)

    selected_backend = "auto"
    if idevicerestore:
        selected_backend = "idevicerestore"
    elif cfgutil:
        selected_backend = "cfgutil"

    settings_written = False
    if write_settings:
        current = load_settings()
        save_settings(
            AppSettings(
                backend=selected_backend,
                cfgutil_path=str(cfgutil["path"]) if cfgutil else current.cfgutil_path,
                idevicerestore_path=str(idevicerestore["path"]) if idevicerestore else current.idevicerestore_path,
                verbose_logging=current.verbose_logging,
                dry_run_only=True,
                theme=current.theme,
                last_ipsw=current.last_ipsw,
            )
        )
        settings_written = True

    return {
        "generated_by": "iPS-UU dependency setup",
        "settings_written": settings_written,
        "selected_backend": selected_backend,
        "restore_execution_available": bool(cfgutil or idevicerestore),
        "cfgutil_candidates": cfgutil_candidates,
        "cfgutil_requirements": [
            "Apple Configurator installed in /Applications.",
            "Apple Configurator's bundled cfgutil and support frameworks remain in Apple's app bundle.",
            "iPS-UU uses tools/cfgutil as a wrapper and does not copy Apple binaries or private frameworks.",
            "A connected device must be trusted or in a restorable mode supported by Apple Configurator.",
            "Network access may be required for Apple signing, activation, and restore validation.",
        ],
        "path_tools": path_tools,
        "contents_research_components": _contents_components(),
        "non_vendorable_components": NON_VENDORABLE_COMPONENTS,
        "setup_policy": {
            "copies_external_binaries": False,
            "uses_supported_tools_in_place": True,
            "private_restore_helpers_blocked": True,
            "unsigned_or_offline_restore_supported": False,
        },
        "next_steps": [
            "Install Apple Configurator from Apple to enable the tools/cfgutil wrapper, or use tools/idevicerestore.",
            "A local compiled idevicerestore at tools/idevicerestore or idevicerestore-1.0.0/src/idevicerestore is detected automatically when present.",
            "Keep 3uTools Contents as research input only; do not ship it with iPS-UU.",
            "Run restore-research restore --dry-run before any supported restore execution.",
        ],
    }
