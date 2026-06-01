from pathlib import Path

from app.bootstrap import build_container
from app.schemas import JobOptions, PersonProfile, PersonTrackStats, TranscriptSegment, VideoReport
from app.services.types import FaceTrackSample, VisionAnalysisResult


def test_pipeline_end_to_end_with_mocks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NEWTONSPECT_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("NEWTONSPECT_DB_PATH", str(tmp_path / "storage" / "db.sqlite3"))
    container = build_container()

    video_path = tmp_path / "demo.mp4"
    video_path.write_bytes(b"fake video")
    options = JobOptions(
        export_formats=["srt", "vtt", "ass", "mp4_burned"],
        subtitle_embed_mode="burned",
    )
    container.repository.create_job(
        job_id="job-e2e",
        original_filename="demo.mp4",
        input_video_path=str(video_path),
        options=options,
        created_by_device="Windows",
        locale="en",
    )
    job = container.repository.get_job("job-e2e")
    assert job is not None

    pipeline = container.build_pipeline()

    def fake_extract(_video: Path, output_wav: Path) -> Path:
        output_wav.write_bytes(b"RIFFfake")
        return output_wav

    def fake_transcribe(**_kwargs):
        return [
            TranscriptSegment(start=0.0, end=2.0, text="Тестовая фраза", confidence=0.9),
            TranscriptSegment(start=2.1, end=3.8, text="Еще одна фраза", confidence=0.8),
        ]

    def fake_analyze(_video: Path, people_output_dir: Path, progress_callback=None) -> VisionAnalysisResult:
        if progress_callback:
            progress_callback(10, 10, 1.0)
        portrait = people_output_dir / "P001.jpg"
        portrait.parent.mkdir(parents=True, exist_ok=True)
        portrait.write_bytes(b"\xff\xd8\xff\xd9")
        people = [
            PersonProfile(
                person_id="P001",
                portrait_path=str(portrait),
                track_stats=PersonTrackStats(
                    screen_time_seconds=10.0,
                    first_seen=0.0,
                    last_seen=10.0,
                    avg_confidence=0.8,
                    speaking_seconds=4.2,
                ),
                key_comments=[],
            )
        ]
        samples = [FaceTrackSample(person_id="P001", time_sec=1.0, confidence=0.8, mouth_activity=0.4)]
        return VisionAnalysisResult(people=people, samples=samples, device_used="CPU")

    def fake_report(**_kwargs) -> VideoReport:
        return VideoReport(
            summary_md="## Summary\nTest",
            latex_blocks=["E=mc^2"],
            key_topics=["Topic A"],
            people_highlights={"P001": ["Главный тезис"]},
            raw_markdown="",
        )

    def fake_burn(
        _video: Path,
        ass_path: Path,
        output_video: Path,
        duration_seconds: float,
        container_format: str | None = None,
        progress_callback=None,
    ) -> Path:
        _ = ass_path
        _ = duration_seconds
        _ = container_format
        if progress_callback:
            progress_callback(2.0, 2.0, 0.0)
        output_video.write_bytes(b"fake mp4")
        return output_video

    monkeypatch.setattr(pipeline.media, "extract_audio_wav", fake_extract)
    monkeypatch.setattr(pipeline.asr, "transcribe", fake_transcribe)
    monkeypatch.setattr(pipeline.people_analyzer, "analyze", fake_analyze)
    monkeypatch.setattr(pipeline.report_generator, "generate", fake_report)
    monkeypatch.setattr(pipeline.media, "burn_ass_subtitles_with_progress", fake_burn)

    pipeline.process(job)

    final_job = container.repository.get_job("job-e2e")
    assert final_job is not None
    assert final_job.status == "completed"
    artifacts = container.repository.decode_artifacts(final_job.artifacts_json)
    suffixes = {Path(item.path).suffix for item in artifacts}
    assert ".srt" in suffixes
    assert ".vtt" in suffixes
    assert ".ass" in suffixes
    assert ".mp4" in suffixes

    people = container.repository.decode_people(final_job.people_json)
    assert people[0].person_id == "P001"
    assert people[0].key_comments
