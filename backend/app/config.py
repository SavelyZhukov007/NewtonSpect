from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_name: str
    app_version: str
    storage_root: Path
    db_path: Path
    ollama_base_url: str
    ollama_model: str
    whisper_model: str
    worker_poll_seconds: float
    worker_stuck_timeout_seconds: int
    openvino_models_dir: Path
    openvino_models_search_paths: tuple[Path, ...]
    preferred_openvino_devices: tuple[str, ...]
    frontend_dist_dir: Path


def _split_devices(value: str) -> tuple[str, ...]:
    parts = [item.strip() for item in value.split(",") if item.strip()]
    return tuple(parts or ["MYRIAD", "CPU"])


def _split_paths(value: str) -> tuple[Path, ...]:
    parts = [item.strip() for item in value.split(";") if item.strip()]
    return tuple(Path(item).resolve() for item in parts)


def get_settings() -> Settings:
    storage_root = Path(os.getenv("NEWTONSPECT_STORAGE_ROOT", "storage")).resolve()
    db_path = Path(os.getenv("NEWTONSPECT_DB_PATH", storage_root / "newtonspect.db")).resolve()
    openvino_models_dir = Path(
        os.getenv("NEWTONSPECT_OPENVINO_MODELS_DIR", storage_root / "models" / "openvino")
    ).resolve()
    default_fai_path = Path(r"C:\Projects\FAI_NCS2_WS\FAI_NCS2_WS\models\intel").resolve()
    search_paths_env = os.getenv(
        "NEWTONSPECT_OPENVINO_MODEL_PATHS",
        f"{default_fai_path};{openvino_models_dir}",
    )
    openvino_model_paths = _split_paths(search_paths_env)
    frontend_dist_dir = Path(
        os.getenv("NEWTONSPECT_FRONTEND_DIST_DIR", Path(__file__).resolve().parents[2] / "frontend" / "dist")
    ).resolve()
    storage_root.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    openvino_models_dir.mkdir(parents=True, exist_ok=True)
    for model_dir in openvino_model_paths:
        model_dir.mkdir(parents=True, exist_ok=True)
    frontend_dist_dir.parent.mkdir(parents=True, exist_ok=True)

    return Settings(
        app_name="AstraOrpheus API",
        app_version="0.2.0",
        storage_root=storage_root,
        db_path=db_path,
        ollama_base_url=os.getenv("NEWTONSPECT_OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        ollama_model=os.getenv("NEWTONSPECT_OLLAMA_MODEL", "qwen2.5:3b"),
        whisper_model=os.getenv("NEWTONSPECT_WHISPER_MODEL", "large-v3"),
        worker_poll_seconds=float(os.getenv("NEWTONSPECT_WORKER_POLL_SECONDS", "2.0")),
        worker_stuck_timeout_seconds=int(
            os.getenv("NEWTONSPECT_WORKER_STUCK_TIMEOUT_SECONDS", "900")
        ),
        openvino_models_dir=openvino_models_dir,
        openvino_models_search_paths=openvino_model_paths,
        preferred_openvino_devices=_split_devices(
            os.getenv("NEWTONSPECT_OPENVINO_DEVICES", "MYRIAD,CPU")
        ),
        frontend_dist_dir=frontend_dist_dir,
    )
