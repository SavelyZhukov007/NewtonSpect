from __future__ import annotations

import json
import hashlib
import re
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Annotated, BinaryIO
from urllib.parse import quote
from uuid import uuid4

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse

from ..bootstrap import Container
from ..schemas import (
    ArtifactsResponse,
    ChaptersResponse,
    ComparisonResponse,
    CreateJobResponse,
    ExportRequest,
    FactCheckResponse,
    FormatCapabilitiesResponse,
    JobLibraryResponse,
    JobOptions,
    QuotesResponse,
    QualityResponse,
    RunComparison,
    ShortsRequest,
    ShortsResponse,
    SubtitlesResponse,
    SubtitlesUpdateRequest,
    TranscriptSegment,
    TranslationsResponse,
    VideoFormatCapability,
    JobView,
    PeopleResponse,
    ReportResponse,
)
from ..services.exporter import create_zip_bundle, filter_files_for_formats, make_artifact
from ..services.insights import compare_runs
from ..services.media import MediaService
from ..services.shorts import ShortsGenerator
from ..services.subtitles import write_subtitle_files

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@dataclass
class UploadSession:
    upload_id: str
    filename: str
    temp_path: Path
    handle: BinaryIO
    total_size: int | None
    received_bytes: int
    started_at: float


def _container(request: Request) -> Container:
    return request.app.state.container  # type: ignore[attr-defined]


def _to_job_view(container: Container, job_id: str) -> JobView:
    job = container.repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return JobView(
        id=job.id,
        original_filename=job.original_filename,
        source_fingerprint=job.source_fingerprint,
        created_by_device=job.created_by_device,
        locale=job.locale,  # type: ignore[arg-type]
        status=job.status,  # type: ignore[arg-type]
        progress=job.progress,
        current_step=job.current_step,
        error_message=job.error_message,
        options=container.repository.decode_options(job.options_json),
        artifacts=container.repository.decode_artifacts(job.artifacts_json),
        runtime=container.repository.decode_runtime(job.runtime_json),
        created_at=datetime.fromisoformat(job.created_at),
        updated_at=datetime.fromisoformat(job.updated_at),
    )


def _device_label(user_agent: str | None) -> str:
    if not user_agent:
        return "Unknown device"

    ua = user_agent
    lower = ua.lower()

    def _extract_version(pattern: str) -> str | None:
        match = re.search(pattern, ua, flags=re.IGNORECASE)
        if not match:
            return None
        return match.group(1).replace("_", ".")

    os_label = "Unknown OS"
    if "iphone" in lower or "ipad" in lower:
        ios_ver = _extract_version(r"os ([\d_]+)")
        os_label = f"iOS {ios_ver}" if ios_ver else "iOS"
    elif "android" in lower:
        android_ver = _extract_version(r"android ([\d.]+)")
        os_label = f"Android {android_ver}" if android_ver else "Android"
    elif "windows nt" in lower:
        windows_ver = _extract_version(r"windows nt ([\d.]+)")
        os_label = f"Windows NT {windows_ver}" if windows_ver else "Windows"
    elif "mac os x" in lower:
        mac_ver = _extract_version(r"mac os x ([\d_]+)")
        os_label = f"macOS {mac_ver}" if mac_ver else "macOS"
    elif "linux" in lower:
        os_label = "Linux"

    device_label = "Unknown device"
    if "iphone" in lower:
        device_label = "Apple iPhone"
    elif "ipad" in lower:
        device_label = "Apple iPad"
    elif "android" in lower:
        device_label = "Android device"
        model_match = re.search(r"\(([^)]*)\)", ua)
        if model_match:
            parts = [part.strip() for part in model_match.group(1).split(";")]
            for part in reversed(parts):
                part_lower = part.lower()
                if (
                    part
                    and "android" not in part_lower
                    and "linux" not in part_lower
                    and "wv" != part_lower
                    and "mobile" not in part_lower
                    and "build/" not in part_lower
                ):
                    device_label = part
                    break
    elif "windows" in lower:
        device_label = "Windows PC"
    elif "macintosh" in lower or "mac os x" in lower:
        device_label = "Mac"
    elif "linux" in lower:
        device_label = "Linux device"

    browser_label = "Unknown browser"
    if "edg/" in lower:
        browser_label = "Edge"
    elif "opr/" in lower or "opera" in lower:
        browser_label = "Opera"
    elif "chrome/" in lower and "chromium" not in lower:
        browser_label = "Chrome"
    elif "firefox/" in lower:
        browser_label = "Firefox"
    elif "safari/" in lower and "chrome/" not in lower:
        browser_label = "Safari"

    return f"{device_label} / {os_label} ({browser_label})"


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _as_string(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned if cleaned else None
    return str(value).strip() or None


def _as_int(value: object, default: int) -> int:
    try:
        if value is None:
            return default
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _normalize_export_formats(value: object) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                result.append(item.strip())
        return result
    return []


def _as_string_list(value: object, *, delimiter: str = ",") -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(delimiter) if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _build_options(
    container: Container,
    *,
    language: str | None,
    auto_detect_language: bool,
    quality_preset: str,
    export_formats: list[str],
    detect_people: bool,
    generate_summary: bool,
    enable_active_speaker_model: bool,
    enable_subtitles: bool,
    enable_burned_video: bool,
    enable_mask_overlay: bool,
    ui_locale: str,
    streaming_mode: str = "dual_pass_hq",
    camera_mode: bool = False,
    auto_stop_seconds: int = 20,
    show_face_mask_preview: bool = False,
    output_video_format: str = "mp4",
    subtitle_embed_mode: str = "auto",
    subtitle_style: dict[str, object] | None = None,
    generate_shorts: bool = False,
    shorts_preset: dict[str, object] | None = None,
    privacy_mode: str = "auto_risk",
    translate_languages: list[str] | None = None,
    enable_fact_check: bool = True,
    enable_chapters: bool = True,
    enable_quotes: bool = True,
    enable_quality_score: bool = True,
    platform_presets: list[str] | None = None,
    enable_live_draft: bool = False,
) -> JobOptions:
    return JobOptions(
        language=language,
        auto_detect_language=auto_detect_language,
        quality_preset=quality_preset,  # type: ignore[arg-type]
        whisper_model=container.settings.whisper_model,
        export_formats=export_formats,  # type: ignore[arg-type]
        detect_people=detect_people,
        generate_summary=generate_summary,
        enable_active_speaker_model=enable_active_speaker_model,
        enable_subtitles=enable_subtitles,
        enable_burned_video=enable_burned_video,
        enable_mask_overlay=enable_mask_overlay,
        ui_locale="ru" if ui_locale == "ru" else "en",
        streaming_mode=streaming_mode if streaming_mode in {"dual_pass_hq", "final_only_hq", "live_only_fast"} else "dual_pass_hq",  # type: ignore[arg-type]
        camera_mode=camera_mode,
        auto_stop_seconds=max(5, min(int(auto_stop_seconds), 120)),
        show_face_mask_preview=show_face_mask_preview,
        output_video_format=(output_video_format or "mp4").lower(),
        subtitle_embed_mode=subtitle_embed_mode if subtitle_embed_mode in {"auto", "embedded", "sidecar", "burned"} else "auto",  # type: ignore[arg-type]
        subtitle_style=subtitle_style or {},
        generate_shorts=generate_shorts,
        shorts_preset=shorts_preset or {},
        privacy_mode=privacy_mode if privacy_mode in {"auto_risk", "enabled", "disabled"} else "auto_risk",  # type: ignore[arg-type]
        translate_languages=translate_languages or [],
        enable_fact_check=enable_fact_check,
        enable_chapters=enable_chapters,
        enable_quotes=enable_quotes,
        enable_quality_score=enable_quality_score,
        platform_presets=platform_presets or [],
        enable_live_draft=enable_live_draft,
    )


def _job_options_from_payload(container: Container, payload: dict[str, object]) -> JobOptions:
    parsed_formats = _normalize_export_formats(payload.get("export_formats"))
    if not parsed_formats:
        parsed_formats = _normalize_export_formats(payload.get("exportFormats"))
    if not parsed_formats:
        parsed_formats = ["srt", "vtt", "ass", "mp4_burned"]

    return _build_options(
        container,
        language=_as_string(payload.get("language")),
        auto_detect_language=_as_bool(payload.get("auto_detect_language"), True)
        if "auto_detect_language" in payload
        else _as_bool(payload.get("autoDetectLanguage"), True),
        quality_preset=str(
            payload.get("quality_preset")
            if payload.get("quality_preset") is not None
            else payload.get("qualityPreset") or "max_quality"
        ),
        export_formats=parsed_formats,
        detect_people=_as_bool(
            payload.get("detect_people")
            if payload.get("detect_people") is not None
            else payload.get("detectPeople"),
            True,
        ),
        generate_summary=_as_bool(
            payload.get("generate_summary")
            if payload.get("generate_summary") is not None
            else payload.get("generateSummary"),
            True,
        ),
        enable_active_speaker_model=_as_bool(
            payload.get("enable_active_speaker_model")
            if payload.get("enable_active_speaker_model") is not None
            else payload.get("enableActiveSpeakerModel"),
            True,
        ),
        enable_subtitles=_as_bool(
            payload.get("enable_subtitles")
            if payload.get("enable_subtitles") is not None
            else payload.get("enableSubtitles"),
            True,
        ),
        enable_burned_video=_as_bool(
            payload.get("enable_burned_video")
            if payload.get("enable_burned_video") is not None
            else payload.get("enableBurnedVideo"),
            True,
        ),
        enable_mask_overlay=_as_bool(
            payload.get("enable_mask_overlay")
            if payload.get("enable_mask_overlay") is not None
            else payload.get("enableMaskOverlay"),
            False,
        ),
        ui_locale=str(
            payload.get("ui_locale")
            if payload.get("ui_locale") is not None
            else payload.get("uiLocale") or "en"
        ),
        streaming_mode=str(
            payload.get("streaming_mode")
            if payload.get("streaming_mode") is not None
            else payload.get("streamingMode") or "dual_pass_hq"
        ),
        camera_mode=_as_bool(
            payload.get("camera_mode")
            if payload.get("camera_mode") is not None
            else payload.get("cameraMode"),
            False,
        ),
        auto_stop_seconds=_as_int(
            payload.get("auto_stop_seconds")
            if payload.get("auto_stop_seconds") is not None
            else payload.get("autoStopSeconds"),
            20,
        ),
        show_face_mask_preview=_as_bool(
            payload.get("show_face_mask_preview")
            if payload.get("show_face_mask_preview") is not None
            else payload.get("showFaceMaskPreview"),
            False,
        ),
        output_video_format=str(
            payload.get("output_video_format")
            if payload.get("output_video_format") is not None
            else payload.get("outputVideoFormat") or "mp4"
        ),
        subtitle_embed_mode=str(
            payload.get("subtitle_embed_mode")
            if payload.get("subtitle_embed_mode") is not None
            else payload.get("subtitleEmbedMode") or "auto"
        ),
        subtitle_style=payload.get("subtitle_style")
        if isinstance(payload.get("subtitle_style"), dict)
        else payload.get("subtitleStyle")
        if isinstance(payload.get("subtitleStyle"), dict)
        else {},
        generate_shorts=_as_bool(
            payload.get("generate_shorts")
            if payload.get("generate_shorts") is not None
            else payload.get("generateShorts"),
            False,
        ),
        shorts_preset=payload.get("shorts_preset")
        if isinstance(payload.get("shorts_preset"), dict)
        else payload.get("shortsPreset")
        if isinstance(payload.get("shortsPreset"), dict)
        else {},
        privacy_mode=str(
            payload.get("privacy_mode")
            if payload.get("privacy_mode") is not None
            else payload.get("privacyMode") or "auto_risk"
        ),
        translate_languages=_as_string_list(
            payload.get("translate_languages")
            if payload.get("translate_languages") is not None
            else payload.get("translateLanguages")
        ),
        enable_fact_check=_as_bool(
            payload.get("enable_fact_check")
            if payload.get("enable_fact_check") is not None
            else payload.get("enableFactCheck"),
            True,
        ),
        enable_chapters=_as_bool(
            payload.get("enable_chapters")
            if payload.get("enable_chapters") is not None
            else payload.get("enableChapters"),
            True,
        ),
        enable_quotes=_as_bool(
            payload.get("enable_quotes")
            if payload.get("enable_quotes") is not None
            else payload.get("enableQuotes"),
            True,
        ),
        enable_quality_score=_as_bool(
            payload.get("enable_quality_score")
            if payload.get("enable_quality_score") is not None
            else payload.get("enableQualityScore"),
            True,
        ),
        platform_presets=_as_string_list(
            payload.get("platform_presets")
            if payload.get("platform_presets") is not None
            else payload.get("platformPresets")
        ),
        enable_live_draft=_as_bool(
            payload.get("enable_live_draft")
            if payload.get("enable_live_draft") is not None
            else payload.get("enableLiveDraft"),
            False,
        ),
    )


def _persist_job(
    container: Container,
    *,
    job_id: str,
    filename: str,
    input_path: Path,
    source_fingerprint: str,
    options: JobOptions,
    user_agent: str | None,
) -> JobView:
    device_label = _device_label(user_agent)
    container.repository.create_job(
        job_id=job_id,
        original_filename=filename,
        input_video_path=str(input_path),
        source_fingerprint=source_fingerprint,
        options=options,
        created_by_device=device_label,
        locale=options.ui_locale,
    )
    return _to_job_view(container, job_id)


def _cleanup_upload_session(session: UploadSession | None) -> None:
    if session is None:
        return
    with suppress(Exception):
        if not session.handle.closed:
            session.handle.close()
    with suppress(FileNotFoundError):
        session.temp_path.unlink()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


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
    enable_subtitles: Annotated[bool, Form()] = True,
    enable_burned_video: Annotated[bool, Form()] = True,
    enable_mask_overlay: Annotated[bool, Form()] = False,
    ui_locale: Annotated[str, Form()] = "en",
    streaming_mode: Annotated[str, Form()] = "dual_pass_hq",
    camera_mode: Annotated[bool, Form()] = False,
    auto_stop_seconds: Annotated[int, Form()] = 20,
    show_face_mask_preview: Annotated[bool, Form()] = False,
    output_video_format: Annotated[str, Form()] = "mp4",
    subtitle_embed_mode: Annotated[str, Form()] = "auto",
    subtitle_style_json: Annotated[str | None, Form()] = None,
    generate_shorts: Annotated[bool, Form()] = False,
    shorts_preset_json: Annotated[str | None, Form()] = None,
    privacy_mode: Annotated[str, Form()] = "auto_risk",
    translate_languages: Annotated[str, Form()] = "",
    enable_fact_check: Annotated[bool, Form()] = True,
    enable_chapters: Annotated[bool, Form()] = True,
    enable_quotes: Annotated[bool, Form()] = True,
    enable_quality_score: Annotated[bool, Form()] = True,
    platform_presets: Annotated[str, Form()] = "",
    enable_live_draft: Annotated[bool, Form()] = False,
) -> CreateJobResponse:
    container = _container(request)
    data = await video.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded video is empty")

    filename = Path(video.filename or "uploaded_video.mp4").name
    parsed_formats = [item.strip() for item in export_formats.split(",") if item.strip()]
    subtitle_style: dict[str, object] = {}
    shorts_preset: dict[str, object] = {}
    if subtitle_style_json:
        try:
            raw_style = json.loads(subtitle_style_json)
            if isinstance(raw_style, dict):
                subtitle_style = raw_style
        except json.JSONDecodeError:
            subtitle_style = {}
    if shorts_preset_json:
        try:
            raw_preset = json.loads(shorts_preset_json)
            if isinstance(raw_preset, dict):
                shorts_preset = raw_preset
        except json.JSONDecodeError:
            shorts_preset = {}
    options = _build_options(
        container,
        language=language,
        auto_detect_language=auto_detect_language,
        quality_preset=quality_preset,
        export_formats=parsed_formats,
        detect_people=detect_people,
        generate_summary=generate_summary,
        enable_active_speaker_model=enable_active_speaker_model,
        enable_subtitles=enable_subtitles,
        enable_burned_video=enable_burned_video,
        enable_mask_overlay=enable_mask_overlay,
        ui_locale=ui_locale,
        streaming_mode=streaming_mode,
        camera_mode=camera_mode,
        auto_stop_seconds=auto_stop_seconds,
        show_face_mask_preview=show_face_mask_preview,
        output_video_format=output_video_format,
        subtitle_embed_mode=subtitle_embed_mode,
        subtitle_style=subtitle_style,
        generate_shorts=generate_shorts,
        shorts_preset=shorts_preset,
        privacy_mode=privacy_mode,
        translate_languages=_as_string_list(translate_languages),
        enable_fact_check=enable_fact_check,
        enable_chapters=enable_chapters,
        enable_quotes=enable_quotes,
        enable_quality_score=enable_quality_score,
        platform_presets=_as_string_list(platform_presets),
        enable_live_draft=enable_live_draft,
    )

    job_id = str(uuid4())
    source_fingerprint = _sha256_bytes(data)
    input_path = container.storage.save_upload(job_id, filename, data)
    job_view = _persist_job(
        container,
        job_id=job_id,
        filename=filename,
        input_path=input_path,
        source_fingerprint=source_fingerprint,
        options=options,
        user_agent=request.headers.get("user-agent"),
    )
    return CreateJobResponse(job=job_view)


@router.websocket("/ws/upload")
async def create_job_chunked_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    container: Container = websocket.app.state.container  # type: ignore[attr-defined]
    session: UploadSession | None = None
    try:
        while True:
            message = await websocket.receive()

            if message.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code", 1000))

            payload_bytes = message.get("bytes")
            if payload_bytes is not None:
                if session is None:
                    await websocket.send_json(
                        {"type": "error", "message": "Upload is not initialized. Send start first."}
                    )
                    continue
                session.handle.write(payload_bytes)
                session.received_bytes += len(payload_bytes)
                elapsed = max(perf_counter() - session.started_at, 1e-6)
                speed = session.received_bytes / elapsed
                eta_seconds: float | None = None
                percent: float | None = None
                if session.total_size:
                    remaining = max(session.total_size - session.received_bytes, 0)
                    eta_seconds = remaining / max(speed, 1e-6)
                    percent = min(session.received_bytes / session.total_size, 1.0)
                await websocket.send_json(
                    {
                        "type": "progress",
                        "upload_id": session.upload_id,
                        "received_bytes": session.received_bytes,
                        "total_size": session.total_size,
                        "percent": percent,
                        "speed_bytes_per_sec": speed,
                        "eta_seconds": eta_seconds,
                    }
                )
                continue

            raw_text = message.get("text")
            if raw_text is None:
                continue

            try:
                payload = json.loads(raw_text)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON payload."})
                continue

            message_type = payload.get("type")
            if message_type == "start":
                if session is not None:
                    await websocket.send_json({"type": "error", "message": "Upload already started."})
                    continue
                filename = Path(str(payload.get("filename") or "uploaded_video.mp4")).name
                upload_id = str(uuid4())
                temp_dir = container.storage.uploads_dir / "chunked"
                temp_dir.mkdir(parents=True, exist_ok=True)
                temp_path = temp_dir / f"{upload_id}_{filename}"
                total_size_raw = payload.get("total_size", payload.get("totalSize"))
                try:
                    total_size = (
                        int(total_size_raw) if isinstance(total_size_raw, (int, float, str)) else None
                    )
                except (TypeError, ValueError):
                    total_size = None
                handle = temp_path.open("wb")
                session = UploadSession(
                    upload_id=upload_id,
                    filename=filename,
                    temp_path=temp_path,
                    handle=handle,
                    total_size=total_size if total_size and total_size > 0 else None,
                    received_bytes=0,
                    started_at=perf_counter(),
                )
                await websocket.send_json(
                    {
                        "type": "started",
                        "upload_id": upload_id,
                        "filename": filename,
                        "total_size": session.total_size,
                    }
                )
                continue

            if message_type == "finish":
                if session is None:
                    await websocket.send_json({"type": "error", "message": "Upload session not found."})
                    continue
                session.handle.close()
                if session.received_bytes == 0:
                    _cleanup_upload_session(session)
                    session = None
                    await websocket.send_json({"type": "error", "message": "Uploaded video is empty."})
                    continue

                options_payload = payload.get("options")
                if not isinstance(options_payload, dict):
                    options_payload = {}
                options = _job_options_from_payload(container, options_payload)
                job_id = str(uuid4())
                final_input_path = container.storage.job_input_path(job_id, session.filename)
                session.temp_path.replace(final_input_path)
                source_fingerprint = _sha256_file(final_input_path)
                job_view = _persist_job(
                    container,
                    job_id=job_id,
                    filename=session.filename,
                    input_path=final_input_path,
                    source_fingerprint=source_fingerprint,
                    options=options,
                    user_agent=websocket.headers.get("user-agent"),
                )
                await websocket.send_json({"type": "completed", "job": job_view.model_dump(mode="json")})
                await websocket.send_json({"type": "final_pass_started", "job_id": job_id})
                await websocket.send_json(
                    {
                        "type": "live_progress",
                        "phase": "final_pass",
                        "job_id": job_id,
                        "progress": 0.0,
                    }
                )
                if options.enable_live_draft:
                    await websocket.send_json(
                        {
                            "type": "live_transcript_delta",
                            "message": "Draft mode enabled. Final HQ pass started in background.",
                        }
                    )
                    await websocket.send_json(
                        {
                            "type": "draft_summary_delta",
                            "message": "Draft summary will be available after worker stages.",
                        }
                    )
                await websocket.send_json({"type": "final_pass_completed", "job_id": job_id})
                session = None
                await websocket.close()
                return

            if message_type == "cancel":
                _cleanup_upload_session(session)
                session = None
                await websocket.send_json({"type": "cancelled"})
                await websocket.close()
                return

            await websocket.send_json({"type": "error", "message": "Unknown message type."})
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover - defensive guard for long-running ws uploads
        with suppress(Exception):
            await websocket.send_json({"type": "error", "message": str(exc)})
    finally:
        _cleanup_upload_session(session)


@router.get("", response_model=JobLibraryResponse)
def list_jobs(request: Request, limit: int = 100) -> JobLibraryResponse:
    container = _container(request)
    items = [_to_job_view(container, job.id) for job in container.repository.list_jobs(limit=limit)]
    return JobLibraryResponse(items=items)


@router.get("/capabilities/formats", response_model=FormatCapabilitiesResponse)
def get_format_capabilities(request: Request) -> FormatCapabilitiesResponse:
    container = _container(request)
    media = MediaService()
    curated_rows = media.curated_video_formats()
    curated = [
        VideoFormatCapability(
            format=str(row["format"]),
            ffmpeg_muxer=str(row["ffmpeg_muxer"]),
            curated=True,
            can_embed_subtitles=bool(row["can_embed_subtitles"]),
            preferred_subtitle_codec=(
                str(row["preferred_subtitle_codec"]) if row["preferred_subtitle_codec"] else None
            ),
            notes=str(row["notes"]) if row["notes"] else None,
        )
        for row in curated_rows
    ]
    curated_muxers = {item.ffmpeg_muxer for item in curated}
    all_muxers = [
        VideoFormatCapability(
            format=muxer,
            ffmpeg_muxer=muxer,
            curated=muxer in curated_muxers,
            can_embed_subtitles=muxer in {"mp4", "mov", "matroska"},
            preferred_subtitle_codec=(
                "mov_text" if muxer in {"mp4", "mov"} else "srt" if muxer == "matroska" else None
            ),
            notes=None,
        )
        for muxer in media.list_ffmpeg_muxers()
    ]
    _ = container  # explicit use to preserve container lifecycle expectations
    return FormatCapabilitiesResponse(curated=curated, all_muxers=all_muxers)


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


@router.get("/{job_id}/chapters", response_model=ChaptersResponse)
def get_chapters(job_id: str, request: Request) -> ChaptersResponse:
    container = _container(request)
    if container.repository.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return ChaptersResponse(job_id=job_id, chapters=container.repository.get_chapters(job_id))


@router.get("/{job_id}/quotes", response_model=QuotesResponse)
def get_quotes(job_id: str, request: Request) -> QuotesResponse:
    container = _container(request)
    if container.repository.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return QuotesResponse(job_id=job_id, quotes=container.repository.get_quotes(job_id))


@router.get("/{job_id}/quality", response_model=QualityResponse)
def get_quality(job_id: str, request: Request) -> QualityResponse:
    container = _container(request)
    if container.repository.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return QualityResponse(job_id=job_id, quality=container.repository.get_quality(job_id))


@router.get("/{job_id}/comparison", response_model=ComparisonResponse)
def get_comparison(job_id: str, request: Request) -> ComparisonResponse:
    container = _container(request)
    current = container.repository.get_job(job_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Job not found")
    previous = container.repository.previous_job_for_fingerprint(
        source_fingerprint=current.source_fingerprint,
        current_job_id=current.id,
    )
    current_segments = container.repository.get_subtitle_segments(current.id)
    current_people = container.repository.decode_people(current.people_json)
    current_quality = container.repository.get_quality(current.id)
    if previous is None:
        comparison = RunComparison(
            current_job_id=current.id,
            previous_job_id=None,
            summary_md="No previous run for this source fingerprint.",
        )
    else:
        previous_segments = container.repository.get_subtitle_segments(previous.id)
        previous_people = container.repository.decode_people(previous.people_json)
        previous_quality = container.repository.get_quality(previous.id)
        comparison = compare_runs(
            current_job_id=current.id,
            previous_job_id=previous.id,
            current_segments=current_segments,
            previous_segments=previous_segments,
            current_people=current_people,
            previous_people=previous_people,
            current_quality=current_quality,
            previous_quality=previous_quality,
        )
    return ComparisonResponse(job_id=job_id, comparison=comparison)


@router.get("/{job_id}/subtitles", response_model=SubtitlesResponse)
def get_subtitles(job_id: str, request: Request) -> SubtitlesResponse:
    container = _container(request)
    if container.repository.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return SubtitlesResponse(
        job_id=job_id,
        segments=container.repository.get_subtitle_segments(job_id),
        revisions=container.repository.get_subtitle_revisions(job_id),
    )


@router.put("/{job_id}/subtitles", response_model=SubtitlesResponse)
def update_subtitles(
    job_id: str,
    payload: SubtitlesUpdateRequest,
    request: Request,
) -> SubtitlesResponse:
    container = _container(request)
    job = container.repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    segments = payload.segments
    container.repository.set_subtitle_segments(
        job_id,
        segments,
        editor_device=_device_label(request.headers.get("user-agent")),
        note=payload.note,
    )
    subtitles_dir = container.storage.job_stage_dir(job_id, "subtitles")
    srt_path = subtitles_dir / "captions.srt"
    vtt_path = subtitles_dir / "captions.vtt"
    ass_path = subtitles_dir / "captions.ass"
    write_subtitle_files(segments, srt_path=srt_path, vtt_path=vtt_path, ass_path=ass_path)
    artifacts = container.repository.decode_artifacts(job.artifacts_json)
    missing_paths = {
        "subtitle_srt": srt_path,
        "subtitle_vtt": vtt_path,
        "subtitle_ass": ass_path,
    }
    existing_kinds = {item.kind for item in artifacts}
    for kind, path in missing_paths.items():
        if kind not in existing_kinds and path.exists():
            mime = (
                "application/x-subrip"
                if kind == "subtitle_srt"
                else "text/vtt"
                if kind == "subtitle_vtt"
                else "text/x-ssa"
            )
            artifacts.append(
                make_artifact(name=path.name, kind=kind, path=path, mime_type=mime)
            )
    container.repository.set_artifacts(job_id, artifacts)
    return SubtitlesResponse(
        job_id=job_id,
        segments=container.repository.get_subtitle_segments(job_id),
        revisions=container.repository.get_subtitle_revisions(job_id),
    )


@router.get("/{job_id}/translations", response_model=TranslationsResponse)
def get_translations(job_id: str, request: Request) -> TranslationsResponse:
    container = _container(request)
    if container.repository.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return TranslationsResponse(job_id=job_id, tracks=container.repository.get_translations(job_id))


@router.get("/{job_id}/fact-check", response_model=FactCheckResponse)
def get_fact_check(job_id: str, request: Request) -> FactCheckResponse:
    container = _container(request)
    if container.repository.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return FactCheckResponse(job_id=job_id, items=container.repository.get_fact_checks(job_id))


@router.post("/{job_id}/shorts", response_model=ShortsResponse)
def build_shorts(job_id: str, payload: ShortsRequest, request: Request) -> ShortsResponse:
    container = _container(request)
    job = container.repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    chapters = container.repository.get_chapters(job_id)
    quotes = container.repository.get_quotes(job_id)
    from ..schemas import ShortsPreset

    generator = ShortsGenerator(MediaService())
    shorts = generator.generate(
        job_id=job_id,
        input_video=Path(job.input_video_path),
        output_dir=container.storage.job_shorts_dir(job_id),
        preset=ShortsPreset(
            clip_count=max(payload.clip_count, 1),
            clip_duration_seconds=max(payload.clip_duration_seconds, 10),
        ),
        quotes=quotes,
        chapters=chapters,
    )
    container.repository.set_shorts_exports(job_id, shorts)
    artifacts = container.repository.decode_artifacts(job.artifacts_json)
    for short in shorts:
        artifacts.append(
            make_artifact(
                name=Path(short.path).name,
                kind="short_video",
                path=Path(short.path),
                mime_type="video/mp4",
            )
        )
    container.repository.set_artifacts(job_id, artifacts)
    return ShortsResponse(job_id=job_id, shorts=shorts)


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
    file_size = path.stat().st_size
    safe_name = match.name.replace('"', "_")
    return FileResponse(
        path=path,
        filename=safe_name,
        media_type=match.mime_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{safe_name}"; '
                f"filename*=UTF-8''{quote(safe_name)}"
            ),
            "Content-Length": str(file_size),
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
        },
    )
