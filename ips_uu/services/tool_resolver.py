"""Resolve supported local restore tools."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_IDEVICERESTORE = REPO_ROOT / "idevicerestore-1.0.0/src/idevicerestore"
LOCAL_TOOLS_IDEVICERESTORE = REPO_ROOT / "tools/idevicerestore"
APPLE_CONFIGURATOR_CFGUTIL = Path("/Applications/Apple Configurator.app/Contents/MacOS/cfgutil")
LOCAL_TOOLS_CFGUTIL = REPO_ROOT / "tools/cfgutil"


def _is_executable(path: Path) -> bool:
    return path.exists() and path.is_file() and os.access(path, os.X_OK)


def _pyinstaller_root() -> Path | None:
    root = getattr(sys, "_MEIPASS", None)
    return Path(root) if root else None


def idevicerestore_candidates(explicit: str | None = None) -> list[dict[str, Any]]:
    """Return candidate idevicerestore executables in priority order."""
    candidates: list[tuple[str, Path | None]] = []
    if explicit:
        candidates.append(("explicit_argument", Path(explicit).expanduser()))
    env_path = os.environ.get("IPS_UU_IDEVICERESTORE")
    if env_path:
        candidates.append(("IPS_UU_IDEVICERESTORE", Path(env_path).expanduser()))
    bundled_root = _pyinstaller_root()
    if bundled_root:
        candidates.append(("pyinstaller_bundle", bundled_root / "tools/idevicerestore"))
    candidates.extend(
        [
            ("local_tools_directory", LOCAL_TOOLS_IDEVICERESTORE),
            ("local_compiled_source_tree", LOCAL_IDEVICERESTORE),
            ("path", Path(shutil.which("idevicerestore")) if shutil.which("idevicerestore") else None),
        ]
    )

    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for source, path in candidates:
        if path is None:
            result.append({"source": source, "path": None, "present": False, "usable": False})
            continue
        resolved = str(path)
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(
            {
                "source": source,
                "path": resolved,
                "present": path.exists(),
                "usable": _is_executable(path),
            }
        )
    return result


def resolve_idevicerestore(explicit: str | None = None) -> str | None:
    for candidate in idevicerestore_candidates(explicit):
        if candidate.get("usable"):
            return str(candidate["path"])
    return None


def idevicerestore_available(explicit: str | None = None) -> bool:
    return resolve_idevicerestore(explicit) is not None


def cfgutil_candidates(explicit: str | None = None) -> list[dict[str, Any]]:
    """Return candidate cfgutil executables in priority order."""
    candidates: list[tuple[str, Path | None]] = []
    if explicit:
        candidates.append(("explicit_argument", Path(explicit).expanduser()))
    env_path = os.environ.get("IPS_UU_CFGUTIL")
    if env_path:
        candidates.append(("IPS_UU_CFGUTIL", Path(env_path).expanduser()))
    bundled_root = _pyinstaller_root()
    if bundled_root:
        candidates.append(("pyinstaller_wrapper", bundled_root / "tools/cfgutil"))
    candidates.extend(
        [
            ("local_tools_wrapper", LOCAL_TOOLS_CFGUTIL),
            ("apple_configurator_install", APPLE_CONFIGURATOR_CFGUTIL),
            ("path", Path(shutil.which("cfgutil")) if shutil.which("cfgutil") else None),
        ]
    )

    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for source, path in candidates:
        if path is None:
            result.append({"source": source, "path": None, "present": False, "usable": False})
            continue
        resolved = str(path)
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(
            {
                "source": source,
                "path": resolved,
                "present": path.exists(),
                "usable": _is_executable(path),
                "requires_apple_configurator": source in {"pyinstaller_wrapper", "local_tools_wrapper", "apple_configurator_install"},
            }
        )
    return result


def resolve_cfgutil(explicit: str | None = None) -> str | None:
    for candidate in cfgutil_candidates(explicit):
        if candidate.get("usable"):
            return str(candidate["path"])
    return None


def cfgutil_available(explicit: str | None = None) -> bool:
    return resolve_cfgutil(explicit) is not None
