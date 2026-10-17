from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Playwright

from .config import Settings


def new_context(playwright: Playwright, settings: Settings, *, visible: bool) -> tuple[Browser, BrowserContext]:
    browser = playwright.chromium.launch(headless=not visible)
    options: dict[str, object] = {"accept_downloads": True}
    if settings.auth_state_path.exists():
        options["storage_state"] = str(settings.auth_state_path)
    return browser, browser.new_context(**options)


def save_auth(context: BrowserContext, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    context.storage_state(path=str(temporary))
    temporary.replace(destination)

