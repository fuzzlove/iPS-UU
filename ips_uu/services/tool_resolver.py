"""Resolve supported local restore tools."""

from __future__ import annotations

import os
import platform
import subprocess
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


def _host_architecture() -> str:
    machine = platform.machine().lower()
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    if machine in {"x86_64", "amd64"}:
        return "x86_64"
    return machine


def _binary_architectures(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"kind": "missing", "architectures": [], "compatible": False, "reason": "missing"}
    if not path.is_file():
        return {"kind": "not_file", "architectures": [], "compatible": False, "reason": "not a file"}
    try:
        file_result = subprocess.run(["file", str(path)], capture_output=True, text=True, timeout=5, check=False)
    except Exception as exc:
        return {"kind": "unknown", "architectures": [], "compatible": False, "reason": str(exc)}
    file_text = "\n".join(part for part in (file_result.stdout, file_result.stderr) if part).strip()
    if "script text executable" in file_text:
        return {"kind": "script", "architectures": [], "compatible": True, "reason": "script", "file": file_text}
    try:
        lipo_result = subprocess.run(["lipo", "-archs", str(path)], capture_output=True, text=True, timeout=5, check=False)
    except Exception as exc:
        return {"kind": "unknown", "architectures": [], "compatible": False, "reason": str(exc), "file": file_text}
    archs = sorted(set(lipo_result.stdout.strip().split())) if lipo_result.returncode == 0 else []
    host_arch = _host_architecture()
    compatible = host_arch in archs
    return {
        "kind": "mach-o" if archs else "unknown",
        "architectures": archs,
        "host_architecture": host_arch,
        "compatible": compatible,
        "reason": "compatible" if compatible else f"missing host architecture {host_arch}",
        "file": file_text,
    }


def _pyinstaller_root() -> Path | None:
    root = getattr(sys, "_MEIPASS", None)
    return Path(root) if root else None


def _idevicerestore_wrapper_ready(path: Path) -> tuple[bool, str | None]:
    native = path.with_name(f"idevicerestore.{_host_architecture()}")
    if _is_executable(native):
        return True, None
    path_tool = shutil.which("idevicerestore")
    if path_tool and Path(path_tool).resolve() != path.resolve():
        return True, None
    return False, f"wrapper is missing native binary {native}"


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
        architecture = _binary_architectures(path) if path.exists() else {"kind": "missing", "architectures": [], "compatible": False, "reason": "missing"}
        executable = _is_executable(path)
        compatible = bool(architecture.get("compatible"))
        unusable_reason = architecture.get("reason") or "not executable"
        if executable and architecture.get("kind") == "script":
            compatible, wrapper_reason = _idevicerestore_wrapper_ready(path)
            unusable_reason = wrapper_reason or "script wrapper"
        result.append(
            {
                "source": source,
                "path": resolved,
                "present": path.exists(),
                "executable": executable,
                "architecture": architecture,
                "usable": executable and compatible,
                "unusable_reason": None if executable and compatible else unusable_reason,
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
