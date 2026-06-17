"""Discovery and source inspection for bundled open-source backend tools."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
TOOLS_ROOT = REPO_ROOT / "tools"


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    filenames: tuple[str, ...]
    purpose: str
    supported_workflows: tuple[str, ...]
    required_mode: str
    license_hint: str
    supported_device_families: tuple[str, ...]


TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        "idevicerestore",
        ("idevicerestore",),
        "libimobiledevice restore backend for iOS firmware restore/update.",
        ("restore", "update", "recovery"),
        "normal, recovery, or DFU depending on restore path",
        "LGPL/GPL-family libimobiledevice project license; verify bundled copy",
        ("iPhone", "iPad", "iPod touch", "Apple TV where backend supports it"),
    ),
    ToolDefinition(
        "ideviceinstaller",
        ("ideviceinstaller",),
        "libimobiledevice app install/list/uninstall utility.",
        ("apps/install", "app inventory"),
        "normal trusted USB pairing",
        "GPL-family libimobiledevice project license; verify bundled copy",
        ("iPhone", "iPad", "iPod touch"),
    ),
    ToolDefinition(
        "libimobiledevice utilities",
        ("idevice_id", "ideviceinfo", "idevicepair", "idevicediagnostics", "ideviceenterrecovery", "idevicescreenshot", "irecovery"),
        "Device detection, pairing, recovery-mode, and diagnostics utilities.",
        ("connected device", "recovery", "diagnostics"),
        "normal trusted USB pairing or recovery depending on utility",
        "LGPL/GPL-family libimobiledevice project licenses; verify bundled copy",
        ("iPhone", "iPad", "iPod touch", "Apple TV where supported"),
    ),
)

PASSIVE_FLAGS = (("--version",), ("-v",), ("-V",), ("version",), ("--help",))
SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".h", ".hpp", ".m", ".mm", ".py", ".sh", ".rs", ".go", ".swift"}


def _candidate_paths(filename: str) -> list[Path]:
    found: list[Path] = []
    bundled = TOOLS_ROOT / filename
    if bundled.exists():
        found.append(bundled)
    bundled_nested = TOOLS_ROOT / "libimobiledevice" / filename
    if bundled_nested.exists():
        found.append(bundled_nested)
    nested = TOOLS_ROOT / filename / filename
    if nested.exists():
        found.append(nested)
    path_tool = shutil.which(filename)
    if path_tool:
        found.append(Path(path_tool))
    return found


def _version_for(path: Path) -> str | None:
    if not path.exists() or not os.access(path, os.X_OK):
        return None
    for flags in PASSIVE_FLAGS:
        try:
            completed = subprocess.run([str(path), *flags], capture_output=True, text=True, timeout=5, check=False)
        except Exception:
            continue
        text = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
        if text:
            return text.splitlines()[0].strip()
    return None


def _architecture_for(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"kind": "missing", "architectures": [], "universal2": False}
    if path.is_dir():
        return {"kind": "directory", "architectures": [], "universal2": False}
    try:
        file_result = subprocess.run(["file", str(path)], capture_output=True, text=True, timeout=5, check=False)
    except Exception as exc:
        return {"kind": "unknown", "architectures": [], "universal2": False, "error": str(exc)}
    file_text = "\n".join(part for part in (file_result.stdout, file_result.stderr) if part).strip()
    if "script text executable" in file_text:
        return {"kind": "script", "architectures": [], "universal2": True, "file": file_text}
    try:
        lipo_result = subprocess.run(["lipo", "-archs", str(path)], capture_output=True, text=True, timeout=5, check=False)
    except Exception as exc:
        return {"kind": "unknown", "architectures": [], "universal2": False, "file": file_text, "error": str(exc)}
    archs = sorted(set(lipo_result.stdout.strip().split())) if lipo_result.returncode == 0 else []
    missing = sorted({"arm64", "x86_64"} - set(archs))
    return {
        "kind": "mach-o" if archs else "unknown",
        "architectures": archs,
        "universal2": not missing,
        "missing_architectures": missing,
        "file": file_text,
    }


def _license_for(path: Path, hint: str) -> str:
    roots = [path.parent]
    if path.is_dir():
        roots.insert(0, path)
    for root in roots:
        for name in ("LICENSE", "LICENSE.md", "COPYING", "COPYING.LESSER"):
            candidate = root / name
            if candidate.exists() and candidate.is_file():
                try:
                    first = candidate.read_text(encoding="utf-8", errors="replace").splitlines()[0]
                except OSError:
                    first = candidate.name
                return first or candidate.name
    return hint


def discover_tools() -> dict[str, Any]:
    tools = []
    for definition in TOOL_DEFINITIONS:
        candidates = []
        for filename in definition.filenames:
            paths = _candidate_paths(filename)
            path = paths[0] if paths else TOOLS_ROOT / filename
            present = path.exists()
            executable = present and path.is_file() and os.access(path, os.X_OK)
            candidates.append(
                {
                    "filename": filename,
                    "detected": present,
                    "path": str(path),
                    "executable": executable,
                    "version": _version_for(path) if executable else None,
                    "architecture": _architecture_for(path) if present else {"kind": "missing", "architectures": [], "universal2": False},
                    "license": _license_for(path, definition.license_hint) if present else definition.license_hint,
                }
            )
        tools.append(
            {
                "name": definition.name,
                "detected": any(item["detected"] for item in candidates),
                "all_required_detected": all(item["detected"] for item in candidates),
                "path": candidates[0]["path"] if candidates else str(TOOLS_ROOT),
                "executable": any(item["executable"] for item in candidates),
                "version": next((item["version"] for item in candidates if item.get("version")), None),
                "purpose": definition.purpose,
                "supported_workflows": list(definition.supported_workflows),
                "required_device_mode": definition.required_mode,
                "open_source_license": next((item["license"] for item in candidates if item.get("detected")), definition.license_hint),
                "supported_device_families": list(definition.supported_device_families),
                "components": candidates,
                "universal2_ready": all((item.get("architecture") or {}).get("universal2") for item in candidates if item.get("detected")),
            }
        )
    return {
        "tools_root": str(TOOLS_ROOT),
        "platform": platform.platform(),
        "tools": tools,
        "philosophy": "iPS-UU is a professional wrapper. Backend tools remain external open-source tools and are shown by path, command plan, output, and logs.",
    }


def run_diagnostics(tool_name: str) -> dict[str, Any]:
    inventory = discover_tools()
    tool = next((item for item in inventory["tools"] if item["name"] == tool_name), None)
    if not tool:
        raise ValueError(f"unknown tool: {tool_name}")
    diagnostics = []
    for component in tool.get("components") or []:
        path = Path(str(component["path"]))
        diagnostics.append(
            {
                "filename": component["filename"],
                "exists": path.exists(),
                "is_file": path.is_file(),
                "is_dir": path.is_dir(),
                "readable": os.access(path, os.R_OK) if path.exists() else False,
                "executable": os.access(path, os.X_OK) if path.exists() else False,
                "version": component.get("version"),
                "architecture": component.get("architecture"),
            }
        )
    return {"tool": tool, "diagnostics": diagnostics}


def _iter_source_files(root: Path) -> list[Path]:
    if root.is_file() and root.suffix.lower() in SOURCE_SUFFIXES:
        return [root]
    if not root.is_dir():
        return []
    files = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES:
            files.append(path)
    return files[:200]


def analyze_open_source_tool(tool_name: str) -> dict[str, Any]:
    inventory = discover_tools()
    tool = next((item for item in inventory["tools"] if item["name"] == tool_name), None)
    if not tool:
        raise ValueError(f"unknown tool: {tool_name}")
    roots = [Path(str(component["path"])) for component in tool.get("components") or [] if component.get("detected")]
    source_files = []
    for root in roots:
        source_files.extend(_iter_source_files(root if root.is_dir() else root.parent))
    source_files = sorted(set(source_files))
    entry_points: list[str] = []
    cli_args: set[str] = set()
    output_patterns: set[str] = set()
    required_files: set[str] = set()
    phases: set[str] = set()
    for path in source_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)
        if "int main" in text or "def main" in text or "__main__" in text:
            entry_points.append(rel)
        for token in text.replace("'", '"').split('"'):
            if token.startswith("--") or (token.startswith("-") and len(token) > 1 and token[1].isalpha()):
                cli_args.add(token.strip())
            lower = token.lower()
            if any(word in lower for word in ("error", "failed", "success", "done", "waiting")) and 4 <= len(token) <= 160:
                output_patterns.add(token.strip())
            if any(token.endswith(ext) for ext in (".ipsw", ".plist", ".shsh", ".shsh2", ".img4", ".im4p", ".dmg")):
                required_files.add(token.strip())
            if lower in {"prepare", "restore", "boot", "dfu", "recovery", "exploit", "upload", "install"}:
                phases.add(lower)
    return {
        "tool": tool["name"],
        "source_roots": [str(path) for path in roots],
        "source_files_scanned": [str(path) for path in source_files],
        "closed_source_decompile": False,
        "entry_points": entry_points,
        "cli_arguments": sorted(cli_args)[:200],
        "supported_devices": tool.get("supported_device_families") or [],
        "required_files": sorted(required_files)[:200],
        "output_error_patterns": sorted(output_patterns)[:200],
        "environment_requirements": [tool.get("required_device_mode") or "unknown", platform.platform()],
        "workflow_phases": sorted(phases) or list(tool.get("supported_workflows") or []),
        "note": "Analysis reads open-source source files included in the repository only. It does not decompile closed-source binaries.",
    }
