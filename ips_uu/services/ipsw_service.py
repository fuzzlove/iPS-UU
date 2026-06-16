"""IPSW parsing service."""

from __future__ import annotations

import plistlib
import zipfile
from pathlib import Path
from typing import Any

from ips_uu.planner import PlannerError, choose_identity, load_build_manifest, supported_product_types


def _load_optional_plist_from_ipsw(ipsw: Path, name: str) -> dict[str, Any] | None:
    try:
        with zipfile.ZipFile(ipsw) as archive:
            candidates = [entry for entry in archive.namelist() if entry.endswith(name)]
            if not candidates:
                return None
            preferred = name if name in candidates else candidates[0]
            with archive.open(preferred) as handle:
                value = plistlib.load(handle)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def parse_ipsw(
    ipsw_path: str,
    product_type: str | None = None,
    device_class: str | None = None,
    variant: str | None = None,
) -> dict[str, Any]:
    ipsw = Path(ipsw_path)
    manifest = load_build_manifest(ipsw)
    restore_plist = _load_optional_plist_from_ipsw(ipsw, "Restore.plist")
    selected = None
    match_error = None
    try:
        selected = choose_identity(manifest, product_type, device_class, variant).__dict__
    except PlannerError as exc:
        match_error = str(exc)
    return {
        "path": str(ipsw.resolve()),
        "product_version": manifest.get("ProductVersion"),
        "product_build_version": manifest.get("ProductBuildVersion"),
        "supported_product_types": supported_product_types(manifest),
        "build_identity_count": len(manifest.get("BuildIdentities") or []),
        "selected_identity": selected,
        "match_error": match_error,
        "restore_plist_present": restore_plist is not None,
        "restore_plist_keys": sorted(str(key) for key in (restore_plist or {}).keys()),
    }


def compatibility_summary(device: dict[str, Any] | None, ipsw: dict[str, Any] | None) -> dict[str, Any]:
    if not ipsw:
        return {"status": "missing_ipsw", "message": "Select an IPSW to check compatibility."}
    product_type = (device or {}).get("product_type")
    if not product_type:
        return {"status": "unknown_device", "message": "No detected ProductType. Compatibility can be checked after device detection."}
    supported = ipsw.get("supported_product_types") or []
    if product_type in supported:
        return {"status": "compatible", "message": f"{product_type} is listed in the IPSW SupportedProductTypes."}
    return {"status": "incompatible", "message": f"{product_type} is not listed in the IPSW SupportedProductTypes."}
