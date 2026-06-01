# AstraOrpheus V3 (Local-Only)

AstraOrpheus is a local-first `frontend + backend + worker` platform for:

- automatic subtitles (Whisper `large-v3`)
- people/face analytics (OpenVINO)
- AI summaries and structured reports (Ollama `qwen2.5:3b`)
- editable subtitle workflow with artifact exports

No cloud dependency is required for core flow.

## Stack

- Backend: FastAPI + SQLite + filesystem artifacts
- Worker: Python background polling worker
- Frontend: React + Vite + TypeScript (mobile-friendly)
- ASR: `faster-whisper` (`openai/whisper-large-v3`)
- Vision: OpenVINO (`MYRIAD -> CPU` fallback)
- LLM: local Ollama (`qwen2.5:3b`)

## V3 Highlights

- Run comparison by `source_fingerprint` (SHA256): current run vs previous run of the same source
- Subtitle editor with persisted segment revisions
- Chapters + key quotes generation
- Quality score (`ASR confidence`, subtitle coverage, speaker stability, people stability, report completeness)
- Translation tracks (`EN/ES/DE/FR`) with local fallback
- Offline KB reindex + local fact-check (`supported / contradicted / not_found`)
- Global person registry across jobs (merge/split operations)
- Privacy mode (`auto_risk | enabled | disabled`) with subtitle redaction path
- Optional 9:16 shorts generation
- Extended UI tabs: `Chapters`, `Quotes`, `Compare`, `Quality`, `Glossary`, `Knowledge Base`
- Camera studio + chunked WebSocket upload + dual-pass events
- Reliable desktop/mobile artifact download (including iPhone share flow)

## Pipeline

`ingest -> audio_extract -> asr -> subtitle_postprocess -> vision -> speaker_attribution -> report -> burned_video -> mask_overlay -> done`

V3 post-processing adds:

- chapters / quotes / quality
- translations
- offline fact-check
- optional shorts exports
- global person-registry updates

## Project Layout

- `backend/` - FastAPI API, queue repository, pipeline services
- `backend/worker.py` - background worker
- `frontend/` - React UI
- `storage/` - DB + artifacts
- `storage/kb` - global offline KB source directory
- `build.py` - unified setup/check/build/run/force entrypoint

## Requirements

- Python `3.10+`
- Node.js `20+`
- `ffmpeg` in PATH
- OpenVINO runtime
- Ollama with `qwen2.5:3b`

## Build and Run

Install dependencies:

```powershell
python build.py setup
```

Checks:

```powershell
python build.py check
```

Frontend production build:

```powershell
python build.py build
```

Dev stack (`api + worker + vite dev`):

```powershell
python build.py run
```

### One-command production pipeline

```powershell
python build.py force
```

`force` order:

1. pre-check
2. smart install missing/invalid dependencies
3. strict checks + frontend build
4. production host (`uvicorn + worker`, frontend served from compiled `frontend/dist` by FastAPI)

Clean reinstall variant:

```powershell
python build.py force --clean
```

## API (V3)

Core:

- `POST /api/v1/jobs`
- `GET /api/v1/jobs`
- `GET /api/v1/jobs/capabilities/formats`
- `GET /api/v1/jobs/{job_id}`
- `GET /api/v1/jobs/{job_id}/artifacts`
- `GET /api/v1/jobs/{job_id}/people`
- `GET /api/v1/jobs/{job_id}/report`
- `POST /api/v1/jobs/{job_id}/export`
- `GET /api/v1/jobs/{job_id}/artifacts/{artifact_name}/download`
- `WS /api/v1/jobs/ws/upload`

V3 analytics/editor:

- `GET /api/v1/jobs/{job_id}/chapters`
- `GET /api/v1/jobs/{job_id}/quotes`
- `GET /api/v1/jobs/{job_id}/quality`
- `GET /api/v1/jobs/{job_id}/comparison`
- `GET /api/v1/jobs/{job_id}/subtitles`
- `PUT /api/v1/jobs/{job_id}/subtitles`
- `GET /api/v1/jobs/{job_id}/translations`
- `GET /api/v1/jobs/{job_id}/fact-check`
- `POST /api/v1/jobs/{job_id}/shorts`

Global registries:

- `GET /api/v1/glossary`
- `POST /api/v1/glossary`
- `DELETE /api/v1/glossary/{term_id}`
- `GET /api/v1/person-registry`
- `POST /api/v1/person-registry/merge`
- `POST /api/v1/person-registry/split`
- `GET /api/v1/kb/status`
- `POST /api/v1/kb/reindex`

## OpenVINO Model Paths

Priority model root can be configured with:

- `NEWTONSPECT_OPENVINO_MODEL_PATHS`

Default includes:

- `C:\Projects\FAI_NCS2_WS\FAI_NCS2_WS\models\intel`
- `<storage>/models/openvino`

## Hugging Face on Windows (optional quality-of-life)

For higher HF Hub limits:

- `HF_TOKEN=<your_token>`

Symlink warning mitigation:

1. Enable Windows Developer Mode, or
2. Run Python as Administrator, or
3. Silence warning:

```powershell
setx HF_HUB_DISABLE_SYMLINKS_WARNING 1
```

## Main Environment Variables

- `NEWTONSPECT_STORAGE_ROOT`
- `NEWTONSPECT_DB_PATH`
- `NEWTONSPECT_OLLAMA_BASE_URL`
- `NEWTONSPECT_OLLAMA_MODEL`
- `NEWTONSPECT_WHISPER_MODEL`
- `NEWTONSPECT_OPENVINO_MODELS_DIR`
- `NEWTONSPECT_OPENVINO_MODEL_PATHS`
- `NEWTONSPECT_OPENVINO_DEVICES`
- `NEWTONSPECT_FRONTEND_DIST_DIR`

## Test

```powershell
python build.py check
```

## License

MIT
