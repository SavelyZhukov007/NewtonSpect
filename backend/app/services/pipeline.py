from __future__ import annotations

import json
from pathlib import Path

from ..config import Settings
from ..repository import JobRecord, JobRepository
from ..schemas import Artifact, TranscriptSegment, VideoReport
from .asr import ASRService
from .exporter import make_artifact
from .media import MediaService
from .ollama_client import OllamaClient
from .openvino_people import OpenVINOPeopleAnalyzer
from .paths import StorageService
from .reporting import ReportGenerator
from .speaker import ActiveSpeakerAttributor
from .subtitles import write_subtitle_files


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

    def process(self, job: JobRecord) -> None:
        options = self.repository.decode_options(job.options_json)
        artifacts: list[Artifact] = self.repository.decode_artifacts(job.artifacts_json)
        input_video = Path(job.input_video_path)
        job_dir = self.storage.job_dir(job.id)

        self.repository.update_job_status(job.id, status="running", progress=0.01, step="ingest")
        self.repository.add_event(job.id, "ingest", "Pipeline started", 0.01)

        audio_dir = self.storage.job_stage_dir(job.id, "audio")
        subtitles_dir = self.storage.job_stage_dir(job.id, "subtitles")
        people_dir = self.storage.job_stage_dir(job.id, "people")
        report_dir = self.storage.job_stage_dir(job.id, "report")
        exports_dir = self.storage.job_stage_dir(job.id, "exports")

        audio_path = audio_dir / "audio.wav"
        transcript_path = subtitles_dir / "transcript.json"
        srt_path = subtitles_dir / "captions.srt"
        vtt_path = subtitles_dir / "captions.vtt"
        ass_path = subtitles_dir / "captions.ass"
        burned_video_path = exports_dir / "video_burned.mp4"

        self.repository.set_progress(job.id, 0.08, "audio_extract", "Extracting audio with ffmpeg")
        self.media.extract_audio_wav(input_video, audio_path)
        artifacts.append(
            make_artifact(
                name=audio_path.name, kind="audio_wav", path=audio_path, mime_type="audio/wav"
            )
        )

        self.repository.set_progress(
            job.id, 0.24, "asr", f"Transcribing speech with Whisper {options.whisper_model}"
        )
        segments = self._safe_transcribe(audio_path, options.language, options.auto_detect_language, options.quality_preset)
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

        self.repository.set_progress(job.id, 0.42, "subtitle_postprocess", "Generating subtitle files")
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
        self.repository.set_artifacts(job.id, artifacts)

        self.repository.set_progress(
            job.id, 0.58, "vision", "Detecting and clustering unique people with OpenVINO"
        )
        vision_result = self.people_analyzer.analyze(input_video, people_output_dir=people_dir)

        self.repository.set_progress(
            job.id,
            0.68,
            "speaker_attribution",
            "Assigning speaking segments to detected people",
        )
        attribution = self.speaker_attributor.assign_speakers(
            segments=segments,
            samples=vision_result.samples,
            use_asd_model=options.enable_active_speaker_model,
        )
        segments = attribution.segments
        write_subtitle_files(segments, srt_path=srt_path, vtt_path=vtt_path, ass_path=ass_path)

        self.repository.set_progress(job.id, 0.78, "report", "Generating AI summary via Ollama")
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

        if "mp4_burned" in options.export_formats:
            self.repository.set_progress(
                job.id, 0.9, "burned_video", "Rendering MP4 with animated subtitles"
            )
            self.media.burn_ass_subtitles(input_video, ass_path=ass_path, output_video=burned_video_path)
            artifacts.append(
                make_artifact(
                    name=burned_video_path.name,
                    kind="video_burned",
                    path=burned_video_path,
                    mime_type="video/mp4",
                )
            )

        self.repository.set_people(job.id, vision_result.people)
        self.repository.set_report(job.id, report)
        self.repository.set_artifacts(job.id, artifacts)
        self.repository.update_job_status(job.id, status="completed", progress=1.0, step="done")
        self.repository.add_event(
            job.id,
            "done",
            (
                f"Completed successfully. OpenVINO device={vision_result.device_used}, "
                f"speaker_method={attribution.method}"
            ),
            1.0,
        )

    def _safe_transcribe(
        self,
        audio_path: Path,
        language: str | None,
        auto_detect_language: bool,
        quality_preset: str,
    ) -> list[TranscriptSegment]:
        try:
            return self.asr.transcribe(
                audio_path=audio_path,
                language=language,
                auto_detect_language=auto_detect_language,
                quality_preset=quality_preset,
            )
        except Exception as exc:  # noqa: BLE001
            return [
                TranscriptSegment(
                    start=0.0,
                    end=2.0,
                    text=f"[ASR fallback] Не удалось выполнить Whisper: {exc}",
                    confidence=0.0,
                )
            ]

