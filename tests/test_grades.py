from lms_collector.collectors import _grade_display, _grade_numbers, _nullable_grade, _number
from lms_collector.db import connect


def test_nullable_grade_preserves_display_values() -> None:
    assert _nullable_grade(" 8.50 / 10.00 ") == "8.50 / 10.00"
    assert _nullable_grade("-") is None
    assert _nullable_grade("") is None
    assert _number("38.40 %") == 38.4
    assert _grade_numbers("8.50", "0–10") == (8.5, 0.0, 10.0)
    assert _grade_display("- Actions Grade analysis") is None
    assert _grade_display("8.50 Actions Grade analysis") == "8.50"


def test_grade_schema_records_current_state_and_history(tmp_path) -> None:
    with connect(tmp_path / "state.sqlite") as connection:
        connection.execute(
            "INSERT INTO grade_items(course_id,grade_item_id,activity_id,course,title,grade,first_seen,last_checked) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("7", "91", "123", "Course A", "Quiz 1", "8 / 10", "now", "now"),
        )
        connection.execute(
            "INSERT INTO grade_history(course_id,grade_item_id,old_grade,new_grade,changed_at) VALUES(?,?,?,?,?)",
            ("7", "91", None, "8 / 10", "now"),
        )
        assert connection.execute("SELECT grade FROM grade_items").fetchone()["grade"] == "8 / 10"
        assert connection.execute("SELECT new_grade FROM grade_history").fetchone()["new_grade"] == "8 / 10"
