"""palera1n workflow documentation, preflight, and explicit terminal launch."""

from __future__ import annotations

import json
import os
import platform
import shlex
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from ips_uu.services.device_service import detect_target
from ips_uu.services.external_tools_service import inspect_palera1n
from ips_uu.services.logging_service import get_log_dir


GUIDE_URL = "https://ios.cfw.guide/installing-palera1n/#running-palera1n-1"


class Palera1nError(RuntimeError):
    pass


def _family_number(product_type: str | None, prefix: str) -> int | None:
    if not product_type or not product_type.startswith(prefix):
        return None
    family = product_type.split(",", 1)[0]
    digits = "".join(ch for ch in family if ch.isdigit())
    return int(digits) if digits else None


def _version_tuple(version: str | None) -> tuple[int, ...]:
    if not version:
        return ()
    parts = []
    for item in str(version).split("."):
        digits = "".join(ch for ch in item if ch.isdigit())
        if digits:
            parts.append(int(digits))
    return tuple(parts)


def compatibility_for_device(device: dict[str, Any] | None) -> dict[str, Any]:
    product = (device or {}).get("product_type")
    version = (device or {}).get("product_version")
    iphone = _family_number(product, "iPhone")
    ipad = _family_number(product, "iPad")
    ipod = _family_number(product, "iPod")
    os_version = _version_tuple(version)
    supported_family = bool(
        (iphone is not None and iphone <= 10)
        or (ipad is not None and ipad <= 7)
        or (ipod is not None and ipod <= 9)
    )
    version_supported = bool(os_version and os_version >= (15, 0))
    a11_like = bool(iphone == 10)
    status = "unknown"
    if product and os_version:
        status = "compatible_static_guidance" if supported_family and version_supported else "not_indicated_by_static_guidance"
    elif product:
        status = "version_unknown"
    return {
        "status": status,
        "product_type": product,
        "product_version": version,
        "supported_family_a11_or_earlier": supported_family,
        "ios_15_or_later": version_supported,
        "a11_passcode_sep_caveat": a11_like,
        "notes": [
            "iOS Guide states palera1n is compatible with A11 and earlier devices on iOS 15.0 and later, with caveats for A11 devices.",
            "A11 devices require passcode/SEP caveats to be reviewed before any external workflow.",
            "This is static guidance only; iPS-UU does not validate or run jailbreak workflows.",
        ],
    }


def find_toolchain() -> dict[str, Any]:
    palera1n = inspect_palera1n()
    return {
        "found": palera1n["status"] == "Installed",
        "tool": palera1n,
        "setup_error": None if palera1n["status"] == "Installed" else "palera1n was not found at tools/palera1n.",
    }


def run_rootless_version_check(timeout: int = 5) -> dict[str, Any]:
    """Run the passive palera1n --version metadata check."""
    toolchain = find_toolchain()
    metadata = (toolchain.get("tool") or {}).get("metadata") or {}
    path = Path(str(metadata.get("path") or ""))
    if not toolchain.get("found") or not path.exists():
        raise Palera1nError("palera1n was not found at tools/palera1n.")
    if not os.access(path, os.X_OK):
        raise Palera1nError("palera1n exists but is not executable.")
    command = [str(path), "--version"]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        return {
            "action": "rootless",
            "command": command,
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "palera1n --version timed out",
            "timed_out": True,
            "succeeded": False,
            "safety": {"device_action": False, "jailbreak_action": False, "metadata_only": True},
        }
    return {
        "action": "rootless",
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "timed_out": False,
        "succeeded": completed.returncode == 0,
        "safety": {"device_action": False, "jailbreak_action": False, "metadata_only": True},
    }


def build_rootless_launch_plan() -> dict[str, Any]:
    toolchain = find_toolchain()
    metadata = (toolchain.get("tool") or {}).get("metadata") or {}
    path = Path(str(metadata.get("path") or "tools/palera1n"))
    command = [str(path), "-l"]
    return {
        "backend": "palera1n",
        "workflow": "rootless_default_launch",
        "command": command,
        "command_preview": shlex.join(command),
        "terminal": "Terminal.app" if platform.system() == "Darwin" else "system terminal",
        "requires_user_terminal": True,
        "toolchain": toolchain,
    }


def launch_rootless_in_terminal() -> dict[str, Any]:
    plan = build_rootless_launch_plan()
    command = list(plan["command"])
    path = Path(command[0])
    if not path.exists():
        raise Palera1nError("palera1n was not found at tools/palera1n.")
    if not os.access(path, os.X_OK):
        raise Palera1nError("palera1n exists but is not executable.")
    display_command = shlex.join(command)
    if platform.system() == "Darwin":
        script = f'tell application "Terminal" to do script {json.dumps(display_command)}'
        completed = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False)
        return {
            **plan,
            "launcher": ["osascript", "-e", script],
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "succeeded": completed.returncode == 0,
        }
    raise Palera1nError("Opening palera1n in a new terminal is currently implemented for macOS Terminal.app.")


def inspect_device() -> dict[str, Any]:
    device = detect_target("auto")
    return {
        **device,
        "palera1n_compatibility": compatibility_for_device(device),
    }


def check_requirements(device: dict[str, Any] | None, caveat_ack: bool = False, external_ack: bool = False) -> dict[str, Any]:
    toolchain = find_toolchain()
    compatibility = compatibility_for_device(device)
    free_bytes = shutil.disk_usage(str(Path(__file__).resolve().parents[2])).free
    checks = [
        ("palera1n tool found", toolchain["found"], toolchain.get("setup_error") or ""),
        ("macOS/Linux style host", platform.system() in {"Darwin", "Linux"}, f"Current platform: {platform.system()}"),
        ("device connected", bool(device and not device.get("error")), str((device or {}).get("error") or "")),
        ("static compatibility reviewed", compatibility["status"] != "not_indicated_by_static_guidance", compatibility["status"]),
        ("A11/passcode/SEP caveats acknowledged", caveat_ack, "A11 users must review passcode, Face ID/Touch ID, Apple Pay, and erase caveats."),
        ("external execution requirement acknowledged", external_ack, "Any palera1n command must be run by the user outside iPS-UU."),
        ("enough diagnostic log space", free_bytes > 256 * 1024**2, f"Free: {free_bytes // 1024**2} MiB"),
    ]
    return {
        "toolchain": toolchain,
        "compatibility": compatibility,
        "checks": [{"label": label, "passed": passed, "detail": detail} for label, passed, detail in checks],
        "passed": all(passed for _label, passed, _detail in checks),
    }


def build_manual_plan(device: dict[str, Any], preflight: dict[str, Any] | None = None) -> dict[str, Any]:
    compatibility = compatibility_for_device(device)
    return {
        "workflow": "palera1n_external_manual_prerequisite",
        "execute_supported_by_ips_uu": False,
        "guide_url": GUIDE_URL,
        "device": device,
        "compatibility": compatibility,
        "preflight": preflight,
        "command": [],
        "command_preview": "No palera1n command is generated or executed by iPS-UU.",
        "user_guidance": [
            "Review the iOS Guide palera1n instructions outside iPS-UU.",
            "Connect the device by USB.",
            "If externally running palera1n, follow its on-screen DFU instructions outside iPS-UU.",
            "Return to iPS-UU only for inventory, diagnostics, and status documentation.",
        ],
        "warnings": [
            "iPS-UU does not execute, automate, launch, wrap, or expose palera1n jailbreak actions.",
            "USB-C to Lightning cables may cause DFU entry issues; the guide suggests USB-A to Lightning if needed.",
            "On Apple Silicon Macs using USB-C, the guide notes the device may need to be unplugged and replugged after Checkmate appears.",
            "A9(X) and earlier devices may get stuck midway in pongoOS and may require rerunning the external command.",
            "A11 devices have passcode/SEP caveats; passcode, Face ID/Touch ID, and Apple Pay are affected while jailbroken.",
        ],
    }


def create_session_dir(root: Path | None = None) -> Path:
    base = root or (get_log_dir() / "palera1n")
    path = base / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        path = Path(tempfile.gettempdir()) / "ips-uu" / "logs" / "palera1n" / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path.mkdir(parents=True, exist_ok=True)
    return path


def run_dry_run(plan: dict[str, Any], preflight: dict[str, Any] | None = None) -> dict[str, Any]:
    session = create_session_dir()
    (session / "palera1n_plan.json").write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
    if preflight is not None:
        (session / "preflight.json").write_text(json.dumps(preflight, indent=2, sort_keys=True), encoding="utf-8")
    (session / "summary.txt").write_text(
        "Documentation-only dry-run. iPS-UU did not execute palera1n or any jailbreak/exploit workflow.\n",
        encoding="utf-8",
    )
    return {"session_dir": str(session), "plan": plan, "preflight": preflight}
