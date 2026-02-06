"""Task API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()


class TaskSubmit(BaseModel):
    queue_name: str = "default"
    command: str
    command_type: str = "python"
    priority: int = 0
    timeout: int = 3600
    resource_requirements: list[str] = Field(default_factory=list)


def _get_orchestrator(request: Request) -> Any:
    orch = request.app.state.orchestrator
    if orch is None:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")
    return orch


@router.post("/tasks")
async def submit_task(body: TaskSubmit, request: Request) -> dict[str, Any]:
    """Submit a standalone task to a queue."""
    from agent.queue.models import Task

    orch = _get_orchestrator(request)
    task = Task(
        queue_name=body.queue_name,
        command=body.command,
        command_type=body.command_type,
        priority=body.priority,
        timeout=body.timeout,
        resource_requirements=body.resource_requirements,
    )
    await orch.queue_manager.add_task(task)
    return {
        "id": str(task.id),
        "queue_name": task.queue_name,
        "status": task.status.value,
    }


@router.get("/tasks")
async def list_tasks(
    request: Request,
    queue_name: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List tasks, optionally filtered by queue or status."""
    orch = _get_orchestrator(request)
    tasks = await orch.queue_manager.list_tasks(
        queue_name=queue_name, status=status
    )
    return [
        {
            "id": str(t.id),
            "queue_name": t.queue_name,
            "command_type": t.command_type,
            "status": t.status.value,
            "priority": t.priority,
            "created_at": t.created_at.isoformat(),
        }
        for t in tasks
    ]


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, request: Request) -> dict[str, Any]:
    """Get task details."""
    orch = _get_orchestrator(request)
    task = await orch.queue_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "id": str(task.id),
        "queue_name": task.queue_name,
        "command": task.command,
        "command_type": task.command_type,
        "status": task.status.value,
        "priority": task.priority,
        "created_at": task.created_at.isoformat(),
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "result": task.result,
        "error": task.error,
    }


@router.delete("/tasks/{task_id}")
async def cancel_task(task_id: str, request: Request) -> dict[str, str]:
    """Cancel a task."""
    orch = _get_orchestrator(request)
    await orch.queue_manager.cancel_task(task_id)
    return {"status": "cancelled", "task_id": task_id}
