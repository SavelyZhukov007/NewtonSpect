from __future__ import annotations

import json
import time
from pathlib import Path

from ..config import Settings
from ..repository import JobRecord, JobRepository
from ..schemas import Artifact, TranscriptSegment, VideoReport
from .asr import ASRService
from .exporter import make_artifact
from .mask_overlay import OpenVINOMaskOverlayRenderer
from .media import MediaService
from .ollama_client import OllamaClient
from .openvino_people import OpenVINOPeopleAnalyzer
from .paths import StorageService
from .reporting import ReportGenerator
from .speaker import ActiveSpeakerAttributor
from .subtitles import write_subtitle_files


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
        self.people_analyzer = OpenVINOPeopleAnalyzer(
            models_dir=settings.openvino_models_dir,
            preferred_devices=settings.preferred_openvino_devices,
        )
        self.speaker_attributor = ActiveSpeakerAttributor(settings.openvino_models_dir)
        self.report_generator = ReportGenerator(
            OllamaClient(settings.ollama_base_url, settings.ollama_model)
        )
        self.mask_renderer = OpenVINOMaskOverlayRenderer(
            settings.openvino_models_dir,
            settings.preferred_openvino_devices,
        )

    def process(self, job: JobRecord) -> None:
        options = self.repository.decode_options(job.options_json)
        artifacts: list[Artifact] = self.repository.decode_artifacts(job.artifacts_json)
        input_video = Path(job.input_video_path)
        self.storage.job_dir(job.id)

        self.repository.update_job_status(job.id, status="running", progress=0.01, step="ingest")
        self._stage_update(job.id, "ingest", 1.0, "Pipeline started")

        audio_dir = self.storage.job_stage_dir(job.id, "audio")
        subtitles_dir = self.storage.job_stage_dir(job.id, "subtitles")
        people_dir = self.storage.job_stage_dir(job.id, "people")
        report_dir = self.storage.job_stage_dir(job.id, "report")
        exports_dir = self.storage.job_stage_dir(job.id, "exports")
        mask_dir = self.storage.job_stage_dir(job.id, "mask")

        audio_path = audio_dir / "audio.wav"
        transcript_path = subtitles_dir / "transcript.json"
        srt_path = subtitles_dir / "captions.srt"
        vtt_path = subtitles_dir / "captions.vtt"
        ass_path = subtitles_dir / "captions.ass"
        burned_video_path = exports_dir / "video_burned.mp4"

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
        self._stage_update(job.id, "asr", 1.0, "Transcription completed", eta_seconds=0.0)
        transcript_path.write_text(
            json.dumps([segment.model_dump() for segment in segments], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
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

            vision_result = self.people_analyzer.analyze(
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
        else:
            from .types import VisionAnalysisResult

            vision_result = VisionAnalysisResult(people=[], samples=[], device_used="disabled")
            self._stage_update(job.id, "vision", 1.0, "Vision disabled")

        # Stage: speaker attribution
        if options.detect_people and options.enable_active_speaker_model and vision_result.samples:
            self._stage_update(job.id, "speaker_attribution", 0.10, "Assigning speaking segments")
            attribution = self.speaker_attributor.assign_speakers(
                segments=segments,
                samples=vision_result.samples,
                use_asd_model=options.enable_active_speaker_model,
            )
            segments = attribution.segments
            if options.enable_subtitles:
                write_subtitle_files(segments, srt_path=srt_path, vtt_path=vtt_path, ass_path=ass_path)
            self._stage_update(
                job.id,
                "speaker_attribution",
                1.0,
                f"Speaker attribution method: {attribution.method}",
            )
        else:
            attribution = type("Attr", (), {"method": "disabled"})()
            self._stage_update(job.id, "speaker_attribution", 1.0, "Speaker attribution disabled")

        # Stage: report
        self._stage_update(job.id, "report", 0.03, "Generating AI report")
        report = (
            self.report_generator.generate(segments=segments, people=vision_result.people)
            if options.generate_summary
            else VideoReport(
                summary_md="Summary generation disabled.",
                key_topics=[],
                latex_blocks=[],
                people_highlights={},
                raw_markdown="",
            )
        )
        for person in vision_result.people:
            person.key_comments = report.people_highlights.get(person.person_id, [])

        report_md_path = report_dir / "report.md"
        report_json_path = report_dir / "report.json"
        report_md_path.write_text(report.summary_md, encoding="utf-8")
        report_json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
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
            ]
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

        # Stage: burned video
        if options.enable_burned_video and "mp4_burned" in options.export_formats and options.enable_subtitles:
            video_duration = self.media.probe_duration_seconds(input_video)
            self._stage_update(job.id, "burned_video", 0.01, "Rendering MP4 with subtitles")

            def _burn_progress(processed: float, total: float, eta: float) -> None:
                progress = processed / total if total > 0 else 0.0
                speed = processed / max(time.perf_counter() - asr_started, 1e-6)
                self._stage_update(
                    job.id,
                    "burned_video",
                    progress,
                    f"Burning subtitles {processed:.1f}/{total:.1f}s",
                    speed=speed,
                    speed_unit="video sec/s",
                    eta_seconds=eta,
                )

            self.media.burn_ass_subtitles_with_progress(
                input_video,
                ass_path=ass_path,
                output_video=burned_video_path,
                duration_seconds=video_duration,
                progress_callback=_burn_progress,
            )
            artifacts.append(
                make_artifact(
                    name=burned_video_path.name,
                    kind="video_burned",
                    path=burned_video_path,
                    mime_type="video/mp4",
                )
            )
            self._stage_update(job.id, "burned_video", 1.0, "Burned MP4 ready")
        else:
            self._stage_update(job.id, "burned_video", 1.0, "Burned video disabled")

        # Stage: mask overlay
        if options.enable_mask_overlay:
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

            mask_result = self.mask_renderer.render(
                input_video,
                output_dir=mask_dir,
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
        else:
            self._stage_update(job.id, "mask_overlay", 1.0, "Mask overlay disabled")

        self.repository.set_people(job.id, vision_result.people)
        self.repository.set_report(job.id, report)
        self.repository.set_artifacts(job.id, artifacts)
        self.repository.update_job_status(job.id, status="completed", progress=1.0, step="done")
        self._stage_update(job.id, "done", 1.0, "Completed successfully")
        self.repository.add_event(
            job.id,
            "done",
            (
                f"Completed successfully. OpenVINO device={vision_result.device_used}, "
                f"speaker_method={attribution.method}, mask_overlay={options.enable_mask_overlay}"
            ),
            1.0,
        )

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
