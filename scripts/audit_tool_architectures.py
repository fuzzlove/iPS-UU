#!/usr/bin/env python3
"""Audit bundled macOS tool architecture compatibility."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


REQUIRED_ARCHES = {"arm64", "x86_64"}
REQUIRED_TOOLS = (
    "idevicerestore",
    "ideviceinstaller",
    "idevice_id",
    "ideviceinfo",
    "idevicepair",
    "idevicediagnostics",
    "ideviceenterrecovery",
    "irecovery",
    "idevicescreenshot",
)
SCRIPT_TOOLS = {"cfgutil"}


def run(command: list[str]) -> tuple[int, str]:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=10)
    except Exception as exc:
        return 1, str(exc)
    return completed.returncode, "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()


def architecture_info(path: Path, required_arches: set[str] | None = None) -> dict[str, Any]:
    required = required_arches or REQUIRED_ARCHES
    if not path.exists():
        return {"path": str(path), "exists": False, "ok": False, "reason": "missing"}
    if not path.is_file():
        return {"path": str(path), "exists": True, "ok": True, "kind": "directory", "reason": "source/tool directory"}
    executable = os.access(path, os.X_OK)
    file_rc, file_text = run(["file", str(path)])
    if "script text executable" in file_text or path.name in SCRIPT_TOOLS:
        if path.name == "idevicerestore":
            arm = path.with_name("idevicerestore.arm64")
            x86 = path.with_name("idevicerestore.x86_64")
            arm_info = architecture_info(arm, {"arm64"})
            x86_info = architecture_info(x86, {"x86_64"})
            ok = bool(arm_info.get("ok") and x86_info.get("ok"))
            return {
                "path": str(path),
                "exists": True,
                "executable": executable,
                "ok": executable and ok,
                "kind": "script-wrapper",
                "architectures": [],
                "wrapped_tools": {"arm64": arm_info, "x86_64": x86_info},
                "file": file_text,
                "reason": "script wrapper with native per-arch binaries" if ok else "script wrapper missing native per-arch binaries",
            }
        return {
            "path": str(path),
            "exists": True,
            "executable": executable,
            "ok": executable,
            "kind": "script",
            "architectures": [],
            "file": file_text,
            "reason": "script uses host interpreter",
        }
    lipo_rc, lipo_text = run(["lipo", "-archs", str(path)])
    archs = set(lipo_text.split()) if lipo_rc == 0 else set()
    missing = sorted(required - archs)
    return {
        "path": str(path),
        "exists": True,
        "executable": executable,
        "ok": executable and not missing,
        "kind": "mach-o" if archs else "unknown",
        "architectures": sorted(archs),
        "missing_architectures": missing,
        "file": file_text if file_rc == 0 else file_text,
        "reason": "universal2" if not missing and required == REQUIRED_ARCHES else "native slice present" if not missing else f"missing {', '.join(missing)}",
    }


def audit(root: Path = Path("tools")) -> dict[str, Any]:
    tools = []
    for name in REQUIRED_TOOLS:
        path = root / name
        tools.append({"name": name, **architecture_info(path)})
    ok = all(item["ok"] for item in tools)
    return {
        "required_architectures": sorted(REQUIRED_ARCHES),
        "tools_root": str(root),
        "ok": ok,
        "tools": tools,
    }


def main() -> int:
    result = audit(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("tools"))
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["ok"]:
        bad = [f"{item['name']} ({item.get('reason')})" for item in result["tools"] if not item["ok"]]
        print("architecture check failed: " + ", ".join(bad), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
