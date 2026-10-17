from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


@dataclass(frozen=True)
class Settings:
    base_url: str
    output_dir: Path
    resources_dir: Path
    timezone: ZoneInfo
    reminder_hours: tuple[int, ...]
    headless: bool
    include_other_deadline_activities: bool
    auth_state_path: Path
    username: str | None
    password: str | None

    @classmethod
    def load(cls, env_file: Path = Path(".env")) -> "Settings":
        load_dotenv(env_file, override=False)
        base_url = os.getenv("LMS_BASE_URL", "").strip()
        if not base_url:
            raise ValueError("LMS_BASE_URL must be set in .env")
        output_dir = Path(os.getenv("OUTPUT_DIR", "class-notes")).expanduser().resolve()
        resources_dir = Path(os.getenv("RESOURCES_DIR", "../Resources")).expanduser().resolve()
        auth_value = os.getenv("AUTH_STATE_PATH", "").strip()
        auth_path = (
            Path(auth_value).expanduser().resolve()
            if auth_value
            else output_dir / "playwright-state" / "storage-state.json"
        )
        hours = tuple(
            sorted(
                {int(value.strip()) for value in os.getenv("REMINDER_HOURS", "72,24,3").split(",")},
                reverse=True,
            )
        )
        if not hours or any(hour <= 0 for hour in hours):
            raise ValueError("REMINDER_HOURS must contain positive integers")
        return cls(
            base_url=base_url.rstrip("/"),
            output_dir=output_dir,
            resources_dir=resources_dir,
            timezone=ZoneInfo(os.getenv("TIMEZONE", "Asia/Kolkata")),
            reminder_hours=hours,
            headless=_bool("HEADLESS", False),
            include_other_deadline_activities=_bool("INCLUDE_OTHER_DEADLINE_ACTIVITIES", False),
            auth_state_path=auth_path,
            username=os.getenv("LMS_USERNAME") or None,
            password=os.getenv("LMS_PASSWORD") or None,
        )
