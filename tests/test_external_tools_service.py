from __future__ import annotations

from pathlib import Path

from ips_uu.services import external_tools_service as ext


def test_palera1n_inventory_detects_metadata_without_action(tmp_path: Path, monkeypatch) -> None:
    tool = tmp_path / "palera1n"
    tool.write_text("#!/bin/sh\nprintf 'palera1n test-version\\n'\n", encoding="utf-8")
    tool.chmod(0o755)
    monkeypatch.setattr(ext, "TOOLS_ROOT", tmp_path)

    result = ext.inspect_palera1n()

    assert result["status"] == "Installed"
    assert result["version_status"] == "Version Detected"
    assert result["metadata"]["permissions"]["executable"] is True
    assert result["metadata"]["sha256"]
    assert result["classification"] == "external_dependency_inventory_only"
    assert "does not execute" in " ".join(result["documentation"]["policy"])


def test_external_tool_scan_reports_safety_and_static_device_guidance(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ext, "TOOLS_ROOT", tmp_path)
    monkeypatch.setattr(
        ext,
        "detect_target",
        lambda _device="auto": {
            "current_mode": "normal",
            "product_type": "iPhone10,3",
            "product_version": "16.7.8",
            "build_version": "20H343",
        },
    )
    monkeypatch.setattr(ext, "resolve_idevicerestore", lambda: None)
    monkeypatch.setattr(ext, "resolve_cfgutil", lambda: None)

    payload = ext.scan_external_tools()

    assert payload["tools"]["palera1n"]["status"] == "Missing"
    assert payload["device"]["architecture"] == "A7-A11 family"
    assert payload["device"]["compatibility_information"]["palera1n_static_compatibility"] == "possibly_supported_by_external_tool"
    assert payload["safety"]["executes_jailbreak_actions"] is False
    assert payload["safety"]["modifies_connected_device"] is False
    assert payload["safety"]["one_click_jailbreak"] is False
