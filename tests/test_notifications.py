from dataclasses import replace
from datetime import datetime, timedelta

from lms_collector.config import Settings
from lms_collector.notifications import build_deadline_digest, send_auth_required


def test_digest_prioritizes_and_excludes(config: Settings) -> None:
    now = datetime(2026, 10, 17, 12, 0, tzinfo=config.timezone)
    rows = [
        {"activity_id": "1", "course": "Course A", "title": "Missed", "url": "https://example/1", "due_at": (now - timedelta(hours=1)).isoformat(), "submission_status": "No submissions have been made yet"},
        {"activity_id": "2", "course": "Course A", "title": "Soon", "url": "https://example/2", "due_at": (now + timedelta(days=1)).isoformat(), "submission_status": "No submissions have been made yet"},
        {"activity_id": "3", "course": "Course A", "title": "Week", "url": "https://example/3", "due_at": (now + timedelta(days=5)).isoformat(), "submission_status": "No submissions have been made yet"},
        {"activity_id": "4", "course": "Excluded Course", "title": "Excluded", "url": "https://example/4", "due_at": (now + timedelta(hours=1)).isoformat(), "submission_status": None},
        {"activity_id": "5", "course": "Course A", "title": "Done", "url": "https://example/5", "due_at": (now + timedelta(hours=1)).isoformat(), "submission_status": "Submitted for grading"},
    ]
    settings = replace(config, webhook_excluded_courses=("Excluded Course",))
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


def test_digest_includes_upcoming_live_lectures(config: Settings) -> None:
    now = datetime(2026, 10, 25, 12, 0, tzinfo=config.timezone)
    live_rows = [
        {"event_id": "10", "course": "Course A", "starts_at": (now + timedelta(days=1)).isoformat(), "url": "https://example/join/10"},
        {"event_id": "11", "course": "Excluded Course", "starts_at": (now + timedelta(days=1)).isoformat(), "url": "https://example/join/11"},
        {"event_id": "12", "course": "Course A", "starts_at": (now + timedelta(days=8)).isoformat(), "url": "https://example/join/12"},
    ]
    settings = replace(config, webhook_excluded_courses=("Excluded Course",), live_lecture_days=7)
    text, ids = build_deadline_digest([], settings, now, live_rows)
    assert ids == ["live:10"]
    assert "https://example/join/10" in text
    assert "https://example/join/11" not in text
    assert "https://example/join/12" not in text


def test_auth_required_alert_uses_configured_webhook(config: Settings, monkeypatch) -> None:
    delivered: dict[str, str] = {}

    def fake_send(url: str, text: str) -> str:
        delivered["url"] = url
        delivered["text"] = text
        return "spaces/example/messages/1"

    settings = replace(config, google_chat_webhook_url="https://chat.example/webhook", google_chat_enabled=True)
    monkeypatch.setattr("lms_collector.notifications._send_google_chat", fake_send)
    result = send_auth_required(settings)
    assert result["sent"] is True
    assert delivered["url"] == "https://chat.example/webhook"
    assert "Moodle login required" in delivered["text"]


def test_auth_required_alert_is_optional(config: Settings, monkeypatch) -> None:
    monkeypatch.setattr(
        "lms_collector.notifications._send_google_chat",
        lambda *_: (_ for _ in ()).throw(AssertionError("webhook should not be called")),
    )
    result = send_auth_required(replace(config, google_chat_enabled=False))
    assert result["disabled"] is True
