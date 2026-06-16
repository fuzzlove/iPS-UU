"""Structured logging helpers for iPS-UU."""

from __future__ import annotations

import json
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


LOG_DIR = Path.home() / ".ips-uu" / "logs"
LOG_FILE = LOG_DIR / "ips-uu.log"


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True)


def configure_logging(verbose: bool = False) -> logging.Logger:
    global LOG_DIR, LOG_FILE
    def use_temp_log_dir() -> None:
        global LOG_DIR, LOG_FILE
        LOG_DIR = Path(tempfile.gettempdir()) / "ips-uu" / "logs"
        LOG_FILE = LOG_DIR / "ips-uu.log"
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        use_temp_log_dir()
    logger = logging.getLogger("ips_uu")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False
    if not any(isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == LOG_FILE for handler in logger.handlers):
        try:
            handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        except OSError:
            use_temp_log_dir()
            handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        handler.setFormatter(JsonLineFormatter())
        logger.addHandler(handler)
    return logger


def get_log_dir() -> Path:
    return LOG_DIR
