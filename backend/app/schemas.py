from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


JobStatus = Literal["queued", "running", "completed", "failed"]
QualityPreset = Literal["max_quality", "balanced", "max_speed"]
ExportFormat = Literal["srt", "vtt", "ass", "mp4_burned", "zip"]


class JobOptions(BaseModel):
    language: str | None = None
    auto_detect_language: bool = True
    quality_preset: QualityPreset = "max_quality"
    whisper_model: str = "large-v3"
    export_formats: list[ExportFormat] = Field(
        default_factory=lambda: ["srt", "vtt", "ass", "mp4_burned"]
    )
    detect_people: bool = True
    generate_summary: bool = True
    enable_active_speaker_model: bool = True


class Artifact(BaseModel):
    name: str
    kind: str
    path: str
    mime_type: str
    created_at: datetime


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str
    confidence: float = 0.0
    speaker_ref: str | None = None


class PersonTrackStats(BaseModel):
    screen_time_seconds: float = 0.0
    first_seen: float = 0.0
    last_seen: float = 0.0
    avg_confidence: float = 0.0
    speaking_seconds: float = 0.0


class PersonProfile(BaseModel):
    person_id: str
    portrait_path: str | None = None
    track_stats: PersonTrackStats
    key_comments: list[str] = Field(default_factory=list)


class VideoReport(BaseModel):
    summary_md: str = ""
    latex_blocks: list[str] = Field(default_factory=list)
    key_topics: list[str] = Field(default_factory=list)
    people_highlights: dict[str, list[str]] = Field(default_factory=dict)
    raw_markdown: str = ""


class JobView(BaseModel):
    id: str
    original_filename: str
    status: JobStatus
    progress: float
    current_step: str | None = None
    error_message: str | None = None
    options: JobOptions
    artifacts: list[Artifact] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CreateJobResponse(BaseModel):
    job: JobView


class ArtifactsResponse(BaseModel):
    job_id: str
    artifacts: list[Artifact]


class PeopleResponse(BaseModel):
    job_id: str
    people: list[PersonProfile]


class ReportResponse(BaseModel):
    job_id: str
    report: VideoReport


class ExportRequest(BaseModel):
    formats: list[ExportFormat]

