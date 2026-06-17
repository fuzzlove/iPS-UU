from __future__ import annotations

import json

from ips_uu.gui.app import json_text


def test_json_text_serializes_bytes_for_gui_diagnostics() -> None:
    text = json_text({"manifest": {"ApBoardID": b"\x01\x02\x03"}})
    payload = json.loads(text)

    assert payload["manifest"]["ApBoardID"]["type"] == "bytes"
    assert payload["manifest"]["ApBoardID"]["length"] == 3
    assert payload["manifest"]["ApBoardID"]["hex_preview"] == "010203"
