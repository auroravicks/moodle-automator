# moodle-lms-automation

Local, Windows-first Moodle discovery tooling. Structured, non-secret behavior is configured only in ignored `config.yml`; credentials and webhook URLs are configured only in ignored `.env`. Copy the corresponding `.example` files when setting up a new machine. There is no override layer between them.

## Setup (PowerShell)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
playwright install chromium
Copy-Item .env.example .env
```

Edit `.env` locally. It is ignored by Git. Leaving `LMS_USERNAME` and `LMS_PASSWORD` blank is recommended: `login` will let you enter credentials directly in the isolated browser, so the collector never reads them.

```powershell
moodle-lms-automation doctor
moodle-lms-automation login
moodle-lms-automation discover
moodle-lms-automation sync
```

`discover` writes the locally observed DOM inventory to `class-notes/reports/discovery.json`. `sync` collects transcripts, notes, and assignments. `deadlines` refreshes assignments only.

## Output layout

Each subject keeps transcripts and notes beside each other:

```text
class-notes/
  state.sqlite
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
```

`transcripts.jsonl` is the batch-processing manifest intended for future summarization. Every record contains a schema version, subject/course identity, stable LMS activity ID, source URL, caption status/language, content hash, and paths to the original VTT and normalized timestamped text. A summarizer can group records by `subject` and write its results elsewhere without changing source material.

Notes are content-addressed under `versions/`, so changed files create a new version rather than overwriting an earlier one.

## Clean resources export

`RESOURCES_DIR` in `.env` selects a separate, code-independent export. Generate it with:

```powershell
moodle-lms-automation export-resources
```

Each course contains only `Transcripts/*.txt`, `Captions/*.vtt`, original files under `Notes/`, and a readable `Assignments.md`. Internal metadata, hashes, SQLite state, and version-directory layers remain under `class-notes` and are not copied into this view.

## One-command workflow

From the project root, run:

```powershell
.\run.ps1
```

The script creates the virtual environment and installs dependencies when needed, creates `.env` from the example when missing, validates the saved LMS session, opens the local login browser only when authentication is missing or expired, synchronizes the LMS, and refreshes the clean `Resources` export. Add `-Discover` to refresh the structural inventory, or `-DeadlinesOnly` for a lighter assignment refresh followed by export.

Application information and errors are written to `class-notes/logs/moodle-lms-automation.log`. Rotation size and retained backups are configured under `logging` in `config.yml`. Secrets are never written to the log.

Google Chat sends a fresh Moodle digest on every pipeline run by default. It includes actionable deadlines plus live lectures in the configured upcoming window, with direct Join links. Set `notifications.google_chat.deduplicate: true` in `config.yml` only if unchanged successful digests should be suppressed.

The browser uses the dedicated persistent Chrome profile configured by `browser.profile_dir`. Scheduled runs are headless and never open or wait at a password popup. If the saved Moodle session is missing or expired, the pipeline logs the failure, sends a Google Chat login-required alert, exits nonzero, and does not start Knowledge. Restore access explicitly with `moodle-lms-automation login`, which always opens the isolated profile visibly, then rerun the base orchestrator. Do not open the same profile in another Chrome process while automation is running.

The saved Playwright state, SQLite database, `.env`, and downloaded private course content are ignored by Git. Remove the tool's session with:

```powershell
moodle-lms-automation clear-auth
```

Normal visits may cause Moodle to mark activities as viewed or complete. Monitoring only runs while the Windows machine is on and has network access. External notifications are intentionally not enabled until a provider is selected and an explicit live-message test is requested.
