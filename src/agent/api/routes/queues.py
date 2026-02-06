"""Queue management API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()


class QueueCreate(BaseModel):
    name: str
    type: str = "priority"
    workers: int = Field(default=2, ge=1)
    priority: int = 0


def _get_orchestrator(request: Request) -> Any:
    orch = request.app.state.orchestrator
    if orch is None:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")
    return orch


@router.post("/queues")
async def create_queue(body: QueueCreate, request: Request) -> dict[str, Any]:
    """Create a new queue."""
    orch = _get_orchestrator(request)
    qm = orch.queue_manager
    await qm.create_queue(
        name=body.name,
        queue_type=body.type,
        workers=body.workers,
        priority=body.priority,
    )
    return {"name": body.name, "type": body.type, "workers": body.workers}


@router.get("/queues")
async def list_queues(request: Request) -> list[dict[str, Any]]:
    """List all queues."""
    orch = _get_orchestrator(request)
    return await orch.queue_manager.list_queues()


@router.get("/queues/{name}")
async def get_queue(name: str, request: Request) -> dict[str, Any]:
    """Get queue details."""
    orch = _get_orchestrator(request)
    status = await orch.queue_manager.get_queue_status(name)
    if status is None:
        raise HTTPException(status_code=404, detail="Queue not found")
    return status


@router.post("/queues/{name}/pause")
async def pause_queue(name: str, request: Request) -> dict[str, str]:
    """Pause a queue."""
    orch = _get_orchestrator(request)
    await orch.queue_manager.pause_queue(name)
    return {"status": "paused", "queue": name}


@router.post("/queues/{name}/resume")
async def resume_queue(name: str, request: Request) -> dict[str, str]:
    """Resume a paused queue."""
    orch = _get_orchestrator(request)
    await orch.queue_manager.resume_queue(name)
    return {"status": "active", "queue": name}


@router.delete("/queues/{name}")
async def delete_queue(name: str, request: Request) -> dict[str, str]:
    """Delete a queue."""
    orch = _get_orchestrator(request)
    await orch.queue_manager.delete_queue(name)
    return {"status": "deleted", "queue": name}
