"""Local GUI settings."""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


SETTINGS_DIR = Path.home() / ".ips-uu"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"


@dataclass
class AppSettings:
    backend: str = "auto"
    cfgutil_path: str = "/Applications/Apple Configurator.app/Contents/MacOS/cfgutil"
    idevicerestore_path: str = ""
    verbose_logging: bool = False
    dry_run_only: bool = True
    theme: str = "system"
    last_ipsw: str = ""


def load_settings(path: Path = SETTINGS_FILE) -> AppSettings:
    if not path.exists():
        return AppSettings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return AppSettings()
    allowed = {field: data.get(field) for field in AppSettings.__dataclass_fields__ if field in data}
    return AppSettings(**allowed)


def save_settings(settings: AppSettings, path: Path = SETTINGS_FILE) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        path = Path(tempfile.gettempdir()) / "ips-uu" / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(settings), indent=2, sort_keys=True)
    try:
        path.write_text(payload, encoding="utf-8")
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "ips-uu" / "settings.json"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        fallback.write_text(payload, encoding="utf-8")


def settings_payload(settings: AppSettings) -> dict[str, Any]:
    return asdict(settings)
