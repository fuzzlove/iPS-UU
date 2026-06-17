"""Safe Turdus Merula workflow wrapper.

This module treats Turdus Merula as an external bundled toolchain. It performs
discovery, compatibility checks, dry-run planning, and session logging. It does
not reimplement, patch, hide, or execute exploit/downgrade internals.
"""

from __future__ import annotations

import json
import os
import platform
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ips_uu.services.device_service import detect_target
from ips_uu.services.ipsw_service import parse_ipsw
from ips_uu.services.logging_service import get_log_dir


REPO_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
TOOLS_ROOT = REPO_ROOT / "tools"
TOOL_NAMES = ("turdus_merula", "turdusra1n")
GUIDE_URL = "https://ios.cfw.guide/turdusmerula-tethered-macos/"

A9_PRODUCT_TYPES = {
    "iPhone8,1",
    "iPhone8,2",
    "iPhone8,4",
    "iPad6,3",
    "iPad6,4",
    "iPad6,7",
    "iPad6,8",
}
A10_PRODUCT_TYPES = {
    "iPhone9,1",
    "iPhone9,2",
    "iPhone9,3",
    "iPhone9,4",
    "iPad7,5",
    "iPad7,6",
    "iPad7,11",
    "iPad7,12",
    "iPod9,1",
}
A10X_PRODUCT_TYPES = {"iPad7,1", "iPad7,2", "iPad7,3", "iPad7,4"}


class TurdusMerulaError(RuntimeError):
    pass


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def _repo_tool_path(name: str) -> Path:
    folder_form = TOOLS_ROOT / "turdus_merula" / name
    if folder_form.exists():
        return folder_form
    return TOOLS_ROOT / name


def _is_executable(path: Path) -> bool:
    return path.exists() and path.is_file() and os.access(path, os.X_OK)


def _run_capture(command: list[str], timeout: int = 8) -> CommandResult:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except Exception as exc:
        return CommandResult(1, "", str(exc))
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _version_for(path: Path) -> str:
    if not _is_executable(path):
        return "unavailable"
    for args in (["--version"], ["-v"], ["--help"]):
        result = _run_capture([str(path), *args])
        text = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        if text:
            for line in text.splitlines():
                if "version" in line.lower() or path.name in line.lower() or "tool info" in line.lower():
                    return line.strip()
            return text.splitlines()[0].strip()
    return "unknown"


def find_toolchain() -> dict[str, Any]:
    folder_form = TOOLS_ROOT / "turdus_merula"
    root = folder_form if folder_form.is_dir() else TOOLS_ROOT
    tools = []
    for name in TOOL_NAMES:
        path = _repo_tool_path(name)
        tools.append(
            {
                "name": name,
                "path": str(path),
                "present": path.exists(),
                "executable": _is_executable(path),
                "version": _version_for(path) if path.exists() else "missing",
            }
        )
    found = all(item["present"] for item in tools)
    executable = all(item["executable"] for item in tools)
    return {
        "root": str(root),
        "found": found,
        "executable_permissions_ok": executable,
        "tools": tools,
        "setup_error": None if found else "Required Turdus Merula tool files were not found under tools/.",
    }


def repair_permissions() -> dict[str, Any]:
    changed = []
    for name in TOOL_NAMES:
        path = _repo_tool_path(name)
        if path.exists() and path.is_file():
            mode = path.stat().st_mode
            path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            changed.append(str(path))
    return {"changed": changed, "toolchain": find_toolchain()}


def tool_path(name: str) -> str:
    return str(_repo_tool_path(name))


def terminal_command(command: list[str]) -> dict[str, Any]:
    preview = shlex.join(command)
    if platform.system() != "Darwin":
        raise TurdusMerulaError("Opening workflow steps in a new terminal is currently implemented for macOS Terminal.app.")
    script = f'tell application "Terminal" to do script {json.dumps(preview)}'
    completed = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False)
    return {
        "command": command,
        "command_preview": preview,
        "launcher": ["osascript", "-e", script],
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "succeeded": completed.returncode == 0,
    }


def chip_class_for_product(product_type: str | None) -> str:
    if not product_type:
        return "unknown"
    if product_type in A9_PRODUCT_TYPES:
        return "A9/A9X"
    if product_type in A10_PRODUCT_TYPES:
        return "A10"
    if product_type in A10X_PRODUCT_TYPES:
        return "A10X"
    return "unsupported"


def inspect_device() -> dict[str, Any]:
    device = detect_target("auto")
    product_type = device.get("product_type")
    chip = chip_class_for_product(product_type)
    supported = chip in {"A9/A9X", "A10", "A10X"}
    return {
        **device,
        "chip_class": chip,
        "appears_supported": supported,
        "support_note": "Turdus Merula guide targets A9(X), A10, and A10X devices." if supported else "Device is not recognized as A9(X), A10, or A10X.",
    }


def inspect_ipsw(path: str, product_type: str | None = None) -> dict[str, Any]:
    info = parse_ipsw(path, product_type)
    supported = info.get("supported_product_types") or []
    compatible = bool(product_type and product_type in supported)
    product_version = str(info.get("product_version") or "")
    activation_warning = False
    if product_version.startswith("10.") and product_type:
        activation_warning = product_type.startswith("iPhone9,") or product_type in {"iPad7,2", "iPad7,4"}
    return {
        **info,
        "compatible_with_device": compatible if product_type else None,
        "activation_baseband_warning": activation_warning,
        "activation_baseband_warning_text": (
            "iOS 10 cellular A10X/iPhone 7 class restores may fail activation due to baseband compatibility."
            if activation_warning
            else ""
        ),
    }


def inspect_artifacts(paths: dict[str, str | None]) -> dict[str, Any]:
    artifacts = []
    for name, raw_path in paths.items():
        if not raw_path:
            artifacts.append({"name": name, "path": None, "selected": False, "exists": False})
            continue
        path = Path(raw_path).expanduser()
        artifacts.append({"name": name, "path": str(path), "selected": True, "exists": path.exists(), "is_file": path.is_file()})
    required_failures = [item for item in artifacts if item["selected"] and not item["exists"]]
    return {
        "artifacts": artifacts,
        "valid": not required_failures,
        "notes": [
            "Artifact fields are validated for existence only.",
            "iPS-UU does not parse, patch, generate, replay, or submit SHSH/blob material.",
        ],
    }


def guide_profile_for_device(device: dict[str, Any] | None) -> str:
    chip = (device or {}).get("chip_class") or chip_class_for_product((device or {}).get("product_type"))
    if chip in {"A10", "A10X"}:
        return "a10x"
    if chip == "A9/A9X":
        return "a9x"
    return "unknown"


def build_guide_workflow(
    device: dict[str, Any] | None,
    ipsw: dict[str, Any] | None,
    artifacts: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    artifacts = artifacts or {}
    ipsw_path = str((ipsw or {}).get("path") or "<ipsw file>")
    profile = guide_profile_for_device(device)
    turdusra1n = tool_path("turdusra1n")
    turdus_merula = tool_path("turdus_merula")
    xattr_target = str(Path(turdus_merula).parent)
    common = [
        {
            "id": "clear_quarantine",
            "title": "Clear quarantine attributes",
            "purpose": "Guide step: run /usr/bin/xattr -cr against the extracted tool folder before using the binaries.",
            "command": ["/usr/bin/xattr", "-cr", xattr_target],
            "required_mode_before": "macOS host",
        },
        {
            "id": "enter_pwned_dfu",
            "title": "Run turdusra1n DFU preparation",
            "purpose": "Guide step: run turdusra1n -D and follow terminal prompts to enter DFU mode.",
            "command": [turdusra1n, "-D"],
            "required_mode_before": "Recovery mode, then follow DFU prompts",
        },
    ]
    if profile == "a10x":
        restore = {
            "id": "a10_restore",
            "title": "Restore A10/A10X device",
            "purpose": "Guide step: run turdus_merula -o with the target IPSW.",
            "command": [turdus_merula, "-o", ipsw_path],
            "required_mode_before": "DFU after turdusra1n -D",
        }
        boot = {
            "id": "a10_boot",
            "title": "Boot tethered A10/A10X device",
            "purpose": "Guide step: use files produced in image4 after restore.",
            "command": [
                turdusra1n,
                "-t",
                artifacts.get("iboot_img4") or "<iBoot.img4>",
                "-i",
                artifacts.get("signed_sep_img4") or "<signed-SEP.img4>",
                "-p",
                artifacts.get("target_sep_im4p") or "<target-SEP.im4p>",
            ],
            "required_mode_before": "DFU when prompted",
        }
        steps = [*common, restore, boot]
    elif profile == "a9x":
        pre = {
            "id": "a9_get_pre_shcblock",
            "title": "Get pre-restore shcblock",
            "purpose": "Guide step: run turdus_merula --get-shcblock with the target IPSW.",
            "command": [turdus_merula, "--get-shcblock", ipsw_path],
            "required_mode_before": "DFU after turdusra1n -D",
        }
        restore = {
            "id": "a9_restore",
            "title": "Restore A9/A9X device with shcblock",
            "purpose": "Guide step: run turdus_merula -o --load-shcblock <shcblock> <ipsw>.",
            "command": [
                turdus_merula,
                "-o",
                "--load-shcblock",
                artifacts.get("shcblock") or "<shcblock>",
                ipsw_path,
            ],
            "required_mode_before": "DFU after turdusra1n -D",
        }
        post = {
            "id": "a9_get_post_shcblock",
            "title": "Get post-restore shcblock",
            "purpose": "Guide step: run turdusra1n -g.",
            "command": [turdusra1n, "-g"],
            "required_mode_before": "DFU when prompted",
        }
        pte = {
            "id": "a9_get_pteblock",
            "title": "Get pteblock",
            "purpose": "Guide step: run turdusra1n -g -i <signed-SEP.img4> -C <post shcblock>.",
            "command": [
                turdusra1n,
                "-g",
                "-i",
                artifacts.get("signed_sep_img4") or "<signed-SEP.img4>",
                "-C",
                artifacts.get("post_shcblock") or "<post-restore shcblock>",
            ],
            "required_mode_before": "DFU when prompted",
        }
        boot = {
            "id": "a9_boot",
            "title": "Boot tethered A9/A9X device",
            "purpose": "Guide step: run turdusra1n -TP <pteblock>.",
            "command": [turdusra1n, "-TP", artifacts.get("pteblock") or "<pteblock>"],
            "required_mode_before": "DFU when prompted",
        }
        steps = [*common, pre, *common[1:], restore, post, pte, boot]
    else:
        steps = common
    return {
        "workflow": "ios_guide_turdus_merula_tethered_macos",
        "guide_url": GUIDE_URL,
        "profile": profile,
        "device": device,
        "ipsw": ipsw,
        "steps": [
            {
                **step,
                "command_preview": shlex.join([str(part) for part in step["command"]]),
            }
            for step in steps
        ],
        "warnings": [
            "Tethered restores require a computer to boot every time.",
            "Cellular A10X iPad Pros and some iPhone 7 devices may fail activation on iOS 10 due to baseband compatibility.",
            "checkra1n/palera1n need extra steps and cannot be run standalone on tethered downgraded devices.",
            "Commands are external backend commands; iPS-UU does not rewrite Turdus Merula exploit logic.",
        ],
    }


def _required_mode_ok(mode: str | None) -> bool:
    return mode in {"dfu", "recovery"}


def check_requirements(
    device: dict[str, Any] | None,
    ipsw: dict[str, Any] | None,
    tethered_ack: bool = False,
    data_loss_ack: bool = False,
    artifacts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    toolchain = find_toolchain()
    free_bytes = shutil.disk_usage(str(REPO_ROOT)).free
    mode = (device or {}).get("current_mode")
    checks = [
        ("turdus merula folder found", toolchain["found"], toolchain.get("setup_error") or ""),
        ("required binaries found", all(item["present"] for item in toolchain["tools"]), ""),
        ("executable permissions OK", toolchain["executable_permissions_ok"], "Use Repair permissions if this fails."),
        ("macOS version OK", platform.system() == "Darwin", f"Current platform: {platform.system()}"),
        ("device connected", bool(device and not device.get("error")), str((device or {}).get("error") or "")),
        ("device supported", bool(device and device.get("appears_supported")), str((device or {}).get("support_note") or "")),
        ("device in required mode", _required_mode_ok(mode), "DFU or recovery mode is required before execution."),
        ("IPSW selected", bool(ipsw and ipsw.get("path")), ""),
        ("IPSW compatible", bool(ipsw and ipsw.get("compatible_with_device") is True), ""),
        ("selected blob/artifact paths valid", bool((artifacts or {"valid": True}).get("valid")), "Selected optional files must exist."),
        ("enough disk space", free_bytes > 15 * 1024**3, f"Free: {free_bytes // 1024**3} GiB"),
        ("user understands tethered limitation", tethered_ack, "Tethered restores require this computer/tool to boot every time."),
        ("user understands data loss risk", data_loss_ack, "Restore workflows may erase device data."),
    ]
    return {
        "toolchain": toolchain,
        "checks": [{"label": label, "passed": passed, "detail": detail} for label, passed, detail in checks],
        "passed": all(passed for _label, passed, _detail in checks),
        "disk_free_bytes": free_bytes,
    }


def build_tethered_plan(
    device: dict[str, Any],
    ipsw: dict[str, Any],
    mode: str = "tethered",
    cache_dir: str | None = None,
    artifacts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if mode != "tethered":
        raise TurdusMerulaError("Only tethered dry-run planning is supported for this wrapper.")
    product_type = device.get("product_type")
    if not device.get("appears_supported"):
        raise TurdusMerulaError("Unsupported device. Expected A9(X), A10, or A10X.")
    if ipsw.get("compatible_with_device") is not True:
        raise TurdusMerulaError("IPSW does not match the detected device ProductType.")
    mode_state = device.get("current_mode")
    return {
        "workflow": "manual_prerequisite_tethered_restore_preparation",
        "execute_supported_by_ips_uu": False,
        "device": device,
        "ipsw": ipsw,
        "artifacts": artifacts or {},
        "manual_prerequisite": {
            "required": True,
            "expected_state": "dfu_or_recovery_after_user_completed_external_prerequisite",
            "current_mode": mode_state,
            "satisfied": _required_mode_ok(mode_state),
            "checklist": [
                "Connect device.",
                "Manually complete the required external prerequisite outside iPS-UU.",
                "Return to iPS-UU.",
                "Refresh device mode.",
                "Continue only after iPS-UU verifies the expected state.",
            ],
        },
        "command": [],
        "command_preview": "No exploit, pwnDFU, or Turdus Merula execution command is generated by iPS-UU.",
        "post_prerequisite_steps": [
            "Validate toolchain presence and executable permissions.",
            "Verify device is already in the expected mode/state.",
            "Validate IPSW ProductType compatibility.",
            "Validate selected user-supplied artifact paths exist.",
            "Prepare logs and diagnostics for the servicing session.",
        ],
        "phases": [
            "preparing backend",
            "waiting for DFU/recovery",
            "manual external prerequisite already completed",
            "verifying device state",
            "preparing restore inputs",
            "ready for supported post-prerequisite workflow",
            "tethered boot required",
        ],
        "warnings": [
            "Tethered restores require this computer/tool to boot the device every time.",
            "iPS-UU does not execute pwnDFU, exploit, or Turdus Merula commands.",
            "Waiting for device in required mode/state before continuing.",
            ipsw.get("activation_baseband_warning_text") or "",
        ],
    }


def create_session_dir(root: Path | None = None) -> Path:
    base = root or (get_log_dir() / "turdus_merula")
    path = base / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        path = Path(tempfile.gettempdir()) / "ips-uu" / "logs" / "turdus_merula" / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path.mkdir(parents=True, exist_ok=True)
    return path


def run_dry_run(plan: dict[str, Any], preflight: dict[str, Any] | None = None) -> dict[str, Any]:
    session = create_session_dir()
    (session / "restore_plan.json").write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
    if preflight is not None:
        (session / "preflight.json").write_text(json.dumps(preflight, indent=2, sort_keys=True), encoding="utf-8")
    (session / "command_preview.txt").write_text(str(plan.get("command_preview") or ""), encoding="utf-8")
    (session / "stdout.log").write_text("", encoding="utf-8")
    (session / "stderr.log").write_text("", encoding="utf-8")
    (session / "summary.txt").write_text(
        "Dry-run only. Waiting for device in required mode. iPS-UU did not execute pwnDFU, exploit, or Turdus Merula commands.\n",
        encoding="utf-8",
    )
    return {"session_dir": str(session), "plan": plan, "preflight": preflight}


def execute_plan(_plan: dict[str, Any], callbacks: Callable[[str, str], None] | None = None) -> dict[str, Any]:
    if callbacks:
        callbacks("error", "Execution is intentionally disabled in iPS-UU for Turdus Merula exploit/downgrade workflows.")
    raise TurdusMerulaError("Turdus Merula execution is disabled in iPS-UU. Dry-run planning and diagnostics are supported.")


def cancel_current_process() -> dict[str, Any]:
    return {"cancelled": False, "reason": "No Turdus Merula subprocess is started by iPS-UU."}
