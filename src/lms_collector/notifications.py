from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Iterable
from urllib.request import Request, urlopen

from .config import Settings
from .db import connect
from .storage import utc_now

LOGGER = logging.getLogger(__name__)


def _value(row: Any, name: str) -> Any:
    return row[name]


def _submitted(status: str | None) -> bool:
    return bool(status and ("submitted" in status.casefold() or "graded" in status.casefold()))


def _excluded(course: str, exclusions: tuple[str, ...]) -> bool:
    normalized = course.casefold()
    return any(value.casefold() in normalized or normalized in value.casefold() for value in exclusions)


def build_deadline_digest(
    rows: Iterable[Any], settings: Settings, now: datetime | None = None
) -> tuple[str, list[str]]:
    now = now or datetime.now(settings.timezone)
    urgent_days, upcoming_days = settings.deadline_windows_days
    two_days = now + timedelta(days=urgent_days)
    seven_days = now + timedelta(days=upcoming_days)
    groups: dict[str, list[tuple[datetime, Any]]] = {"missed": [], "two_days": [], "seven_days": []}
    included_ids: list[str] = []
    for row in rows:
        due_value = _value(row, "due_at")
        course = _value(row, "course")
        status = _value(row, "submission_status")
        # Fail closed: an absent/unknown status must never be reported as pending.
        if not due_value or not status or _submitted(status) or _excluded(course, settings.webhook_excluded_courses):
            continue
        due = datetime.fromisoformat(due_value).astimezone(settings.timezone)
        if due < now:
            group = "missed"
        elif due <= two_days:
            group = "two_days"
        elif due <= seven_days:
            group = "seven_days"
        else:
            continue
        groups[group].append((due, row))
        included_ids.append(str(_value(row, "activity_id")))

    headings = {
        "missed": "🚨 *Missed*",
        "two_days": f"🔴 *Due within {urgent_days} days*",
        "seven_days": f"🟡 *Due within {upcoming_days} days*",
    }
    lines = ["📚 *Moodle deadline digest*", ""]
    for key in ("missed", "two_days", "seven_days"):
        lines.append(headings[key])
        items = sorted(groups[key], key=lambda item: item[0])
        if not items:
            lines.append("• None")
        for due, row in items:
            status = _value(row, "submission_status") or "Not submitted"
            lines.append(
                f"• *{_value(row, 'course')}* — {_value(row, 'title')}\n"
                f"  {due.strftime('%a, %d %b %Y %I:%M %p %Z')} · {status}\n"
                f"  {_value(row, 'url')}"
            )
        lines.append("")
    if settings.webhook_excluded_courses:
        lines.append("Excluded: " + ", ".join(settings.webhook_excluded_courses))
    return "\n".join(lines).strip(), sorted(included_ids)


def send_deadline_digest(settings: Settings, *, force: bool = False) -> dict[str, Any]:
    if not settings.google_chat_webhook_url:
        raise ValueError("GOOGLE_CHAT_WEBHOOK_URL is not configured in .env")
    with connect(settings.output_dir / "state.sqlite") as connection:
        rows = connection.execute(
            "SELECT activity_id,course,title,url,due_at,submission_status FROM assignments"
        ).fetchall()
        text, activity_ids = build_deadline_digest(rows, settings)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        dedupe_key = f"google-chat:deadline-digest:{digest}"
        previous = connection.execute(
            "SELECT status FROM alert_deliveries WHERE dedupe_key=?", (dedupe_key,)
        ).fetchone()
        if previous and previous["status"] == "sent" and settings.google_chat_deduplicate and not force:
            LOGGER.info("Deadline digest unchanged; skipping previously successful delivery items=%s", len(activity_ids))
            return {"sent": False, "deduplicated": True, "items": len(activity_ids)}

        attempted = utc_now()
        LOGGER.info("Sending Google Chat deadline digest items=%s", len(activity_ids))
        try:
            payload = json.dumps({"text": text}).encode("utf-8")
            request = Request(
                settings.google_chat_webhook_url,
                data=payload,
                headers={"Content-Type": "application/json; charset=UTF-8"},
                method="POST",
            )
            with urlopen(request, timeout=30) as response:
                if not 200 <= response.status < 300:
                    raise RuntimeError(f"Google Chat returned HTTP {response.status}")
                response_body = response.read().decode("utf-8", errors="replace")
                try:
                    response_data = json.loads(response_body) if response_body else {}
                    message_name = response_data.get("name")
                except json.JSONDecodeError:
                    message_name = None
            status, error = "sent", None
        except Exception as exc:
            status, error = "failed", f"{type(exc).__name__}: {exc}"[:500]
            LOGGER.error("Google Chat deadline digest delivery failed: %s", error)
        connection.execute(
            "INSERT INTO alert_deliveries(dedupe_key,channel,status,attempts,last_attempt,error) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(dedupe_key) DO UPDATE SET status=excluded.status,attempts=alert_deliveries.attempts+1,"
            "last_attempt=excluded.last_attempt,error=excluded.error",
            (dedupe_key, "google_chat", status, 1, attempted, error),
        )
        connection.commit()
        if status != "sent":
            raise RuntimeError(error or "Google Chat delivery failed")
        LOGGER.info(
            "Google Chat deadline digest delivered items=%s message=%s",
            len(activity_ids), message_name or "not-returned",
        )
        return {
            "sent": True,
            "deduplicated": False,
            "items": len(activity_ids),
            "message_name": message_name,
        }
