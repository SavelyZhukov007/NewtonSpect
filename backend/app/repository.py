from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import db_session
from .schemas import Artifact, JobOptions, PersonProfile, VideoReport


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass
class JobRecord:
    id: str
    original_filename: str
    input_video_path: str
    status: str
    progress: float
    current_step: str | None
    error_message: str | None
    options_json: str
    artifacts_json: str
    people_json: str
    report_json: str
    created_at: str
    updated_at: str


class JobRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def create_job(
        self, job_id: str, original_filename: str, input_video_path: str, options: JobOptions
    ) -> None:
        now = utc_now_iso()
        with db_session(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                  id, original_filename, input_video_path, status, progress, current_step, error_message,
                  options_json, artifacts_json, people_json, report_json, created_at, updated_at
                )
                VALUES (?, ?, ?, 'queued', 0, 'ingest', NULL, ?, '[]', '[]', '{}', ?, ?)
                """,
                (job_id, original_filename, input_video_path, options.model_dump_json(), now, now),
            )
            self.add_event(job_id, "ingest", "Job created and queued", 0, conn=conn)

    def add_event(
        self,
        job_id: str,
        step: str,
        message: str,
        progress: float,
        level: str = "info",
        conn: sqlite3.Connection | None = None,
    ) -> None:
        created_at = utc_now_iso()
        if conn is not None:
            conn.execute(
                """
                INSERT INTO task_events (job_id, step, message, level, progress, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (job_id, step, message, level, progress, created_at),
            )
            return
        with db_session(self.db_path) as local_conn:
            local_conn.execute(
                """
                INSERT INTO task_events (job_id, step, message, level, progress, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (job_id, step, message, level, progress, created_at),
            )

    def get_job(self, job_id: str) -> JobRecord | None:
        with db_session(self.db_path) as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return self._row_to_record(row) if row else None

    def update_job_status(
        self,
        job_id: str,
        *,
        status: str,
        progress: float,
        step: str,
        error_message: str | None = None,
    ) -> None:
        now = utc_now_iso()
        with db_session(self.db_path) as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, progress = ?, current_step = ?, error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, progress, step, error_message, now, job_id),
            )
            self.add_event(
                job_id,
                step,
                f"Status changed to {status}",
                progress,
                level="error" if status == "failed" else "info",
                conn=conn,
            )

    def set_progress(self, job_id: str, progress: float, step: str, message: str) -> None:
        now = utc_now_iso()
        with db_session(self.db_path) as conn:
            conn.execute(
                """
                UPDATE jobs SET progress = ?, current_step = ?, updated_at = ?
                WHERE id = ?
                """,
                (progress, step, now, job_id),
            )
            self.add_event(job_id, step, message, progress, conn=conn)

    def set_artifacts(self, job_id: str, artifacts: list[Artifact]) -> None:
        now = utc_now_iso()
        payload = json.dumps([item.model_dump(mode="json") for item in artifacts], ensure_ascii=False)
        with db_session(self.db_path) as conn:
            conn.execute(
                "UPDATE jobs SET artifacts_json = ?, updated_at = ? WHERE id = ?",
                (payload, now, job_id),
            )

    def append_artifact(self, job_id: str, artifact: Artifact) -> None:
        job = self.get_job(job_id)
        if not job:
            return
        artifacts = self._decode_artifacts(job.artifacts_json)
        artifacts.append(artifact)
        self.set_artifacts(job_id, artifacts)

    def set_people(self, job_id: str, people: list[PersonProfile]) -> None:
        now = utc_now_iso()
        payload = json.dumps([item.model_dump(mode="json") for item in people], ensure_ascii=False)
        with db_session(self.db_path) as conn:
            conn.execute(
                "UPDATE jobs SET people_json = ?, updated_at = ? WHERE id = ?",
                (payload, now, job_id),
            )

    def set_report(self, job_id: str, report: VideoReport) -> None:
        now = utc_now_iso()
        payload = report.model_dump_json()
        with db_session(self.db_path) as conn:
            conn.execute(
                "UPDATE jobs SET report_json = ?, updated_at = ? WHERE id = ?",
                (payload, now, job_id),
            )

    def claim_next_job(self, worker_id: str) -> JobRecord | None:
        with db_session(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'queued'
                ORDER BY created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            now = utc_now_iso()
            conn.execute(
                """
                UPDATE jobs
                SET status = 'running', current_step = 'ingest', worker_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (worker_id, now, row["id"]),
            )
            self.add_event(row["id"], "ingest", f"Claimed by worker {worker_id}", 0.0, conn=conn)
            updated = conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
            return self._row_to_record(updated)

    def requeue_stuck_running_jobs(self, older_than_seconds: int) -> int:
        now_dt = datetime.now(tz=timezone.utc)
        reclaimed = 0
        with db_session(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, updated_at FROM jobs WHERE status = 'running'"
            ).fetchall()
            for row in rows:
                updated_at = datetime.fromisoformat(row["updated_at"])
                age = (now_dt - updated_at).total_seconds()
                if age > older_than_seconds:
                    conn.execute(
                        """
                        UPDATE jobs
                        SET status = 'queued', current_step = 'ingest', worker_id = NULL, updated_at = ?
                        WHERE id = ?
                        """,
                        (utc_now_iso(), row["id"]),
                    )
                    self.add_event(
                        row["id"],
                        "ingest",
                        "Re-queued after worker timeout",
                        0.0,
                        level="warning",
                        conn=conn,
                    )
                    reclaimed += 1
        return reclaimed

    def decode_options(self, options_json: str) -> JobOptions:
        return JobOptions.model_validate_json(options_json)

    def decode_artifacts(self, artifacts_json: str) -> list[Artifact]:
        return self._decode_artifacts(artifacts_json)

    def decode_people(self, people_json: str) -> list[PersonProfile]:
        data = json.loads(people_json or "[]")
        return [PersonProfile.model_validate(item) for item in data]

    def decode_report(self, report_json: str) -> VideoReport:
        payload = report_json or "{}"
        parsed: dict[str, Any] = json.loads(payload)
        return VideoReport.model_validate(parsed)

    @staticmethod
    def _decode_artifacts(artifacts_json: str) -> list[Artifact]:
        data = json.loads(artifacts_json or "[]")
        return [Artifact.model_validate(item) for item in data]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            id=row["id"],
            original_filename=row["original_filename"],
            input_video_path=row["input_video_path"],
            status=row["status"],
            progress=float(row["progress"]),
            current_step=row["current_step"],
            error_message=row["error_message"],
            options_json=row["options_json"],
            artifacts_json=row["artifacts_json"],
            people_json=row["people_json"],
            report_json=row["report_json"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

