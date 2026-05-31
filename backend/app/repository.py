from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import db_session
from .schemas import Artifact, JobOptions, JobRuntime, PersonProfile, StageRuntime, VideoReport


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass
class JobRecord:
    id: str
    original_filename: str
    input_video_path: str
    created_by_device: str
    locale: str
    status: str
    progress: float
    current_step: str | None
    error_message: str | None
    options_json: str
    artifacts_json: str
    people_json: str
    report_json: str
    runtime_json: str
    created_at: str
    updated_at: str


class JobRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def create_job(
        self,
        job_id: str,
        original_filename: str,
        input_video_path: str,
        options: JobOptions,
        *,
        created_by_device: str,
        locale: str,
    ) -> None:
        now = utc_now_iso()
        with db_session(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                  id, original_filename, input_video_path, status, progress, current_step, error_message,
                  options_json, artifacts_json, people_json, report_json, runtime_json,
                  created_by_device, locale, created_at, updated_at
                )
                VALUES (?, ?, ?, 'queued', 0, 'ingest', NULL, ?, '[]', '[]', '{}', '{}', ?, ?, ?, ?)
                """,
                (
                    job_id,
                    original_filename,
                    input_video_path,
                    options.model_dump_json(),
                    created_by_device,
                    locale,
                    now,
                    now,
                ),
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

    def list_jobs(self, limit: int = 50) -> list[JobRecord]:
        with db_session(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM jobs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [self._row_to_record(row) for row in rows]

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

    def set_progress(
        self,
        job_id: str,
        progress: float,
        step: str,
        message: str,
        *,
        stage_progress: float | None = None,
        speed: float | None = None,
        speed_unit: str | None = None,
        eta_seconds: float | None = None,
    ) -> None:
        now = utc_now_iso()
        with db_session(self.db_path) as conn:
            row = conn.execute(
                "SELECT runtime_json FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            runtime = self.decode_runtime(str(row["runtime_json"])) if row else JobRuntime()
            stage = runtime.stages.get(step) or StageRuntime(step=step, started_at=datetime.now(timezone.utc))
            stage.progress = (
                max(min(stage_progress, 1.0), 0.0)
                if stage_progress is not None
                else max(min(progress, 1.0), 0.0)
            )
            stage.message = message
            stage.speed = speed
            stage.speed_unit = speed_unit
            stage.eta_seconds = eta_seconds
            stage.updated_at = datetime.now(timezone.utc)
            stage.completed = stage.progress >= 0.999
            runtime.stages[step] = stage
            runtime.overall_eta_seconds = eta_seconds
            runtime.current_speed = speed
            runtime.current_speed_unit = speed_unit
            runtime_json = runtime.model_dump_json()

            conn.execute(
                """
                UPDATE jobs SET progress = ?, current_step = ?, runtime_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (progress, step, runtime_json, now, job_id),
            )
            self.add_event(job_id, step, message, progress, conn=conn)

    def finalize_stage(
        self,
        job_id: str,
        step: str,
        message: str,
        *,
        global_progress: float,
    ) -> None:
        self.set_progress(
            job_id,
            global_progress,
            step,
            message,
            speed=None,
            speed_unit=None,
            eta_seconds=0.0,
        )

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

    def decode_runtime(self, runtime_json: str) -> JobRuntime:
        payload = runtime_json or "{}"
        parsed: dict[str, Any] = json.loads(payload)
        return JobRuntime.model_validate(parsed)

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
            created_by_device=row["created_by_device"],
            locale=row["locale"],
            status=row["status"],
            progress=float(row["progress"]),
            current_step=row["current_step"],
            error_message=row["error_message"],
            options_json=row["options_json"],
            artifacts_json=row["artifacts_json"],
            people_json=row["people_json"],
            report_json=row["report_json"],
            runtime_json=row["runtime_json"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
