from __future__ import annotations

import shutil
from pathlib import Path


class StorageService:
    def __init__(self, storage_root: Path) -> None:
        self.storage_root = storage_root
        self.uploads_dir = self.storage_root / "uploads"
        self.jobs_dir = self.storage_root / "jobs"
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    def job_dir(self, job_id: str) -> Path:
        path = self.jobs_dir / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def job_input_path(self, job_id: str, filename: str) -> Path:
        input_dir = self.job_dir(job_id) / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        return input_dir / filename

    def job_stage_dir(self, job_id: str, stage: str) -> Path:
        stage_dir = self.job_dir(job_id) / stage
        stage_dir.mkdir(parents=True, exist_ok=True)
        return stage_dir

    def save_upload(self, job_id: str, filename: str, content: bytes) -> Path:
        input_path = self.job_input_path(job_id, filename)
        input_path.write_bytes(content)
        return input_path

    def cleanup_job_temp(self, job_id: str) -> None:
        temp_dir = self.job_stage_dir(job_id, "temp")
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

