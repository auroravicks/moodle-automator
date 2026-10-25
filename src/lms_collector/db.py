from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS assignments (
  activity_id TEXT PRIMARY KEY,
  course TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT NOT NULL,
  opens_at TEXT,
  due_at TEXT,
  cutoff_at TEXT,
  submission_status TEXT,
  first_seen TEXT NOT NULL,
  last_checked TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deadline_history (
  id INTEGER PRIMARY KEY,
  activity_id TEXT NOT NULL REFERENCES assignments(activity_id),
  old_due_at TEXT,
  new_due_at TEXT,
  changed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS resources (
  source_url TEXT PRIMARY KEY,
  course TEXT NOT NULL,
  title TEXT NOT NULL,
  content_hash TEXT,
  local_path TEXT,
  first_seen TEXT NOT NULL,
  last_checked TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recordings (
  source_url TEXT PRIMARY KEY,
  course TEXT NOT NULL,
  title TEXT NOT NULL,
  recording_date TEXT,
  caption_hash TEXT,
  caption_status TEXT NOT NULL,
  local_path TEXT,
  first_seen TEXT NOT NULL,
  last_checked TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS live_lectures (
  event_id TEXT PRIMARY KEY,
  course TEXT NOT NULL,
  starts_at TEXT NOT NULL,
  url TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  last_checked TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alert_deliveries (
  dedupe_key TEXT PRIMARY KEY,
  channel TEXT NOT NULL,
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_attempt TEXT,
  error TEXT
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection
