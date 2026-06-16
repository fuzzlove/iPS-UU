from __future__ import annotations

import plistlib
from pathlib import Path

from ips_uu.services.shsh_blob_service import inspect_blob


def test_inspect_blob_extracts_metadata_and_comparisons(tmp_path: Path) -> None:
    blob = tmp_path / "example.shsh2"
    payload = {
        "ProductType": "iPhone10,3",
        "ECID": "0x1234",
        "ApNonce": bytes.fromhex("aabbccdd"),
        "ApGenerator": "0x1111111111111111",
        "ApImg4Ticket": b"ticket-bytes",
    }
    blob.write_bytes(plistlib.dumps(payload))

    result = inspect_blob(str(blob), expected_product_type="iPhone10,3", expected_ecid="1234", expected_apnonce="aabbccdd")

    assert result["parse_status"] == "parseable_plist"
    assert result["appears_to_contain_ticket"] is True
    assert result["nonce_values"][0]["sha256"]
    assert all(item["matched"] for item in result["comparisons"])
    assert "does not prove Apple cryptographic signature validity" in " ".join(result["limitations"])
