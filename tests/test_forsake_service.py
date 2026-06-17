from pathlib import Path

from ips_uu.services import forsake_service as fs


def test_forsake_missing_tool_reports_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(fs, "TOOLS_ROOT", tmp_path)
    toolchain = fs.find_forsake_toolchain()
    assert toolchain["found"] is False
    assert "not found" in toolchain["setup_error"].lower()


def test_parse_supported_arguments_from_help():
    parsed = fs.parse_supported_arguments("usage: forsake --ipsw file.ipsw --blob ticket.shsh2 --dfu iPhone9,1 iOS 10.3.3")
    assert "--ipsw" in parsed["arguments"]
    assert "--blob" in parsed["arguments"]
    assert "file.ipsw" in parsed["required_files"]
    assert "iPhone9,1" in parsed["supported_product_types"]
