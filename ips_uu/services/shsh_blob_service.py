"""Clean-room SHSH/APTicket blob inspector.

This module parses local plist-style blob files for diagnostics only. It does
not fetch, create, modify, replay, submit, or select blobs for restore.
"""

from __future__ import annotations

import hashlib
import plistlib
from pathlib import Path
from typing import Any


COMMON_FIELDS = {
    "ECID",
    "ApECID",
    "UniqueChipID",
    "ApNonce",
    "APNonce",
    "ApGenerator",
    "Generator",
    "generator",
    "BoardConfig",
    "ProductType",
    "ProductVersion",
    "BuildVersion",
    "ApBoardID",
    "ApChipID",
    "ApSecurityDomain",
    "ApProductionMode",
    "ApSecurityMode",
    "ApImg4Ticket",
    "Manifest",
}


class ShshBlobError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _short_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"type": "bytes", "length": len(value), "sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return {"type": "list", "length": len(value)}
    if isinstance(value, dict):
        return {"type": "dict", "keys": sorted(str(key) for key in value.keys())[:20]}
    return str(value)


def _walk(value: Any, path: str = "") -> list[tuple[str, str, Any]]:
    found: list[tuple[str, str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            if key_text in COMMON_FIELDS or "nonce" in key_text.lower() or "ticket" in key_text.lower() or "generator" in key_text.lower():
                found.append((key_text, child_path, child))
            found.extend(_walk(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_walk(child, f"{path}[{index}]"))
    return found


def _normalize(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.hex().lower()
    return str(value).lower().replace("0x", "").replace(" ", "")


def inspect_blob(
    path: str,
    expected_product_type: str | None = None,
    expected_ecid: str | None = None,
    expected_apnonce: str | None = None,
) -> dict[str, Any]:
    blob_path = Path(path).expanduser()
    if not blob_path.exists() or not blob_path.is_file():
        raise ShshBlobError(f"blob file was not found: {blob_path}")
    try:
        payload = plistlib.loads(blob_path.read_bytes())
    except Exception as exc:
        raise ShshBlobError(f"blob is not a parseable plist-style SHSH file: {exc}") from exc
    fields = _walk(payload)
    raw_values: dict[str, list[Any]] = {}
    extracted: dict[str, list[dict[str, Any]]] = {}
    for key, field_path, value in fields:
        raw_values.setdefault(key, []).append(value)
        extracted.setdefault(key, []).append({"path": field_path, "value": _short_value(value)})
    flat_values = {key: [item["value"] for item in values] for key, values in extracted.items()}
    ticket_entries = [
        item
        for key, values in extracted.items()
        if "ticket" in key.lower() or key == "ApImg4Ticket"
        for item in values
    ]
    nonce_values = [
        item["value"]
        for key, values in extracted.items()
        if "nonce" in key.lower()
        for item in values
    ]
    generator_values = [
        item["value"]
        for key, values in extracted.items()
        if "generator" in key.lower()
        for item in values
    ]

    comparisons = []
    product_values = [str(value) for value in raw_values.get("ProductType", [])]
    ecid_values = [str(value) for key in ("ECID", "ApECID", "UniqueChipID") for value in raw_values.get(key, [])]
    if expected_product_type:
        comparisons.append({"field": "ProductType", "expected": expected_product_type, "matched": expected_product_type in product_values, "observed": product_values})
    if expected_ecid:
        expected = _normalize(expected_ecid)
        observed = [_normalize(value) for value in ecid_values]
        comparisons.append({"field": "ECID", "expected": expected_ecid, "matched": expected in observed, "observed": ecid_values})
    if expected_apnonce:
        expected = _normalize(expected_apnonce)
        observed = [_normalize(value) for key, values in raw_values.items() if "nonce" in key.lower() for value in values]
        comparisons.append({"field": "APNonce", "expected": expected_apnonce, "matched": expected in observed, "observed": nonce_values})

    return {
        "path": str(blob_path),
        "file": {
            "size_bytes": blob_path.stat().st_size,
            "sha256": _sha256(blob_path),
        },
        "parse_status": "parseable_plist",
        "appears_to_contain_ticket": bool(ticket_entries),
        "ticket_entries": ticket_entries,
        "nonce_values": nonce_values,
        "generator_values": generator_values,
        "extracted_fields": extracted,
        "comparisons": comparisons,
        "limitations": [
            "This is structural and metadata inspection only.",
            "It does not prove Apple cryptographic signature validity.",
            "It does not prove restore usability for a live device nonce, SEP, baseband, board, build, or component set.",
            "iPS-UU does not fetch, create, replay, submit, patch, or select SHSH/APTicket blobs for restore.",
        ],
    }
