from __future__ import annotations

import shutil
import logging
from pathlib import Path

import click
from playwright.sync_api import sync_playwright

from .browser import new_context, save_auth
from .config import Settings
from .collectors import run_sync
from .db import connect
from .discovery import crawl_courses, observe_course_links, write_discovery_report
from .exporter import export_readable
from .notifications import send_auth_required, send_deadline_digest
from .logging_setup import configure_logging

LOGGER = logging.getLogger(__name__)


def settings() -> Settings:
    try:
        config = Settings.load()
        configure_logging(config)
        return config
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@click.group()
def cli() -> None:
    """Collect local materials and deadlines from a Moodle LMS."""


@cli.command()
def login() -> None:
    """Open a visible, isolated browser and save the local authenticated session."""
    config = settings()
    with sync_playwright() as playwright:
        browser, context = new_context(playwright, config, visible=True)
        page = context.new_page()
        page.goto(f"{config.base_url}/login/index.php", wait_until="domcontentloaded")
        if config.username and config.password:
            page.locator('input[name="username"]').fill(config.username)
            page.locator('input[name="password"]').fill(config.password)
            page.get_by_role("button", name="Sign in").click()
        else:
            click.echo("Sign in in the browser. Credentials are not read by this process.")
        click.prompt("Press Enter here after the dashboard has loaded", default="", show_default=False)
        page.goto(f"{config.base_url}/my/", wait_until="domcontentloaded")
        login_form_visible = page.locator('input[name="username"]').is_visible()
        if "/login/" in page.url or login_form_visible:
            browser.close()
            raise click.ClickException("Login still appears active; authentication state was not saved.")
        save_auth(context, config.auth_state_path)
        browser.close()
    click.echo(f"Saved private authentication state to {config.auth_state_path}")


@cli.command()
def discover() -> None:
    """Perform a conservative visible pass and record candidate LMS links."""
    config = settings()
    if not config.auth_state_path.exists():
        raise click.ClickException("No authentication state. Run: moodle-lms-automation login")
    with sync_playwright() as playwright:
        browser, context = new_context(playwright, config, visible=True)
        page = context.new_page()
        links = observe_course_links(page, config)
        if "/login/" in page.url:
            browser.close()
            raise click.ClickException("Authentication expired. Run login again.")
        courses, activities = crawl_courses(page, links)
        write_discovery_report(config, links, courses, activities)
        browser.close()
    click.echo(
        f"Recorded {len(links)} courses and {len(activities)} activity detail observations in "
        f"{config.output_dir / 'reports' / 'discovery.json'}"
    )


@cli.command(name="doctor")
def doctor() -> None:
    """Validate local configuration and initialize the database schema."""
    config = settings()
    config.output_dir.mkdir(parents=True, exist_ok=True)
    with connect(config.output_dir / "state.sqlite") as connection:
        connection.execute("SELECT 1")
    click.echo(f"Configuration OK; timezone={config.timezone.key}; output={config.output_dir}")
    click.echo("Authentication state: " + ("present" if config.auth_state_path.exists() else "missing (run login)"))


@cli.command(name="clear-auth")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def clear_auth(yes: bool) -> None:
    """Delete the storage-state mirror; retain the dedicated Chrome profile."""
    config = settings()
    target = config.auth_state_path
    if not target.exists():
        click.echo("No saved authentication state found.")
        return
    if not yes and not click.confirm(f"Delete {target}?"):
        return
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    click.echo("Storage-state mirror removed. The dedicated Chrome profile was retained for safety.")


@cli.command(name="auth-check")
def auth_check() -> None:
    """Verify that the saved LMS session still reaches the dashboard."""
    config = settings()
    if not config.auth_state_path.exists():
        LOGGER.warning("Saved Moodle authentication state is missing")
        raise click.ClickException("No saved authentication state.")
    with sync_playwright() as playwright:
        browser, context = new_context(playwright, config, visible=False)
        page = context.new_page()
        page.goto(f"{config.base_url}/my/", wait_until="domcontentloaded", timeout=45_000)
        authenticated = "/login/" not in page.url and not page.locator('input[name="username"]').is_visible()
        if authenticated:
            save_auth(context, config.auth_state_path)
        browser.close()
    if not authenticated:
        LOGGER.warning("Saved Moodle authentication has expired")
        raise click.ClickException("Saved authentication has expired.")
    click.echo("Authentication session is valid.")


@cli.command(name="notify-auth-required")
def notify_auth_required() -> None:
    """Send a Google Chat alert that manual Moodle login is required."""
    try:
        result = send_auth_required(settings())
    except (OSError, RuntimeError, ValueError) as exc:
        LOGGER.exception("Login-required notification failed")
        raise click.ClickException(str(exc)) from exc
    suffix = f" Message: {result['message_name']}" if result.get("message_name") else ""
    click.echo(f"Login-required alert delivered.{suffix}")


@cli.command()
def sync() -> None:
    """Collect lecture transcripts, notes, and assignment state."""
    config = settings()
    if not config.auth_state_path.exists():
        raise click.ClickException("No authentication state. Run: moodle-lms-automation login")
    LOGGER.info("Starting full LMS synchronization")
    result = run_sync(config)
    LOGGER.info(
        "Synchronization complete: transcripts=%s notes=%s assignments=%s live_lectures=%s errors=%s",
        result["transcripts"], result["notes"], result["assignments"], result["live_lectures"], len(result["errors"]),
    )
    click.echo(
        f"Collected {result['transcripts']} transcript records, {result['notes']} note records, "
        f"{result['assignments']} assignments, and {result['live_lectures']} upcoming live lectures; "
        f"errors={len(result['errors'])}."
    )


@cli.command()
def deadlines() -> None:
    """Refresh assignment dates and submission states only."""
    config = settings()
    if not config.auth_state_path.exists():
        raise click.ClickException("No authentication state. Run: moodle-lms-automation login")
    LOGGER.info("Starting assignment status refresh")
    result = run_sync(config, include_materials=False, include_assignments=True)
    LOGGER.info("Assignment refresh complete: assignments=%s errors=%s", result["assignments"], len(result["errors"]))
    click.echo(f"Collected {result['assignments']} assignments; errors={len(result['errors'])}.")


@cli.command(name="export-resources")
def export_resources() -> None:
    """Export a metadata-free, human-readable course view."""
    result = export_readable(settings())
    click.echo(
        f"Exported {result['courses']} courses, {result['transcripts']} transcripts, "
        f"{result['captions']} captions, "
        f"{result['notes']} notes, and {result['assignments']} assignments to {result['destination']}."
    )


@cli.command(name="notify-deadlines")
@click.option("--force", is_flag=True, help="Resend even when the current digest was delivered successfully.")
def notify_deadlines(force: bool) -> None:
    """Send a deduplicated Google Chat digest for actionable deadlines."""
    try:
        result = send_deadline_digest(settings(), force=force)
    except (OSError, RuntimeError, ValueError) as exc:
        LOGGER.exception("Deadline notification failed")
        raise click.ClickException(str(exc)) from exc
    if result["deduplicated"]:
        click.echo(f"Moodle digest unchanged; already delivered ({result['items']} items).")
    else:
        suffix = f" Message: {result['message_name']}" if result.get("message_name") else ""
        click.echo(f"Moodle digest delivered ({result['items']} items).{suffix}")


if __name__ == "__main__":
    cli()
