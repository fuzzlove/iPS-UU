from ips_uu.services import device_service as ds


def test_detect_target_uses_system_profiler_usb_fallback(monkeypatch):
    monkeypatch.setattr(
        ds,
        "_tool_inventory",
        lambda: {
            "idevice_id": {"found": False, "path": None},
            "ideviceinfo": {"found": False, "path": None},
            "irecovery": {"found": False, "path": None},
            "pymobiledevice3": {"found": False, "path": None},
            "system_profiler": {"found": True, "path": "system_profiler"},
        },
    )
    monkeypatch.setattr(ds, "_normal_detection", lambda tools, timeout: {"backend": "normal", "commands": []})
    monkeypatch.setattr(ds, "_recovery_dfu_detection", lambda tools, timeout: {"backend": "irecovery", "commands": []})
    monkeypatch.setattr(ds, "_pymobiledevice3_detection", lambda tools, timeout: {"backend": "pymobiledevice3", "commands": [], "missing": True})
    monkeypatch.setattr(
        ds,
        "_usb_entries",
        lambda timeout: {
            "available": True,
            "apple_entries": [{"name": "Apple Mobile Device", "vendor_id": "0x05ac", "product_id": "0x1227"}],
            "raw": "Apple Mobile Device",
        },
    )

    result = ds.detect_target()

    assert result["current_mode"] == "dfu"
    assert result["detection_method"] == "system_profiler"
    assert result["diagnostics"]["recommended_fix"]["issue"] == "missing libimobiledevice"


def test_device_map_unknown_is_explicit():
    assert ds.device_map_entry("iPhone999,1") == {}
