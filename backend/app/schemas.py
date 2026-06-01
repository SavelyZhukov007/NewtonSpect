from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


JobStatus = Literal["queued", "running", "completed", "failed"]
QualityPreset = Literal["max_quality", "balanced", "max_speed"]
ExportFormat = str
StreamingMode = Literal["dual_pass_hq", "final_only_hq", "live_only_fast"]
SubtitleEmbedMode = Literal["auto", "embedded", "sidecar", "burned"]
PrivacyMode = Literal["auto_risk", "enabled", "disabled"]
FactCheckStatus = Literal["supported", "contradicted", "not_found"]


class ShortsPreset(BaseModel):
    clip_count: int = 3
    clip_duration_seconds: int = 35
    safe_area: bool = True
    subtitle_style: dict[str, Any] = Field(default_factory=dict)


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
    enable_mask_overlay: bool = False
    mask_model_names: list[str] = Field(
        default_factory=lambda: [
            "age-gender-recognition-retail-0013",
            "emotions-recognition-retail-0003",
            "face-detection-retail-0004",
            "face-reidentification-retail-0095",
            "facial-landmarks-35-adas-0002",
            "facial-landmarks-98-detection-0001",
            "human-pose-estimation-0001",
            "person-detection-retail-0013",
            "person-reidentification-retail-0277",
        ]
    )
    enable_subtitles: bool = True
    enable_burned_video: bool = True
    ui_locale: Literal["ru", "en"] = "en"
    streaming_mode: StreamingMode = "dual_pass_hq"
    camera_mode: bool = False
    auto_stop_seconds: int = 20
    show_face_mask_preview: bool = False
    output_video_format: str = "mp4"
    subtitle_embed_mode: SubtitleEmbedMode = "auto"
    subtitle_style: dict[str, Any] = Field(default_factory=dict)
    generate_shorts: bool = False
    shorts_preset: ShortsPreset = Field(default_factory=ShortsPreset)
    privacy_mode: PrivacyMode = "auto_risk"
    translate_languages: list[str] = Field(default_factory=list)
    enable_fact_check: bool = True
    enable_chapters: bool = True
    enable_quotes: bool = True
    enable_quality_score: bool = True
    platform_presets: list[str] = Field(default_factory=list)
    enable_live_draft: bool = False


class StageRuntime(BaseModel):
    step: str
    progress: float = 0.0
    speed: float | None = None
    speed_unit: str | None = None
    eta_seconds: float | None = None
    message: str | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None
    completed: bool = False


class JobRuntime(BaseModel):
    stages: dict[str, StageRuntime] = Field(default_factory=dict)
    overall_eta_seconds: float | None = None
    current_speed: float | None = None
    current_speed_unit: str | None = None


class Artifact(BaseModel):
    name: str
    kind: str
    path: str
    mime_type: str
    created_at: datetime


class TranscriptSegment(BaseModel):
    id: str | None = None
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
    display_name: str | None = None
    display_name_confidence: float = 0.0
    portrait_path: str | None = None
    track_stats: PersonTrackStats
    key_comments: list[str] = Field(default_factory=list)


class VideoReport(BaseModel):
    summary_md: str = ""
    latex_blocks: list[str] = Field(default_factory=list)
    key_topics: list[str] = Field(default_factory=list)
    people_highlights: dict[str, list[str]] = Field(default_factory=dict)
    raw_markdown: str = ""


class Chapter(BaseModel):
    chapter_id: str
    title: str
    start: float
    end: float
    confidence: float = 0.0


class KeyQuote(BaseModel):
    quote_id: str
    start: float
    end: float
    text: str
    score: float
    speaker_ref: str | None = None


class SpeakerTimelineItem(BaseModel):
    speaker_ref: str
    start: float
    end: float
    duration: float


class QualityScore(BaseModel):
    overall: float = 0.0
    asr_confidence: float = 0.0
    subtitle_coverage: float = 0.0
    speaker_stability: float = 0.0
    people_stability: float = 0.0
    report_completeness: float = 0.0
    notes: list[str] = Field(default_factory=list)
    speaker_timeline: list[SpeakerTimelineItem] = Field(default_factory=list)


class FactCheckItem(BaseModel):
    claim: str
    status: FactCheckStatus
    reason: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class TranslationTrack(BaseModel):
    language: str
    segments: list[TranscriptSegment] = Field(default_factory=list)


class RunComparison(BaseModel):
    current_job_id: str
    previous_job_id: str | None = None
    wer_like_delta: float = 0.0
    people_delta: int = 0
    subtitle_coverage_delta: float = 0.0
    speaker_stability_delta: float = 0.0
    duration_speech_delta: float = 0.0
    summary_md: str = ""


class SubtitleRevision(BaseModel):
    revision_id: int
    job_id: str
    editor_device: str
    note: str
    created_at: datetime


class GlossaryTerm(BaseModel):
    term_id: str
    source: str
    target: str
    locale: str = "global"
    created_at: datetime
    updated_at: datetime


class PersonRegistryEntry(BaseModel):
    registry_id: str
    display_name: str
    aliases: list[str] = Field(default_factory=list)
    portrait_path: str | None = None
    linked_job_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    created_at: datetime
    updated_at: datetime


class KnowledgeBaseStatus(BaseModel):
    documents: int = 0
    chunks: int = 0
    indexed_at: datetime | None = None
    kb_root: str = ""


class ShortsExport(BaseModel):
    short_id: str
    job_id: str
    label: str
    path: str
    start: float
    end: float
    created_at: datetime


class JobView(BaseModel):
    id: str
    original_filename: str
    source_fingerprint: str = ""
    created_by_device: str = "Unknown device"
    locale: Literal["ru", "en"] = "en"
    status: JobStatus
    progress: float
    current_step: str | None = None
    error_message: str | None = None
    options: JobOptions
    artifacts: list[Artifact] = Field(default_factory=list)
    runtime: JobRuntime = Field(default_factory=JobRuntime)
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


class ChaptersResponse(BaseModel):
    job_id: str
    chapters: list[Chapter]


class QuotesResponse(BaseModel):
    job_id: str
    quotes: list[KeyQuote]


class QualityResponse(BaseModel):
    job_id: str
    quality: QualityScore


class ComparisonResponse(BaseModel):
    job_id: str
    comparison: RunComparison


class SubtitlesResponse(BaseModel):
    job_id: str
    segments: list[TranscriptSegment]
    revisions: list[SubtitleRevision] = Field(default_factory=list)


class SubtitlesUpdateRequest(BaseModel):
    segments: list[TranscriptSegment]
    note: str = "manual edit"


class ExportRequest(BaseModel):
    formats: list[ExportFormat]


class JobLibraryResponse(BaseModel):
    items: list[JobView]


class VideoFormatCapability(BaseModel):
    format: str
    ffmpeg_muxer: str
    curated: bool = False
    can_embed_subtitles: bool = False
    preferred_subtitle_codec: str | None = None
    notes: str | None = None


class FormatCapabilitiesResponse(BaseModel):
    curated: list[VideoFormatCapability] = Field(default_factory=list)
    all_muxers: list[VideoFormatCapability] = Field(default_factory=list)


class GlossaryResponse(BaseModel):
    items: list[GlossaryTerm]


class GlossaryUpsertRequest(BaseModel):
    source: str
    target: str
    locale: str = "global"


class PersonRegistryResponse(BaseModel):
    items: list[PersonRegistryEntry]


class PersonMergeRequest(BaseModel):
    source_registry_id: str
    target_registry_id: str


class PersonSplitRequest(BaseModel):
    registry_id: str
    alias_to_split: str


class KnowledgeBaseStatusResponse(BaseModel):
    status: KnowledgeBaseStatus


class ShortsRequest(BaseModel):
    clip_count: int = 3
    clip_duration_seconds: int = 35


class ShortsResponse(BaseModel):
    job_id: str
    shorts: list[ShortsExport]


class TranslationsResponse(BaseModel):
    job_id: str
    tracks: list[TranslationTrack]


class FactCheckResponse(BaseModel):
    job_id: str
    items: list[FactCheckItem]
