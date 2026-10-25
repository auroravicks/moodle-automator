from __future__ import annotations

from pathlib import Path

import json

from playwright.sync_api import Browser, BrowserContext, Playwright

from .config import Settings


def new_context(
    playwright: Playwright, settings: Settings, *, visible: bool
) -> tuple[Browser | BrowserContext, BrowserContext]:
    if settings.browser_profile_dir:
        settings.browser_profile_dir.mkdir(parents=True, exist_ok=True)
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(settings.browser_profile_dir),
            channel=settings.browser_channel,
            headless=not visible,
            accept_downloads=True,
        )
        # MoodleSession is a session cookie, so Chrome discards it at shutdown.
        # Rehydrate the latest private cookie mirror on every unattended launch.
        if settings.auth_state_path.exists():
            state = json.loads(settings.auth_state_path.read_text(encoding="utf-8"))
            cookies = state.get("cookies", [])
            if cookies:
                context.add_cookies(cookies)
        return context, context
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
