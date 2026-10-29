from lms_collector.collectors import _quiz_status


def test_quiz_status_detects_finished_attempt() -> None:
    assert _quiz_status("Quiz 1 Submitted · 1 of 1 attempts used Status Finished") == "Submitted"


def test_quiz_status_detects_missing_attempt() -> None:
    assert _quiz_status("Quiz 4 Not yet attempted · 0 of 1 attempts used") == "Not yet attempted"


def test_quiz_status_fails_closed_for_unknown_markup() -> None:
    assert _quiz_status("Quiz availability details unavailable") is None
