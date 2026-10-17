from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .config import Settings

_configured_for: str | None = None


def configure_logging(settings: Settings) -> None:
    global _configured_for
    target = settings.log_dir / "moodle-lms-automation.log"
    if _configured_for == str(target):
        return
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        target,
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level, logging.INFO))
    root.addHandler(handler)
    _configured_for = str(target)
