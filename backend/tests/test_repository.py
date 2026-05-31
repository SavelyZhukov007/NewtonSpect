from pathlib import Path

from app.db import init_db
from app.repository import JobRepository
from app.schemas import JobOptions


def test_repository_queue_and_claim(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    repo = JobRepository(db_path)

    repo.create_job(
        job_id="job-1",
        original_filename="video.mp4",
        input_video_path=str(tmp_path / "video.mp4"),
        options=JobOptions(),
        created_by_device="Windows",
        locale="en",
    )
    job = repo.get_job("job-1")
    assert job is not None
    assert job.status == "queued"

    claimed = repo.claim_next_job("worker-a")
    assert claimed is not None
    assert claimed.id == "job-1"
    assert claimed.status == "running"

    repo.set_progress("job-1", 0.5, "asr", "Halfway")
    updated = repo.get_job("job-1")
    assert updated is not None
    assert updated.progress == 0.5
    assert updated.current_step == "asr"
    assert updated.created_by_device == "Windows"
