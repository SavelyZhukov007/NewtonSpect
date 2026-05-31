from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def db_session(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path) -> None:
    with db_session(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                original_filename TEXT NOT NULL,
                input_video_path TEXT NOT NULL,
                status TEXT NOT NULL,
                progress REAL NOT NULL DEFAULT 0,
                current_step TEXT,
                error_message TEXT,
                options_json TEXT NOT NULL,
                artifacts_json TEXT NOT NULL DEFAULT '[]',
                people_json TEXT NOT NULL DEFAULT '[]',
                report_json TEXT NOT NULL DEFAULT '{}',
                worker_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                step TEXT NOT NULL,
                message TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'info',
                progress REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_task_events_job_created ON task_events(job_id, created_at);
            """
        )

