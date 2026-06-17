from pathlib import Path

from ips_uu.services.contents_research_service import contents_requirements


def test_contents_requirements_includes_rengineer_restore_findings(tmp_path: Path) -> None:
    root = tmp_path / "rengineer"
    (root / "MacOS").mkdir(parents=True)
    (root / "Frameworks").mkdir()
    (root / "MacOS" / "iTunesFlash").write_bytes(b"")
    (root / "Frameworks" / "libidevicerestore.dylib").write_bytes(b"")
    (root / "REVERSE_ENGINEERING_REPORT.md").write_text("# notes\n", encoding="utf-8")

    result = contents_requirements(root)

    assert result["contents_root"] == str(root)
    assert result["research_report"] == str(root / "REVERSE_ENGINEERING_REPORT.md")
    assert any(item["id"] == "itunesflash_mobiledevice_wrapper" for item in result["restore_engine_findings"])
    assert result["itunes_flash_helper_model"]["option_dictionary"]["AuthInstallRestoreBehavior"] == "Update or Erase"
    assert result["release_policy"]["private_restore_api_execution_supported"] is False
