from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .db import db_session
from .schemas import (
    Artifact,
    Chapter,
    FactCheckItem,
    GlossaryTerm,
    JobOptions,
    JobRuntime,
    KeyQuote,
    PersonProfile,
    PersonRegistryEntry,
    QualityScore,
    RunComparison,
    ShortsExport,
    StageRuntime,
    SubtitleRevision,
    TranscriptSegment,
    TranslationTrack,
    VideoReport,
)


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass
class JobRecord:
    id: str
    original_filename: str
    input_video_path: str
    source_fingerprint: str
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
        source_fingerprint: str = "",
        created_by_device: str,
        locale: str,
    ) -> None:
        now = utc_now_iso()
        with db_session(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                  id, original_filename, input_video_path, source_fingerprint, status, progress, current_step, error_message,
                  options_json, artifacts_json, people_json, report_json, runtime_json,
                  created_by_device, locale, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 'queued', 0, 'ingest', NULL, ?, '[]', '[]', '{}', '{}', ?, ?, ?, ?)
                """,
                (
                    job_id,
                    original_filename,
                    input_video_path,
                    source_fingerprint,
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

    def previous_job_for_fingerprint(
        self, *, source_fingerprint: str, current_job_id: str
    ) -> JobRecord | None:
        if not source_fingerprint:
            return None
        with db_session(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM jobs
                WHERE source_fingerprint = ?
                  AND id != ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (source_fingerprint, current_job_id),
            ).fetchone()
            return self._row_to_record(row) if row else None

    def record_job_run(
        self, *, job_id: str, source_fingerprint: str, previous_job_id: str | None
    ) -> None:
        now = utc_now_iso()
        with db_session(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO job_runs (job_id, source_fingerprint, previous_job_id, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    source_fingerprint = excluded.source_fingerprint,
                    previous_job_id = excluded.previous_job_id
                """,
                (job_id, source_fingerprint, previous_job_id, now),
            )

    def set_subtitle_segments(
        self,
        job_id: str,
        segments: list[TranscriptSegment],
        *,
        editor_device: str = "system",
        note: str = "auto",
    ) -> None:
        now = utc_now_iso()
        payload = json.dumps([seg.model_dump(mode="json") for seg in segments], ensure_ascii=False)
        with db_session(self.db_path) as conn:
            conn.execute("DELETE FROM subtitle_segments WHERE job_id = ?", (job_id,))
            for index, seg in enumerate(segments):
                conn.execute(
                    """
                    INSERT INTO subtitle_segments (
                        job_id, segment_order, start, end, text, confidence, speaker_ref, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        index,
                        seg.start,
                        seg.end,
                        seg.text,
                        seg.confidence,
                        seg.speaker_ref,
                        now,
                        now,
                    ),
                )
            conn.execute(
                """
                INSERT INTO subtitle_revisions (job_id, editor_device, note, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, editor_device, note, payload, now),
            )

    def get_subtitle_segments(self, job_id: str) -> list[TranscriptSegment]:
        with db_session(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT segment_order, start, end, text, confidence, speaker_ref
                FROM subtitle_segments
                WHERE job_id = ?
                ORDER BY segment_order ASC
                """,
                (job_id,),
            ).fetchall()
            if not rows:
                return []
            result: list[TranscriptSegment] = []
            for row in rows:
                result.append(
                    TranscriptSegment(
                        id=str(row["segment_order"]),
                        start=float(row["start"]),
                        end=float(row["end"]),
                        text=str(row["text"]),
                        confidence=float(row["confidence"]),
                        speaker_ref=row["speaker_ref"],
                    )
                )
            return result

    def get_subtitle_revisions(self, job_id: str) -> list[SubtitleRevision]:
        with db_session(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, job_id, editor_device, note, created_at
                FROM subtitle_revisions
                WHERE job_id = ?
                ORDER BY id DESC
                LIMIT 30
                """,
                (job_id,),
            ).fetchall()
            return [
                SubtitleRevision(
                    revision_id=int(row["id"]),
                    job_id=str(row["job_id"]),
                    editor_device=str(row["editor_device"]),
                    note=str(row["note"]),
                    created_at=datetime.fromisoformat(str(row["created_at"])),
                )
                for row in rows
            ]

    def set_chapters(self, job_id: str, chapters: list[Chapter]) -> None:
        now = utc_now_iso()
        with db_session(self.db_path) as conn:
            conn.execute("DELETE FROM chapters WHERE job_id = ?", (job_id,))
            for chapter in chapters:
                conn.execute(
                    """
                    INSERT INTO chapters (job_id, chapter_id, title, start, "end", confidence, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        chapter.chapter_id,
                        chapter.title,
                        chapter.start,
                        chapter.end,
                        chapter.confidence,
                        now,
                    ),
                )

    def get_chapters(self, job_id: str) -> list[Chapter]:
        with db_session(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT chapter_id, title, start, "end", confidence
                FROM chapters
                WHERE job_id = ?
                ORDER BY start ASC
                """,
                (job_id,),
            ).fetchall()
            return [
                Chapter(
                    chapter_id=str(row["chapter_id"]),
                    title=str(row["title"]),
                    start=float(row["start"]),
                    end=float(row["end"]),
                    confidence=float(row["confidence"]),
                )
                for row in rows
            ]

    def set_quotes(self, job_id: str, quotes: list[KeyQuote]) -> None:
        now = utc_now_iso()
        with db_session(self.db_path) as conn:
            conn.execute("DELETE FROM key_quotes WHERE job_id = ?", (job_id,))
            for quote in quotes:
                conn.execute(
                    """
                    INSERT INTO key_quotes (job_id, quote_id, start, "end", text, score, speaker_ref, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        quote.quote_id,
                        quote.start,
                        quote.end,
                        quote.text,
                        quote.score,
                        quote.speaker_ref,
                        now,
                    ),
                )

    def get_quotes(self, job_id: str) -> list[KeyQuote]:
        with db_session(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT quote_id, start, "end", text, score, speaker_ref
                FROM key_quotes
                WHERE job_id = ?
                ORDER BY score DESC, start ASC
                """,
                (job_id,),
            ).fetchall()
            return [
                KeyQuote(
                    quote_id=str(row["quote_id"]),
                    start=float(row["start"]),
                    end=float(row["end"]),
                    text=str(row["text"]),
                    score=float(row["score"]),
                    speaker_ref=row["speaker_ref"],
                )
                for row in rows
            ]

    def set_quality(self, job_id: str, quality: QualityScore) -> None:
        now = utc_now_iso()
        with db_session(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO quality_scores (job_id, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (job_id, quality.model_dump_json(), now, now),
            )

    def get_quality(self, job_id: str) -> QualityScore:
        with db_session(self.db_path) as conn:
            row = conn.execute(
                "SELECT payload_json FROM quality_scores WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if not row:
                return QualityScore()
            return QualityScore.model_validate_json(str(row["payload_json"]))

    def set_translations(self, job_id: str, tracks: list[TranslationTrack]) -> None:
        now = utc_now_iso()
        with db_session(self.db_path) as conn:
            conn.execute("DELETE FROM translations WHERE job_id = ?", (job_id,))
            for track in tracks:
                conn.execute(
                    """
                    INSERT INTO translations (job_id, language, payload_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (job_id, track.language, track.model_dump_json(), now, now),
                )

    def get_translations(self, job_id: str) -> list[TranslationTrack]:
        with db_session(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM translations
                WHERE job_id = ?
                ORDER BY language ASC
                """,
                (job_id,),
            ).fetchall()
            return [TranslationTrack.model_validate_json(str(row["payload_json"])) for row in rows]

    def set_fact_checks(self, job_id: str, items: list[FactCheckItem]) -> None:
        now = utc_now_iso()
        payload = json.dumps([item.model_dump(mode="json") for item in items], ensure_ascii=False)
        with db_session(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO fact_checks (job_id, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (job_id, payload, now, now),
            )

    def get_fact_checks(self, job_id: str) -> list[FactCheckItem]:
        with db_session(self.db_path) as conn:
            row = conn.execute(
                "SELECT payload_json FROM fact_checks WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if not row:
                return []
            data = json.loads(str(row["payload_json"]))
            return [FactCheckItem.model_validate(item) for item in data]

    def upsert_glossary_term(self, source: str, target: str, locale: str = "global") -> GlossaryTerm:
        now = utc_now_iso()
        term_id = f"term-{uuid4()}"
        with db_session(self.db_path) as conn:
            existing = conn.execute(
                "SELECT term_id, created_at FROM glossary_terms WHERE source = ? AND locale = ?",
                (source, locale),
            ).fetchone()
            if existing:
                term_id = str(existing["term_id"])
                created_at = str(existing["created_at"])
                conn.execute(
                    """
                    UPDATE glossary_terms
                    SET target = ?, updated_at = ?
                    WHERE term_id = ?
                    """,
                    (target, now, term_id),
                )
            else:
                created_at = now
                conn.execute(
                    """
                    INSERT INTO glossary_terms (term_id, source, target, locale, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (term_id, source, target, locale, now, now),
                )
        return GlossaryTerm(
            term_id=term_id,
            source=source,
            target=target,
            locale=locale,
            created_at=datetime.fromisoformat(created_at),
            updated_at=datetime.fromisoformat(now),
        )

    def delete_glossary_term(self, term_id: str) -> bool:
        with db_session(self.db_path) as conn:
            result = conn.execute(
                "DELETE FROM glossary_terms WHERE term_id = ?",
                (term_id,),
            )
            return result.rowcount > 0

    def list_glossary_terms(self) -> list[GlossaryTerm]:
        with db_session(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT term_id, source, target, locale, created_at, updated_at
                FROM glossary_terms
                ORDER BY source ASC
                """
            ).fetchall()
            return [
                GlossaryTerm(
                    term_id=str(row["term_id"]),
                    source=str(row["source"]),
                    target=str(row["target"]),
                    locale=str(row["locale"]),
                    created_at=datetime.fromisoformat(str(row["created_at"])),
                    updated_at=datetime.fromisoformat(str(row["updated_at"])),
                )
                for row in rows
            ]

    def upsert_person_registry_from_people(self, job_id: str, people: list[PersonProfile]) -> None:
        now = utc_now_iso()
        with db_session(self.db_path) as conn:
            for person in people:
                label = person.display_name or person.person_id
                row = conn.execute(
                    "SELECT * FROM person_registry WHERE display_name = ?",
                    (label,),
                ).fetchone()
                if row:
                    aliases = json.loads(str(row["aliases_json"]) or "[]")
                    if person.person_id not in aliases:
                        aliases.append(person.person_id)
                    linked_jobs = json.loads(str(row["linked_job_ids_json"]) or "[]")
                    if job_id not in linked_jobs:
                        linked_jobs.append(job_id)
                    conf = max(float(row["confidence"]), person.track_stats.avg_confidence)
                    conn.execute(
                        """
                        UPDATE person_registry
                        SET aliases_json = ?, linked_job_ids_json = ?, portrait_path = COALESCE(?, portrait_path),
                            confidence = ?, updated_at = ?
                        WHERE registry_id = ?
                        """,
                        (
                            json.dumps(aliases, ensure_ascii=False),
                            json.dumps(linked_jobs, ensure_ascii=False),
                            person.portrait_path,
                            conf,
                            now,
                            str(row["registry_id"]),
                        ),
                    )
                else:
                    registry_id = f"pr-{uuid4()}"
                    conn.execute(
                        """
                        INSERT INTO person_registry (
                            registry_id, display_name, aliases_json, portrait_path, linked_job_ids_json, confidence, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            registry_id,
                            label,
                            json.dumps([person.person_id], ensure_ascii=False),
                            person.portrait_path,
                            json.dumps([job_id], ensure_ascii=False),
                            person.track_stats.avg_confidence,
                            now,
                            now,
                        ),
                    )

    def list_person_registry(self) -> list[PersonRegistryEntry]:
        with db_session(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT registry_id, display_name, aliases_json, portrait_path, linked_job_ids_json, confidence, created_at, updated_at
                FROM person_registry
                ORDER BY updated_at DESC
                """
            ).fetchall()
            return [
                PersonRegistryEntry(
                    registry_id=str(row["registry_id"]),
                    display_name=str(row["display_name"]),
                    aliases=json.loads(str(row["aliases_json"]) or "[]"),
                    portrait_path=row["portrait_path"],
                    linked_job_ids=json.loads(str(row["linked_job_ids_json"]) or "[]"),
                    confidence=float(row["confidence"]),
                    created_at=datetime.fromisoformat(str(row["created_at"])),
                    updated_at=datetime.fromisoformat(str(row["updated_at"])),
                )
                for row in rows
            ]

    def merge_person_registry(self, source_registry_id: str, target_registry_id: str) -> bool:
        with db_session(self.db_path) as conn:
            source = conn.execute(
                "SELECT * FROM person_registry WHERE registry_id = ?",
                (source_registry_id,),
            ).fetchone()
            target = conn.execute(
                "SELECT * FROM person_registry WHERE registry_id = ?",
                (target_registry_id,),
            ).fetchone()
            if not source or not target:
                return False
            aliases = list(
                {
                    *json.loads(str(target["aliases_json"]) or "[]"),
                    *json.loads(str(source["aliases_json"]) or "[]"),
                }
            )
            linked_jobs = list(
                {
                    *json.loads(str(target["linked_job_ids_json"]) or "[]"),
                    *json.loads(str(source["linked_job_ids_json"]) or "[]"),
                }
            )
            confidence = max(float(source["confidence"]), float(target["confidence"]))
            now = utc_now_iso()
            conn.execute(
                """
                UPDATE person_registry
                SET aliases_json = ?, linked_job_ids_json = ?, confidence = ?, updated_at = ?
                WHERE registry_id = ?
                """,
                (
                    json.dumps(aliases, ensure_ascii=False),
                    json.dumps(linked_jobs, ensure_ascii=False),
                    confidence,
                    now,
                    target_registry_id,
                ),
            )
            conn.execute("DELETE FROM person_registry WHERE registry_id = ?", (source_registry_id,))
            return True

    def split_person_registry_alias(self, registry_id: str, alias_to_split: str) -> bool:
        with db_session(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM person_registry WHERE registry_id = ?",
                (registry_id,),
            ).fetchone()
            if not row:
                return False
            aliases = json.loads(str(row["aliases_json"]) or "[]")
            if alias_to_split not in aliases or len(aliases) <= 1:
                return False
            aliases.remove(alias_to_split)
            now = utc_now_iso()
            conn.execute(
                """
                UPDATE person_registry
                SET aliases_json = ?, updated_at = ?
                WHERE registry_id = ?
                """,
                (json.dumps(aliases, ensure_ascii=False), now, registry_id),
            )
            new_id = f"pr-{uuid4()}"
            conn.execute(
                """
                INSERT INTO person_registry (
                    registry_id, display_name, aliases_json, portrait_path, linked_job_ids_json, confidence, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id,
                    alias_to_split,
                    json.dumps([alias_to_split], ensure_ascii=False),
                    row["portrait_path"],
                    row["linked_job_ids_json"],
                    row["confidence"],
                    now,
                    now,
                ),
            )
            return True

    def reset_kb(self) -> None:
        with db_session(self.db_path) as conn:
            conn.execute("DELETE FROM kb_embeddings_meta")
            conn.execute("DELETE FROM kb_chunks")
            conn.execute("DELETE FROM kb_documents")

    def add_kb_document(self, path: str, checksum: str) -> int:
        now = utc_now_iso()
        with db_session(self.db_path) as conn:
            row = conn.execute("SELECT id FROM kb_documents WHERE path = ?", (path,)).fetchone()
            if row:
                doc_id = int(row["id"])
                conn.execute(
                    "UPDATE kb_documents SET checksum = ?, updated_at = ? WHERE id = ?",
                    (checksum, now, doc_id),
                )
                conn.execute("DELETE FROM kb_chunks WHERE document_id = ?", (doc_id,))
                return doc_id
            conn.execute(
                "INSERT INTO kb_documents (path, checksum, updated_at) VALUES (?, ?, ?)",
                (path, checksum, now),
            )
            new_row = conn.execute("SELECT id FROM kb_documents WHERE path = ?", (path,)).fetchone()
            return int(new_row["id"]) if new_row else -1

    def add_kb_chunk(self, document_id: int, chunk_order: int, text: str, token_count: int) -> int:
        with db_session(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO kb_chunks (document_id, chunk_order, text, token_count)
                VALUES (?, ?, ?, ?)
                """,
                (document_id, chunk_order, text, token_count),
            )
            row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
            return int(row["id"]) if row else -1

    def set_kb_chunk_meta(self, chunk_id: int, hash_value: str, vector_dim: int) -> None:
        now = utc_now_iso()
        with db_session(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO kb_embeddings_meta (chunk_id, hash, vector_dim, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chunk_id) DO UPDATE SET
                    hash = excluded.hash,
                    vector_dim = excluded.vector_dim,
                    updated_at = excluded.updated_at
                """,
                (chunk_id, hash_value, vector_dim, now),
            )

    def kb_status(self) -> tuple[int, int, datetime | None]:
        with db_session(self.db_path) as conn:
            docs = conn.execute("SELECT COUNT(*) AS c FROM kb_documents").fetchone()
            chunks = conn.execute("SELECT COUNT(*) AS c FROM kb_chunks").fetchone()
            stamp = conn.execute("SELECT MAX(updated_at) AS m FROM kb_documents").fetchone()
            indexed = (
                datetime.fromisoformat(str(stamp["m"])) if stamp and stamp["m"] else None
            )
            return int(docs["c"] if docs else 0), int(chunks["c"] if chunks else 0), indexed

    def list_kb_chunks(self) -> list[dict[str, Any]]:
        with db_session(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT d.path, c.text
                FROM kb_chunks c
                JOIN kb_documents d ON d.id = c.document_id
                ORDER BY d.path ASC, c.chunk_order ASC
                """
            ).fetchall()
            return [{"path": str(row["path"]), "text": str(row["text"])} for row in rows]

    def set_shorts_exports(self, job_id: str, shorts: list[ShortsExport]) -> None:
        with db_session(self.db_path) as conn:
            conn.execute("DELETE FROM shorts_exports WHERE job_id = ?", (job_id,))
            for short in shorts:
                conn.execute(
                    """
                    INSERT INTO shorts_exports (short_id, job_id, label, path, start, "end", created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        short.short_id,
                        job_id,
                        short.label,
                        short.path,
                        short.start,
                        short.end,
                        short.created_at.isoformat(),
                    ),
                )

    def get_shorts_exports(self, job_id: str) -> list[ShortsExport]:
        with db_session(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT short_id, job_id, label, path, start, "end", created_at
                FROM shorts_exports
                WHERE job_id = ?
                ORDER BY start ASC
                """,
                (job_id,),
            ).fetchall()
            return [
                ShortsExport(
                    short_id=str(row["short_id"]),
                    job_id=str(row["job_id"]),
                    label=str(row["label"]),
                    path=str(row["path"]),
                    start=float(row["start"]),
                    end=float(row["end"]),
                    created_at=datetime.fromisoformat(str(row["created_at"])),
                )
                for row in rows
            ]

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
            source_fingerprint=row["source_fingerprint"],
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
