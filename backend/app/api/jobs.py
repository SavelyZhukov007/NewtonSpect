from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse

from ..bootstrap import Container
from ..schemas import (
    ArtifactsResponse,
    CreateJobResponse,
    ExportRequest,
    JobOptions,
    JobView,
    PeopleResponse,
    ReportResponse,
)
from ..services.exporter import create_zip_bundle, filter_files_for_formats

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def _container(request: Request) -> Container:
    return request.app.state.container  # type: ignore[attr-defined]


def _to_job_view(container: Container, job_id: str) -> JobView:
    job = container.repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return JobView(
        id=job.id,
        original_filename=job.original_filename,
        status=job.status,  # type: ignore[arg-type]
        progress=job.progress,
        current_step=job.current_step,
        error_message=job.error_message,
        options=container.repository.decode_options(job.options_json),
        artifacts=container.repository.decode_artifacts(job.artifacts_json),
        created_at=datetime.fromisoformat(job.created_at),
        updated_at=datetime.fromisoformat(job.updated_at),
    )


@router.post("", response_model=CreateJobResponse)
async def create_job(
    request: Request,
    video: UploadFile = File(...),
    language: Annotated[str | None, Form()] = None,
    auto_detect_language: Annotated[bool, Form()] = True,
    quality_preset: Annotated[str, Form()] = "max_quality",
    export_formats: Annotated[str, Form()] = "srt,vtt,ass,mp4_burned",
    detect_people: Annotated[bool, Form()] = True,
    generate_summary: Annotated[bool, Form()] = True,
    enable_active_speaker_model: Annotated[bool, Form()] = True,
) -> CreateJobResponse:
    container = _container(request)
    data = await video.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded video is empty")

    filename = video.filename or "uploaded_video.mp4"
    job_id = str(uuid4())
    parsed_formats = [item.strip() for item in export_formats.split(",") if item.strip()]
    options = JobOptions(
        language=language,
        auto_detect_language=auto_detect_language,
        quality_preset=quality_preset,  # type: ignore[arg-type]
        whisper_model=container.settings.whisper_model,
        export_formats=parsed_formats,  # type: ignore[arg-type]
        detect_people=detect_people,
        generate_summary=generate_summary,
        enable_active_speaker_model=enable_active_speaker_model,
    )

    input_path = container.storage.save_upload(job_id, filename, data)
    container.repository.create_job(
        job_id=job_id,
        original_filename=filename,
        input_video_path=str(input_path),
        options=options,
    )
    return CreateJobResponse(job=_to_job_view(container, job_id))


@router.get("/{job_id}", response_model=JobView)
def get_job(job_id: str, request: Request) -> JobView:
    return _to_job_view(_container(request), job_id)


@router.get("/{job_id}/artifacts", response_model=ArtifactsResponse)
def get_artifacts(job_id: str, request: Request) -> ArtifactsResponse:
    container = _container(request)
    job = _to_job_view(container, job_id)
    return ArtifactsResponse(job_id=job_id, artifacts=job.artifacts)


@router.get("/{job_id}/people", response_model=PeopleResponse)
def get_people(job_id: str, request: Request) -> PeopleResponse:
    container = _container(request)
    job = container.repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    people = container.repository.decode_people(job.people_json)
    return PeopleResponse(job_id=job_id, people=people)


@router.get("/{job_id}/report", response_model=ReportResponse)
def get_report(job_id: str, request: Request) -> ReportResponse:
    container = _container(request)
    job = container.repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    report = container.repository.decode_report(job.report_json)
    return ReportResponse(job_id=job_id, report=report)


@router.post("/{job_id}/export", response_model=ArtifactsResponse)
def export_bundle(job_id: str, payload: ExportRequest, request: Request) -> ArtifactsResponse:
    container = _container(request)
    job = container.repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    artifacts = container.repository.decode_artifacts(job.artifacts_json)
    paths = [Path(artifact.path) for artifact in artifacts]
    selected = filter_files_for_formats(payload.formats, paths)
    if not selected:
        raise HTTPException(
            status_code=400,
            detail="No files available for requested formats. Process job first.",
        )
    zip_artifact = create_zip_bundle(
        job_id=job_id,
        export_dir=container.storage.job_stage_dir(job_id, "exports"),
        selected_files=selected,
    )
    artifacts.append(zip_artifact)
    container.repository.set_artifacts(job_id, artifacts)
    return ArtifactsResponse(job_id=job_id, artifacts=artifacts)


@router.get("/{job_id}/artifacts/{artifact_name}/download")
def download_artifact(job_id: str, artifact_name: str, request: Request) -> FileResponse:
    container = _container(request)
    job = container.repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    artifacts = container.repository.decode_artifacts(job.artifacts_json)
    match = next((item for item in artifacts if item.name == artifact_name), None)
    if match is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    path = Path(match.path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Artifact file missing on disk")
    return FileResponse(path=path, filename=match.name, media_type=match.mime_type)

