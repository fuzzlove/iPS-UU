"""Multi-backend iOS device detection and diagnostics."""

from __future__ import annotations

import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
TOOLS_ROOT = REPO_ROOT / "tools"
DEVICE_MAP_PATH = REPO_ROOT / "data" / "ios_device_map.json"
DEFAULT_TIMEOUT = 10


TOOL_NAMES = ("idevice_id", "ideviceinfo", "irecovery", "pymobiledevice3", "system_profiler")
APPLE_USB_MARKERS = ("iPhone", "iPad", "iPod", "Apple Mobile Device", "Vendor ID: 0x05ac")
DFU_PRODUCT_IDS = {"0x1227", "0x1222"}
RECOVERY_PRODUCT_IDS = {"0x1280", "0x1281", "0x1282", "0x1283"}


def resolve_tool(name: str) -> str | None:
    local = TOOLS_ROOT / name
    if local.exists() and local.is_file() and os.access(local, os.X_OK):
        return str(local)
    nested = TOOLS_ROOT / "libimobiledevice" / name
    if nested.exists() and nested.is_file() and os.access(nested, os.X_OK):
        return str(nested)
    return shutil.which(name)


def run_command(command: list[str], timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "command timed out",
            "timed_out": True,
            "succeeded": False,
        }
    except FileNotFoundError as exc:
        return {
            "command": command,
            "returncode": 127,
            "stdout": "",
            "stderr": str(exc),
            "timed_out": False,
            "succeeded": False,
        }
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "timed_out": False,
        "succeeded": completed.returncode == 0,
    }


def load_device_map() -> dict[str, Any]:
    try:
        with DEVICE_MAP_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return {"devices": {}}
    return payload if isinstance(payload, dict) else {"devices": {}}


def device_map_entry(product_type: str | None) -> dict[str, Any]:
    if not product_type:
        return {}
    devices = load_device_map().get("devices") or {}
    entry = devices.get(product_type)
    return entry if isinstance(entry, dict) else {}


def _parse_plist(text: str) -> dict[str, Any]:
    try:
        value = plistlib.loads(text.encode())
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _parse_irecovery(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
        elif "=" in line:
            key, value = line.split("=", 1)
        else:
            continue
        values[key.strip().lower()] = value.strip()
    return values


def _usb_entries(timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    binary = resolve_tool("system_profiler") or "system_profiler"
    if platform.system() != "Darwin" and not shutil.which(binary):
        return {"available": False, "entries": [], "raw": "", "error": "system_profiler unavailable"}
    result = run_command([binary, "SPUSBDataType"], timeout=timeout)
    raw = "\n".join(part for part in (result.get("stdout"), result.get("stderr")) if part)
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.endswith(":") and not stripped.startswith(("Product ID", "Vendor ID", "Location ID")):
            if current:
                entries.append(current)
            current = {"name": stripped.rstrip(":"), "lines": [stripped]}
            continue
        if current is not None:
            current["lines"].append(stripped)
            if stripped.startswith("Product ID:"):
                current["product_id"] = stripped.split(":", 1)[1].strip().split()[0]
            elif stripped.startswith("Vendor ID:"):
                current["vendor_id"] = stripped.split(":", 1)[1].strip().split()[0]
            elif stripped.startswith("Serial Number:"):
                current["serial_number"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("Location ID:"):
                current["location_id"] = stripped.split(":", 1)[1].strip()
    if current:
        entries.append(current)
    apple_entries = [
        entry
        for entry in entries
        if str(entry.get("vendor_id")).lower() == "0x05ac"
        or any(marker.lower() in " ".join(entry.get("lines") or []).lower() for marker in APPLE_USB_MARKERS)
    ]
    return {
        "available": result.get("returncode") == 0 and not result.get("timed_out"),
        "entries": entries,
        "apple_entries": apple_entries,
        "raw": raw,
        "command_result": result,
    }


def _mode_from_usb(usb: dict[str, Any]) -> str:
    for entry in usb.get("apple_entries") or []:
        product = str(entry.get("product_id") or "").lower()
        if product in DFU_PRODUCT_IDS:
            return "dfu"
        if product in RECOVERY_PRODUCT_IDS:
            return "recovery"
        return "normal"
    return "unknown"


def _normal_detection(tools: dict[str, Any], timeout: int) -> dict[str, Any]:
    result: dict[str, Any] = {"backend": "normal", "commands": []}
    idevice_id = tools.get("idevice_id", {}).get("path")
    ideviceinfo = tools.get("ideviceinfo", {}).get("path")
    if idevice_id:
        listing = run_command([idevice_id, "-l"], timeout=timeout)
        result["commands"].append(listing)
        result["udids"] = [line.strip() for line in listing.get("stdout", "").splitlines() if line.strip()]
    if ideviceinfo:
        command = [ideviceinfo, "-x"]
        if result.get("udids"):
            command = [ideviceinfo, "-u", result["udids"][0], "-x"]
        info_result = run_command(command, timeout=timeout)
        result["commands"].append(info_result)
        if info_result.get("succeeded"):
            result["info"] = _parse_plist(str(info_result.get("stdout") or ""))
    return result


def _pymobiledevice3_detection(tools: dict[str, Any], timeout: int) -> dict[str, Any]:
    binary = tools.get("pymobiledevice3", {}).get("path")
    result: dict[str, Any] = {"backend": "pymobiledevice3", "commands": []}
    if not binary:
        result["missing"] = True
        return result
    for args in ([binary, "usbmux", "list"], [binary, "lockdown", "info"]):
        command_result = run_command(list(args), timeout=timeout)
        result["commands"].append(command_result)
        if command_result.get("succeeded") and command_result.get("stdout"):
            try:
                result.setdefault("raw_outputs", []).append(json.loads(str(command_result["stdout"])))
            except Exception:
                result.setdefault("raw_outputs", []).append(command_result["stdout"])
    return result


def _recovery_dfu_detection(tools: dict[str, Any], timeout: int) -> dict[str, Any]:
    result: dict[str, Any] = {"backend": "irecovery", "commands": []}
    binary = tools.get("irecovery", {}).get("path")
    if not binary:
        result["missing"] = True
        return result
    query = run_command([binary, "-q"], timeout=timeout)
    result["commands"].append(query)
    if query.get("succeeded"):
        result["info"] = _parse_irecovery(str(query.get("stdout") or ""))
    return result


def _tool_inventory() -> dict[str, Any]:
    tools = {}
    for name in TOOL_NAMES:
        path = resolve_tool(name)
        tools[name] = {
            "path": path,
            "found": path is not None,
            "executable": bool(path and os.access(path, os.X_OK)),
        }
    return tools


def _normalize(normal: dict[str, Any], recovery: dict[str, Any], pmd3: dict[str, Any], usb: dict[str, Any]) -> dict[str, Any]:
    info = normal.get("info") or {}
    rec = recovery.get("info") or {}
    usb_mode = _mode_from_usb(usb)
    mode = "normal" if info else "unknown"
    if rec:
        raw_mode = str(rec.get("mode") or rec.get("device mode") or "").lower()
        mode = "dfu" if "dfu" in raw_mode else "recovery" if raw_mode else usb_mode
    elif usb_mode != "unknown":
        mode = usb_mode
    product_type = info.get("ProductType") or rec.get("product") or rec.get("product type")
    entry = device_map_entry(product_type)
    usb_location = None
    for usb_entry in usb.get("apple_entries") or []:
        if usb_entry.get("location_id"):
            usb_location = usb_entry.get("location_id")
            break
    identity = {
        "product_type": product_type,
        "product_version": info.get("ProductVersion"),
        "build_version": info.get("BuildVersion"),
        "device_name": info.get("DeviceName"),
        "serial_number": info.get("SerialNumber") or rec.get("serial number"),
        "ecid": str(info.get("UniqueChipID") or rec.get("ecid") or "") or None,
        "cpid": str(info.get("ChipID") or rec.get("cpid") or "") or None,
        "bdid": str(info.get("BoardId") or info.get("BoardID") or rec.get("bdid") or "") or None,
        "model_identifier": info.get("ModelNumber") or info.get("HardwareModel") or entry.get("board_config"),
        "usb_location": usb_location,
        "usb_mode": mode,
        "backend_used": "ideviceinfo" if info else "irecovery" if rec else "system_profiler" if usb_mode != "unknown" else "none",
        "marketing_name": entry.get("marketing_name") or "Unknown / needs manual verification.",
        "chip_family": entry.get("chip_family") or "Unknown / needs manual verification.",
        "board_config": entry.get("board_config") or "Unknown / needs manual verification.",
        "supported_backend_categories": entry.get("supported_backend_categories") or ["Unknown / needs manual verification."],
        "notes": entry.get("notes") or "Unknown / needs manual verification.",
    }
    return identity


def _recommendation(tools: dict[str, Any], normal: dict[str, Any], recovery: dict[str, Any], usb: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    failures = "\n".join(
        str(command.get("stderr") or command.get("stdout") or "")
        for section in (normal, recovery)
        for command in section.get("commands", [])
        if not command.get("succeeded")
    ).lower()
    if not tools.get("idevice_id", {}).get("found") or not tools.get("ideviceinfo", {}).get("found"):
        return {"issue": "missing libimobiledevice", "recommended_fix": "Install or bundle idevice_id and ideviceinfo, or configure tool paths in Settings."}
    if not tools.get("irecovery", {}).get("found"):
        return {"issue": "missing irecovery", "recommended_fix": "Install or bundle irecovery for recovery/DFU detection."}
    if not tools.get("pymobiledevice3", {}).get("found"):
        pmd3_note = "pymobiledevice3 is optional but missing; libimobiledevice fallback is being used."
    else:
        pmd3_note = ""
    if "lock" in failures or "password" in failures:
        return {"issue": "device locked", "recommended_fix": "Unlock the device, keep it on the Home Screen, and refresh detection."}
    if "trust" in failures or "invalid host" in failures or "pair" in failures:
        return {"issue": "device not trusted", "recommended_fix": "Unlock the device and tap Trust This Computer, then run pair validation again."}
    if "permission" in failures or "operation not permitted" in failures:
        return {"issue": "insufficient permissions", "recommended_fix": "Run outside a restricted sandbox, reconnect the device, and ensure usbmuxd access is allowed."}
    if "usbmux" in failures:
        return {"issue": "Apple Mobile Device stack not responding", "recommended_fix": "Reconnect the device, restart the app, or restart Apple usbmuxd/MobileDevice services."}
    if not usb.get("apple_entries") and identity.get("backend_used") == "none":
        return {"issue": "bad cable or no USB device", "recommended_fix": "Use a data-capable cable, connect directly to the Mac, unlock the device, and refresh."}
    if identity.get("usb_mode") in {"recovery", "dfu"} and not normal.get("info"):
        return {"issue": "device in wrong mode for normal tools", "recommended_fix": "Normal-mode tools will not identify recovery/DFU devices; use irecovery details or exit recovery."}
    if identity.get("product_type") is None and usb.get("apple_entries"):
        return {"issue": "USB device detected, ProductType unresolved", "recommended_fix": "Use irecovery for recovery/DFU or trust/unlock the device for ideviceinfo metadata."}
    return {"issue": "none" if identity.get("backend_used") != "none" else "unknown", "recommended_fix": pmd3_note or "Device identity is available."}


def detect_target(device: str = "auto", timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    tools = _tool_inventory()
    normal = _normal_detection(tools, timeout)
    recovery = _recovery_dfu_detection(tools, timeout)
    pmd3 = _pymobiledevice3_detection(tools, timeout)
    usb = _usb_entries(timeout)
    identity = _normalize(normal, recovery, pmd3, usb)
    recommendation = _recommendation(tools, normal, recovery, usb, identity)
    error = None if identity.get("backend_used") != "none" else recommendation.get("recommended_fix")
    return {
        "detection_method": identity.get("backend_used"),
        "current_mode": identity.get("usb_mode"),
        "product_type": identity.get("product_type"),
        "product_version": identity.get("product_version"),
        "build_version": identity.get("build_version"),
        "serial_number": identity.get("serial_number"),
        "usb_location": identity.get("usb_location"),
        "ecid": identity.get("ecid"),
        "udid": (normal.get("udids") or [None])[0],
        "device_name": identity.get("device_name"),
        "cpid": identity.get("cpid"),
        "bdid": identity.get("bdid"),
        "model_identifier": identity.get("model_identifier"),
        "marketing_name": identity.get("marketing_name"),
        "chip_family": identity.get("chip_family"),
        "board_config": identity.get("board_config"),
        "supported_backend_categories": identity.get("supported_backend_categories"),
        "notes": identity.get("notes"),
        "raw": {"normal": normal, "recovery_dfu": recovery, "pymobiledevice3": pmd3, "usb": usb},
        "diagnostics": {
            "tools": tools,
            "normal_mode": normal,
            "recovery_dfu": recovery,
            "pymobiledevice3": pmd3,
            "usb_entries": usb.get("apple_entries") or [],
            "recommended_fix": recommendation,
        },
        "error": error,
    }
