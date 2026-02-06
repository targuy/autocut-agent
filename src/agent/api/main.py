"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

if TYPE_CHECKING:
    from agent.core.orchestrator import AgentOrchestrator

_GUI_DIR = Path(__file__).resolve().parent.parent / "gui"


def create_app(orchestrator: AgentOrchestrator | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="AutoCut-Agent API",
        description="Pipeline-oriented task orchestration system",
        version="0.1.0",
    )

    # Store orchestrator reference for route handlers
    app.state.orchestrator = orchestrator

    # CORS
    origins = ["http://localhost:3000", "http://localhost:8080"]
    if orchestrator and orchestrator.config.api.cors_origins:
        origins = orchestrator.config.api.cors_origins

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    from agent.api.routes.pipelines import router as pipelines_router
    from agent.api.routes.programs import router as programs_router
    from agent.api.routes.queues import router as queues_router
    from agent.api.routes.status import router as status_router
    from agent.api.routes.storage import router as storage_router
    from agent.api.routes.tasks import router as tasks_router
    from agent.api.routes.templates import router as templates_router

    app.include_router(pipelines_router, prefix="/api/v1", tags=["pipelines"])
    app.include_router(templates_router, prefix="/api/v1", tags=["templates"])
    app.include_router(programs_router, prefix="/api/v1", tags=["programs"])
    app.include_router(queues_router, prefix="/api/v1", tags=["queues"])
    app.include_router(tasks_router, prefix="/api/v1", tags=["tasks"])
    app.include_router(status_router, prefix="/api/v1", tags=["system"])
    app.include_router(storage_router, prefix="/api/v1", tags=["storage"])

    # ----- Web GUI (static files + index.html) -----
    static_dir = _GUI_DIR / "static"
    if static_dir.is_dir():
        app.mount("/gui/static", StaticFiles(directory=str(static_dir)), name="gui-static")

    index_html = _GUI_DIR / "templates" / "index.html"

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/gui", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/gui/{rest_of_path:path}", response_class=HTMLResponse, include_in_schema=False)
    async def serve_gui(rest_of_path: str = "") -> HTMLResponse:
        if index_html.is_file():
            return HTMLResponse(content=index_html.read_text())
        return HTMLResponse(
            content="<h1>GUI not found</h1><p>index.html is missing.</p>",
            status_code=404,
        )

    return app
