"""Pipeline API endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()


class CompileRequest(BaseModel):
    description: str
    inputs: dict[str, Any] = Field(default_factory=dict)


class PipelineResponse(BaseModel):
    id: str
    name: str
    status: str
    template_id: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    steps: list[dict[str, Any]] = Field(default_factory=list)


def _get_orchestrator(request: Request) -> Any:
    orch = request.app.state.orchestrator
    if orch is None:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")
    return orch


@router.post("/pipelines/compile", response_model=PipelineResponse)
async def compile_pipeline(body: CompileRequest, request: Request) -> dict[str, Any]:
    """Compile natural language into a pipeline via LLM."""
    orch = _get_orchestrator(request)
    compiler = orch.pipeline_engine.compiler

    pipeline, steps = await compiler.compile(
        description=body.description,
        inputs=body.inputs,
    )

    return {
        "id": str(pipeline.id),
        "name": pipeline.name,
        "status": pipeline.status.value,
        "template_id": str(pipeline.template_id) if pipeline.template_id else None,
        "inputs": pipeline.inputs,
        "created_at": pipeline.created_at.isoformat(),
        "steps": [
            {
                "id": str(s.id),
                "order": s.order,
                "name": s.name,
                "command_type": s.command_type.value,
                "status": s.status.value,
            }
            for s in steps
        ],
    }


@router.post("/pipelines", response_model=PipelineResponse)
async def create_pipeline(body: dict[str, Any], request: Request) -> dict[str, Any]:
    """Create a pipeline from a template ID or inline definition."""
    orch = _get_orchestrator(request)

    template_id = body.get("template_id")
    inputs = body.get("inputs", {})

    if not template_id:
        raise HTTPException(status_code=400, detail="template_id is required")

    template_mgr = orch.pipeline_engine.template_manager
    template = await template_mgr.get(uuid.UUID(template_id))
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")

    pipeline, steps = template_mgr.clone(template, inputs=inputs)

    # Persist and optionally start
    engine = orch.pipeline_engine
    await engine.persist_pipeline(pipeline, steps)

    if body.get("start", True):
        await engine.run_pipeline(pipeline, steps)

    return {
        "id": str(pipeline.id),
        "name": pipeline.name,
        "status": pipeline.status.value,
        "template_id": str(pipeline.template_id) if pipeline.template_id else None,
        "inputs": pipeline.inputs,
        "created_at": pipeline.created_at.isoformat(),
        "steps": [
            {
                "id": str(s.id),
                "order": s.order,
                "name": s.name,
                "command_type": s.command_type.value,
                "status": s.status.value,
            }
            for s in steps
        ],
    }


@router.get("/pipelines")
async def list_pipelines(request: Request) -> list[dict[str, Any]]:
    """List all pipelines."""
    orch = _get_orchestrator(request)
    engine = orch.pipeline_engine
    pipelines = await engine.list_pipelines()
    return [
        {
            "id": str(p.id),
            "name": p.name,
            "status": p.status.value,
            "created_at": p.created_at.isoformat(),
        }
        for p in pipelines
    ]


@router.get("/pipelines/{pipeline_id}")
async def get_pipeline(pipeline_id: str, request: Request) -> dict[str, Any]:
    """Get pipeline details with step statuses."""
    orch = _get_orchestrator(request)
    engine = orch.pipeline_engine

    pipeline = await engine.get_pipeline(uuid.UUID(pipeline_id))
    if pipeline is None:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    steps = await engine.get_pipeline_steps(uuid.UUID(pipeline_id))

    return {
        "id": str(pipeline.id),
        "name": pipeline.name,
        "status": pipeline.status.value,
        "template_id": str(pipeline.template_id) if pipeline.template_id else None,
        "inputs": pipeline.inputs,
        "context": pipeline.context,
        "created_at": pipeline.created_at.isoformat(),
        "started_at": pipeline.started_at.isoformat() if pipeline.started_at else None,
        "completed_at": (
            pipeline.completed_at.isoformat() if pipeline.completed_at else None
        ),
        "steps": [
            {
                "id": str(s.id),
                "order": s.order,
                "name": s.name,
                "command_type": s.command_type.value,
                "status": s.status.value,
                "condition": s.condition,
            }
            for s in steps
        ],
    }


@router.post("/pipelines/{pipeline_id}/resume")
async def resume_pipeline(pipeline_id: str, request: Request) -> dict[str, str]:
    """Resume an interrupted pipeline."""
    orch = _get_orchestrator(request)
    await orch.pipeline_engine.resume_pipeline(uuid.UUID(pipeline_id))
    return {"status": "resumed", "pipeline_id": pipeline_id}


@router.post("/pipelines/{pipeline_id}/cancel")
async def cancel_pipeline(pipeline_id: str, request: Request) -> dict[str, str]:
    """Cancel a running pipeline."""
    orch = _get_orchestrator(request)
    await orch.pipeline_engine.cancel_pipeline(uuid.UUID(pipeline_id))
    return {"status": "cancelled", "pipeline_id": pipeline_id}


@router.get("/pipelines/{pipeline_id}/artifacts")
async def list_artifacts(pipeline_id: str, request: Request) -> list[dict[str, Any]]:
    """List all artifacts for a pipeline."""
    orch = _get_orchestrator(request)
    artifacts = await orch.pipeline_engine.get_pipeline_artifacts(
        uuid.UUID(pipeline_id)
    )
    return [
        {
            "id": str(a.id),
            "step_id": str(a.step_id),
            "key": a.key,
            "value": a.value,
            "file_path": a.file_path,
            "created_at": a.created_at.isoformat(),
        }
        for a in artifacts
    ]


@router.post("/pipelines/{pipeline_id}/steps/{step_id}/skip")
async def skip_step(
    pipeline_id: str, step_id: str, request: Request
) -> dict[str, str]:
    """Skip a failed or interrupted step."""
    orch = _get_orchestrator(request)
    await orch.pipeline_engine.skip_step(
        uuid.UUID(pipeline_id), uuid.UUID(step_id)
    )
    return {"status": "skipped", "step_id": step_id}
