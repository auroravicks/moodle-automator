from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
import yaml


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
    google_chat_webhook_url: str | None = field(repr=False)
    webhook_excluded_courses: tuple[str, ...]
    google_chat_deduplicate: bool
    deadline_windows_days: tuple[int, int]
    log_level: str
    log_dir: Path
    log_max_bytes: int
    log_backup_count: int

    @classmethod
    def load(cls, env_file: Path = Path(".env"), config_file: Path = Path("config.yml")) -> "Settings":
        load_dotenv(env_file, override=False)
        config: dict = {}
        if config_file.exists():
            loaded = yaml.safe_load(config_file.read_text(encoding="utf-8"))
            if loaded is not None and not isinstance(loaded, dict):
                raise ValueError("config.yml must contain a mapping at its root")
            config = loaded or {}

        def section(name: str) -> dict:
            result = config.get(name, {})
            if not isinstance(result, dict):
                raise ValueError(f"config.yml section {name!r} must be a mapping")
            return result

        storage = section("storage")
        lms = section("lms")
        browser = section("browser")
        logging_config = section("logging")
        locale = section("locale")
        deadlines = section("deadlines")
        notifications = section("notifications")
        google_chat = notifications.get("google_chat", {})
        if not isinstance(google_chat, dict):
            raise ValueError("config.yml notifications.google_chat must be a mapping")
        base_url = str(lms.get("base_url", "")).strip()
        if not base_url:
            raise ValueError("config.yml lms.base_url must be set")
        output_dir = Path(str(storage.get("internal_dir", "class-notes"))).expanduser().resolve()
        resources_dir = Path(str(storage.get("resources_dir", "../Resources"))).expanduser().resolve()
        auth_value = str(storage.get("auth_state_path") or "").strip()
        auth_path = (
            Path(auth_value).expanduser().resolve()
            if auth_value
            else output_dir / "playwright-state" / "storage-state.json"
        )
        hours = tuple(sorted({int(value) for value in deadlines.get("reminder_hours", [72, 24, 3])}, reverse=True))
        if not hours or any(hour <= 0 for hour in hours):
            raise ValueError("REMINDER_HOURS must contain positive integers")
        return cls(
            base_url=base_url.rstrip("/"),
            output_dir=output_dir,
            resources_dir=resources_dir,
            timezone=ZoneInfo(str(locale.get("timezone", "Asia/Kolkata"))),
            reminder_hours=hours,
            headless=bool(browser.get("headless", False)),
            include_other_deadline_activities=bool(deadlines.get("include_other_activity_types", False)),
            auth_state_path=auth_path,
            username=os.getenv("LMS_USERNAME") or None,
            password=os.getenv("LMS_PASSWORD") or None,
            google_chat_webhook_url=os.getenv("GOOGLE_CHAT_WEBHOOK_URL") or None,
            webhook_excluded_courses=tuple(
                str(value).strip() for value in google_chat.get("excluded_courses", []) if str(value).strip()
            ),
            google_chat_deduplicate=bool(google_chat.get("deduplicate", False)),
            deadline_windows_days=(
                int(deadlines.get("digest_windows_days", {}).get("urgent", 2)),
                int(deadlines.get("digest_windows_days", {}).get("upcoming", 7)),
            ),
            log_level=str(logging_config.get("level", "INFO")).upper(),
            log_dir=output_dir / str(logging_config.get("directory", "logs")),
            log_max_bytes=int(logging_config.get("max_bytes", 2_097_152)),
            log_backup_count=int(logging_config.get("backup_count", 5)),
        )
