# NewtonSpect

NewtonSpect is a local `frontend + backend + worker` platform for automatic subtitling and video analytics.

Core stack:

- ASR: `faster-whisper` + `openai/whisper-large-v3`
- Summary: local Ollama `qwen2.5:3b`
- Vision: OpenVINO (NCS2-ready with `MYRIAD -> CPU` fallback)
- Queue/storage: SQLite + artifact filesystem

## What V1 does

- Upload video and process it in background (UI never blocks)
- Real per-stage runtime telemetry:
  - stage progress in %
  - live speed
  - ETA
- Chunked upload over WebSocket with live upload telemetry
- RU/EN localization switch in UI
- Feature toggles per job (enable/disable parts of pipeline)
- Mobile-ready UI for iPhone/Android uploads and downloads
- Storage page (job library) to open old jobs and re-download artifacts
- Job author tagged by detected `device + OS + browser` from `User-Agent`
- In-app video preview player for source and generated outputs
- Exports:
  - subtitles: `SRT`, `VTT`, `ASS`
  - video with burned subtitles: `MP4`
  - optional masked video with OpenVINO overlays
  - ZIP bundle

## Architecture

- `backend/`: FastAPI API + queue repository + pipeline services
- `backend/worker.py`: background poller worker
- `frontend/`: React + Vite + TypeScript
- `storage/`: DB, uploads, generated artifacts
- `build.py`: one-file setup/check/build/run entrypoint

Pipeline stages:

`ingest -> audio_extract -> asr -> subtitle_postprocess -> vision -> speaker_attribution -> report -> burned_video -> mask_overlay -> done`

## Requirements

- Python `3.10+`
- Node.js `20+`
- `ffmpeg` in PATH
- Ollama with `qwen2.5:3b` pulled locally
- OpenVINO runtime installed

## Quick start

1) Setup dependencies:

```powershell
python build.py setup
```

2) Run checks:

```powershell
python build.py check
```

3) Build frontend:

```powershell
python build.py build
```

4) Run full stack:

```powershell
python build.py run
```

UI default: `http://127.0.0.1:5173`

One-command flow:

```powershell
python build.py dev
```

Useful flags:

- `python build.py run --no-setup`
- `python build.py run --no-reload`
- `python build.py check --skip-frontend-lint`
- `python build.py build --skip-checks`

## API

- `POST /api/v1/jobs` - upload and create job
- `GET /api/v1/jobs` - storage/library job list
- `GET /api/v1/jobs/{job_id}` - status, progress, runtime telemetry
- `GET /api/v1/jobs/{job_id}/artifacts` - artifacts list
- `GET /api/v1/jobs/{job_id}/people` - unique people + portraits + stats
- `GET /api/v1/jobs/{job_id}/report` - generated summary/report
- `POST /api/v1/jobs/{job_id}/export` - build ZIP bundle
- `GET /api/v1/jobs/{job_id}/artifacts/{artifact_name}/download` - file download

## OpenVINO models for mask mode

When `enable_mask_overlay=true`, pipeline tries these models (if present):

- `age-gender-recognition-retail-0013`
- `emotions-recognition-retail-0003`
- `face-detection-retail-0004`
- `face-reidentification-retail-0095`
- `facial-landmarks-35-adas-0002`
- `facial-landmarks-98-detection-0001`
- `human-pose-estimation-0001`
- `person-detection-retail-0013`
- `person-reidentification-retail-0277`

Model directory:

`<storage>/models/openvino/**`

Device policy:

- tries `MYRIAD` first (Intel NCS2)
- falls back to `CPU`

## Environment variables

- `NEWTONSPECT_STORAGE_ROOT` (default: `storage`)
- `NEWTONSPECT_DB_PATH` (default: `<storage>/newtonspect.db`)
- `NEWTONSPECT_OLLAMA_BASE_URL` (default: `http://127.0.0.1:11434`)
- `NEWTONSPECT_OLLAMA_MODEL` (default: `qwen2.5:3b`)
- `NEWTONSPECT_WHISPER_MODEL` (default: `large-v3`)
- `NEWTONSPECT_OPENVINO_MODELS_DIR` (default: `<storage>/models/openvino`)
- `NEWTONSPECT_OPENVINO_DEVICES` (default: `MYRIAD,CPU`)
- `NEWTONSPECT_WORKER_POLL_SECONDS` (default: `2.0`)
- `NEWTONSPECT_WORKER_STUCK_TIMEOUT_SECONDS` (default: `900`)

Hugging Face stability/performance:

- `HF_TOKEN` (recommended) for higher Hub rate limits
- `HF_HUB_DISABLE_SYMLINKS_WARNING=1` to hide symlink warning text
- On Windows, enable Developer Mode to allow symlink cache optimization

## Tests

```powershell
python build.py check
```

## License

MIT. See [LICENSE](LICENSE).
