import plistlib
import zipfile
from pathlib import Path

from ips_uu.services import restore_options_service as ro


def make_ipsw(path: Path, product_type: str = "iPhone9,1", version: str = "16.7.10", build: str = "20H350") -> Path:
    manifest = {
        "ProductVersion": version,
        "ProductBuildVersion": build,
        "SupportedProductTypes": [product_type],
        "BuildIdentities": [
            {
                "Info": {"Variant": "Customer Erase Install (IPSW)", "DeviceClass": "d10ap", "BuildNumber": build},
                "Manifest": {},
            }
        ],
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("BuildManifest.plist", plistlib.dumps(manifest))
    return path


def test_restore_options_marks_signed_compatible_ipsw_installable(monkeypatch, tmp_path: Path) -> None:
    ipsw = make_ipsw(tmp_path / "signed.ipsw")
    monkeypatch.setattr(
        ro,
        "signed_firmware_lookup",
        lambda product_type, timeout: {
            "source": "test",
            "product_type": product_type,
            "firmwares": [{"version": "16.7.10", "build": "20H350", "signed": True}],
            "policy": {"metadata_only": True},
        },
    )
    result = ro.analyze_restore_options(
        str(ipsw),
        device={"product_type": "iPhone9,1", "product_version": "16.7.10", "current_mode": "normal", "ecid": "1234"},
    )

    assert result["firmware_check"]["status"] == "Installable"
    assert result["dry_run_command_plan"]["shell"] is False
    assert result["dry_run_command_plan"]["command"]
    assert result["reverse_engineering_findings"]["private_mobiledevice_restore_supported"] is False
    assert result["reverse_engineering_findings"]["offline_unsigned_restore_supported"] is False
    assert Path(result["session_dir"]).exists()


def test_restore_options_marks_unsigned_ipsw_not_installable(monkeypatch, tmp_path: Path) -> None:
    ipsw = make_ipsw(tmp_path / "unsigned.ipsw")
    monkeypatch.setattr(
        ro,
        "signed_firmware_lookup",
        lambda product_type, timeout: {
            "source": "test",
            "product_type": product_type,
            "firmwares": [{"version": "16.7.10", "build": "20H350", "signed": False}],
            "policy": {"metadata_only": True},
        },
    )
    result = ro.analyze_restore_options(
        str(ipsw),
        device={"product_type": "iPhone9,1", "product_version": "15.0", "current_mode": "recovery"},
    )

    assert result["firmware_check"]["status"] in {"Tethered only", "Not installable"}
    assert result["dry_run_command_plan"]["command"] == []
    assert "Unsigned firmware is normally refused" in "\n".join(result["warnings"])


def test_restore_options_inspects_restore_document(monkeypatch, tmp_path: Path) -> None:
    doc = tmp_path / "restore.pr"
    doc.write_bytes(
        plistlib.dumps(
            {
                "RestoreOptions": {
                    "AuthInstallVariant": "Internal Install",
                    "BundleOverrides": {"ImageFile": "/tmp/OS.dmg"},
                    "UpdateBaseband": True,
                }
            }
        )
    )
    monkeypatch.setattr(
        ro,
        "signed_firmware_lookup",
        lambda product_type, timeout: {
            "source": "test",
            "product_type": product_type,
            "firmwares": [],
            "policy": {"metadata_only": True},
        },
    )

    result = ro.analyze_restore_options(
        None,
        device={"product_type": "iPhone9,1", "product_version": "16.7.10", "current_mode": "normal", "ecid": "1234"},
        restore_document_path=str(doc),
    )

    assert result["restore_document"]["status"] == "ok"
    assert result["restore_document"]["match_count"] == 1
    summary = result["restore_document"]["matches"][0]["summary"]
    assert summary["auth_install_variant"] == "Internal Install"
    assert summary["updates_baseband"] is True
    assert "Personalizing restore bundle" in result["restore_progress_model"]["phases"]


def test_restore_options_imports_classic_pr2_schema(tmp_path: Path) -> None:
    doc = tmp_path / "PR2Document.plist"
    doc.write_bytes(
        plistlib.dumps(
            {
                "Type": "PezRoot",
                "zChildren": [
                    {
                        "Label": "DFU File:",
                        "Type": "PezPathEntryItem",
                        "PRKeyPath": "BootOptions.DFUFile",
                    },
                    {
                        "Label": "Force Update",
                        "Type": "PezCheckItem",
                        "PRKeyPath": "RestoreOptions.ForceBasebandUpdate",
                    },
                ],
            }
        )
    )

    result = ro.inspect_restore_document(str(doc))

    assert result is not None
    assert result["status"] == "ok"
    assert result["matches"][0]["kind"] == "classic_pr2_schema"
    assert "BootOptions.DFUFile" in result["matches"][0]["override_candidates"]
    assert "PurpleRestore Classic PR2" in result["technique"]


def test_restore_options_flags_legacy_older_epoch_dfu_requirement(monkeypatch, tmp_path: Path) -> None:
    ipsw = make_ipsw(tmp_path / "legacy.ipsw", product_type="iPhone3,1", version="6.1.3", build="10B329")
    monkeypatch.setattr(
        ro,
        "signed_firmware_lookup",
        lambda product_type, timeout: {
            "source": "test",
            "product_type": product_type,
            "firmwares": [{"version": "6.1.3", "build": "10B329", "signed": True}],
            "policy": {"metadata_only": True},
        },
    )

    result = ro.analyze_restore_options(
        str(ipsw),
        device={"product_type": "iPhone3,1", "product_version": "7.1.2", "current_mode": "recovery", "ecid": "1234", "chip_family": "A4"},
    )

    preflight = result["downgrade_preflight"]
    assert preflight["target_older_than_current"] is True
    assert preflight["legacy_older_epoch_route"] is True
    assert preflight["required_mode"] == "dfu"
    assert any("DFU" in item for item in preflight["blockers"])


def test_restore_options_blocks_force_baseband_update_from_classic_doc(monkeypatch, tmp_path: Path) -> None:
    doc = tmp_path / "classic-settings.plist"
    doc.write_bytes(
        plistlib.dumps(
            {
                "RestoreOptions": {
                    "UpdateBaseband": True,
                    "ForceBasebandUpdate": True,
                    "CloseModemTickets": True,
                },
                "BootOptions": {"DFUFile": "/tmp/WTF.s5l8900xall.RELEASE.dfu"},
            }
        )
    )
    monkeypatch.setattr(
        ro,
        "signed_firmware_lookup",
        lambda product_type, timeout: {
            "source": "test",
            "product_type": product_type,
            "firmwares": [],
            "policy": {"metadata_only": True},
        },
    )

    result = ro.analyze_restore_options(
        None,
        device={"product_type": "iPhone3,1", "product_version": "7.1.2", "current_mode": "dfu", "ecid": "1234"},
        restore_document_path=str(doc),
    )

    assert any(match["kind"] == "classic_pr2_settings" for match in result["restore_document"]["matches"])
    assert result["downgrade_preflight"]["document_flags"]["force_baseband_update"] is True
    assert any("ForceBasebandUpdate" in item for item in result["downgrade_preflight"]["blockers"])


def test_purple_restore_capabilities_are_guarded_by_env(monkeypatch) -> None:
    monkeypatch.delenv("IPS_UU_INTERNAL", raising=False)
    public_profile = ro.purple_restore_capabilities()
    assert public_profile["mode"] == "public_guarded"
    assert public_profile["enabled"] is False

    monkeypatch.setenv("IPS_UU_INTERNAL", "1")
    internal_profile = ro.purple_restore_capabilities()
    assert internal_profile["mode"] == "apple_internal"
    assert internal_profile["enabled"] is True
    assert {candidate["id"] for candidate in internal_profile["executor_candidates"]} >= {
        "purple_restore_cli",
        "restore_framework_adapter",
        "software_bundle_provider",
        "knox_nfa_provider",
    }
