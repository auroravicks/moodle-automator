from dataclasses import replace
from datetime import datetime, timedelta

from lms_collector.config import Settings
from lms_collector.notifications import build_deadline_digest


def test_digest_prioritizes_and_excludes(config: Settings) -> None:
    now = datetime(2026, 10, 17, 12, 0, tzinfo=config.timezone)
    rows = [
        {"activity_id": "1", "course": "Course A", "title": "Missed", "url": "https://example/1", "due_at": (now - timedelta(hours=1)).isoformat(), "submission_status": "No submissions have been made yet"},
        {"activity_id": "2", "course": "Course A", "title": "Soon", "url": "https://example/2", "due_at": (now + timedelta(days=1)).isoformat(), "submission_status": "No submissions have been made yet"},
        {"activity_id": "3", "course": "Course A", "title": "Week", "url": "https://example/3", "due_at": (now + timedelta(days=5)).isoformat(), "submission_status": "No submissions have been made yet"},
        {"activity_id": "4", "course": "Financial Analytics", "title": "Excluded", "url": "https://example/4", "due_at": (now + timedelta(hours=1)).isoformat(), "submission_status": None},
        {"activity_id": "5", "course": "Course A", "title": "Done", "url": "https://example/5", "due_at": (now + timedelta(hours=1)).isoformat(), "submission_status": "Submitted for grading"},
    ]
    settings = replace(config, webhook_excluded_courses=("Financial Analytics",))
    text, ids = build_deadline_digest(rows, settings, now)
    assert ids == ["1", "2", "3"]
    assert text.index("Missed") < text.index("Due within 2 days") < text.index("Due within 7 days")
    assert "https://example/4" not in text


def test_unknown_status_is_not_actionable(config: Settings) -> None:
    now = datetime(2026, 10, 17, 12, 0, tzinfo=config.timezone)
    row = {"activity_id": "1", "course": "Course A", "title": "Unknown", "url": "https://example/1", "due_at": (now + timedelta(hours=1)).isoformat(), "submission_status": None}
    text, ids = build_deadline_digest([row], config, now)
    assert ids == []
    assert "https://example/1" not in text
