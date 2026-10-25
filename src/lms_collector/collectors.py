from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from playwright.sync_api import BrowserContext, Page, sync_playwright

from .browser import new_context, save_auth
from .config import Settings
from .db import connect
from .storage import atomic_json, atomic_write, safe_name, sha256, utc_now, write_jsonl
from .vtt import to_timestamped_text

ACTIVITY_PATTERNS = {
    "/mod/resource/view.php": "resource",
    "/mod/folder/view.php": "folder",
    "/mod/assign/view.php": "assignment",
    "/mod/page/view.php": "page",
    "/mod/url/view.php": "external_url",
}


@dataclass(frozen=True)
class Course:
    course_id: str
    name: str
    url: str
    directory: Path


@dataclass(frozen=True)
class Activity:
    activity_id: str
    kind: str
    title: str
    url: str
    section: str | None = None


def _id(url: str) -> str:
    return parse_qs(urlparse(url).query).get("id", [sha256(url.encode())[:12]])[0]


def _clean(value: str) -> str:
    return " ".join(value.split())


def _course_title(page: Page, fallback: str) -> str:
    for selector in (".page-header-headings h1", "#page-header h1", "h1"):
        locator = page.locator(selector)
        if locator.count():
            value = _clean(locator.first.inner_text())
            if value:
                return value
    title = page.title().split("|")[0].strip()
    return title or fallback


def inventory(page: Page, settings: Settings) -> tuple[list[Course], dict[str, list[Activity]]]:
    page.goto(f"{settings.base_url}/my/courses.php", wait_until="domcontentloaded", timeout=45_000)
    if "/login/" in page.url:
        raise RuntimeError("Authentication expired; run moodle-lms-automation login")
    course_urls = sorted(
        {
            urljoin(page.url, href)
            for href in page.locator('a[href*="/course/view.php"]').evaluate_all("els => els.map(e => e.getAttribute('href'))")
            if href
        }
    )
    courses: list[Course] = []
    activity_map: dict[str, list[Activity]] = {}
    for url in course_urls:
        page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        course_id = _id(url)
        name = _course_title(page, f"course-{course_id}")
        directory = settings.output_dir / "courses" / f"{safe_name(name)}--{course_id}"
        course = Course(course_id, name, url, directory)
        courses.append(course)
        found: dict[str, Activity] = {}
        for anchor in page.locator("a[href]").all():
            href = anchor.get_attribute("href")
            if not href:
                continue
            absolute = urljoin(page.url, href)
            kind = next((value for fragment, value in ACTIVITY_PATTERNS.items() if fragment in absolute), None)
            if not kind:
                continue
            activity_id = _id(absolute)
            title = _clean(anchor.inner_text()) or f"{kind}-{activity_id}"
            section = anchor.evaluate(
                """el => {
                    const box = el.closest('li.section, [data-sectionid]');
                    if (!box) return null;
                    const heading = box.querySelector('.sectionname, h3, h2');
                    return heading ? heading.innerText.trim() : null;
                }"""
            )
            found[absolute] = Activity(
                activity_id, kind, title, absolute, _clean(section) if section else None
            )
        activity_map[course_id] = sorted(found.values(), key=lambda value: (value.kind, value.activity_id))
        atomic_json(
            directory / "course.json",
            {"schema_version": 1, "course_id": course_id, "subject": name, "source_url": url, "last_checked": utc_now()},
        )
        (directory / "transcripts").mkdir(parents=True, exist_ok=True)
        (directory / "notes").mkdir(parents=True, exist_ok=True)
    return courses, activity_map


def _caption_tracks(page: Page) -> list[dict[str, str]]:
    tracks: list[dict[str, str]] = []
    for iframe in page.locator("iframe").all():
        handle = iframe.element_handle()
        frame = handle.content_frame() if handle else None
        if not frame:
            continue
        try:
            page.wait_for_timeout(250)
            tracks.extend(
                frame.locator("track[src]").evaluate_all(
                    "els => els.map(e => ({kind:e.kind||'', label:e.label||'', language:e.srclang||'', url:e.src}))"
                )
            )
        except Exception:
            continue
    return tracks


def collect_transcript(
    page: Page, context: BrowserContext, course: Course, activity: Activity, connection: Any
) -> dict[str, Any]:
    page.goto(activity.url, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(750)
    tracks = _caption_tracks(page)
    checked = utc_now()
    base = course.directory / "transcripts" / f"{safe_name(activity.title)}--{activity.activity_id}"
    if not tracks:
        metadata = {
            "schema_version": 1,
            "document_type": "lecture_transcript",
            "activity_id": activity.activity_id,
            "course_id": course.course_id,
            "subject": course.name,
            "title": activity.title,
            "section": activity.section,
            "source_url": activity.url,
            "recording_date": None,
            "caption_status": "missing",
            "caption_language": None,
            "content_sha256": None,
            "vtt_path": None,
            "text_path": None,
            "last_checked": checked,
        }
        atomic_json(base / "metadata.json", metadata)
        return metadata
    track = next((item for item in tracks if item.get("language", "").lower().startswith("en")), tracks[0])
    response = context.request.get(track["url"], timeout=45_000)
    if not response.ok:
        raise RuntimeError(f"Caption request failed with HTTP {response.status}")
    content = response.body()
    digest = sha256(content)
    vtt_path = base / "captions.vtt"
    text_path = base / "transcript.txt"
    previous = None
    metadata_path = base / "metadata.json"
    if metadata_path.exists():
        previous = json.loads(metadata_path.read_text(encoding="utf-8")).get("content_sha256")
    if previous != digest or not vtt_path.exists() or not text_path.exists():
        atomic_write(vtt_path, content)
        atomic_write(text_path, to_timestamped_text(content.decode("utf-8-sig", errors="replace")).encode("utf-8"))
    metadata = {
        "schema_version": 1,
        "document_type": "lecture_transcript",
        "activity_id": activity.activity_id,
        "course_id": course.course_id,
        "subject": course.name,
        "title": activity.title,
        "section": activity.section,
        "source_url": activity.url,
        "recording_date": None,
        "caption_status": "available",
        "caption_language": track.get("language") or track.get("label"),
        "content_sha256": digest,
        "vtt_path": str(vtt_path.relative_to(course.directory)),
        "text_path": str(text_path.relative_to(course.directory)),
        "last_checked": checked,
    }
    atomic_json(metadata_path, metadata)
    connection.execute(
        "INSERT INTO recordings(source_url,course,title,caption_hash,caption_status,local_path,first_seen,last_checked) "
        "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(source_url) DO UPDATE SET caption_hash=excluded.caption_hash, "
        "caption_status=excluded.caption_status,local_path=excluded.local_path,last_checked=excluded.last_checked",
        (activity.url, course.name, activity.title, digest, "available", str(base), checked, checked),
    )
    return metadata


def _filename(response: Any, url: str, fallback: str) -> str:
    disposition = response.headers.get("content-disposition", "")
    match = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", disposition, re.I)
    if match:
        return safe_name(unquote(match.group(1)))
    name = Path(unquote(urlparse(response.url or url).path)).name
    return safe_name(name, fallback)


def collect_resource(
    page: Page, context: BrowserContext, course: Course, activity: Activity, connection: Any
) -> list[dict[str, Any]]:
    page.goto(activity.url, wait_until="domcontentloaded", timeout=45_000)
    links = page.locator('a[href*="pluginfile.php"]').evaluate_all("els => els.map(e => e.href)")
    if "pluginfile.php" in page.url:
        links.append(page.url)
    links = list(dict.fromkeys(links))
    if not links:
        return [{"activity_id": activity.activity_id, "title": activity.title, "status": "unsupported_or_external", "url": activity.url}]
    results: list[dict[str, Any]] = []
    base = course.directory / "notes" / f"{safe_name(activity.title)}--{activity.activity_id}"
    for index, link in enumerate(links, 1):
        response = context.request.get(link, timeout=60_000)
        if not response.ok:
            raise RuntimeError(f"Resource request failed with HTTP {response.status}")
        content = response.body()
        digest = sha256(content)
        filename = _filename(response, link, f"resource-{index}")
        version_path = base / "versions" / f"{digest[:12]}--{filename}"
        if not version_path.exists():
            atomic_write(version_path, content)
        checked = utc_now()
        metadata = {
            "schema_version": 1,
            "document_type": "course_note",
            "activity_id": activity.activity_id,
            "course_id": course.course_id,
            "subject": course.name,
            "title": activity.title,
            "section": activity.section,
            "source_url": activity.url,
            "download_url": link,
            "filename": filename,
            "content_type": response.headers.get("content-type"),
            "content_sha256": digest,
            "current_path": str(version_path.relative_to(course.directory)),
            "last_checked": checked,
        }
        atomic_json(base / f"metadata-{index}.json", metadata)
        connection.execute(
            "INSERT INTO resources(source_url,course,title,content_hash,local_path,first_seen,last_checked) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(source_url) DO UPDATE SET content_hash=excluded.content_hash,local_path=excluded.local_path,last_checked=excluded.last_checked",
            (link, course.name, activity.title, digest, str(version_path), checked, checked),
        )
        results.append(metadata)
    return results


def _parse_moodle_date(value: str, settings: Settings) -> str | None:
    value = value.strip()
    for pattern in ("%A, %d %B %Y, %I:%M %p", "%A, %d %B %Y, %H:%M"):
        try:
            return datetime.strptime(value, pattern).replace(tzinfo=settings.timezone).isoformat()
        except ValueError:
            pass
    return None


def collect_assignment(page: Page, course: Course, activity: Activity, settings: Settings, connection: Any) -> dict[str, Any]:
    page.goto(activity.url, wait_until="domcontentloaded", timeout=45_000)
    dates_text = _clean(page.locator("[data-region='activity-dates']").inner_text())
    values: dict[str, str | None] = {"opens_at": None, "due_at": None, "cutoff_at": None}
    labels = {"Opened": "opens_at", "Due": "due_at", "Cut-off date": "cutoff_at", "Submission cutoff": "cutoff_at"}
    for label, key in labels.items():
        match = re.search(rf"{re.escape(label)}:\s*(.*?)(?=\s+(?:Opened|Due|Cut-off date|Submission cutoff):|$)", dates_text)
        if match:
            values[key] = _parse_moodle_date(match.group(1), settings)
    status = None
    table = page.locator(".submissionstatustable")
    try:
        table.wait_for(state="visible", timeout=10_000)
    except Exception:
        pass
    if table.count():
        for cell in table.locator(".baz-asg-cell").all():
            label = cell.locator(".baz-asg-k")
            if label.count() and _clean(label.inner_text()).casefold() == "submission status":
                value = label.evaluate("e => e.nextElementSibling?.innerText || ''")
                status = _clean(value) or None
                break
        if status is None:
            for row in table.locator("tr").all():
                cells = row.locator("th,td,.cell")
                if cells.count() >= 2 and _clean(cells.nth(0).inner_text()).casefold() == "submission status":
                    status = _clean(cells.nth(1).inner_text())
                    break
    checked = utc_now()
    old = connection.execute("SELECT due_at, first_seen FROM assignments WHERE activity_id=?", (activity.activity_id,)).fetchone()
    first_seen = old["first_seen"] if old else checked
    if old and old["due_at"] != values["due_at"]:
        connection.execute(
            "INSERT INTO deadline_history(activity_id,old_due_at,new_due_at,changed_at) VALUES(?,?,?,?)",
            (activity.activity_id, old["due_at"], values["due_at"], checked),
        )
    connection.execute(
        "INSERT INTO assignments(activity_id,course,title,url,opens_at,due_at,cutoff_at,submission_status,first_seen,last_checked) "
        "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(activity_id) DO UPDATE SET course=excluded.course,title=excluded.title,url=excluded.url,"
        "opens_at=excluded.opens_at,due_at=excluded.due_at,cutoff_at=excluded.cutoff_at,submission_status=excluded.submission_status,last_checked=excluded.last_checked",
        (activity.activity_id, course.name, activity.title, activity.url, values["opens_at"], values["due_at"], values["cutoff_at"], status, first_seen, checked),
    )
    return {
        "activity_id": activity.activity_id,
        "course_id": course.course_id,
        "subject": course.name,
        "title": activity.title,
        "url": activity.url,
        **values,
        "submission_status": status,
        "first_seen": first_seen,
        "last_checked": checked,
    }


def _calendar_date(label: str, now: datetime) -> datetime:
    normalized = _clean(label).casefold()
    if normalized == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if normalized == "tomorrow":
        return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    value = datetime.strptime(_clean(label).title(), "%A, %d %b").replace(year=now.year, tzinfo=now.tzinfo)
    if value.date() < now.date():
        value = value.replace(year=now.year + 1)
    return value


def collect_live_lectures(page: Page, settings: Settings, connection: Any) -> list[dict[str, str]]:
    page.goto(f"{settings.base_url}/calendar/view.php?view=upcoming", wait_until="domcontentloaded", timeout=45_000)
    if "/login/" in page.url:
        raise RuntimeError("Authentication expired while reading the calendar")
    now = datetime.now(settings.timezone)
    checked = utc_now()
    lectures: list[dict[str, str]] = []
    for group in page.locator(".cal-upgroup").all():
        heading = group.locator(".cal-uphead")
        if not heading.count():
            continue
        try:
            day = _calendar_date(heading.first.inner_text(), now)
        except ValueError:
            continue
        for row in group.locator(".cal-uprow.t-live").all():
            time_text = _clean(row.locator(".cal-uptime").inner_text())
            title = _clean(row.locator(".cal-upttl").inner_text())
            url = row.get_attribute("href") or ""
            try:
                clock = datetime.strptime(time_text, "%H:%M")
            except ValueError:
                continue
            starts_at = day.replace(hour=clock.hour, minute=clock.minute).isoformat()
            event_id = _id(url)
            previous = connection.execute(
                "SELECT first_seen FROM live_lectures WHERE event_id=?", (event_id,)
            ).fetchone()
            first_seen = previous["first_seen"] if previous else checked
            connection.execute(
                "INSERT INTO live_lectures(event_id,course,starts_at,url,first_seen,last_checked) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(event_id) DO UPDATE SET course=excluded.course,starts_at=excluded.starts_at,"
                "url=excluded.url,last_checked=excluded.last_checked",
                (event_id, title, starts_at, url, first_seen, checked),
            )
            lectures.append({"event_id": event_id, "course": title, "starts_at": starts_at, "url": url})
    return lectures


def run_sync(settings: Settings, *, include_materials: bool = True, include_assignments: bool = True) -> dict[str, Any]:
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    transcripts: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    assignments: list[dict[str, Any]] = []
    live_lectures: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    with connect(settings.output_dir / "state.sqlite") as connection, sync_playwright() as playwright:
        browser, context = new_context(playwright, settings, visible=not settings.headless)
        page = context.new_page()
        courses, activities = inventory(page, settings)
        live_lectures = collect_live_lectures(page, settings, connection)
        for course in courses:
            for activity in activities[course.course_id]:
                try:
                    if include_materials and activity.kind == "page":
                        transcripts.append(collect_transcript(page, context, course, activity, connection))
                    elif include_materials and activity.kind in {"resource", "folder"}:
                        notes.extend(collect_resource(page, context, course, activity, connection))
                    elif include_assignments and activity.kind == "assignment":
                        assignments.append(collect_assignment(page, course, activity, settings, connection))
                    elif include_materials and activity.kind == "external_url":
                        notes.append({"activity_id": activity.activity_id, "title": activity.title, "status": "external", "url": activity.url})
                except Exception as exc:
                    errors.append({"course": course.name, "activity_id": activity.activity_id, "kind": activity.kind, "error": str(exc)[:500]})
                connection.commit()
        save_auth(context, settings.auth_state_path)
        browser.close()
    reports = settings.output_dir / "reports"
    write_jsonl(reports / "transcripts.jsonl", transcripts)
    write_jsonl(reports / "notes.jsonl", notes)
    atomic_json(reports / "deadlines.json", assignments)
    summary = {
        "schema_version": 1,
        "completed_at": utc_now(),
        "transcripts": len(transcripts),
        "notes": len(notes),
        "assignments": len(assignments),
        "live_lectures": len(live_lectures),
        "errors": errors,
    }
    atomic_json(reports / "last-run.json", summary)
    return summary
