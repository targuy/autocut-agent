"""System status, health check, and metrics endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response

router = APIRouter()


@router.get("/status")
async def system_status(request: Request) -> dict[str, Any]:
    """Return system status overview."""
    orch = getattr(request.app.state, "orchestrator", None)
    if orch is None:
        return {"status": "unavailable"}

    return {
        "status": orch.state.state.value,
        "agent_name": orch.config.agent.name,
        "uptime_seconds": orch.state.uptime_seconds,
        "workers": orch.config.agent.workers,
    }


@router.get("/health")
async def health_check(request: Request) -> dict[str, str]:
    """Simple health check."""
    orch = getattr(request.app.state, "orchestrator", None)
    if orch and orch.state.is_running():
        return {"status": "healthy"}
    return {"status": "degraded"}


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    """Prometheus metrics endpoint."""
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
