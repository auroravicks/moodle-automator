# moodle-lms-automation

A cross-platform Moodle collector for lecture transcripts, captions, notes, assignments, live-lecture reminders, and clean local exports. It runs on Windows, Linux, and macOS with Python 3.11 or newer.

Behavior is configured in the ignored `config.yml`. Credentials and webhook URLs belong only in the ignored `.env`. Neither file overrides the other.

## Requirements

- Python 3.11+
- A Chromium-compatible environment supported by Playwright
- Network access to the target Moodle site
- Google Chat webhook access only when notifications are enabled

Linux hosts may need Playwright's system packages during initial setup:

```bash
python3 -m playwright install --with-deps chromium
```

## Quick start

### Linux and macOS (`sh`, `bash`, or `zsh`)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m playwright install chromium
cp .env.example .env
cp config.example.yml config.yml

moodle-lms-automation doctor
moodle-lms-automation login
sh run.sh
```

The runner is POSIX-shell compatible: use `sh run.sh`, `bash run.sh`, or `zsh run.sh`.

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m playwright install chromium
Copy-Item .env.example .env
Copy-Item config.example.yml config.yml

moodle-lms-automation doctor
moodle-lms-automation login
.\run.ps1
```

Both runners expose the same modes; only the host shell's argument spelling differs:

| Mode | POSIX shell | PowerShell |
| --- | --- | --- |
| Normal synchronization | `sh run.sh` | `.\run.ps1` |
| Refresh structure discovery first | `sh run.sh --discover` | `.\run.ps1 -Discover` |
| Refresh deadlines and live lectures only | `sh run.sh --deadlines-only` | `.\run.ps1 -DeadlinesOnly` |
| Open interactive login when needed | `sh run.sh --login-if-needed` | `.\run.ps1 -LoginIfNeeded` |

## Authentication and unattended runs

`login` always opens a visible, isolated persistent browser profile. Unattended workflows are headless and never wait at a password prompt. If the Moodle session is missing or expired, the default runner logs the failure, optionally sends a Google Chat login-required alert, exits nonzero, and skips the remaining collection steps. For a manual run, opt into an interactive recovery with `--login-if-needed` or `-LoginIfNeeded`; after login, the runner verifies the new session before continuing.

The example configuration uses Playwright's bundled Chromium (`browser.channel: null`) for portability. Set a branded channel such as `chrome` or `msedge` only when it is installed on the host. Do not open the configured profile in another browser process while collection is running.

The saved Playwright state, persistent browser profile, SQLite database, `.env`, `config.yml`, logs, and downloaded private content are ignored by Git.

## Commands

```text
moodle-lms-automation doctor
moodle-lms-automation login
moodle-lms-automation auth-check
moodle-lms-automation discover
moodle-lms-automation sync
moodle-lms-automation deadlines
moodle-lms-automation notify-deadlines
moodle-lms-automation export-resources
moodle-lms-automation clear-auth
```

`discover` records observed Moodle structure. `sync` collects materials, assignments, and upcoming live lectures. `deadlines` performs the lighter assignment/calendar refresh. `export-resources` produces a human-readable, metadata-free view at `storage.resources_dir`.

## Output layout

```text
class-notes/
  state.sqlite
  browser-profile/
  courses/
    <subject>--<course-id>/
      course.json
      transcripts/
        <lecture>--<activity-id>/
          captions.vtt
          transcript.txt
          metadata.json
      notes/
        <resource>--<activity-id>/
          metadata-1.json
          versions/
            <content-hash>--<original-filename>
  reports/
    transcripts.jsonl
    notes.jsonl
    deadlines.json
    last-run.json
  logs/
```

`transcripts.jsonl` is suitable for later batch summarization. Notes are content-addressed, so changed files create new versions rather than overwriting earlier material.

## Notifications

Google Chat notifications are optional. Enable them under `notifications.google_chat` in `config.yml`, then set `GOOGLE_CHAT_WEBHOOK_URL` in `.env`. The digest includes actionable deadlines and live lectures within the configured window. Both missed-work sections appear at the bottom, with recent misses before older misses; the boundary is configured with `deadlines.recent_missed_days`. Course exclusions and deduplication remain non-secret YAML settings.

When notifications are disabled, collection and export continue normally without requiring a webhook.

## Operational notes

- Application logs rotate under the configured internal directory.
- Secrets are never intentionally written to logs.
- LMS visits may mark activities as viewed or complete.
- Selectors target the Moodle variant this project was developed against; use `discover` when adapting it to another deployment.
- `clear-auth` removes the portable storage-state mirror. Delete a persistent browser profile manually only after verifying its configured path and accepting that saved browser data will be lost.
