from __future__ import annotations

import json
import re
import time
from pathlib import Path

from ..config import Settings
from ..repository import JobRecord, JobRepository
from ..schemas import (
    Artifact,
    FactCheckItem,
    QualityScore,
    ShortsExport,
    TranscriptSegment,
    TranslationTrack,
    VideoReport,
)
from .asr import ASRService
from .exporter import make_artifact
from .insights import (
    apply_glossary_to_segments,
    build_chapters,
    build_key_quotes,
    compute_quality_score,
    extract_claim_candidates,
    redact_segments_for_privacy,
    run_offline_fact_check,
)
from .knowledge_base import OfflineKnowledgeBase
from .mask_overlay import OpenVINOMaskOverlayRenderer
from .media import MediaService
from .ollama_client import OllamaClient
from .openvino_people import OpenVINOPeopleAnalyzer
from .paths import StorageService
from .reporting import ReportGenerator
from .shorts import ShortsGenerator
from .speaker import ActiveSpeakerAttributor
from .subtitles import write_subtitle_files
from .translator import LocalTranslator
from .types import VisionAnalysisResult


STAGE_WINDOWS: dict[str, tuple[float, float]] = {
    "ingest": (0.00, 0.04),
    "audio_extract": (0.04, 0.12),
    "asr": (0.12, 0.38),
    "subtitle_postprocess": (0.38, 0.48),
    "vision": (0.48, 0.72),
    "speaker_attribution": (0.72, 0.78),
    "report": (0.78, 0.88),
    "burned_video": (0.88, 0.96),
    "mask_overlay": (0.96, 0.995),
    "done": (1.0, 1.0),
}


class PipelineRunner:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: JobRepository,
        storage: StorageService,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.storage = storage
        self.media = MediaService()
        self.asr = ASRService(model_name=settings.whisper_model)
        self.people_analyzer: OpenVINOPeopleAnalyzer | None = None
        self.speaker_attributor: ActiveSpeakerAttributor | None = None
        self.report_generator: ReportGenerator | None = None
        self.translator: LocalTranslator | None = None
        self.kb = OfflineKnowledgeBase(repository=repository, kb_root=storage.kb_dir)
        self.shorts_generator = ShortsGenerator(self.media)
        self.mask_renderer: OpenVINOMaskOverlayRenderer | None = None

    def _get_people_analyzer(self) -> OpenVINOPeopleAnalyzer:
        if self.people_analyzer is None:
            self.people_analyzer = OpenVINOPeopleAnalyzer(
                models_dir=self.settings.openvino_models_dir,
                preferred_devices=self.settings.preferred_openvino_devices,
                model_search_paths=self.settings.openvino_models_search_paths,
            )
        return self.people_analyzer

    def _get_speaker_attributor(self) -> ActiveSpeakerAttributor:
        if self.speaker_attributor is None:
            self.speaker_attributor = ActiveSpeakerAttributor(self.settings.openvino_models_dir)
        return self.speaker_attributor

    def _get_report_generator(self) -> ReportGenerator:
        if self.report_generator is None:
            self.report_generator = ReportGenerator(
                OllamaClient(self.settings.ollama_base_url, self.settings.ollama_model)
            )
        return self.report_generator

    def _get_translator(self) -> LocalTranslator:
        if self.translator is None:
            self.translator = LocalTranslator(
                OllamaClient(self.settings.ollama_base_url, self.settings.ollama_model)
            )
        return self.translator

    def _get_mask_renderer(self) -> OpenVINOMaskOverlayRenderer:
        if self.mask_renderer is None:
            self.mask_renderer = OpenVINOMaskOverlayRenderer(
                self.settings.openvino_models_dir,
                self.settings.preferred_openvino_devices,
                model_search_paths=self.settings.openvino_models_search_paths,
            )
        return self.mask_renderer

    def process(self, job: JobRecord) -> None:
        options = self.repository.decode_options(job.options_json)
        artifacts: list[Artifact] = self.repository.decode_artifacts(job.artifacts_json)
        warnings: list[str] = []
        input_video = Path(job.input_video_path)
        self.storage.job_dir(job.id)
        previous = self.repository.previous_job_for_fingerprint(
            source_fingerprint=job.source_fingerprint,
            current_job_id=job.id,
        )
        self.repository.record_job_run(
            job_id=job.id,
            source_fingerprint=job.source_fingerprint,
            previous_job_id=previous.id if previous else None,
        )

        self.repository.update_job_status(job.id, status="running", progress=0.01, step="ingest")
        self._stage_update(job.id, "ingest", 1.0, "Pipeline started")

        audio_dir = self.storage.job_stage_dir(job.id, "audio")
        subtitles_dir = self.storage.job_stage_dir(job.id, "subtitles")
        people_dir = self.storage.job_stage_dir(job.id, "people")
        report_dir = self.storage.job_stage_dir(job.id, "report")
        exports_dir = self.storage.job_stage_dir(job.id, "exports")
        mask_dir = self.storage.job_stage_dir(job.id, "mask")
        insights_dir = self.storage.job_chapters_quotes_quality_dir(job.id)
        shorts_dir = self.storage.job_shorts_dir(job.id)

        audio_path = audio_dir / "audio.wav"
        transcript_path = subtitles_dir / "transcript.json"
        srt_path = subtitles_dir / "captions.srt"
        vtt_path = subtitles_dir / "captions.vtt"
        ass_path = subtitles_dir / "captions.ass"
        privacy_redacted = False
        output_format = (options.output_video_format or "mp4").lower().strip(".")
        if not output_format:
            output_format = "mp4"
        output_extension = self.media.output_extension(output_format)
        burned_video_path = exports_dir / f"video_burned.{output_extension}"
        embedded_video_path = exports_dir / f"video_subtitled.{output_extension}"

        # Stage: audio extract
        audio_start = time.perf_counter()
        self._stage_update(job.id, "audio_extract", 0.05, "Extracting audio with ffmpeg")
        self.media.extract_audio_wav(input_video, audio_path)
        elapsed_audio = max(time.perf_counter() - audio_start, 1e-6)
        input_duration = self.media.probe_duration_seconds(input_video)
        audio_speed = (input_duration / elapsed_audio) if input_duration > 0 else None
        self._stage_update(
            job.id,
            "audio_extract",
            1.0,
            "Audio extracted",
            speed=audio_speed,
            speed_unit="x realtime",
            eta_seconds=0.0,
        )
        artifacts.append(
            make_artifact(
                name=audio_path.name,
                kind="audio_wav",
                path=audio_path,
                mime_type="audio/wav",
            )
        )

        # Stage: ASR
        asr_started = time.perf_counter()

        def _asr_progress(processed_sec: float, total_sec: float, eta_sec: float) -> None:
            elapsed = max(time.perf_counter() - asr_started, 1e-6)
            speed = processed_sec / elapsed if elapsed > 0 else None
            local = processed_sec / total_sec if total_sec > 0 else 0.0
            self._stage_update(
                job.id,
                "asr",
                local,
                f"Transcribing speech ({processed_sec:.1f}/{total_sec:.1f}s)",
                speed=speed,
                speed_unit="audio sec/s",
                eta_seconds=eta_sec,
            )

        self._stage_update(job.id, "asr", 0.01, f"Transcribing speech with Whisper {options.whisper_model}")
        segments = self._safe_transcribe(
            audio_path,
            options.language,
            options.auto_detect_language,
            options.quality_preset,
            progress_callback=_asr_progress,
        )
        glossary_pairs = [
            (item.source, item.target) for item in self.repository.list_glossary_terms()
        ]
        segments = apply_glossary_to_segments(segments, glossary_pairs)
        if options.privacy_mode in {"auto_risk", "enabled"}:
            redacted_segments, changed = redact_segments_for_privacy(segments)
            if options.privacy_mode == "enabled" or changed:
                segments = redacted_segments
                privacy_redacted = changed
        self._stage_update(job.id, "asr", 1.0, "Transcription completed", eta_seconds=0.0)
        transcript_path.write_text(
            json.dumps([segment.model_dump() for segment in segments], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.repository.set_subtitle_segments(job.id, segments, editor_device="system", note="auto-asr")
        artifacts.append(
            make_artifact(
                name=transcript_path.name,
                kind="transcript_json",
                path=transcript_path,
                mime_type="application/json",
            )
        )

        # Stage: subtitles
        if options.enable_subtitles:
            self._stage_update(job.id, "subtitle_postprocess", 0.05, "Generating subtitle files")
            write_subtitle_files(segments, srt_path=srt_path, vtt_path=vtt_path, ass_path=ass_path)
            artifacts.extend(
                [
                    make_artifact(
                        name=srt_path.name,
                        kind="subtitle_srt",
                        path=srt_path,
                        mime_type="application/x-subrip",
                    ),
                    make_artifact(
                        name=vtt_path.name,
                        kind="subtitle_vtt",
                        path=vtt_path,
                        mime_type="text/vtt",
                    ),
                    make_artifact(
                        name=ass_path.name,
                        kind="subtitle_ass",
                        path=ass_path,
                        mime_type="text/x-ssa",
                    ),
                ]
            )
            self._stage_update(job.id, "subtitle_postprocess", 1.0, "Subtitle files generated")
        else:
            self._stage_update(job.id, "subtitle_postprocess", 1.0, "Subtitle generation disabled")
        self.repository.set_artifacts(job.id, artifacts)

        # Stage: vision
        vision_result = VisionAnalysisResult(people=[], samples=[], device_used="disabled")
        if options.detect_people:
            self._stage_update(job.id, "vision", 0.02, "Detecting and clustering people")

            def _vision_progress(done_frames: int, total_frames: int, elapsed: float) -> None:
                progress = (done_frames / total_frames) if total_frames > 0 else 0.0
                fps = done_frames / max(elapsed, 1e-6)
                eta = (total_frames - done_frames) / fps if fps > 1e-6 and total_frames > 0 else None
                self._stage_update(
                    job.id,
                    "vision",
                    progress,
                    f"OpenVINO analysis {done_frames}/{total_frames or '?'} frames",
                    speed=fps,
                    speed_unit="fps",
                    eta_seconds=eta,
                )

            try:
                vision_result = self._get_people_analyzer().analyze(
                    input_video,
                    people_output_dir=people_dir,
                    progress_callback=_vision_progress,
                )
                self._stage_update(
                    job.id,
                    "vision",
                    1.0,
                    f"Vision done on device {vision_result.device_used}",
                    eta_seconds=0.0,
                )
            except Exception as exc:
                warning = f"Vision stage failed; continuing without people analytics: {exc}"
                warnings.append(warning)
                self.repository.add_event(job.id, "vision", warning, 0.72, level="warning")
                self._stage_update(job.id, "vision", 1.0, "Vision disabled due to error")
                vision_result = VisionAnalysisResult(people=[], samples=[], device_used="error")
        else:
            self._stage_update(job.id, "vision", 1.0, "Vision disabled")

        # Stage: speaker attribution
        if options.detect_people and options.enable_active_speaker_model and vision_result.samples:
            self._stage_update(job.id, "speaker_attribution", 0.10, "Assigning speaking segments")
            try:
                attribution = self._get_speaker_attributor().assign_speakers(
                    segments=segments,
                    samples=vision_result.samples,
                    use_asd_model=options.enable_active_speaker_model,
                )
                segments = attribution.segments
                if options.enable_subtitles:
                    write_subtitle_files(segments, srt_path=srt_path, vtt_path=vtt_path, ass_path=ass_path)
                self.repository.set_subtitle_segments(
                    job.id, segments, editor_device="system", note="speaker-attribution"
                )
                self._stage_update(
                    job.id,
                    "speaker_attribution",
                    1.0,
                    f"Speaker attribution method: {attribution.method}",
                )
            except Exception as exc:
                warning = f"Speaker attribution failed; continuing with base transcript: {exc}"
                warnings.append(warning)
                self.repository.add_event(
                    job.id, "speaker_attribution", warning, 0.78, level="warning"
                )
                attribution = type("Attr", (), {"method": "failed"})()
                self.repository.set_subtitle_segments(
                    job.id, segments, editor_device="system", note="speaker-failed"
                )
                self._stage_update(
                    job.id,
                    "speaker_attribution",
                    1.0,
                    "Speaker attribution failed; used base transcript",
                )
        else:
            attribution = type("Attr", (), {"method": "disabled"})()
            self.repository.set_subtitle_segments(
                job.id, segments, editor_device="system", note="speaker-disabled"
            )
            self._stage_update(job.id, "speaker_attribution", 1.0, "Speaker attribution disabled")

        extracted_names = self._extract_names_by_person(segments)
        for person in vision_result.people:
            resolved = extracted_names.get(person.person_id)
            if resolved is not None:
                person.display_name = resolved[0]
                person.display_name_confidence = resolved[1]

        # Stage: report
        self._stage_update(job.id, "report", 0.03, "Generating AI report")
        report = VideoReport(
            summary_md="Summary generation disabled.",
            key_topics=[],
            latex_blocks=[],
            people_highlights={},
            raw_markdown="",
        )
        if options.generate_summary:
            try:
                report = self._get_report_generator().generate(
                    segments=segments, people=vision_result.people
                )
            except Exception as exc:
                warning = f"Summary generation failed; continuing with transcript outputs only: {exc}"
                warnings.append(warning)
                self.repository.add_event(job.id, "report", warning, 0.82, level="warning")
                report = VideoReport(
                    summary_md="Summary generation failed. Transcript and subtitle artifacts are available.",
                    key_topics=[],
                    latex_blocks=[],
                    people_highlights={},
                    raw_markdown="",
                )
        for person in vision_result.people:
            person.key_comments = report.people_highlights.get(person.person_id, [])

        chapters = build_chapters(segments) if options.enable_chapters else []
        quotes = build_key_quotes(segments) if options.enable_quotes else []
        quality = (
            compute_quality_score(
                segments=segments,
                people=vision_result.people,
                report=report,
                transcript_duration=input_duration,
            )
            if options.enable_quality_score
            else QualityScore(notes=["Quality score generation disabled by user"])
        )

        translation_tracks: list[TranslationTrack] = []
        for language in sorted(set([lang.lower() for lang in options.translate_languages])):
            try:
                translation_tracks.append(
                    self._get_translator().translate(segments, target_language=language)
                )
            except Exception as exc:
                warning = f"Translation for '{language}' failed: {exc}"
                warnings.append(warning)
                self.repository.add_event(job.id, "report", warning, 0.85, level="warning")

        fact_checks: list[FactCheckItem] = []
        if options.enable_fact_check:
            try:
                claims = extract_claim_candidates(segments)
                kb_chunks = self.repository.list_kb_chunks()
                fact_checks = run_offline_fact_check(claims, kb_chunks)
            except Exception as exc:
                warning = f"Fact-check stage failed: {exc}"
                warnings.append(warning)
                self.repository.add_event(job.id, "report", warning, 0.86, level="warning")
                fact_checks = []

        self.repository.set_chapters(job.id, chapters)
        self.repository.set_quotes(job.id, quotes)
        self.repository.set_quality(job.id, quality)
        self.repository.set_translations(job.id, translation_tracks)
        self.repository.set_fact_checks(job.id, fact_checks)

        report_md_path = report_dir / "report.md"
        report_json_path = report_dir / "report.json"
        chapters_path = insights_dir / "chapters.json"
        quotes_path = insights_dir / "quotes.json"
        quality_path = insights_dir / "quality.json"
        factcheck_path = insights_dir / "fact_check.json"
        publication_meta_path = insights_dir / "publication_meta.json"
        report_md_path.write_text(report.summary_md, encoding="utf-8")
        report_json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        chapters_path.write_text(
            json.dumps([item.model_dump() for item in chapters], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        quotes_path.write_text(
            json.dumps([item.model_dump() for item in quotes], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        quality_path.write_text(quality.model_dump_json(indent=2), encoding="utf-8")
        factcheck_path.write_text(
            json.dumps([item.model_dump() for item in fact_checks], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        publication_meta = {
            "titles": [
                (report.key_topics[0] if report.key_topics else "AstraOrpheus Clip"),
                (chapters[0].title if chapters else "Video Summary"),
                "AI Highlight Reel",
            ],
            "description": report.summary_md[:1200],
            "chapters": [
                {"start": item.start, "title": item.title}
                for item in chapters
            ],
            "cover_candidates": [quote.start for quote in quotes[:5]],
        }
        publication_meta_path.write_text(
            json.dumps(publication_meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        artifacts.extend(
            [
                make_artifact(
                    name=report_md_path.name,
                    kind="report_markdown",
                    path=report_md_path,
                    mime_type="text/markdown",
                ),
                make_artifact(
                    name=report_json_path.name,
                    kind="report_json",
                    path=report_json_path,
                    mime_type="application/json",
                ),
                make_artifact(
                    name=chapters_path.name,
                    kind="chapters_json",
                    path=chapters_path,
                    mime_type="application/json",
                ),
                make_artifact(
                    name=quotes_path.name,
                    kind="quotes_json",
                    path=quotes_path,
                    mime_type="application/json",
                ),
                make_artifact(
                    name=quality_path.name,
                    kind="quality_json",
                    path=quality_path,
                    mime_type="application/json",
                ),
                make_artifact(
                    name=factcheck_path.name,
                    kind="fact_check_json",
                    path=factcheck_path,
                    mime_type="application/json",
                ),
                make_artifact(
                    name=publication_meta_path.name,
                    kind="publication_meta_json",
                    path=publication_meta_path,
                    mime_type="application/json",
                ),
            ]
        )
        for track in translation_tracks:
            translation_path = insights_dir / f"translations_{track.language}.json"
            translation_path.write_text(
                track.model_dump_json(indent=2),
                encoding="utf-8",
            )
            artifacts.append(
                make_artifact(
                    name=translation_path.name,
                    kind="translation_json",
                    path=translation_path,
                    mime_type="application/json",
                )
            )
        self._stage_update(job.id, "report", 1.0, "Report generated")

        for person in vision_result.people:
            if person.portrait_path:
                portrait_path = Path(person.portrait_path)
                artifacts.append(
                    make_artifact(
                        name=portrait_path.name,
                        kind="portrait",
                        path=portrait_path,
                        mime_type="image/jpeg",
                    )
                )

        # Stage: burned/embedded export
        should_render_burned = (
            options.enable_burned_video
            and options.enable_subtitles
            and (
                "mp4_burned" in options.export_formats
                or options.subtitle_embed_mode == "burned"
            )
        )
        format_map = {entry["format"]: entry for entry in self.media.curated_video_formats()}
        selected_format = (options.output_video_format or "mp4").lower()
        format_cap = format_map.get(selected_format)
        can_embed_subtitles = bool(format_cap and format_cap["can_embed_subtitles"])
        subtitle_codec = (
            str(format_cap["preferred_subtitle_codec"]) if format_cap and format_cap["preferred_subtitle_codec"] else "mov_text"
        )
        should_embed = (
            options.enable_subtitles
            and options.enable_burned_video
            and options.subtitle_embed_mode in {"auto", "embedded"}
            and can_embed_subtitles
        )

        if should_render_burned or should_embed:
            video_duration = self.media.probe_duration_seconds(input_video)
            self._stage_update(job.id, "burned_video", 0.01, "Rendering exported video")

            if should_render_burned:
                def _burn_progress(processed: float, total: float, eta: float) -> None:
                    progress = processed / total if total > 0 else 0.0
                    speed = processed / max(time.perf_counter() - asr_started, 1e-6)
                    self._stage_update(
                        job.id,
                        "burned_video",
                        progress * 0.8,
                        f"Burning subtitles {processed:.1f}/{total:.1f}s",
                        speed=speed,
                        speed_unit="video sec/s",
                        eta_seconds=eta,
                    )

                try:
                    self.media.burn_ass_subtitles_with_progress(
                        input_video,
                        ass_path=ass_path,
                        output_video=burned_video_path,
                        duration_seconds=video_duration,
                        container_format=selected_format,
                        progress_callback=_burn_progress,
                    )
                    artifacts.append(
                        make_artifact(
                            name=burned_video_path.name,
                            kind="video_burned",
                            path=burned_video_path,
                            mime_type=f"video/{selected_format if selected_format != 'mpegts' else 'mp2t'}",
                        )
                    )
                except Exception as exc:
                    warning = f"Burned subtitle export failed; continuing without burned video: {exc}"
                    warnings.append(warning)
                    self.repository.add_event(job.id, "burned_video", warning, 0.92, level="warning")

            if should_embed:
                subtitle_source = srt_path if subtitle_codec in {"mov_text", "srt"} else ass_path
                self._stage_update(
                    job.id,
                    "burned_video",
                    0.85 if should_render_burned else 0.35,
                    "Embedding soft subtitles",
                    eta_seconds=5.0,
                )
                try:
                    self.media.embed_soft_subtitles(
                        input_video=input_video,
                        subtitle_path=subtitle_source,
                        output_video=embedded_video_path,
                        container_format=selected_format,
                        subtitle_codec=subtitle_codec,
                    )
                    artifacts.append(
                        make_artifact(
                            name=embedded_video_path.name,
                            kind="video_subtitled",
                            path=embedded_video_path,
                            mime_type=f"video/{selected_format if selected_format != 'mpegts' else 'mp2t'}",
                        )
                    )
                except Exception as exc:
                    warning = f"Embedded subtitle export failed; sidecar subtitles are still available: {exc}"
                    warnings.append(warning)
                    self.repository.add_event(job.id, "burned_video", warning, 0.94, level="warning")

            if options.subtitle_embed_mode == "embedded" and not can_embed_subtitles:
                self.repository.add_event(
                    job.id,
                    "burned_video",
                    f"Embedded subtitles are not supported for format '{selected_format}'. Exported sidecar subtitles instead.",
                    0.95,
                    level="warning",
                )
            self._stage_update(job.id, "burned_video", 1.0, "Video export ready")
        else:
            self._stage_update(job.id, "burned_video", 1.0, "Video export disabled")

        # Stage: mask overlay
        should_render_mask_overlay = (
            options.enable_mask_overlay
            or options.privacy_mode == "enabled"
            or (options.privacy_mode == "auto_risk" and privacy_redacted)
        )
        if should_render_mask_overlay:
            self._stage_update(job.id, "mask_overlay", 0.01, "Rendering OpenVINO mask overlay")

            def _mask_progress(done_frames: int, total_frames: int, elapsed: float) -> None:
                progress = (done_frames / total_frames) if total_frames > 0 else 0.0
                fps = done_frames / max(elapsed, 1e-6)
                eta = (total_frames - done_frames) / fps if fps > 1e-6 and total_frames > 0 else None
                self._stage_update(
                    job.id,
                    "mask_overlay",
                    progress,
                    f"Mask overlay {done_frames}/{total_frames or '?'} frames",
                    speed=fps,
                    speed_unit="fps",
                    eta_seconds=eta,
                )

            try:
                mask_result = self._get_mask_renderer().render(
                    input_video,
                    output_dir=mask_dir,
                    person_display_names={
                        person.person_id: person.display_name or person.person_id
                        for person in vision_result.people
                    },
                    progress_callback=_mask_progress,
                )
                artifacts.extend(
                    [
                        make_artifact(
                            name=mask_result.output_video_path.name,
                            kind="video_masked",
                            path=mask_result.output_video_path,
                            mime_type="video/mp4",
                        ),
                        make_artifact(
                            name=mask_result.metadata_path.name,
                            kind="mask_metadata",
                            path=mask_result.metadata_path,
                            mime_type="application/json",
                        ),
                    ]
                )
                self._stage_update(job.id, "mask_overlay", 1.0, "Mask overlay video ready")
            except Exception as exc:
                warning = f"Mask overlay failed; continuing without masked video: {exc}"
                warnings.append(warning)
                self.repository.add_event(job.id, "mask_overlay", warning, 0.99, level="warning")
                self._stage_update(job.id, "mask_overlay", 1.0, "Mask overlay skipped due to error")
        else:
            self._stage_update(job.id, "mask_overlay", 1.0, "Mask overlay disabled")

        shorts_exports: list[ShortsExport] = []
        if options.generate_shorts:
            self.repository.add_event(job.id, "burned_video", "Generating 9:16 shorts", 0.97)
            try:
                shorts_exports = self.shorts_generator.generate(
                    job_id=job.id,
                    input_video=input_video,
                    output_dir=shorts_dir,
                    preset=options.shorts_preset,
                    quotes=quotes,
                    chapters=chapters,
                )
                for short in shorts_exports:
                    artifacts.append(
                        make_artifact(
                            name=Path(short.path).name,
                            kind="short_video",
                            path=Path(short.path),
                            mime_type="video/mp4",
                        )
                    )
            except Exception as exc:
                warning = f"Shorts generation failed; continuing without shorts: {exc}"
                warnings.append(warning)
                self.repository.add_event(job.id, "burned_video", warning, 0.98, level="warning")
        self.repository.set_shorts_exports(job.id, shorts_exports)

        self.repository.upsert_person_registry_from_people(job.id, vision_result.people)
        self.repository.set_people(job.id, vision_result.people)
        self.repository.set_report(job.id, report)
        self.repository.set_artifacts(job.id, artifacts)
        self.repository.update_job_status(job.id, status="completed", progress=1.0, step="done")
        self._stage_update(job.id, "done", 1.0, "Completed successfully")
        done_message = (
            f"Completed successfully. OpenVINO device={vision_result.device_used}, "
            f"speaker_method={attribution.method}, mask_overlay={should_render_mask_overlay}"
        )
        if warnings:
            done_message = f"{done_message}. Completed with {len(warnings)} warning(s)."
        self.repository.add_event(
            job.id,
            "done",
            done_message,
            1.0,
        )
        if warnings:
            for item in warnings[-5:]:
                self.repository.add_event(job.id, "done", item, 1.0, level="warning")

    def _extract_names_by_person(
        self, segments: list[TranscriptSegment]
    ) -> dict[str, tuple[str, float]]:
        # RU-first + basic EN patterns for self-introduction.
        patterns = [
            re.compile(r"\bменя зовут\s+([А-ЯЁA-Z][а-яёa-z-]{1,30})", flags=re.IGNORECASE),
            re.compile(r"\bзовите меня\s+([А-ЯЁA-Z][а-яёa-z-]{1,30})", flags=re.IGNORECASE),
            re.compile(r"\bmy name is\s+([A-Z][a-z-]{1,30})", flags=re.IGNORECASE),
            re.compile(r"\bcall me\s+([A-Z][a-z-]{1,30})", flags=re.IGNORECASE),
        ]
        by_person: dict[str, dict[str, float]] = {}
        for segment in segments:
            if not segment.speaker_ref:
                continue
            for pattern in patterns:
                match = pattern.search(segment.text)
                if not match:
                    continue
                normalized = self._normalize_name_token(match.group(1))
                if not normalized:
                    continue
                by_person.setdefault(segment.speaker_ref, {})
                by_person[segment.speaker_ref][normalized] = by_person[segment.speaker_ref].get(
                    normalized, 0.0
                ) + max(segment.confidence, 0.2)

        resolved: dict[str, tuple[str, float]] = {}
        for person_id, candidates in by_person.items():
            if not candidates:
                continue
            sorted_candidates = sorted(candidates.items(), key=lambda item: item[1], reverse=True)
            best_name, best_score = sorted_candidates[0]
            total = sum(candidates.values())
            confidence = best_score / max(total, 1e-6)
            # Require stable enough evidence to avoid accidental renaming.
            if confidence < 0.58 and len(sorted_candidates) > 1:
                continue
            resolved[person_id] = (best_name, min(max(confidence, 0.0), 1.0))
        return resolved

    @staticmethod
    def _normalize_name_token(token: str) -> str:
        cleaned = token.strip().strip(".,!?;:\"'()[]{}")
        if not cleaned:
            return ""
        return cleaned[0].upper() + cleaned[1:].lower()

    def _stage_update(
        self,
        job_id: str,
        stage: str,
        stage_progress: float,
        message: str,
        *,
        speed: float | None = None,
        speed_unit: str | None = None,
        eta_seconds: float | None = None,
    ) -> None:
        window = STAGE_WINDOWS.get(stage, (0.0, 1.0))
        local = max(min(stage_progress, 1.0), 0.0)
        global_progress = window[0] + (window[1] - window[0]) * local
        self.repository.set_progress(
            job_id,
            global_progress,
            stage,
            message,
            stage_progress=local,
            speed=speed,
            speed_unit=speed_unit,
            eta_seconds=eta_seconds,
        )

    def _safe_transcribe(
        self,
        audio_path: Path,
        language: str | None,
        auto_detect_language: bool,
        quality_preset: str,
        *,
        progress_callback,
    ) -> list[TranscriptSegment]:
        try:
            return self.asr.transcribe(
                audio_path=audio_path,
                language=language,
                auto_detect_language=auto_detect_language,
                quality_preset=quality_preset,
                progress_callback=progress_callback,
            )
        except Exception as exc:  # noqa: BLE001
            return [
                TranscriptSegment(
                    start=0.0,
                    end=2.0,
                    text=f"[ASR fallback] Failed to run Whisper: {exc}",
                    confidence=0.0,
                )
            ]
