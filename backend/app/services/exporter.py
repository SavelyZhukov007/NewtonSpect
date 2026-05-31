from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from ..schemas import Artifact, ExportFormat


def artifact_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def make_artifact(name: str, kind: str, path: Path, mime_type: str) -> Artifact:
    return Artifact(
        name=name,
        kind=kind,
        path=str(path),
        mime_type=mime_type,
        created_at=artifact_now(),
    )


def create_zip_bundle(
    *,
    job_id: str,
    export_dir: Path,
    selected_files: list[Path],
) -> Artifact:
    export_dir.mkdir(parents=True, exist_ok=True)
    zip_path = export_dir / f"{job_id}_bundle.zip"
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as zipf:
        for file_path in selected_files:
            if file_path.exists():
                zipf.write(file_path, arcname=file_path.name)
    return make_artifact(
        name=zip_path.name,
        kind="export_zip",
        path=zip_path,
        mime_type="application/zip",
    )


def filter_files_for_formats(formats: list[ExportFormat], all_files: list[Path]) -> list[Path]:
    selected: list[Path] = []
    ext_map = {
        "srt": ".srt",
        "vtt": ".vtt",
        "ass": ".ass",
        "mp4_burned": ".mp4",
    }
    allowed = {ext_map[item] for item in formats if item in ext_map}
    for path in all_files:
        if path.suffix.lower() in allowed:
            selected.append(path)
    return selected

