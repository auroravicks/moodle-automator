from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

from playwright.sync_api import Page

from .config import Settings


@dataclass(frozen=True)
class LinkObservation:
    kind: str
    text: str
    url: str


def observe_course_links(page: Page, settings: Settings) -> list[LinkObservation]:
    """Collect candidate links without claiming their page structures are verified."""
    page.goto(f"{settings.base_url}/my/courses.php", wait_until="domcontentloaded")
    observations: dict[str, LinkObservation] = {}
    patterns = {
        "/course/view.php": "course",
        "/course/section.php": "section",
        "/mod/resource/view.php": "resource",
        "/mod/folder/view.php": "folder",
        "/mod/assign/view.php": "assignment",
        "/mod/page/view.php": "page",
    }
    for anchor in page.locator("a[href]").all():
        href = anchor.get_attribute("href")
        if not href:
            continue
        absolute = urljoin(page.url, href)
        kind = next((value for fragment, value in patterns.items() if fragment in absolute), None)
        if kind:
            observations[absolute] = LinkObservation(kind, anchor.inner_text().strip(), absolute)
    return sorted(observations.values(), key=lambda item: (item.kind, item.url))


def _text(locator: Any, limit: int = 1200) -> str | None:
    if locator.count() == 0:
        return None
    value = " ".join(locator.first.inner_text().split())
    return value[:limit] or None


def _activity_id(url: str) -> str | None:
    match = re.search(r"[?&]id=(\d+)", url)
    return match.group(1) if match else None


def inspect_activity(page: Page, observation: LinkObservation) -> dict[str, Any]:
    page.goto(observation.url, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(500)
    details: dict[str, Any] = {
        **asdict(observation),
        "activity_id": _activity_id(observation.url),
        "final_url": page.url,
        "page_title": page.title(),
        "heading": _text(page.locator("h1")),
        "time_elements": page.locator("time[datetime]").evaluate_all(
            "els => els.map(e => ({datetime: e.getAttribute('datetime'), text: e.innerText.trim()}))"
        ),
        "tables": [],
        "iframes": [],
        "download_links": [],
    }
    for table in page.locator("table").all()[:8]:
        details["tables"].append(
            {
                "classes": table.get_attribute("class"),
                "text": " ".join(table.inner_text().split())[:3000],
            }
        )
    for link in page.locator("a[href]").all():
        href = link.get_attribute("href") or ""
        download = link.get_attribute("download")
        if download is not None or any(token in href for token in ("pluginfile.php", "forcedownload=1")):
            details["download_links"].append(
                {"text": " ".join(link.inner_text().split())[:300], "url": urljoin(page.url, href), "download": download}
            )
    for iframe in page.locator("iframe").all():
        src = iframe.get_attribute("src")
        frame_info: dict[str, Any] = {
            "id": iframe.get_attribute("id"),
            "title": iframe.get_attribute("title"),
            "src": urljoin(page.url, src) if src else None,
            "tracks": [],
        }
        handle = iframe.element_handle()
        frame = handle.content_frame() if handle else None
        if frame:
            try:
                frame_info["tracks"] = frame.locator("track").evaluate_all(
                    "els => els.map(e => ({kind:e.kind, label:e.label, srclang:e.srclang, src:e.src}))"
                )
            except Exception as exc:  # Cross-origin/player timing failures are observations too.
                frame_info["inspection_error"] = type(exc).__name__
        details["iframes"].append(frame_info)
    details["selectors_present"] = {
        selector: page.locator(selector).count()
        for selector in (
            ".submissionstatustable",
            ".generaltable",
            "[data-region='activity-dates']",
            "[data-region='completion-info']",
            ".activity-description",
            ".foldertree",
            ".resourceworkaround",
        )
    }
    return details


def crawl_courses(page: Page, courses: list[LinkObservation]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    course_reports: list[dict[str, Any]] = []
    activities: dict[str, LinkObservation] = {}
    patterns = {
        "/mod/resource/view.php": "resource",
        "/mod/folder/view.php": "folder",
        "/mod/assign/view.php": "assignment",
        "/mod/page/view.php": "page",
        "/mod/url/view.php": "external_url",
    }
    for course in courses:
        page.goto(course.url, wait_until="domcontentloaded", timeout=45_000)
        found: list[LinkObservation] = []
        for anchor in page.locator("a[href]").all():
            href = anchor.get_attribute("href")
            if not href:
                continue
            absolute = urljoin(page.url, href)
            kind = next((value for fragment, value in patterns.items() if fragment in absolute), None)
            if not kind:
                continue
            item = LinkObservation(kind, " ".join(anchor.inner_text().split())[:500], absolute)
            activities[absolute] = item
            found.append(item)
        course_reports.append(
            {
                "url": course.url,
                "heading": _text(page.locator("h1")),
                "activities": [asdict(item) for item in sorted(found, key=lambda value: value.url)],
            }
        )
    detail_reports: list[dict[str, Any]] = []
    for activity in sorted(activities.values(), key=lambda value: (value.kind, value.url)):
        try:
            detail_reports.append(inspect_activity(page, activity))
        except Exception as exc:
            detail_reports.append({**asdict(activity), "error": f"{type(exc).__name__}: {exc}"[:1000]})
    return course_reports, detail_reports


def write_discovery_report(
    settings: Settings,
    links: list[LinkObservation],
    courses: list[dict[str, Any]],
    activities: list[dict[str, Any]],
) -> None:
    report_dir = settings.output_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "observed_at": datetime.now(UTC).isoformat(),
        "source": f"{settings.base_url}/my/courses.php",
        "links": [asdict(link) for link in links],
        "courses": courses,
        "activity_details": activities,
        "warning": "Read-only DOM observations. Extraction logic is not yet a claim that every LMS variant is supported.",
    }
    target = report_dir / "discovery.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(target)
