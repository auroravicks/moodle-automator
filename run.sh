#!/usr/bin/env sh
set -eu

discover=false
deadlines_only=false
for argument in "$@"; do
    case "$argument" in
        --discover) discover=true ;;
        --deadlines-only) deadlines_only=true ;;
        *) echo "Unknown argument: $argument" >&2; exit 2 ;;
    esac
done

root="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$root"

if [ -n "${PYTHON:-}" ]; then
    python="$PYTHON"
elif command -v python3 >/dev/null 2>&1; then
    python=python3
else
    python=python
fi

venv_python="$root/.venv/bin/python"
collector="$root/.venv/bin/moodle-lms-automation"

echo '[1/7] Preparing the local environment...'
if [ ! -x "$venv_python" ]; then
    "$python" -m venv .venv
fi
if [ ! -x "$collector" ]; then
    "$venv_python" -m pip install -e .
    "$venv_python" -m playwright install chromium
fi
[ -f .env ] || cp .env.example .env
[ -f config.yml ] || cp config.example.yml config.yml

echo '[2/7] Checking configuration...'
"$collector" doctor

echo '[3/7] Checking LMS authentication...'
if ! "$collector" auth-check; then
    echo 'Authentication is missing or expired; stopping without opening a browser.' >&2
    "$collector" notify-auth-required
    exit 1
fi

if [ "$discover" = true ]; then
    echo '[4/7] Refreshing LMS structure discovery...'
    "$collector" discover
else
    echo '[4/7] Structure discovery not requested.'
fi

if [ "$deadlines_only" = true ]; then
    echo '[5/7] Refreshing assignments and live lectures...'
    "$collector" deadlines
else
    echo '[5/7] Synchronizing transcripts, captions, notes, assignments, and live lectures...'
    "$collector" sync
fi

echo '[6/7] Sending the configured Moodle digest...'
"$collector" notify-deadlines

echo '[7/7] Refreshing the clean Resources export...'
"$collector" export-resources
echo 'Everything completed successfully.'
