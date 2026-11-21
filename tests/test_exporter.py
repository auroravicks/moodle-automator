from pathlib import Path

import json

from lms_collector.config import Settings
from lms_collector.exporter import _destination, export_readable


def test_collision_uses_content_hash(tmp_path: Path) -> None:
    (tmp_path / "notes.pdf").write_bytes(b"old")
    destination = _destination(tmp_path, "notes.pdf", b"new")
    assert destination.name.startswith("notes--")
    assert destination.suffix == ".pdf"


def test_exports_assignment_brief_and_attachment(config: Settings) -> None:
    course = config.output_dir / "courses" / "Course A--1"
    course.mkdir(parents=True)
    (course / "course.json").write_text(
        json.dumps({"subject": "Course A", "course_id": "1"}), encoding="utf-8"
    )
    attachment = course / "assignments" / "Lab--10" / "attachments" / "versions" / "abc--lab.csv"
    attachment.parent.mkdir(parents=True)
    attachment.write_text("x,y\n1,2\n", encoding="utf-8")
    reports = config.output_dir / "reports"
    reports.mkdir(parents=True)
    (reports / "deadlines.json").write_text(
        json.dumps([
            {
                "activity_id": "10", "activity_type": "assignment", "subject": "Course A",
                "title": "Lab", "url": "https://example/assign/10", "opens_at": None,
                "due_at": None, "cutoff_at": None, "submission_status": "Not submitted",
                "description_text": "Complete the lab.",
                "description_links": [{"text": "Reference", "url": "https://example/reference"}],
                "attachments": [{"filename": "lab.csv", "current_path": str(attachment.relative_to(course))}],
            }
        ]),
        encoding="utf-8",
    )

    result = export_readable(config)

    exported = config.resources_dir / "Course A" / "Assignments" / "Lab"
    assert "Complete the lab." in (exported / "brief.txt").read_text(encoding="utf-8")
    assert (exported / "Attachments" / "lab.csv").read_text(encoding="utf-8") == "x,y\n1,2\n"
    assert result["assignment_materials"] == 1
