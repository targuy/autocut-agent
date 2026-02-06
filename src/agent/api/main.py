"""FastAPI application factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

if TYPE_CHECKING:
    from agent.core.orchestrator import AgentOrchestrator


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
    from agent.api.routes.tasks import router as tasks_router
    from agent.api.routes.templates import router as templates_router

    app.include_router(pipelines_router, prefix="/api/v1", tags=["pipelines"])
    app.include_router(templates_router, prefix="/api/v1", tags=["templates"])
    app.include_router(programs_router, prefix="/api/v1", tags=["programs"])
    app.include_router(queues_router, prefix="/api/v1", tags=["queues"])
    app.include_router(tasks_router, prefix="/api/v1", tags=["tasks"])
    app.include_router(status_router, prefix="/api/v1", tags=["system"])

    return app
