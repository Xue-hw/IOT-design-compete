from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import Settings
from .database import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    project_root = Path(__file__).resolve().parents[1]
    database_path = settings.resolved_database_path(project_root)

    app = FastAPI(
        title="FocusCube Backend",
        version="1.0.0",
        description="S3/P4/Web 共用的 FocusCube 后端接口。",
    )
    app.state.settings = settings
    app.state.db = Database(database_path, settings.timezone)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.parsed_cors_origins(),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    # Serve the Web dashboard from the same origin as the API.  This also
    # works behind the production /focuscube/ reverse-proxy prefix because
    # the browser resolves the dashboard assets relative to that prefix.
    frontend_dir = project_root.parent / "frontend"
    if frontend_dir.is_dir():
        app.mount(
            "/dashboard",
            StaticFiles(directory=frontend_dir, html=True),
            name="dashboard",
        )

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str | bool]:
        return {
            "ok": True,
            "service": "focuscube-backend",
            "version": app.version,
        }

    return app


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
app = create_app()
