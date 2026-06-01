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
                source_fingerprint TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                progress REAL NOT NULL DEFAULT 0,
                current_step TEXT,
                error_message TEXT,
                options_json TEXT NOT NULL,
                artifacts_json TEXT NOT NULL DEFAULT '[]',
                people_json TEXT NOT NULL DEFAULT '[]',
                report_json TEXT NOT NULL DEFAULT '{}',
                runtime_json TEXT NOT NULL DEFAULT '{}',
                created_by_device TEXT NOT NULL DEFAULT 'Unknown device',
                locale TEXT NOT NULL DEFAULT 'en',
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

            CREATE TABLE IF NOT EXISTS job_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL UNIQUE,
                source_fingerprint TEXT NOT NULL,
                previous_job_id TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS subtitle_segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                segment_order INTEGER NOT NULL,
                start REAL NOT NULL,
                end REAL NOT NULL,
                text TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0,
                speaker_ref TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS subtitle_revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                editor_device TEXT NOT NULL,
                note TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS chapters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                chapter_id TEXT NOT NULL,
                title TEXT NOT NULL,
                start REAL NOT NULL,
                "end" REAL NOT NULL,
                confidence REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS key_quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                quote_id TEXT NOT NULL,
                start REAL NOT NULL,
                "end" REAL NOT NULL,
                text TEXT NOT NULL,
                score REAL NOT NULL,
                speaker_ref TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS quality_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS person_registry (
                registry_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                aliases_json TEXT NOT NULL DEFAULT '[]',
                portrait_path TEXT,
                linked_job_ids_json TEXT NOT NULL DEFAULT '[]',
                confidence REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS glossary_terms (
                term_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                locale TEXT NOT NULL DEFAULT 'global',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS kb_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                checksum TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS kb_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                chunk_order INTEGER NOT NULL,
                text TEXT NOT NULL,
                token_count INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (document_id) REFERENCES kb_documents(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS kb_embeddings_meta (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chunk_id INTEGER NOT NULL UNIQUE,
                hash TEXT NOT NULL,
                vector_dim INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (chunk_id) REFERENCES kb_chunks(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS shorts_exports (
                short_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                label TEXT NOT NULL,
                path TEXT NOT NULL,
                start REAL NOT NULL,
                "end" REAL NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS translations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                language TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(job_id, language),
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS fact_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );
            """
        )
        _ensure_jobs_columns(conn)


def _ensure_jobs_columns(conn: sqlite3.Connection) -> None:
    existing = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
    }

    if "runtime_json" not in existing:
        conn.execute("ALTER TABLE jobs ADD COLUMN runtime_json TEXT NOT NULL DEFAULT '{}'")
    if "created_by_device" not in existing:
        conn.execute(
            "ALTER TABLE jobs ADD COLUMN created_by_device TEXT NOT NULL DEFAULT 'Unknown device'"
        )
    if "locale" not in existing:
        conn.execute("ALTER TABLE jobs ADD COLUMN locale TEXT NOT NULL DEFAULT 'en'")
    if "source_fingerprint" not in existing:
        conn.execute("ALTER TABLE jobs ADD COLUMN source_fingerprint TEXT NOT NULL DEFAULT ''")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_source_fp ON jobs(source_fingerprint, created_at)"
    )
