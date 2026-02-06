"""Pipeline template API endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()


class TemplateCreate(BaseModel):
    name: str
    description: str = ""
    steps: list[dict[str, Any]] = Field(default_factory=list)


class CloneRequest(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)
    start: bool = True


def _get_orchestrator(request: Request) -> Any:
    orch = request.app.state.orchestrator
    if orch is None:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")
    return orch


@router.post("/templates")
async def create_template(body: TemplateCreate, request: Request) -> dict[str, Any]:
    """Create a new pipeline template."""
    from agent.pipeline.models import PipelineStepDef, PipelineTemplate

    orch = _get_orchestrator(request)
    tmpl_mgr = orch.pipeline_engine.template_manager

    template = PipelineTemplate(
        name=body.name,
        description=body.description,
        steps=[PipelineStepDef(**s) for s in body.steps],
        created_by="api",
    )
    template = await tmpl_mgr.save(template)

    return {
        "id": str(template.id),
        "name": template.name,
        "version": template.version,
        "step_count": len(template.steps),
    }


@router.get("/templates")
async def list_templates(request: Request) -> list[dict[str, Any]]:
    """List all pipeline templates."""
    orch = _get_orchestrator(request)
    templates = await orch.pipeline_engine.template_manager.list_all()
    return [
        {
            "id": str(t.id),
            "name": t.name,
            "description": t.description,
            "version": t.version,
            "step_count": len(t.steps),
            "created_by": t.created_by,
            "updated_at": t.updated_at.isoformat(),
        }
        for t in templates
    ]


@router.get("/templates/{template_id}")
async def get_template(template_id: str, request: Request) -> dict[str, Any]:
    """Get template details."""
    orch = _get_orchestrator(request)
    tmpl = await orch.pipeline_engine.template_manager.get(uuid.UUID(template_id))
    if tmpl is None:
        raise HTTPException(status_code=404, detail="Template not found")

    return {
        "id": str(tmpl.id),
        "name": tmpl.name,
        "description": tmpl.description,
        "version": tmpl.version,
        "created_by": tmpl.created_by,
        "created_at": tmpl.created_at.isoformat(),
        "updated_at": tmpl.updated_at.isoformat(),
        "steps": [s.model_dump() for s in tmpl.steps],
    }


@router.post("/templates/{template_id}/clone")
async def clone_template(
    template_id: str, body: CloneRequest, request: Request
) -> dict[str, Any]:
    """Clone a template into a new pipeline with given inputs."""
    orch = _get_orchestrator(request)
    tmpl_mgr = orch.pipeline_engine.template_manager

    template = await tmpl_mgr.get(uuid.UUID(template_id))
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")

    pipeline, steps = tmpl_mgr.clone(template, inputs=body.inputs)

    engine = orch.pipeline_engine
    await engine.persist_pipeline(pipeline, steps)

    if body.start:
        await engine.run_pipeline(pipeline, steps)

    return {
        "id": str(pipeline.id),
        "name": pipeline.name,
        "status": pipeline.status.value,
        "template_id": str(pipeline.template_id),
        "inputs": pipeline.inputs,
        "step_count": len(steps),
    }


@router.put("/templates/{template_id}")
async def update_template(
    template_id: str, body: dict[str, Any], request: Request
) -> dict[str, Any]:
    """Update a template (increments version)."""
    orch = _get_orchestrator(request)
    tmpl = await orch.pipeline_engine.template_manager.update(
        uuid.UUID(template_id), body
    )
    if tmpl is None:
        raise HTTPException(status_code=404, detail="Template not found")

    return {
        "id": str(tmpl.id),
        "name": tmpl.name,
        "version": tmpl.version,
    }


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str, request: Request) -> dict[str, str]:
    """Delete a template."""
    orch = _get_orchestrator(request)
    deleted = await orch.pipeline_engine.template_manager.delete(
        uuid.UUID(template_id)
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"status": "deleted", "template_id": template_id}
