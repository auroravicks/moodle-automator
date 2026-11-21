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
CREATE TABLE IF NOT EXISTS grade_items (
  course_id TEXT NOT NULL,
  grade_item_id TEXT NOT NULL,
  activity_id TEXT,
  course TEXT NOT NULL,
  title TEXT NOT NULL,
  activity_type TEXT,
  url TEXT,
  grade TEXT,
  grade_value REAL,
  grade_min REAL,
  grade_max REAL,
  grade_range TEXT,
  percentage TEXT,
  percentage_value REAL,
  calculated_weight TEXT,
  calculated_weight_percent REAL,
  contribution TEXT,
  contribution_percent REAL,
  status TEXT,
  feedback TEXT,
  first_seen TEXT NOT NULL,
  last_checked TEXT NOT NULL,
  PRIMARY KEY (course_id, grade_item_id)
);
CREATE TABLE IF NOT EXISTS grade_history (
  id INTEGER PRIMARY KEY,
  course_id TEXT NOT NULL,
  grade_item_id TEXT NOT NULL,
  old_grade TEXT,
  new_grade TEXT,
  old_status TEXT,
  new_status TEXT,
  changed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS course_grades (
  course_id TEXT PRIMARY KEY,
  course TEXT NOT NULL,
  total_grade TEXT,
  total_grade_value REAL,
  percentage TEXT,
  percentage_value REAL,
  last_checked TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attendance_summaries (
  course_id TEXT PRIMARY KEY,
  course TEXT NOT NULL,
  total_sessions INTEGER NOT NULL,
  attended_sessions INTEGER NOT NULL,
  missed_sessions INTEGER NOT NULL,
  attendance_percent REAL,
  last_checked TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attendance_sessions (
  session_id TEXT PRIMARY KEY,
  course_id TEXT NOT NULL,
  course TEXT NOT NULL,
  title TEXT NOT NULL,
  starts_at TEXT NOT NULL,
  attended_minutes REAL,
  total_minutes REAL,
  attendance_percent REAL,
  status TEXT,
  attendance_method TEXT,
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
    columns = {row[1] for row in connection.execute("PRAGMA table_info(grade_items)")}
    for name in (
        "grade_value REAL",
        "grade_min REAL",
        "grade_max REAL",
        "percentage_value REAL",
        "calculated_weight_percent REAL",
        "contribution_percent REAL",
    ):
        if name.split()[0] not in columns:
            connection.execute(f"ALTER TABLE grade_items ADD COLUMN {name}")
    course_columns = {row[1] for row in connection.execute("PRAGMA table_info(course_grades)")}
    for name in ("total_grade_value REAL", "percentage_value REAL"):
        if name.split()[0] not in course_columns:
            connection.execute(f"ALTER TABLE course_grades ADD COLUMN {name}")
    return connection
