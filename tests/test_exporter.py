from pathlib import Path

from lms_collector.exporter import _destination


def test_collision_uses_content_hash(tmp_path: Path) -> None:
    (tmp_path / "notes.pdf").write_bytes(b"old")
    destination = _destination(tmp_path, "notes.pdf", b"new")
    assert destination.name.startswith("notes--")
    assert destination.suffix == ".pdf"
