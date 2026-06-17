"""Forsake backend discovery, planning, execution, and logging."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ips_uu.services.device_service import detect_target
from ips_uu.services.ipsw_service import compatibility_summary, parse_ipsw
from ips_uu.services.logging_service import get_log_dir


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = REPO_ROOT / "tools"
FORSKAKE_NAMES = ("forsake", "Forsake", "forsake.py", "forsake.sh")
SOURCE_SUFFIXES = {".md", ".txt", ".py", ".sh", ".c", ".h", ".m", ".mm", ".cpp", ".rs"}
PASSIVE_HELP_FLAGS = (("--help",), ("-h",), ("help",))
PASSIVE_VERSION_FLAGS = (("--version",), ("-V",), ("version",))
CURRENT_PROCESS: subprocess.Popen[str] | None = None


class ForsakeError(RuntimeError):
    pass


def _candidate_paths() -> list[Path]:
    candidates: list[Path] = []
    for name in FORSKAKE_NAMES:
        path = TOOLS_ROOT / name
        if path.exists():
            candidates.append(path)
    for folder in (TOOLS_ROOT / "forsake", TOOLS_ROOT / "Forsake"):
        if folder.is_dir():
            for name in FORSKAKE_NAMES:
                path = folder / name
                if path.exists():
                    candidates.append(path)
            candidates.append(folder)
    return list(dict.fromkeys(candidates))


def _is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _run_passive(command: list[str], timeout: int = 8) -> dict[str, Any]:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except Exception as exc:
        return {"command": command, "returncode": 1, "stdout": "", "stderr": str(exc), "succeeded": False}
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "succeeded": completed.returncode in {0, 1},
    }


def _executable_command(path: Path) -> list[str] | None:
    if path.is_dir():
        for name in FORSKAKE_NAMES:
            child = path / name
            if _is_executable(child):
                return [str(child)]
        return None
    if path.suffix == ".py":
        return ["python3", str(path)]
    if _is_executable(path):
        return [str(path)]
    return None


def find_forsake_toolchain() -> dict[str, Any]:
    candidates = []
    for path in _candidate_paths():
        command = _executable_command(path)
        candidates.append(
            {
                "path": str(path),
                "present": path.exists(),
                "is_dir": path.is_dir(),
                "executable": command is not None,
                "command_base": command or [],
                "mode": oct(stat.S_IMODE(path.stat().st_mode)) if path.exists() else None,
            }
        )
    selected = next((item for item in candidates if item.get("executable")), candidates[0] if candidates else None)
    return {
        "found": selected is not None,
        "selected": selected,
        "candidates": candidates,
        "tools_root": str(TOOLS_ROOT),
        "setup_error": None if selected else "Forsake was not found under tools/.",
    }


def get_forsake_version() -> dict[str, Any]:
    toolchain = find_forsake_toolchain()
    base = ((toolchain.get("selected") or {}).get("command_base") or [])
    if not base:
        return {"detected": False, "value": None, "error": toolchain.get("setup_error")}
    attempts = []
    for flags in PASSIVE_VERSION_FLAGS:
        result = _run_passive([*base, *flags])
        attempts.append(result)
        text = "\n".join(part for part in (result.get("stdout"), result.get("stderr")) if part).strip()
        if text:
            return {"detected": True, "value": text.splitlines()[0].strip(), "attempts": attempts}
    return {"detected": False, "value": None, "attempts": attempts}


def inspect_forsake_help() -> dict[str, Any]:
    toolchain = find_forsake_toolchain()
    base = ((toolchain.get("selected") or {}).get("command_base") or [])
    attempts = []
    text = ""
    if base:
        for flags in PASSIVE_HELP_FLAGS:
            result = _run_passive([*base, *flags])
            attempts.append(result)
            candidate = "\n".join(part for part in (result.get("stdout"), result.get("stderr")) if part).strip()
            if candidate:
                text = candidate
                break
    if not text:
        text = _read_docs_text(toolchain)
    return {"text": text, "attempts": attempts, "source": "help_or_docs"}


def _read_docs_text(toolchain: dict[str, Any]) -> str:
    selected = toolchain.get("selected") or {}
    path = Path(str(selected.get("path") or TOOLS_ROOT / "forsake"))
    root = path if path.is_dir() else path.parent
    chunks = []
    if root.exists():
        for source in sorted(root.rglob("*"))[:200]:
            if source.is_file() and source.suffix.lower() in SOURCE_SUFFIXES:
                try:
                    chunks.append(f"\n# {source.name}\n" + source.read_text(encoding="utf-8", errors="replace")[:12000])
                except OSError:
                    continue
    return "\n".join(chunks)


def parse_supported_arguments(help_text: str | None = None) -> dict[str, Any]:
    text = help_text if help_text is not None else inspect_forsake_help().get("text", "")
    arguments = sorted(set(re.findall(r"(?<!\w)--[A-Za-z0-9][A-Za-z0-9_-]*", text)))
    required_files = sorted(set(re.findall(r"[A-Za-z0-9_./-]+\.(?:ipsw|shsh2?|bshsh2|plist|img4|im4p|dmg|bin)", text, flags=re.I)))
    modes = []
    for mode in ("normal", "recovery", "dfu", "pwned dfu", "restore"):
        if mode in text.lower():
            modes.append(mode)
    products = sorted(set(re.findall(r"(?:iPhone|iPad|iPod)\d+,\d+", text)))
    ios_versions = sorted(set(re.findall(r"\biOS\s*[0-9]+(?:\.[0-9]+){0,2}\b", text, flags=re.I)))
    return {
        "arguments": arguments,
        "required_files": required_files,
        "required_device_modes": modes or ["Unknown / needs manual verification."],
        "supported_product_types": products,
        "supported_ios_versions": ios_versions or ["Unknown / needs manual verification."],
        "support_source": "Forsake --help/docs/source only",
    }


def check_forsake_requirements(
    device: dict[str, Any] | None,
    ipsw: dict[str, Any] | None = None,
    selected_files: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    toolchain = find_forsake_toolchain()
    help_info = inspect_forsake_help()
    support = parse_supported_arguments(help_info.get("text", ""))
    selected_files = selected_files or {}
    missing_files = [name for name, value in selected_files.items() if value and not Path(value).expanduser().exists()]
    product = (device or {}).get("product_type")
    mode = (device or {}).get("current_mode") or (device or {}).get("usb_mode")
    products = support.get("supported_product_types") or []
    support_known = bool(products)
    device_supported = product in products if support_known and product else None
    compatible = True
    compatibility = None
    if ipsw:
        compatibility = compatibility_summary(device, ipsw)
        compatible = compatibility.get("status") != "incompatible"
    checks = [
        ("Forsake tool found", bool(toolchain.get("found")), toolchain.get("setup_error") or ""),
        ("Forsake executable", bool((toolchain.get("selected") or {}).get("executable")), ""),
        ("Device detected", bool(device and not device.get("error")), str((device or {}).get("error") or "")),
        ("Device mode known", mode not in {None, "unknown", "not_detected"}, str(mode or "unknown")),
        ("Forsake support known", support_known, "No ProductType list was found in Forsake help/docs/source." if not support_known else ""),
        ("Device supported by Forsake metadata", device_supported is not False, "Unsupported by parsed Forsake metadata." if device_supported is False else ""),
        ("IPSW compatible", compatible, (compatibility or {}).get("message") or ""),
        ("Required files exist", not missing_files, ", ".join(missing_files)),
    ]
    status = "Ready" if all(item[1] for item in checks) and support_known and device_supported is not False else "Unknown support"
    if not toolchain.get("found"):
        status = "Missing tool"
    elif device_supported is False:
        status = "Unsupported device"
    elif mode in {None, "unknown", "not_detected"}:
        status = "Wrong mode"
    elif missing_files:
        status = "Missing files"
    elif not compatible:
        status = "Incompatible firmware"
    return {
        "status": status,
        "checks": [{"label": label, "passed": passed, "detail": detail} for label, passed, detail in checks],
        "toolchain": toolchain,
        "version": get_forsake_version(),
        "help": help_info,
        "support": support,
        "missing_files": missing_files,
    }


def create_session_dir(root: Path | None = None) -> Path:
    base = root or get_log_dir() / "forsake"
    path = base / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        path = Path(tempfile.gettempdir()) / "ips-uu" / "logs" / "forsake" / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path.mkdir(parents=True, exist_ok=True)
    return path


def build_dry_run_plan(
    device: dict[str, Any],
    ipsw: dict[str, Any] | None,
    selected_files: dict[str, str | None] | None = None,
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    toolchain = find_forsake_toolchain()
    base = ((toolchain.get("selected") or {}).get("command_base") or [])
    selected_files = selected_files or {}
    command = [*base]
    if ipsw and ipsw.get("path"):
        command.extend(["--ipsw", str(ipsw["path"])])
    if device.get("ecid"):
        command.extend(["--ecid", str(device["ecid"])])
    for name, value in selected_files.items():
        if value:
            command.extend([f"--{name.replace('_', '-')}", value])
    if extra_args:
        command.extend(extra_args)
    return {
        "backend": "forsake",
        "working_directory": str(Path(base[0]).parent if base else TOOLS_ROOT),
        "environment": {"PATH": os.environ.get("PATH", "")},
        "command": command,
        "command_preview": " ".join(command) if command else "Forsake command unavailable; tool missing.",
        "destructive_actions_executed": False,
        "device": device,
        "ipsw": ipsw,
        "selected_files": selected_files,
    }


def write_session_inputs(
    session: Path,
    device: dict[str, Any],
    tool_detection: dict[str, Any],
    compatibility: dict[str, Any],
    plan: dict[str, Any],
) -> None:
    (session / "device_detection.json").write_text(json.dumps(device, indent=2, sort_keys=True), encoding="utf-8")
    (session / "tool_detection.json").write_text(json.dumps(tool_detection, indent=2, sort_keys=True), encoding="utf-8")
    (session / "compatibility_check.json").write_text(json.dumps(compatibility, indent=2, sort_keys=True), encoding="utf-8")
    (session / "command_plan.json").write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
    (session / "stdout.log").write_text("", encoding="utf-8")
    (session / "stderr.log").write_text("", encoding="utf-8")
    (session / "summary.txt").write_text("Forsake dry-run/session initialized. No command executed yet.\n", encoding="utf-8")


def execute_plan_with_logs(
    plan: dict[str, Any],
    session_dir: str | None = None,
    callback: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    global CURRENT_PROCESS
    command = list(plan.get("command") or [])
    if not command:
        raise ForsakeError("Forsake command is unavailable.")
    session = Path(session_dir) if session_dir else create_session_dir()
    stdout_path = session / "stdout.log"
    stderr_path = session / "stderr.log"
    with stdout_path.open("a", encoding="utf-8") as stdout_file, stderr_path.open("a", encoding="utf-8") as stderr_file:
        CURRENT_PROCESS = subprocess.Popen(
            command,
            cwd=str(plan.get("working_directory") or TOOLS_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            bufsize=1,
        )

        def pump(stream: Any, target: Any, name: str) -> None:
            for line in iter(stream.readline, ""):
                target.write(line)
                target.flush()
                if callback:
                    callback(name, line.rstrip("\n"))

        threads = [
            threading.Thread(target=pump, args=(CURRENT_PROCESS.stdout, stdout_file, "stdout"), daemon=True),
            threading.Thread(target=pump, args=(CURRENT_PROCESS.stderr, stderr_file, "stderr"), daemon=True),
        ]
        for thread in threads:
            thread.start()
        returncode = CURRENT_PROCESS.wait()
        for thread in threads:
            thread.join(timeout=2)
    result = {"returncode": returncode, "succeeded": returncode == 0, "session_dir": str(session), "command": command}
    (session / "summary.txt").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    CURRENT_PROCESS = None
    return result


def cancel_current_process() -> dict[str, Any]:
    global CURRENT_PROCESS
    if CURRENT_PROCESS is None or CURRENT_PROCESS.poll() is not None:
        return {"cancelled": False, "reason": "No active Forsake process."}
    CURRENT_PROCESS.terminate()
    return {"cancelled": True}
