from __future__ import annotations

import plistlib
import zipfile
from pathlib import Path

import pytest

from ips_uu.services import turdus_merula_service as tm


def make_ipsw(path: Path, product_type: str = "iPhone9,1", version: str = "10.3.3") -> Path:
    manifest = {
        "ProductVersion": version,
        "ProductBuildVersion": "14G60",
        "SupportedProductTypes": [product_type],
        "BuildIdentities": [
            {
                "Info": {
                    "Variant": "Customer Erase Install (IPSW)",
                    "BuildNumber": "14G60",
                    "ProductVersion": version,
                },
                "Manifest": {},
            }
        ],
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("BuildManifest.plist", plistlib.dumps(manifest))
        archive.writestr("Restore.plist", plistlib.dumps({"Restore": True}))
    return path


def test_chip_class_detection() -> None:
    assert tm.chip_class_for_product("iPhone9,1") == "A10"
    assert tm.chip_class_for_product("iPad7,4") == "A10X"
    assert tm.chip_class_for_product("iPhone8,1") == "A9/A9X"
    assert tm.chip_class_for_product("iPhone15,2") == "unsupported"


def test_find_toolchain_with_flat_tools_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tm.TOOL_NAMES:
        tool = tmp_path / name
        tool.write_text("#!/bin/sh\nprintf '%s version test\\n' \"$0\"\n", encoding="utf-8")
        tool.chmod(0o755)
    monkeypatch.setattr(tm, "TOOLS_ROOT", tmp_path)
    found = tm.find_toolchain()
    assert found["found"] is True
    assert found["executable_permissions_ok"] is True
    assert {item["name"] for item in found["tools"]} == set(tm.TOOL_NAMES)


def test_inspect_ipsw_detects_compatibility_and_ios10_activation_warning(tmp_path: Path) -> None:
    ipsw = make_ipsw(tmp_path / "firmware.ipsw", "iPhone9,1", "10.3.3")
    info = tm.inspect_ipsw(str(ipsw), "iPhone9,1")
    assert info["compatible_with_device"] is True
    assert info["activation_baseband_warning"] is True


def test_preflight_fails_without_acknowledgements(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tm.TOOL_NAMES:
        tool = tmp_path / name
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        tool.chmod(0o755)
    monkeypatch.setattr(tm, "TOOLS_ROOT", tmp_path)
    device = {"product_type": "iPhone9,1", "chip_class": "A10", "appears_supported": True, "current_mode": "dfu"}
    ipsw = {"path": "/tmp/example.ipsw", "compatible_with_device": True}
    preflight = tm.check_requirements(device, ipsw, tethered_ack=False, data_loss_ack=False)
    failed = {item["label"] for item in preflight["checks"] if not item["passed"]}
    assert "user understands tethered limitation" in failed
    assert "user understands data loss risk" in failed


def test_inspect_artifacts_validates_existence_only(tmp_path: Path) -> None:
    blob = tmp_path / "ticket.shsh2"
    blob.write_text("example", encoding="utf-8")
    result = tm.inspect_artifacts({"shsh_blob": str(blob), "missing_blob": str(tmp_path / "missing.shsh2"), "empty": None})
    assert result["valid"] is False
    selected = {item["name"]: item for item in result["artifacts"] if item["selected"]}
    assert selected["shsh_blob"]["exists"] is True
    assert selected["missing_blob"]["exists"] is False
    assert "does not parse" in " ".join(result["notes"])


def test_build_tethered_plan_is_manual_prerequisite_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tool = tmp_path / "turdus_merula"
    tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    tool.chmod(0o755)
    monkeypatch.setattr(tm, "TOOLS_ROOT", tmp_path)
    device = {
        "product_type": "iPhone9,1",
        "chip_class": "A10",
        "appears_supported": True,
        "current_mode": "dfu",
        "ecid": "1234",
        "udid": None,
    }
    ipsw = {"path": str(tmp_path / "firmware file.ipsw"), "compatible_with_device": True}
    plan = tm.build_tethered_plan(device, ipsw)
    assert plan["execute_supported_by_ips_uu"] is False
    assert plan["manual_prerequisite"]["required"] is True
    assert plan["manual_prerequisite"]["satisfied"] is True
    assert plan["command"] == []
    assert "No exploit" in plan["command_preview"]
    assert "--tethered" not in plan["command_preview"]


def test_build_guide_workflow_for_a10_uses_ios_guide_commands(tmp_path: Path) -> None:
    ipsw = {"path": str(tmp_path / "target.ipsw")}
    device = {"product_type": "iPhone9,1", "chip_class": "A10"}
    workflow = tm.build_guide_workflow(
        device,
        ipsw,
        {
            "iboot_img4": "/tmp/iBoot.img4",
            "signed_sep_img4": "/tmp/signed-SEP.img4",
            "target_sep_im4p": "/tmp/target-SEP.im4p",
        },
    )
    previews = [step["command_preview"] for step in workflow["steps"]]
    assert workflow["profile"] == "a10x"
    assert any("turdusra1n -D" in preview for preview in previews)
    assert any("turdus_merula -o" in preview and "target.ipsw" in preview for preview in previews)
    assert any("-t /tmp/iBoot.img4 -i /tmp/signed-SEP.img4 -p /tmp/target-SEP.im4p" in preview for preview in previews)
