# AstraOrpheus

AstraOrpheus is a local `frontend + backend + worker` platform for automatic subtitles, people analytics, and AI video summaries.

## Core stack

- ASR: `faster-whisper` + `openai/whisper-large-v3`
- Vision: OpenVINO (person/face/landmarks/re-id, with `MYRIAD -> CPU` fallback)
- Summary: local Ollama `qwen2.5:3b`
- Queue/storage: SQLite + filesystem artifacts
- UI: React + TypeScript (mobile-friendly, RU/EN)

## What is implemented now

- Chunked video upload over WebSocket with live upload telemetry (speed, ETA, bytes)
- Real runtime stage telemetry from backend pipeline:
  - per-stage progress
  - stage message
  - stage speed + unit
  - stage ETA
- Camera studio mode:
  - round preview
  - face-mask preview toggle
  - recording hints
  - streaming chunk upload to backend via WebSocket
  - auto-stop support
- Result tabs:
  - `Subtitles`
  - `Video Export`
  - `People`
  - `AI Summary`
  - `Storage`
- Custom player with subtitle overlay styling (size/color/outline/position)
- Reliable artifact downloads (`fetch -> blob`) + share flow for mobile/iPhone
- ZIP bundle export endpoint and UI action
- Device fingerprint in job author: `device / OS (browser)`

## Pipeline stages

`ingest -> audio_extract -> asr -> subtitle_postprocess -> vision -> speaker_attribution -> report -> burned_video -> mask_overlay -> done`

## Project layout

- `backend/` - FastAPI API + pipeline + repository
- `backend/worker.py` - background worker loop
- `frontend/` - React + Vite app
- `storage/` - DB, uploads, artifacts
- `build.py` - single entrypoint for setup/check/build/run/force

## Requirements

- Python `3.10+`
- Node.js `20+`
- `ffmpeg` in PATH
- OpenVINO runtime
- Ollama running locally with `qwen2.5:3b`

## Quick start

Install dependencies:

```powershell
python build.py setup
```

Run checks:

```powershell
python build.py check
```

Build frontend:

```powershell
python build.py build
```

Run dev stack (`api + worker + vite`):

```powershell
python build.py run
```

## Production run from one command

Use the new force pipeline:

```powershell
python build.py force
```

Flow:

1. Pre-check (when deps are already present)
2. Smart install of missing/invalid deps
3. Strict check + frontend build
4. Production host (`uvicorn + worker`, frontend served from compiled `frontend/dist` via FastAPI)

Clean reinstall variant:

```powershell
python build.py force --clean
```

## API

- `POST /api/v1/jobs` - upload + create job
- `GET /api/v1/jobs` - storage/library list
- `GET /api/v1/jobs/capabilities/formats` - curated + all ffmpeg muxers
- `GET /api/v1/jobs/{job_id}` - status/progress/runtime
- `GET /api/v1/jobs/{job_id}/artifacts` - artifacts list
- `GET /api/v1/jobs/{job_id}/people` - people profiles
- `GET /api/v1/jobs/{job_id}/report` - generated report
- `POST /api/v1/jobs/{job_id}/export` - ZIP bundle build
- `GET /api/v1/jobs/{job_id}/artifacts/{artifact_name}/download` - artifact download
- `WS /api/v1/jobs/ws/upload` - chunked upload channel

## OpenVINO model paths

Priority search paths are controlled by env var `NEWTONSPECT_OPENVINO_MODEL_PATHS`.
By default it includes:

- `C:\Projects\FAI_NCS2_WS\FAI_NCS2_WS\models\intel`
- `<storage>/models/openvino`

Supported mask/people model family includes:

- `person-detection-retail-0013`
- `person-reidentification-retail-0277`
- `face-detection-retail-0004` / `0005`
- `facial-landmarks-35-adas-0002`
- `facial-landmarks-98-detection-0001`
- `face-reidentification-retail-0095`
- `face-recognition-resnet100-arcface-onnx` (if present)
- `age-gender-recognition-retail-0013`
- `emotions-recognition-retail-0003`
- `human-pose-estimation-0001`

## Hugging Face warnings on Windows

For higher rate limits and faster model downloads set:

- `HF_TOKEN=<your_token>`

Symlink cache warning can be addressed by either:

1. Enabling Windows Developer Mode
2. Running Python as Administrator
3. Or silencing warning text:

```powershell
setx HF_HUB_DISABLE_SYMLINKS_WARNING 1
```

## Main env vars

- `NEWTONSPECT_STORAGE_ROOT`
- `NEWTONSPECT_DB_PATH`
- `NEWTONSPECT_OLLAMA_BASE_URL`
- `NEWTONSPECT_OLLAMA_MODEL`
- `NEWTONSPECT_WHISPER_MODEL`
- `NEWTONSPECT_OPENVINO_MODELS_DIR`
- `NEWTONSPECT_OPENVINO_MODEL_PATHS`
- `NEWTONSPECT_OPENVINO_DEVICES`
- `NEWTONSPECT_FRONTEND_DIST_DIR`

## Tests

```powershell
python build.py check
```

## License

MIT
