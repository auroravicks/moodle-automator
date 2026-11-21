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
    "/mod/quiz/view.php": "quiz",
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


def _is_assignment_material_url(url: str) -> bool:
    path = urlparse(url).path.casefold()
    return "pluginfile.php" in path and "/mod_assign/intro" in path


def collect_assignment(
    page: Page,
    context: BrowserContext,
    course: Course,
    activity: Activity,
    settings: Settings,
    connection: Any,
) -> dict[str, Any]:
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
    description = page.locator(".activity-description")
    description_text = _clean(description.first.inner_text()) if description.count() else ""
    description_links: list[dict[str, str]] = []
    attachments: list[dict[str, str]] = []
    assignment_dir = course.directory / "assignments" / f"{safe_name(activity.title)}--{activity.activity_id}"
    if description.count():
        observed_links = description.first.locator("a[href]").evaluate_all(
            "els => els.map(e => ({url: e.href, text: (e.innerText || '').trim()}))"
        )
        for link in observed_links:
            url = str(link.get("url") or "")
            if not url:
                continue
            description_links.append({"text": _clean(str(link.get("text") or "")), "url": url})
            if not _is_assignment_material_url(url):
                continue
            response = context.request.get(url, timeout=60_000)
            if not response.ok:
                raise RuntimeError(f"Assignment material request failed with HTTP {response.status}")
            content = response.body()
            digest = sha256(content)
            filename = _filename(response, url, f"attachment-{len(attachments) + 1}")
            version_path = assignment_dir / "attachments" / "versions" / f"{digest[:12]}--{filename}"
            if not version_path.exists():
                atomic_write(version_path, content)
            attachments.append(
                {
                    "filename": filename,
                    "source_url": url,
                    "content_type": response.headers.get("content-type") or "",
                    "content_sha256": digest,
                    "current_path": str(version_path.relative_to(course.directory)),
                }
            )
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
    result = {
        "activity_id": activity.activity_id,
        "activity_type": "assignment",
        "course_id": course.course_id,
        "subject": course.name,
        "title": activity.title,
        "url": activity.url,
        **values,
        "submission_status": status,
        "description_text": description_text,
        "description_links": description_links,
        "attachments": attachments,
        "first_seen": first_seen,
        "last_checked": checked,
    }
    atomic_json(assignment_dir / "metadata.json", result)
    return result


def _quiz_status(page_text: str) -> str | None:
    normalized = _clean(page_text).casefold()
    if "submitted ·" in normalized or re.search(r"\bstatus\s+finished\b", normalized):
        return "Submitted"
    if "not yet attempted" in normalized:
        return "Not yet attempted"
    if "in progress" in normalized or "continue the last attempt" in normalized:
        return "In progress"
    return None


def collect_quiz(
    page: Page, course: Course, activity: Activity, settings: Settings, connection: Any
) -> dict[str, Any]:
    page.goto(activity.url, wait_until="domcontentloaded", timeout=45_000)
    dates = page.locator("[data-region='activity-dates']")
    dates_text = _clean(dates.first.inner_text()) if dates.count() else ""
    values: dict[str, str | None] = {"opens_at": None, "due_at": None, "cutoff_at": None}
    labels = {"Opened": "opens_at", "Closes": "due_at", "Closed": "due_at"}
    for label, key in labels.items():
        match = re.search(rf"{label}:\s*(.*?)(?=\s+(?:Opened|Closes|Closed):|$)", dates_text)
        if match:
            values[key] = _parse_moodle_date(match.group(1), settings)
    main = page.locator("#region-main")
    status = _quiz_status(main.first.inner_text() if main.count() else "")
    title = _course_title(page, activity.title)
    checked = utc_now()
    old = connection.execute(
        "SELECT due_at, first_seen FROM assignments WHERE activity_id=?", (activity.activity_id,)
    ).fetchone()
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
        (activity.activity_id, course.name, title, activity.url, values["opens_at"], values["due_at"], values["cutoff_at"], status, first_seen, checked),
    )
    return {
        "activity_id": activity.activity_id,
        "activity_type": "quiz",
        "course_id": course.course_id,
        "subject": course.name,
        "title": title,
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


def _nullable_grade(value: Any) -> str | None:
    cleaned = _clean(str(value or ""))
    return None if not cleaned or cleaned in {"-", "–", "—"} else cleaned


def _number(value: Any) -> float | None:
    cleaned = _nullable_grade(value)
    if cleaned is None:
        return None
    match = re.search(r"-?\d+(?:[.,]\d+)?", cleaned.replace(",", ""))
    return float(match.group()) if match else None


def _grade_display(value: Any) -> str | None:
    cleaned = _clean(str(value or ""))
    cleaned = re.sub(r"\s*Actions?\s+Grade analysis\b.*$", "", cleaned, flags=re.I).strip()
    return _nullable_grade(cleaned)


def _grade_numbers(grade: Any, grade_range: Any) -> tuple[float | None, float | None, float | None]:
    grade_value = _number(grade)
    range_text = _nullable_grade(grade_range)
    if range_text is None:
        return grade_value, None, None
    values = [float(value.replace(",", "")) for value in re.findall(r"-?\d+(?:[.,]\d+)?", range_text)]
    return grade_value, values[0] if values else None, values[1] if len(values) > 1 else None


def collect_grades(page: Page, course: Course, settings: Settings, connection: Any) -> dict[str, Any]:
    """Collect the signed-in user's grade report for one subscribed course."""
    report_url = f"{settings.base_url}/grade/report/user/index.php?id={course.course_id}"
    page.goto(report_url, wait_until="domcontentloaded", timeout=45_000)
    if "/login/" in page.url:
        raise RuntimeError("Authentication expired while collecting grades")
    checked = utc_now()
    status_by_url = {
        item["url"]: item["status"]
        for item in page.locator("table.sgr-table tr").evaluate_all(
            """rows => rows.map(row => ({
                url: row.querySelector('.sgr-link')?.href || '',
                status: row.querySelector('.sgr-status')?.innerText?.trim() || ''
            })).filter(item => item.url)"""
        )
    }
    raw_rows = page.locator("table.user-grade tbody tr").evaluate_all(
        """rows => rows.map(row => {
            const link = row.querySelector('a.gradeitemheader, .column-itemname a[href*="/mod/"]');
            const text = selector => (row.querySelector(selector)?.innerText || '').trim();
            const itemCell = row.querySelector('.column-itemname');
            return {
                row_id: row.id || '',
                title: (link?.innerText || itemCell?.innerText || '').trim(),
                url: link?.href || '',
                weight: text('.column-weight'),
                grade: text('.column-grade'),
                range: text('.column-range'),
                percentage: text('.column-percentage'),
                feedback: text('.column-feedback'),
                contribution: text('.column-contributiontocoursetotal'),
                is_total: /course total/i.test(itemCell?.innerText || '')
            };
        }).filter(row => row.title)"""
    )
    items: list[dict[str, Any]] = []
    course_total: dict[str, Any] | None = None
    for raw in raw_rows:
        if raw["is_total"]:
            course_total = {
                "grade_display": _nullable_grade(raw["grade"]),
                "grade_value": _number(raw["grade"]),
                "percentage_display": _nullable_grade(raw["percentage"]),
                "percentage_value": _number(raw["percentage"]),
            }
            continue
        url = str(raw["url"] or "")
        if not url:
            continue
        activity_id = _id(url)
        row_id = str(raw["row_id"] or "")
        grade_item_id = row_id.removeprefix("row_").split("_")[0] or activity_id
        activity_type = next(
            (kind for fragment, kind in ACTIVITY_PATTERNS.items() if fragment in url), None
        )
        grade_display = _grade_display(raw["grade"])
        range_display = _nullable_grade(raw["range"])
        grade_value, grade_min, grade_max = _grade_numbers(grade_display, range_display)
        item = {
            "course_id": course.course_id,
            "grade_item_id": grade_item_id,
            "activity_id": activity_id,
            "subject": course.name,
            "title": _clean(raw["title"]),
            "activity_type": activity_type,
            "url": url,
            "grade_display": grade_display,
            "grade_value": grade_value,
            "grade_min": grade_min,
            "grade_max": grade_max,
            "range_display": range_display,
            "percentage_display": _nullable_grade(raw["percentage"]),
            "percentage_value": _number(raw["percentage"]),
            "calculated_weight_display": _nullable_grade(raw["weight"]),
            "calculated_weight_percent": _number(raw["weight"]),
            "contribution_display": _nullable_grade(raw["contribution"]),
            "contribution_percent": _number(raw["contribution"]),
            "status": _nullable_grade(status_by_url.get(url)),
            "feedback": _nullable_grade(raw["feedback"]),
            "last_checked": checked,
        }
        old = connection.execute(
            "SELECT grade,status,first_seen FROM grade_items WHERE course_id=? AND grade_item_id=?",
            (course.course_id, grade_item_id),
        ).fetchone()
        first_seen = old["first_seen"] if old else checked
        if old and (old["grade"] != item["grade_display"] or old["status"] != item["status"]):
            connection.execute(
                "INSERT INTO grade_history(course_id,grade_item_id,old_grade,new_grade,old_status,new_status,changed_at) VALUES(?,?,?,?,?,?,?)",
                (course.course_id, grade_item_id, old["grade"], item["grade_display"], old["status"], item["status"], checked),
            )
        connection.execute(
            "INSERT INTO grade_items(course_id,grade_item_id,activity_id,course,title,activity_type,url,grade,grade_value,grade_min,grade_max,grade_range,percentage,percentage_value,calculated_weight,calculated_weight_percent,contribution,contribution_percent,status,feedback,first_seen,last_checked) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(course_id,grade_item_id) DO UPDATE SET activity_id=excluded.activity_id,course=excluded.course,title=excluded.title,activity_type=excluded.activity_type,url=excluded.url,grade=excluded.grade,grade_value=excluded.grade_value,grade_min=excluded.grade_min,grade_max=excluded.grade_max,grade_range=excluded.grade_range,percentage=excluded.percentage,percentage_value=excluded.percentage_value,calculated_weight=excluded.calculated_weight,calculated_weight_percent=excluded.calculated_weight_percent,contribution=excluded.contribution,contribution_percent=excluded.contribution_percent,status=excluded.status,feedback=excluded.feedback,last_checked=excluded.last_checked",
            (course.course_id, grade_item_id, activity_id, course.name, item["title"], activity_type, url, grade_display, grade_value, grade_min, grade_max, range_display, item["percentage_display"], item["percentage_value"], item["calculated_weight_display"], item["calculated_weight_percent"], item["contribution_display"], item["contribution_percent"], item["status"], item["feedback"], first_seen, checked),
        )
        items.append(item)
    connection.execute(
        "INSERT INTO course_grades(course_id,course,total_grade,total_grade_value,percentage,percentage_value,last_checked) VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(course_id) DO UPDATE SET course=excluded.course,total_grade=excluded.total_grade,total_grade_value=excluded.total_grade_value,percentage=excluded.percentage,percentage_value=excluded.percentage_value,last_checked=excluded.last_checked",
        (course.course_id, course.name, (course_total or {}).get("grade_display"), (course_total or {}).get("grade_value"), (course_total or {}).get("percentage_display"), (course_total or {}).get("percentage_value"), checked),
    )
    return {
        "course_id": course.course_id,
        "subject": course.name,
        "source_url": report_url,
        "total": course_total,
        "items": items,
        "last_checked": checked,
    }


def _minutes(value: str) -> tuple[float | None, float | None]:
    values = re.findall(r"\d+(?:\.\d+)?", value)
    return (
        float(values[0]) if values else None,
        float(values[1]) if len(values) > 1 else None,
    )


def collect_attendance(page: Page, course: Course, settings: Settings, connection: Any) -> dict[str, Any] | None:
    """Collect subject attendance summary and every paginated live-session row."""
    page.goto(course.url, wait_until="domcontentloaded", timeout=45_000)
    view_more = page.locator(".livesession-viewmore")
    try:
        view_more.wait_for(state="visible", timeout=30_000)
    except Exception:
        return None
    checked = utc_now()
    summary = {
        "total_sessions": int(_number(page.locator(".lss-leg--total .lss-leg-n").inner_text()) or 0),
        "attended_sessions": int(_number(page.locator(".lss-leg--attended .lss-leg-n").inner_text()) or 0),
        "missed_sessions": int(_number(page.locator(".lss-leg--missed .lss-leg-n").inner_text()) or 0),
        "attendance_percent": _number(page.locator(".lss-hero-pct").inner_text()),
    }
    view_more.click()
    dialog = page.locator("[role='dialog']:visible")
    dialog.wait_for(state="visible", timeout=15_000)
    page_buttons = dialog.locator(".lsd-page")
    page_count = page_buttons.count()
    sessions: list[dict[str, Any]] = []
    for page_number in range(1, max(page_count, 1) + 1):
        if page_count:
            button = dialog.locator(f".lsd-page[data-page='{page_number}']")
            if button.get_attribute("aria-current") != "page":
                button.click()
                page.wait_for_function(
                    "n => document.querySelector(`.lsd-page[data-page='${n}']`)?.getAttribute('aria-current') === 'page'",
                    arg=page_number,
                    timeout=15_000,
                )
        raw_rows = dialog.locator(".lsd-table tbody tr").evaluate_all(
            """rows => rows.map(row => ({
                title: row.querySelector('.lsd-name')?.innerText?.trim() || '',
                date: row.querySelector('.lsd-date')?.innerText?.trim() || '',
                minutes: row.querySelector('.lsd-num')?.innerText?.trim() || '',
                percentage: row.querySelector('.lsd-pctval')?.innerText?.trim() || '',
                status: row.querySelector('.lsd-pill')?.innerText?.trim() || ''
            }))"""
        )
        for raw in raw_rows:
            try:
                starts_at = datetime.strptime(raw["date"], "%Y-%m-%d %H:%M").replace(
                    tzinfo=settings.timezone
                ).isoformat()
            except ValueError:
                starts_at = raw["date"]
            attended_minutes, total_minutes = _minutes(raw["minutes"])
            status = _nullable_grade(raw["status"])
            method_match = re.search(r"\(([^)]+)\)", status or "")
            session_id = sha256(f"{course.course_id}|{starts_at}|{raw['title']}".encode())[:24]
            previous = connection.execute(
                "SELECT first_seen FROM attendance_sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            first_seen = previous["first_seen"] if previous else checked
            item = {
                "session_id": session_id,
                "course_id": course.course_id,
                "subject": course.name,
                "title": _clean(raw["title"]),
                "starts_at": starts_at,
                "attended_minutes": attended_minutes,
                "total_minutes": total_minutes,
                "attendance_percent": _number(raw["percentage"]),
                "status": status,
                "attendance_method": method_match.group(1) if method_match else None,
                "last_checked": checked,
            }
            connection.execute(
                "INSERT INTO attendance_sessions(session_id,course_id,course,title,starts_at,attended_minutes,total_minutes,attendance_percent,status,attendance_method,first_seen,last_checked) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET course=excluded.course,title=excluded.title,starts_at=excluded.starts_at,attended_minutes=excluded.attended_minutes,total_minutes=excluded.total_minutes,attendance_percent=excluded.attendance_percent,status=excluded.status,attendance_method=excluded.attendance_method,last_checked=excluded.last_checked",
                (session_id, course.course_id, course.name, item["title"], starts_at, attended_minutes, total_minutes, item["attendance_percent"], status, item["attendance_method"], first_seen, checked),
            )
            sessions.append(item)
    connection.execute(
        "INSERT INTO attendance_summaries(course_id,course,total_sessions,attended_sessions,missed_sessions,attendance_percent,last_checked) VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(course_id) DO UPDATE SET course=excluded.course,total_sessions=excluded.total_sessions,attended_sessions=excluded.attended_sessions,missed_sessions=excluded.missed_sessions,attendance_percent=excluded.attendance_percent,last_checked=excluded.last_checked",
        (course.course_id, course.name, summary["total_sessions"], summary["attended_sessions"], summary["missed_sessions"], summary["attendance_percent"], checked),
    )
    return {
        "course_id": course.course_id,
        "subject": course.name,
        **summary,
        "sessions": sessions,
        "last_checked": checked,
    }


def run_sync(
    settings: Settings,
    *,
    include_materials: bool = True,
    include_assignments: bool = True,
    include_grades: bool = True,
    include_attendance: bool = True,
) -> dict[str, Any]:
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    transcripts: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    assignments: list[dict[str, Any]] = []
    quizzes: list[dict[str, Any]] = []
    live_lectures: list[dict[str, str]] = []
    grades: list[dict[str, Any]] = []
    attendance: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    with connect(settings.output_dir / "state.sqlite") as connection, sync_playwright() as playwright:
        browser, context = new_context(playwright, settings, visible=not settings.headless)
        page = context.new_page()
        courses, activities = inventory(page, settings)
        live_lectures = collect_live_lectures(page, settings, connection)
        for course in courses:
            if include_grades:
                try:
                    grades.append(collect_grades(page, course, settings, connection))
                except Exception as exc:
                    errors.append({"course": course.name, "activity_id": "grades", "kind": "grades", "error": str(exc)[:500]})
                connection.commit()
            if include_attendance:
                try:
                    subject_attendance = collect_attendance(page, course, settings, connection)
                    if subject_attendance is not None:
                        attendance.append(subject_attendance)
                except Exception as exc:
                    errors.append({"course": course.name, "activity_id": "attendance", "kind": "attendance", "error": str(exc)[:500]})
                connection.commit()
            for activity in activities[course.course_id]:
                try:
                    if include_materials and activity.kind == "page":
                        transcripts.append(collect_transcript(page, context, course, activity, connection))
                    elif include_materials and activity.kind in {"resource", "folder"}:
                        notes.extend(collect_resource(page, context, course, activity, connection))
                    elif include_assignments and activity.kind == "assignment":
                        assignments.append(collect_assignment(page, context, course, activity, settings, connection))
                    elif include_assignments and activity.kind == "quiz":
                        quizzes.append(collect_quiz(page, course, activity, settings, connection))
                    elif include_materials and activity.kind == "external_url":
                        notes.append({"activity_id": activity.activity_id, "title": activity.title, "status": "external", "url": activity.url})
                except Exception as exc:
                    errors.append({"course": course.name, "activity_id": activity.activity_id, "kind": activity.kind, "error": str(exc)[:500]})
                connection.commit()
        save_auth(context, settings.auth_state_path)
        browser.close()
    reports = settings.output_dir / "reports"
    if include_materials:
        write_jsonl(reports / "transcripts.jsonl", transcripts)
        write_jsonl(reports / "notes.jsonl", notes)
    if include_assignments:
        atomic_json(reports / "deadlines.json", assignments + quizzes)
    if include_grades:
        atomic_json(reports / "grades.json", {"schema_version": 2, "courses": grades})
    if include_attendance:
        atomic_json(reports / "attendance.json", {"schema_version": 1, "subjects": attendance})
    summary = {
        "schema_version": 1,
        "completed_at": utc_now(),
        "transcripts": len(transcripts),
        "notes": len(notes),
        "assignments": len(assignments),
        "quizzes": len(quizzes),
        "live_lectures": len(live_lectures),
        "grade_courses": len(grades),
        "attendance_subjects": len(attendance),
        "errors": errors,
    }
    atomic_json(reports / "last-run.json", summary)
    return summary
