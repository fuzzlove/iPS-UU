from __future__ import annotations

from pathlib import Path

from ips_uu.services import palera1n_service as pal


def test_palera1n_static_compatibility_for_a11_ios15_plus() -> None:
    result = pal.compatibility_for_device({"product_type": "iPhone10,3", "product_version": "16.7.8"})
    assert result["status"] == "compatible_static_guidance"
    assert result["supported_family_a11_or_earlier"] is True
    assert result["ios_15_or_later"] is True
    assert result["a11_passcode_sep_caveat"] is True


def test_palera1n_manual_plan_never_contains_command() -> None:
    device = {"product_type": "iPhone10,3", "product_version": "16.7.8"}
    plan = pal.build_manual_plan(device)
    assert plan["execute_supported_by_ips_uu"] is False
    assert plan["command"] == []
    assert "No palera1n command" in plan["command_preview"]
    assert any("USB-C to Lightning" in warning for warning in plan["warnings"])


def test_palera1n_preflight_requires_external_acknowledgement(tmp_path: Path, monkeypatch) -> None:
    tool = tmp_path / "palera1n"
    tool.write_text("#!/bin/sh\nprintf 'palera1n test\\n'\n", encoding="utf-8")
    tool.chmod(0o755)
    monkeypatch.setattr("ips_uu.services.external_tools_service.TOOLS_ROOT", tmp_path)
    device = {"product_type": "iPhone10,3", "product_version": "16.7.8", "current_mode": "normal"}
    preflight = pal.check_requirements(device, caveat_ack=False, external_ack=False)
    failed = {item["label"] for item in preflight["checks"] if not item["passed"]}
    assert "A11/passcode/SEP caveats acknowledged" in failed
    assert "external execution requirement acknowledged" in failed


def test_rootless_button_backend_runs_version_only(tmp_path: Path, monkeypatch) -> None:
    tool = tmp_path / "palera1n"
    tool.write_text("#!/bin/sh\nprintf 'palera1n version test\\n'\n", encoding="utf-8")
    tool.chmod(0o755)
    monkeypatch.setattr("ips_uu.services.external_tools_service.TOOLS_ROOT", tmp_path)
    result = pal.run_rootless_version_check()
    assert result["command"] == [str(tool), "--version"]
    assert result["safety"]["metadata_only"] is True
    assert result["safety"]["jailbreak_action"] is False
    assert "version test" in result["stdout"]
