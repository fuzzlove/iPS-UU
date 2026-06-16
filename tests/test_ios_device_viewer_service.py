from __future__ import annotations

import subprocess

from ips_uu.services import ios_device_viewer_service as viewer


class FakeDetector:
    def __init__(self, devices=None, error=None):
        self.devices = devices or []
        self.error = error

    def list_udids(self):
        payload = {"devices": self.devices, "tool": "fake-idevice_id"}
        if self.error:
            payload["error"] = self.error
        return payload


class FakeInfo:
    def __init__(self, records):
        self.records = records

    def device_info(self, udid):
        return self.records.get(udid, {})


class FakePairing:
    def __init__(self, records):
        self.records = records

    def pairing_status(self, udid):
        return self.records.get(udid, {"status": "Unknown"})


def snapshot(detector, info=None, pairing=None):
    controller = viewer.DeviceViewerController(
        detector=detector,
        info_provider=info or FakeInfo({}),
        pairing_provider=pairing or FakePairing({}),
        screen_provider=viewer.PlaceholderScreenProvider(),
    )
    return controller.snapshot()


def test_no_device_connected() -> None:
    payload = snapshot(FakeDetector([]))
    assert payload["connection_status"] == "No device connected"
    assert payload["devices"] == []
    assert "Connect an iPhone or iPad" in " ".join(payload["guidance"])


def test_device_connected_and_paired() -> None:
    payload = snapshot(
        FakeDetector(["00008110abcdef"]),
        FakeInfo(
            {
                "00008110abcdef": {
                    "device_name": "Test iPhone",
                    "product_type": "iPhone10,3",
                    "product_version": "16.7.8",
                    "build_version": "20H343",
                    "serial_number": "C39TEST",
                    "logic_number": "MLBTEST",
                    "logic_board": "D221AP",
                    "ecid": "123456",
                    "model_id": "MQA52",
                    "imei": "359000000000000",
                    "wifi_address": "00:11:22:33:44:55",
                    "bluetooth_address": "66:77:88:99:AA:BB",
                    "disk_capacity_bytes": 128000000000,
                    "disk_free_bytes": 64000000000,
                }
            }
        ),
        FakePairing({"00008110abcdef": {"status": "Paired"}}),
    )
    device = payload["devices"][0]
    assert device["device_name"] == "Test iPhone"
    assert device["model_name"] == "iPhone X"
    assert device["serial_number"] == "C39TEST"
    assert device["logic_number"] == "MLBTEST"
    assert device["logic_board"] == "D221AP"
    assert device["ecid"] == "123456"
    assert device["model_id"] == "MQA52"
    assert device["firmware_version"] == "16.7.8"
    assert device["imei"] == "359000000000000"
    assert device["wifi_address"] == "00:11:22:33:44:55"
    assert device["bluetooth_address"] == "66:77:88:99:AA:BB"
    assert device["disk_capacity_bytes"] == 128000000000
    assert device["disk_free_bytes"] == 64000000000
    assert "Connected" in device["badges"]
    assert "Paired" in device["badges"]
    assert device["masked_udid"].endswith("abcdef")


def test_device_connected_but_locked() -> None:
    payload = snapshot(
        FakeDetector(["locked"]),
        FakeInfo({"locked": {"error": "LOCKDOWN_E_PASSWORD_PROTECTED", "lock_status": "Locked", "badges": ["Locked"]}}),
        FakePairing({"locked": {"status": "Needs Trust", "error": "validation unavailable"}}),
    )
    device = payload["devices"][0]
    assert "Locked" in device["badges"]
    assert "Needs Trust" in device["badges"]
    assert "Unlock the device and tap Trust This Computer." in " ".join(payload["guidance"])


def test_device_connected_but_untrusted() -> None:
    payload = snapshot(
        FakeDetector(["untrusted"]),
        FakeInfo({"untrusted": {"error": "Invalid HostID", "pairing_status": "Needs Trust", "badges": ["Needs Trust"]}}),
        FakePairing({"untrusted": {"status": "Needs Trust", "error": "pairing validation failed"}}),
    )
    device = payload["devices"][0]
    assert device["pairing_status"] == "Needs Trust"
    assert "Needs Trust" in device["badges"]


def test_multiple_devices() -> None:
    payload = snapshot(
        FakeDetector(["one", "two"]),
        FakeInfo({"one": {"device_name": "One", "product_type": "iPhone9,1"}, "two": {"device_name": "Two", "product_type": "iPad7,4"}}),
        FakePairing({"one": {"status": "Paired"}, "two": {"status": "Paired"}}),
    )
    assert len(payload["devices"]) == 2
    assert [item["device_name"] for item in payload["devices"]] == ["One", "Two"]


def test_missing_libimobiledevice_tools(monkeypatch) -> None:
    monkeypatch.setattr(viewer, "resolve_tool", lambda _name: None)
    detected = viewer.LibimobiledeviceDetector().list_udids()
    assert detected["devices"] == []
    assert "idevice_id was not found" in detected["error"]


def test_command_timeout(monkeypatch) -> None:
    def raise_timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["fake"], timeout=1)

    monkeypatch.setattr(viewer.subprocess, "run", raise_timeout)
    result = viewer.run_command(["fake"], timeout=1)
    assert result.timed_out is True
    assert result.returncode == 124


def test_perform_device_action_restart_uses_public_tool(monkeypatch) -> None:
    monkeypatch.setattr(viewer, "resolve_tool", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(viewer, "run_command", lambda args, timeout=viewer.DEFAULT_TIMEOUT: viewer.CommandResult(args=args, returncode=0, stdout="", stderr=""))
    result = viewer.perform_device_action("restart", "00008110abcdef")
    assert result["succeeded"] is True
    assert result["command"] == ["/usr/bin/idevicediagnostics", "-u", "00008110abcdef", "restart"]
    assert result["safety"]["restore_or_jailbreak"] is False
