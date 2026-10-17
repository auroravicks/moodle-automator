from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .storage import atomic_write, safe_name, sha256

RESOURCE_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".csv", ".tsv",
    ".ipynb", ".odt", ".ods", ".odp", ".rtf", ".txt", ".md", ".zip", ".7z",
    ".png", ".jpg", ".jpeg", ".svg",
}


def _destination(directory: Path, filename: str, content: bytes) -> Path:
    candidate = directory / safe_name(filename)
    if not candidate.exists() or candidate.read_bytes() == content:
        return candidate
    return directory / f"{candidate.stem}--{sha256(content)[:8]}{candidate.suffix}"


def _copy(source: Path, directory: Path, filename: str) -> Path:
    content = source.read_bytes()
    directory.mkdir(parents=True, exist_ok=True)
    target = _destination(directory, filename, content)
    if not target.exists() or target.read_bytes() != content:
        atomic_write(target, content)
    return target


def _time(value: str | None) -> str:
    if not value:
        return "Not provided"
    try:
        return datetime.fromisoformat(value).strftime("%d %b %Y, %I:%M %p %Z")
    except ValueError:
        return value


def export_readable(settings: Settings) -> dict[str, int | str]:
    root = settings.resources_dir
    root.mkdir(parents=True, exist_ok=True)
    transcript_count = caption_count = note_count = 0
    subjects: set[str] = set()

    for course_dir in sorted((settings.output_dir / "courses").glob("*")):
        course_file = course_dir / "course.json"
        if not course_dir.is_dir() or not course_file.exists():
            continue
        course = json.loads(course_file.read_text(encoding="utf-8"))
        subject = course["subject"]
        subjects.add(subject)
        destination = root / safe_name(subject)

        for metadata_file in course_dir.glob("transcripts/*/metadata.json"):
            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
            relative = metadata.get("text_path")
            source = course_dir / relative if relative else None
            if source and source.exists():
                _copy(source, destination / "Transcripts", f"{safe_name(metadata['title'])}.txt")
                transcript_count += 1
            vtt_relative = metadata.get("vtt_path")
            vtt_source = course_dir / vtt_relative if vtt_relative else None
            if vtt_source and vtt_source.exists():
                _copy(vtt_source, destination / "Captions", f"{safe_name(metadata['title'])}.vtt")
                caption_count += 1

        seen: set[str] = set()
        for metadata_file in course_dir.glob("notes/*/metadata-*.json"):
            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
            digest = metadata.get("content_sha256")
            source = course_dir / metadata["current_path"]
            if digest in seen or not source.exists() or source.suffix.lower() not in RESOURCE_EXTENSIONS:
                continue
            note_directory = destination / "Notes"
            if metadata.get("section"):
                note_directory = note_directory / safe_name(metadata["section"])
            _copy(source, note_directory, metadata.get("filename") or source.name)
            seen.add(digest)
            note_count += 1

    assignment_count = 0
    deadlines = settings.output_dir / "reports" / "deadlines.json"
    if deadlines.exists():
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in json.loads(deadlines.read_text(encoding="utf-8")):
            grouped[item["subject"]].append(item)
        for subject in sorted(subjects):
            items = grouped.get(subject, [])
            lines = [f"# Assignments — {subject}", ""]
            if not items:
                lines += ["No assignments found in the latest LMS sync.", ""]
            for item in sorted(items, key=lambda row: (row.get("due_at") or "9999", row["title"])):
                lines += [
                    f"## {item['title']}", "",
                    f"- Opens: {_time(item.get('opens_at'))}",
                    f"- Due: {_time(item.get('due_at'))}",
                    f"- Submission cutoff: {_time(item.get('cutoff_at'))}",
                    f"- Status: {item.get('submission_status') or 'Not provided'}",
                    f"- LMS: {item['url']}", "",
                ]
                assignment_count += 1
            atomic_write(root / safe_name(subject) / "Assignments.md", "\n".join(lines).encode("utf-8"))

    atomic_write(root / "README.md", (
        "# Course Resources\n\n"
        "Clean LMS export. Each course contains timestamped .txt Transcripts, original .vtt Captions, "
        "original Notes, and Assignments.md. Collector metadata and state remain in the internal store.\n"
    ).encode("utf-8"))
    return {"destination": str(root), "courses": len(subjects), "transcripts": transcript_count,
            "captions": caption_count, "notes": note_count, "assignments": assignment_count}
