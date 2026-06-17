"""Clean-room connected iOS device viewer.

The viewer uses documented/open-source libimobiledevice command-line tools when
available. It does not use 3uTools code or resources and does not invoke
jailbreak, exploit, restore, or device-modifying workflows.
"""

from __future__ import annotations

import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


REPO_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
TOOLS_ROOT = REPO_ROOT / "tools"
DEFAULT_TIMEOUT = 8
LIBIMOBILEDEVICE_TOOL_NAMES = (
    "idevice_id",
    "ideviceinfo",
    "idevicepair",
    "idevicediagnostics",
    "ideviceenterrecovery",
    "ideviceinstaller",
    "idevicescreenshot",
    "irecovery",
)
VERBOSE_INFO_DOMAINS = (
    None,
    "com.apple.disk_usage",
    "com.apple.mobile.battery",
    "com.apple.mobile.iTunes",
    "com.apple.mobile.lockdown_cache",
)


class DeviceViewerError(RuntimeError):
    pass


@dataclass
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass
class DeviceRecord:
    udid: str
    device_name: str | None = None
    model_name: str | None = None
    serial_number: str | None = None
    logic_number: str | None = None
    logic_board: str | None = None
    ecid: str | None = None
    model_id: str | None = None
    imei: str | None = None
    wifi_address: str | None = None
    bluetooth_address: str | None = None
    disk_capacity_bytes: int | None = None
    disk_free_bytes: int | None = None
    product_type: str | None = None
    product_version: str | None = None
    build_version: str | None = None
    firmware_version: str | None = None
    hardware_model: str | None = None
    board_id: str | None = None
    chip_id: str | None = None
    die_id: str | None = None
    unique_device_id: str | None = None
    baseband_version: str | None = None
    baseband_serial_number: str | None = None
    activation_state: str | None = None
    device_class: str | None = None
    cpu_architecture: str | None = None
    region_info: str | None = None
    color: str | None = None
    enclosure_color: str | None = None
    battery_current_capacity: int | None = None
    battery_is_charging: bool | None = None
    fingerprint: dict[str, Any] = field(default_factory=dict)
    connection_status: str = "Connected"
    pairing_status: str = "Unknown"
    lock_status: str = "Unknown"
    badges: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class DeviceDetector(Protocol):
    def list_udids(self) -> dict[str, Any]:
        ...


class DeviceInfoProvider(Protocol):
    def device_info(self, udid: str) -> dict[str, Any]:
        ...


class PairingStatusProvider(Protocol):
    def pairing_status(self, udid: str) -> dict[str, Any]:
        ...


class DeviceScreenProvider(Protocol):
    def screen_status(self, udid: str | None = None) -> dict[str, Any]:
        ...


def mask_udid(udid: str | None) -> str | None:
    if not udid:
        return None
    value = str(udid)
    return f"{'*' * max(len(value) - 6, 0)}{value[-6:]}"


def resolve_tool(name: str) -> str | None:
    local = TOOLS_ROOT / name
    if local.exists() and local.is_file() and os.access(local, os.X_OK):
        return str(local)
    nested = TOOLS_ROOT / "libimobiledevice" / name
    if nested.exists() and nested.is_file() and os.access(nested, os.X_OK):
        return str(nested)
    return shutil.which(name)


def run_command(args: list[str], timeout: int = DEFAULT_TIMEOUT) -> CommandResult:
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        return CommandResult(args=args, returncode=124, stdout=exc.stdout or "", stderr=exc.stderr or "command timed out", timed_out=True)
    except FileNotFoundError as exc:
        return CommandResult(args=args, returncode=127, stdout="", stderr=str(exc))
    return CommandResult(args=args, returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)


def host_usb_inventory(timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Return a lightweight macOS USB inventory focused on Apple mobile devices."""
    if platform.system() != "Darwin":
        return {"available": False, "reason": "USB host inventory is currently implemented for macOS system_profiler."}
    result = run_command(["system_profiler", "SPUSBDataType"], timeout=timeout)
    text = "\n".join(part for part in (result.stdout, result.stderr) if part)
    apple_markers = ("iPhone", "iPad", "iPod", "Apple Mobile Device", "Vendor ID: 0x05ac")
    present = any(marker.lower() in text.lower() for marker in apple_markers)
    matched_lines = [
        line.strip()
        for line in text.splitlines()
        if any(marker.lower() in line.lower() for marker in apple_markers)
    ]
    return {
        "available": result.returncode == 0 and not result.timed_out,
        "apple_mobile_device_present": present,
        "matched_lines": matched_lines,
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "error": (result.stderr or "").strip() if result.returncode != 0 or result.timed_out else None,
    }


def trust_diagnosis(detected: dict[str, Any], devices: list["DeviceRecord"], usb_inventory: dict[str, Any]) -> dict[str, Any]:
    if devices:
        untrusted = any("Needs Trust" in record.badges or record.pairing_status == "Needs Trust" for record in devices)
        locked = any("Locked" in record.badges or record.lock_status == "Locked" for record in devices)
        if untrusted:
            return {
                "status": "connected_not_trusted",
                "summary": "The device is visible over USB but has not trusted this computer.",
                "next_steps": ["Unlock the device.", "Tap Trust This Computer.", "Run Refresh Device again."],
            }
        if locked:
            return {
                "status": "connected_locked",
                "summary": "The device is visible but locked metadata is limited.",
                "next_steps": ["Unlock the device.", "Keep it on the Home Screen.", "Run Refresh Device again."],
            }
        return {
            "status": "trusted_or_metadata_available",
            "summary": "The device is visible and metadata is available.",
            "next_steps": ["Continue with restore option checks or backend diagnostics."],
        }
    if usb_inventory.get("available") and not usb_inventory.get("apple_mobile_device_present"):
        return {
            "status": "not_physically_detected",
            "summary": "macOS does not currently show an Apple mobile USB device. This is not a trust-prompt failure yet.",
            "next_steps": [
                "Use a data-capable cable, not a charge-only cable.",
                "Connect directly or try a different USB port/hub.",
                "Wake and unlock the device.",
                "If in DFU, the screen should be black; use recovery/DFU detection after reconnecting.",
            ],
        }
    if detected.get("error"):
        error = str(detected.get("error"))
        if "usbmuxd" in error.lower():
            return {
                "status": "usbmux_access_error",
                "summary": "The tool could not talk to usbmuxd in this process environment.",
                "next_steps": ["Run outside sandbox/restricted terminal if applicable.", "Restart the app.", "Reconnect the device."],
            }
    return {
        "status": "no_device_found",
        "summary": "No device was returned by libimobiledevice.",
        "next_steps": ["Reconnect the device.", "Unlock and trust when prompted.", "Refresh Device."],
    }


def tool_version(path: str | None) -> dict[str, Any]:
    if not path:
        return {"detected": False, "value": None}
    for flag in ("--version", "-v"):
        result = run_command([path, flag], timeout=3)
        text = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        if text:
            return {"detected": True, "value": text.splitlines()[0].strip(), "flag": flag}
    return {"detected": False, "value": None}


def perform_device_action(action: str, udid: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Run a documented/open-source device state action.

    Supported actions intentionally exclude restore, jailbreak, exploit, or
    private MobileDevice behavior.
    """
    if action not in {"restart", "shutdown", "enter_recovery", "exit_recovery"}:
        raise DeviceViewerError(f"unsupported device action: {action}")
    if action in {"restart", "shutdown"}:
        binary = resolve_tool("idevicediagnostics")
        if not binary:
            raise DeviceViewerError("idevicediagnostics was not found in tools/ or PATH.")
        command = [binary]
        if udid:
            command.extend(["-u", udid])
        command.append(action)
    elif action == "enter_recovery":
        binary = resolve_tool("ideviceenterrecovery")
        if not binary:
            raise DeviceViewerError("ideviceenterrecovery was not found in tools/ or PATH.")
        if not udid:
            raise DeviceViewerError("enter recovery requires a selected normal-mode device UDID.")
        command = [binary, udid]
    else:
        binary = resolve_tool("irecovery")
        if not binary:
            raise DeviceViewerError("irecovery was not found in tools/ or PATH.")
        command = [binary, "-n"]
    result = run_command(command, timeout=timeout)
    return {
        "action": action,
        "udid": mask_udid(udid),
        "command": command,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "timed_out": result.timed_out,
        "succeeded": result.returncode == 0 and not result.timed_out,
        "safety": {
            "uses_private_api": False,
            "restore_or_jailbreak": False,
            "requires_user_confirmation_in_gui": True,
        },
    }


def classify_error(text: str) -> tuple[str, str, list[str]]:
    lower = text.lower()
    if "password" in lower or "locked" in lower:
        return "Locked", "Unknown", ["Locked", "Needs Trust"]
    if "trust" in lower or "pair" in lower or "hostid" in lower or "invalid host" in lower:
        return "Unknown", "Needs Trust", ["Needs Trust"]
    if "timed out" in lower:
        return "Unknown", "Error", ["Error"]
    return "Unknown", "Error", ["Error"]


def architecture_for_product(product_type: str | None) -> str:
    if not product_type:
        return "Unknown"
    family = product_type.split(",", 1)[0]
    number = int("".join(ch for ch in family if ch.isdigit()) or "0")
    if family.startswith("iPhone"):
        if number <= 10:
            return "A7-A11 family"
        if number <= 13:
            return "A12-A15 family"
        return "A16 or newer family"
    if family.startswith("iPad"):
        if number <= 7:
            return "A7-A10X-era iPad family"
        return "Modern iPad family"
    return "Unknown"


MODEL_NAMES = {
    "iPhone8,1": "iPhone 6s",
    "iPhone8,2": "iPhone 6s Plus",
    "iPhone8,4": "iPhone SE (1st generation)",
    "iPhone9,1": "iPhone 7",
    "iPhone9,2": "iPhone 7 Plus",
    "iPhone9,3": "iPhone 7",
    "iPhone9,4": "iPhone 7 Plus",
    "iPhone10,1": "iPhone 8",
    "iPhone10,2": "iPhone 8 Plus",
    "iPhone10,3": "iPhone X",
    "iPhone10,4": "iPhone 8",
    "iPhone10,5": "iPhone 8 Plus",
    "iPhone10,6": "iPhone X",
    "iPhone11,2": "iPhone XS",
    "iPhone11,4": "iPhone XS Max",
    "iPhone11,6": "iPhone XS Max",
    "iPhone11,8": "iPhone XR",
    "iPhone12,1": "iPhone 11",
    "iPhone12,3": "iPhone 11 Pro",
    "iPhone12,5": "iPhone 11 Pro Max",
    "iPhone12,8": "iPhone SE (2nd generation)",
    "iPhone13,1": "iPhone 12 mini",
    "iPhone13,2": "iPhone 12",
    "iPhone13,3": "iPhone 12 Pro",
    "iPhone13,4": "iPhone 12 Pro Max",
    "iPhone14,2": "iPhone 13 Pro",
    "iPhone14,3": "iPhone 13 Pro Max",
    "iPhone14,4": "iPhone 13 mini",
    "iPhone14,5": "iPhone 13",
    "iPhone14,6": "iPhone SE (3rd generation)",
    "iPhone14,7": "iPhone 14",
    "iPhone14,8": "iPhone 14 Plus",
    "iPhone15,2": "iPhone 14 Pro",
    "iPhone15,3": "iPhone 14 Pro Max",
    "iPhone15,4": "iPhone 15",
    "iPhone15,5": "iPhone 15 Plus",
    "iPhone16,1": "iPhone 15 Pro",
    "iPhone16,2": "iPhone 15 Pro Max",
}


def model_name_for_product(product_type: str | None, marketing_name: str | None = None) -> str | None:
    if marketing_name:
        return str(marketing_name)
    if not product_type:
        return None
    return MODEL_NAMES.get(product_type, product_type)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class LibimobiledeviceDetector:
    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout

    def list_udids(self) -> dict[str, Any]:
        binary = resolve_tool("idevice_id")
        if not binary:
            return {"devices": [], "error": "idevice_id was not found in tools/ or PATH.", "tool": None}
        result = run_command([binary, "-l"], timeout=self.timeout)
        if result.timed_out:
            return {"devices": [], "error": "idevice_id timed out.", "tool": binary, "command": result.args}
        if result.returncode != 0:
            return {"devices": [], "error": (result.stderr or result.stdout).strip() or "idevice_id failed.", "tool": binary, "command": result.args}
        devices = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return {"devices": devices, "tool": binary, "command": result.args}


class LibimobiledeviceInfoProvider:
    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout

    def device_info(self, udid: str) -> dict[str, Any]:
        binary = resolve_tool("ideviceinfo")
        if not binary:
            return {"error": "ideviceinfo was not found in tools/ or PATH.", "tool": None}
        result = run_command([binary, "-u", udid, "-x"], timeout=self.timeout)
        if result.timed_out:
            return {"error": "ideviceinfo timed out.", "tool": binary, "command": result.args}
        if result.returncode != 0:
            lock_status, pairing_status, badges = classify_error((result.stderr or result.stdout).strip())
            return {
                "error": (result.stderr or result.stdout).strip() or "ideviceinfo failed.",
                "tool": binary,
                "command": result.args,
                "lock_status": lock_status,
                "pairing_status": pairing_status,
                "badges": badges,
            }
        try:
            info = plistlib.loads(result.stdout.encode())
        except Exception as exc:
            return {"error": f"could not parse ideviceinfo plist output: {exc}", "tool": binary, "command": result.args}
        domains: dict[str, Any] = {"lockdown": info}
        domain_commands = [result.args]
        for domain in VERBOSE_INFO_DOMAINS:
            if domain is None:
                continue
            domain_result = run_command([binary, "-u", udid, "-q", domain, "-x"], timeout=self.timeout)
            domain_commands.append(domain_result.args)
            if domain_result.returncode != 0 or domain_result.timed_out or not domain_result.stdout.strip():
                domains[domain] = {
                    "error": (domain_result.stderr or domain_result.stdout).strip() or "domain query failed",
                    "returncode": domain_result.returncode,
                }
                continue
            try:
                value = plistlib.loads(domain_result.stdout.encode())
            except Exception as exc:
                value = {"error": f"could not parse domain plist output: {exc}"}
            domains[domain] = value
        disk = domains.get("com.apple.disk_usage") if isinstance(domains.get("com.apple.disk_usage"), dict) else {}
        battery = domains.get("com.apple.mobile.battery") if isinstance(domains.get("com.apple.mobile.battery"), dict) else {}
        itunes = domains.get("com.apple.mobile.iTunes") if isinstance(domains.get("com.apple.mobile.iTunes"), dict) else {}
        fingerprint = device_fingerprint(info, domains)
        return {
            "tool": binary,
            "command": result.args,
            "commands": domain_commands,
            "device_name": info.get("DeviceName"),
            "model_name": model_name_for_product(info.get("ProductType"), info.get("MarketingName") or info.get("ModelName")),
            "serial_number": info.get("SerialNumber"),
            "logic_number": info.get("MLBSerialNumber") or info.get("LogicBoardSerialNumber"),
            "logic_board": info.get("HardwareModel") or info.get("BoardId") or info.get("BoardID"),
            "ecid": info.get("UniqueChipID"),
            "model_id": info.get("ModelNumber") or info.get("RegionInfo"),
            "imei": info.get("InternationalMobileEquipmentIdentity") or info.get("IMEI"),
            "wifi_address": info.get("WiFiAddress"),
            "bluetooth_address": info.get("BluetoothAddress"),
            "disk_capacity_bytes": _int_or_none(info.get("TotalDiskCapacity") or info.get("TotalDataCapacity") or disk.get("TotalDiskCapacity") or disk.get("TotalDataCapacity")),
            "disk_free_bytes": _int_or_none(info.get("TotalSystemAvailable") or info.get("TotalDataAvailable") or info.get("AmountDataAvailable") or disk.get("TotalSystemAvailable") or disk.get("TotalDataAvailable")),
            "product_type": info.get("ProductType"),
            "product_version": info.get("ProductVersion"),
            "build_version": info.get("BuildVersion"),
            "firmware_version": info.get("ProductVersion"),
            "hardware_model": info.get("HardwareModel"),
            "board_id": str(info.get("BoardId") or info.get("BoardID") or "") or None,
            "chip_id": str(info.get("ChipID") or "") or None,
            "die_id": str(info.get("DieID") or "") or None,
            "unique_device_id": info.get("UniqueDeviceID"),
            "baseband_version": info.get("BasebandVersion"),
            "baseband_serial_number": info.get("BasebandSerialNumber"),
            "activation_state": info.get("ActivationState"),
            "device_class": info.get("DeviceClass"),
            "cpu_architecture": info.get("CPUArchitecture") or itunes.get("CPUArchitecture"),
            "region_info": info.get("RegionInfo"),
            "color": info.get("DeviceColor") or info.get("Color"),
            "enclosure_color": info.get("DeviceEnclosureColor") or info.get("EnclosureColor"),
            "battery_current_capacity": _int_or_none(battery.get("BatteryCurrentCapacity") or battery.get("CurrentCapacity")),
            "battery_is_charging": battery.get("BatteryIsCharging") if isinstance(battery.get("BatteryIsCharging"), bool) else None,
            "fingerprint": fingerprint,
            "raw": {
                key: info.get(key)
                for key in (
                    "DeviceName",
                    "MarketingName",
                    "ModelName",
                    "ModelNumber",
                    "RegionInfo",
                    "SerialNumber",
                    "MLBSerialNumber",
                    "HardwareModel",
                    "BoardId",
                    "BoardID",
                    "ProductType",
                    "ProductVersion",
                    "BuildVersion",
                    "UniqueChipID",
                    "UniqueDeviceID",
                    "ChipID",
                    "DieID",
                    "BasebandVersion",
                    "BasebandSerialNumber",
                    "ActivationState",
                    "DeviceClass",
                    "CPUArchitecture",
                    "DeviceColor",
                    "DeviceEnclosureColor",
                    "InternationalMobileEquipmentIdentity",
                    "IMEI",
                    "WiFiAddress",
                    "BluetoothAddress",
                    "TotalDiskCapacity",
                    "TotalDataCapacity",
                    "TotalSystemAvailable",
                    "TotalDataAvailable",
                    "AmountDataAvailable",
                )
            },
            "raw_domains": domains,
        }


class LibimobiledevicePairingStatusProvider:
    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout

    def pairing_status(self, udid: str) -> dict[str, Any]:
        binary = resolve_tool("idevicepair")
        if not binary:
            return {"status": "Unknown", "error": "idevicepair was not found in tools/ or PATH.", "tool": None}
        result = run_command([binary, "-u", udid, "validate"], timeout=self.timeout)
        text = (result.stdout or result.stderr).strip()
        if result.timed_out:
            return {"status": "Error", "error": "idevicepair validate timed out.", "tool": binary, "command": result.args}
        if result.returncode == 0:
            return {"status": "Paired", "tool": binary, "command": result.args, "raw": text}
        _lock, pairing, badges = classify_error(text)
        return {"status": pairing if pairing != "Unknown" else "Needs Trust", "error": text or "pairing validation failed.", "badges": badges, "tool": binary, "command": result.args}


class LibimobiledeviceScreenProvider:
    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout

    def screen_status(self, udid: str | None = None) -> dict[str, Any]:
        binary = resolve_tool("idevicescreenshot")
        if not binary:
            return {
                "available": False,
                "udid": mask_udid(udid),
                "message": "idevicescreenshot was not found in tools/ or PATH.",
                "policy": "No private Apple APIs, exploit paths, jailbreak-only methods, or copied third-party behavior are used.",
            }
        if not udid:
            return {
                "available": False,
                "udid": None,
                "tool": binary,
                "message": "Connect and trust a normal-mode device to capture a screen preview.",
                "policy": "Uses idevicescreenshot only.",
            }
        screenshot_dir = Path(tempfile.gettempdir()) / "ips-uu" / "screens"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        target = screenshot_dir / f"{mask_udid(udid) or 'device'}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.png"
        command = [binary, "-u", udid, str(target)]
        result = run_command(command, timeout=self.timeout)
        if result.returncode != 0 or result.timed_out or not target.exists():
            return {
                "available": False,
                "udid": mask_udid(udid),
                "tool": binary,
                "command": command,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "timed_out": result.timed_out,
                "message": (result.stderr or result.stdout).strip() or "Screen capture was unavailable.",
                "policy": "Uses idevicescreenshot only.",
            }
        return {
            "available": True,
            "udid": mask_udid(udid),
            "tool": binary,
            "command": command,
            "path": str(target),
            "message": "Screen preview captured through idevicescreenshot.",
            "policy": "Uses idevicescreenshot only.",
        }


class PlaceholderScreenProvider:
    def screen_status(self, udid: str | None = None) -> dict[str, Any]:
        return LibimobiledeviceScreenProvider().screen_status(udid)


class DeviceViewerController:
    def __init__(
        self,
        detector: DeviceDetector | None = None,
        info_provider: DeviceInfoProvider | None = None,
        pairing_provider: PairingStatusProvider | None = None,
        screen_provider: DeviceScreenProvider | None = None,
    ) -> None:
        self.detector = detector or LibimobiledeviceDetector()
        self.info_provider = info_provider or LibimobiledeviceInfoProvider()
        self.pairing_provider = pairing_provider or LibimobiledevicePairingStatusProvider()
        self.screen_provider = screen_provider or PlaceholderScreenProvider()

    def snapshot(self) -> dict[str, Any]:
        detected = self.detector.list_udids()
        devices: list[DeviceRecord] = []
        last_error = detected.get("error")
        for udid in detected.get("devices", []):
            info = self.info_provider.device_info(udid)
            pairing = self.pairing_provider.pairing_status(udid)
            record = self._record_from_provider_data(udid, info, pairing)
            devices.append(record)
            if record.errors:
                last_error = record.errors[-1]
        screen = self.screen_provider.screen_status(devices[0].udid if devices else None)
        tools = self._tool_diagnostics()
        usb_inventory = host_usb_inventory()
        diagnosis = trust_diagnosis(detected, devices, usb_inventory)
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "os_version": platform.platform(),
            "tools": tools,
            "connection_status": "Connected" if devices else "No device connected",
            "devices": [self._record_to_dict(record) for record in devices],
            "screen": screen,
            "host_usb": usb_inventory,
            "trust_diagnosis": diagnosis,
            "last_error": last_error,
            "guidance": self._guidance(devices, detected),
            "diagnostics": self._diagnostics(devices, tools, last_error),
        }

    def _record_from_provider_data(self, udid: str, info: dict[str, Any], pairing: dict[str, Any]) -> DeviceRecord:
        badges = ["Connected"]
        errors = []
        lock_status = info.get("lock_status") or "Unlocked/Unknown"
        pairing_status = pairing.get("status") or info.get("pairing_status") or "Unknown"
        if pairing_status == "Paired":
            badges.append("Paired")
        if pairing_status == "Needs Trust":
            badges.append("Needs Trust")
        if lock_status == "Locked":
            badges.append("Locked")
        if info.get("error"):
            errors.append(str(info["error"]))
        if pairing.get("error"):
            errors.append(str(pairing["error"]))
        if errors:
            badges.append("Error")
        product_type = info.get("product_type")
        if product_type and architecture_for_product(product_type) == "Unknown":
            badges.append("Unsupported")
        return DeviceRecord(
            udid=udid,
            device_name=info.get("device_name"),
            model_name=info.get("model_name") or model_name_for_product(info.get("product_type")),
            serial_number=info.get("serial_number"),
            logic_number=info.get("logic_number"),
            logic_board=info.get("logic_board"),
            ecid=str(info.get("ecid")) if info.get("ecid") else None,
            model_id=info.get("model_id"),
            imei=info.get("imei"),
            wifi_address=info.get("wifi_address"),
            bluetooth_address=info.get("bluetooth_address"),
            disk_capacity_bytes=info.get("disk_capacity_bytes"),
            disk_free_bytes=info.get("disk_free_bytes"),
            product_type=product_type,
            product_version=info.get("product_version"),
            build_version=info.get("build_version"),
            firmware_version=info.get("firmware_version") or info.get("product_version"),
            hardware_model=info.get("hardware_model"),
            board_id=info.get("board_id"),
            chip_id=info.get("chip_id"),
            die_id=info.get("die_id"),
            unique_device_id=info.get("unique_device_id"),
            baseband_version=info.get("baseband_version"),
            baseband_serial_number=info.get("baseband_serial_number"),
            activation_state=info.get("activation_state"),
            device_class=info.get("device_class"),
            cpu_architecture=info.get("cpu_architecture"),
            region_info=info.get("region_info"),
            color=info.get("color"),
            enclosure_color=info.get("enclosure_color"),
            battery_current_capacity=info.get("battery_current_capacity"),
            battery_is_charging=info.get("battery_is_charging"),
            fingerprint=info.get("fingerprint") or {},
            pairing_status=pairing_status,
            lock_status=lock_status,
            badges=list(dict.fromkeys(badges)),
            errors=errors,
            raw={"info": info.get("raw") or {}, "pairing": pairing.get("raw")},
        )

    def _record_to_dict(self, record: DeviceRecord) -> dict[str, Any]:
        return {
            "udid": record.udid,
            "masked_udid": mask_udid(record.udid),
            "device_name": record.device_name,
            "model_name": record.model_name,
            "serial_number": record.serial_number,
            "logic_number": record.logic_number,
            "logic_board": record.logic_board,
            "ecid": record.ecid,
            "model_id": record.model_id,
            "imei": record.imei,
            "wifi_address": record.wifi_address,
            "bluetooth_address": record.bluetooth_address,
            "disk_capacity_bytes": record.disk_capacity_bytes,
            "disk_free_bytes": record.disk_free_bytes,
            "product_type": record.product_type,
            "architecture": architecture_for_product(record.product_type),
            "product_version": record.product_version,
            "build_version": record.build_version,
            "firmware_version": record.firmware_version,
            "hardware_model": record.hardware_model,
            "board_id": record.board_id,
            "chip_id": record.chip_id,
            "die_id": record.die_id,
            "unique_device_id": record.unique_device_id,
            "baseband_version": record.baseband_version,
            "baseband_serial_number": record.baseband_serial_number,
            "activation_state": record.activation_state,
            "device_class": record.device_class,
            "cpu_architecture": record.cpu_architecture,
            "region_info": record.region_info,
            "color": record.color,
            "enclosure_color": record.enclosure_color,
            "battery_current_capacity": record.battery_current_capacity,
            "battery_is_charging": record.battery_is_charging,
            "fingerprint": record.fingerprint,
            "connection_status": record.connection_status,
            "pairing_status": record.pairing_status,
            "lock_status": record.lock_status,
            "badges": record.badges,
            "errors": record.errors,
            "raw": record.raw,
        }

    def _tool_diagnostics(self) -> dict[str, Any]:
        tools = {}
        for name in LIBIMOBILEDEVICE_TOOL_NAMES:
            path = resolve_tool(name)
            tools[name] = {"path": path, "present": path is not None, "version": tool_version(path)}
        return tools

    def _guidance(self, devices: list[DeviceRecord], detected: dict[str, Any]) -> list[str]:
        if detected.get("error"):
            return [str(detected["error"]), "Install libimobiledevice tools or place supported binaries in tools/."]
        if not devices:
            return ["Connect an iPhone or iPad over USB.", "Unlock the device and tap Trust This Computer if prompted."]
        guidance = []
        for record in devices:
            if "Needs Trust" in record.badges or "Locked" in record.badges:
                guidance.append("Unlock the device and tap Trust This Computer.")
            elif "Paired" in record.badges:
                guidance.append(f"{record.device_name or record.udid} is connected and paired.")
            elif record.errors:
                guidance.append(record.errors[-1])
        return guidance or ["Device metadata is available."]

    def _diagnostics(self, devices: list[DeviceRecord], tools: dict[str, Any], last_error: str | None) -> dict[str, Any]:
        return {
            "os_version": platform.platform(),
            "detected_tool_paths": {name: item.get("path") for name, item in tools.items()},
            "tool_versions": {name: item.get("version") for name, item in tools.items()},
            "masked_udids": [mask_udid(record.udid) for record in devices],
            "last_pairing_or_status_error": last_error,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


def device_fingerprint(info: dict[str, Any], domains: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a verbose hardware/software fingerprint from user-authorized metadata."""
    domains = domains or {}
    disk = domains.get("com.apple.disk_usage") if isinstance(domains.get("com.apple.disk_usage"), dict) else {}
    battery = domains.get("com.apple.mobile.battery") if isinstance(domains.get("com.apple.mobile.battery"), dict) else {}
    return {
        "identity": {
            "product_type": info.get("ProductType"),
            "model_number": info.get("ModelNumber"),
            "region_info": info.get("RegionInfo"),
            "serial_number": info.get("SerialNumber"),
            "udid": info.get("UniqueDeviceID"),
            "ecid": str(info.get("UniqueChipID")) if info.get("UniqueChipID") else None,
        },
        "hardware": {
            "hardware_model": info.get("HardwareModel"),
            "board_id": info.get("BoardId") or info.get("BoardID"),
            "chip_id": info.get("ChipID"),
            "die_id": info.get("DieID"),
            "device_class": info.get("DeviceClass"),
            "cpu_architecture": info.get("CPUArchitecture"),
            "device_color": info.get("DeviceColor") or info.get("Color"),
            "enclosure_color": info.get("DeviceEnclosureColor") or info.get("EnclosureColor"),
        },
        "firmware": {
            "product_version": info.get("ProductVersion"),
            "build_version": info.get("BuildVersion"),
            "activation_state": info.get("ActivationState"),
            "baseband_version": info.get("BasebandVersion"),
            "baseband_serial_number": info.get("BasebandSerialNumber"),
        },
        "radios": {
            "imei": info.get("InternationalMobileEquipmentIdentity") or info.get("IMEI"),
            "meid": info.get("MobileEquipmentIdentifier") or info.get("MEID"),
            "wifi_address": info.get("WiFiAddress"),
            "bluetooth_address": info.get("BluetoothAddress"),
        },
        "storage": {
            "total_bytes": _int_or_none(info.get("TotalDiskCapacity") or info.get("TotalDataCapacity") or disk.get("TotalDiskCapacity") or disk.get("TotalDataCapacity")),
            "free_bytes": _int_or_none(info.get("TotalSystemAvailable") or info.get("TotalDataAvailable") or info.get("AmountDataAvailable") or disk.get("TotalDataAvailable")),
        },
        "battery": {
            "current_capacity": _int_or_none(battery.get("BatteryCurrentCapacity") or battery.get("CurrentCapacity")),
            "is_charging": battery.get("BatteryIsCharging") if isinstance(battery.get("BatteryIsCharging"), bool) else None,
        },
        "metadata_domains": sorted(domains.keys()),
    }


def load_device_viewer_snapshot() -> dict[str, Any]:
    return DeviceViewerController().snapshot()
