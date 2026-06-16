"""Device detection service."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ips_uu.restorectl import detect_device


def detect_target(device: str = "auto") -> dict[str, Any]:
    snapshot = detect_device(device)
    return asdict(snapshot)
