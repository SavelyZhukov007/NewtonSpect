from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.jobs import router as jobs_router
from .bootstrap import build_container


def create_app() -> FastAPI:
    container = build_container()
    app = FastAPI(title=container.settings.app_name, version=container.settings.app_version)
    app.state.container = container

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(jobs_router)
    return app


app = create_app()

