"""Passive external tool inventory.

This module records metadata about optional tools that may exist in tools/.
It does not execute jailbreak, exploit, privilege-escalation, or device-modifying
workflows.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

from ips_uu.services.device_service import detect_target
from ips_uu.services.tool_resolver import resolve_cfgutil, resolve_idevicerestore


REPO_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
TOOLS_ROOT = REPO_ROOT / "tools"
PASSIVE_VERSION_FLAGS = (("--version",), ("-V",), ("version",))


def _run_passive(command: list[str], timeout: int = 5) -> dict[str, Any]:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except Exception as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc)}
    return {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}


def _sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_output(path: Path) -> str:
    binary = shutil.which("file")
    if not binary or not path.exists():
        return "unavailable"
    result = _run_passive([binary, str(path)])
    return (result["stdout"] or result["stderr"]).strip() or "unavailable"


def _codesign_output(path: Path) -> dict[str, Any]:
    binary = shutil.which("codesign")
    if not binary or platform.system() != "Darwin" or not path.exists():
        return {"available": False, "summary": "codesign unavailable"}
    result = _run_passive([binary, "-dv", "--verbose=4", str(path)])
    text = (result["stderr"] or result["stdout"]).strip()
    return {
        "available": True,
        "returncode": result["returncode"],
        "summary": text.splitlines()[0] if text else "no signature output",
        "raw": text,
    }


def _version_for(path: Path) -> dict[str, Any]:
    if not path.exists() or not os.access(path, os.X_OK):
        return {"detected": False, "value": None, "method": "not_executable"}
    for flags in PASSIVE_VERSION_FLAGS:
        result = _run_passive([str(path), *flags])
        text = "\n".join(part for part in (result["stdout"], result["stderr"]) if part).strip()
        if text and result["returncode"] in {0, 1}:
            return {"detected": True, "value": text.splitlines()[0].strip(), "method": " ".join(flags)}
    return {"detected": False, "value": None, "method": "passive_flags_no_output"}


def _permissions(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"mode": None, "executable": False, "readable": False}
    mode = path.stat().st_mode
    return {
        "mode": oct(stat.S_IMODE(mode)),
        "executable": os.access(path, os.X_OK),
        "readable": os.access(path, os.R_OK),
    }


def _binary_metadata(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "path": str(path),
            "present": False,
            "permissions": _permissions(path),
            "sha256": None,
            "signature": {"available": False, "summary": "missing"},
            "file": "missing",
        }
    stat_result = path.stat()
    return {
        "path": str(path),
        "present": True,
        "size_bytes": stat_result.st_size,
        "modified_unix": int(stat_result.st_mtime),
        "permissions": _permissions(path),
        "sha256": _sha256(path),
        "signature": _codesign_output(path),
        "file": _file_output(path),
    }


def _chip_guidance(product_type: str | None) -> dict[str, Any]:
    if not product_type:
        return {
            "architecture": "unknown",
            "palera1n_static_compatibility": "unknown",
            "note": "No ProductType was available for static compatibility guidance.",
        }
    family = product_type.split(",", 1)[0]
    family_number = int("".join(ch for ch in family if ch.isdigit()) or "0")
    if family.startswith("iPhone") and 6 <= family_number <= 10:
        return {
            "architecture": "A7-A11 family",
            "palera1n_static_compatibility": "possibly_supported_by_external_tool",
            "note": "Static ProductType guidance only. iPS-UU does not validate or execute jailbreak workflows.",
        }
    if family.startswith("iPad") and 4 <= family_number <= 7:
        return {
            "architecture": "A7-A10X-era iPad family",
            "palera1n_static_compatibility": "possibly_supported_by_external_tool",
            "note": "Static ProductType guidance only. Confirm support in external documentation.",
        }
    if family.startswith("iPod") and family_number in {7, 9}:
        return {
            "architecture": "A8/A10-era iPod family",
            "palera1n_static_compatibility": "possibly_supported_by_external_tool",
            "note": "Static ProductType guidance only. Confirm support in external documentation.",
        }
    return {
        "architecture": "newer_or_unknown_family",
        "palera1n_static_compatibility": "not_indicated_by_static_rules",
        "note": "This ProductType is not in the conservative static compatibility set.",
    }


def inspect_palera1n() -> dict[str, Any]:
    path = TOOLS_ROOT / "palera1n"
    metadata = _binary_metadata(path)
    version = _version_for(path)
    return {
        "name": "palera1n",
        "classification": "external_dependency_inventory_only",
        "status": "Installed" if metadata["present"] else "Missing",
        "version_status": "Version Detected" if version["detected"] else "Version Not Detected",
        "version": version,
        "metadata": metadata,
        "documentation": {
            "purpose": "External jailbreak/research tool that may be present for user-managed workflows.",
            "policy": [
                "iPS-UU detects and documents this binary only.",
                "iPS-UU does not execute, automate, launch, wrap, or expose palera1n jailbreak actions.",
                "No one-click jailbreak functionality is provided.",
            ],
        },
    }


def scan_external_tools() -> dict[str, Any]:
    device = detect_target("auto")
    guidance = _chip_guidance(device.get("product_type"))
    palera1n = inspect_palera1n()
    core_tools = [
        {"name": "idevicerestore", "path": resolve_idevicerestore(), "status": "Installed" if resolve_idevicerestore() else "Missing", "role": "supported signed restore backend"},
        {"name": "cfgutil", "path": resolve_cfgutil(), "status": "Installed" if resolve_cfgutil() else "Missing", "role": "Apple Configurator backend/wrapper"},
        {"name": "palera1n", "path": palera1n["metadata"]["path"], "status": palera1n["status"], "role": "external dependency inventory only"},
    ]
    return {
        "generated_by": "iPS-UU external tool inventory",
        "tools_root": str(TOOLS_ROOT),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "device": {
            **device,
            "architecture": guidance["architecture"],
            "compatibility_information": guidance,
        },
        "tools": {
            "palera1n": palera1n,
            "core": core_tools,
        },
        "safety": {
            "executes_jailbreak_actions": False,
            "modifies_connected_device": False,
            "one_click_jailbreak": False,
            "notes": [
                "External prerequisites must be completed outside iPS-UU.",
                "Inventory logs are for troubleshooting and reporting only.",
            ],
        },
    }
