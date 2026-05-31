from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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

    dist_dir = container.settings.frontend_dist_dir
    assets_dir = dist_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> FileResponse:
        # Keep API routes isolated and serve SPA only when compiled frontend exists.
        if full_path.startswith("api/") or full_path == "health":
            raise HTTPException(status_code=404, detail="Not found")
        index_file = dist_dir / "index.html"
        if not index_file.exists():
            raise HTTPException(
                status_code=503,
                detail=f"Compiled frontend is missing. Expected {index_file}. Run: python build.py build",
            )
        requested = dist_dir / full_path
        if full_path and requested.exists() and requested.is_file():
            return FileResponse(requested)
        return FileResponse(index_file)

    return app


app = create_app()
