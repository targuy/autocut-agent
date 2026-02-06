"""Program Registry and Scoring API endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class RegisterProgramRequest(BaseModel):
    name: str
    description: str = ""
    purpose: str = ""
    command_type: str = "shell"
    command_template: str = ""
    required_inputs: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    version: str = "1.0"


class UpdateParametersRequest(BaseModel):
    parameters: dict[str, Any]


class ProgramResponse(BaseModel):
    id: str
    name: str
    description: str
    purpose: str
    command_type: str
    command_template: str
    required_inputs: list[str]
    expected_outputs: list[str]
    parameters: list[dict[str, Any]]
    tags: list[str]
    version: str
    active: bool
    created_at: str
    updated_at: str


class ProgramStatsResponse(BaseModel):
    program_name: str
    total_runs: int
    successes: int
    failures: int
    zero_outputs: int
    timeouts: int
    success_rate: float
    zero_output_rate: float
    failure_rate: float
    avg_duration_seconds: float


class AdvisoryResponse(BaseModel):
    program_name: str
    severity: str
    title: str
    message: str
    suggested_changes: dict[str, Any] = Field(default_factory=dict)
    based_on_runs: int
    confidence: float


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_registry(request: Request) -> Any:
    registry = getattr(request.app.state, "program_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Program registry not initialized")
    return registry


def _get_scoring(request: Request) -> Any:
    scoring = getattr(request.app.state, "scoring_manager", None)
    if scoring is None:
        raise HTTPException(status_code=503, detail="Scoring manager not initialized")
    return scoring


def _entry_to_response(entry: Any) -> dict[str, Any]:
    return {
        "id": str(entry.id),
        "name": entry.name,
        "description": entry.description,
        "purpose": entry.purpose,
        "command_type": entry.command_type.value,
        "command_template": entry.command_template,
        "required_inputs": entry.required_inputs,
        "expected_outputs": entry.expected_outputs,
        "parameters": [p.model_dump() for p in entry.parameters],
        "tags": entry.tags,
        "version": entry.version,
        "active": entry.active,
        "created_at": entry.created_at.isoformat(),
        "updated_at": entry.updated_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Program Registry endpoints
# ---------------------------------------------------------------------------


@router.post("/programs", response_model=ProgramResponse)
async def register_program(
    body: RegisterProgramRequest, request: Request
) -> dict[str, Any]:
    """Register a new program in the knowledge base."""
    from agent.pipeline.models import CommandType
    from agent.pipeline.registry import ParameterDef, ProgramEntry

    try:
        cmd_type = CommandType(body.command_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid command_type: {body.command_type}",
        )

    params = [ParameterDef(**p) for p in body.parameters]

    entry = ProgramEntry(
        name=body.name,
        description=body.description,
        purpose=body.purpose,
        command_type=cmd_type,
        command_template=body.command_template,
        required_inputs=body.required_inputs,
        expected_outputs=body.expected_outputs,
        parameters=params,
        tags=body.tags,
        version=body.version,
        registered_by="api",
    )

    registry = _get_registry(request)
    entry = await registry.register(entry)
    return _entry_to_response(entry)


@router.get("/programs")
async def list_programs(request: Request, active_only: bool = True) -> list[dict[str, Any]]:
    """List all registered programs."""
    registry = _get_registry(request)
    programs = await registry.list_all(active_only=active_only)
    return [_entry_to_response(p) for p in programs]


@router.get("/programs/{program_name}")
async def get_program(program_name: str, request: Request) -> dict[str, Any]:
    """Get a program by name."""
    registry = _get_registry(request)
    entry = await registry.get_by_name(program_name)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Program '{program_name}' not found")
    return _entry_to_response(entry)


@router.put("/programs/{program_name}/parameters")
async def update_parameters(
    program_name: str, body: UpdateParametersRequest, request: Request
) -> dict[str, Any]:
    """Update parameter values for a program."""
    registry = _get_registry(request)
    entry, errors = await registry.update_parameters(program_name, body.parameters)

    if entry is None:
        raise HTTPException(status_code=404, detail=errors[0] if errors else "Not found")

    if errors:
        return {
            "status": "partial",
            "errors": errors,
            "program": _entry_to_response(entry),
        }

    return {
        "status": "updated",
        "program": _entry_to_response(entry),
    }


@router.delete("/programs/{program_name}")
async def deactivate_program(
    program_name: str, request: Request
) -> dict[str, str]:
    """Deactivate a program (soft delete)."""
    registry = _get_registry(request)
    success = await registry.deactivate(program_name)
    if not success:
        raise HTTPException(status_code=404, detail=f"Program '{program_name}' not found")
    return {"status": "deactivated", "program": program_name}


# ---------------------------------------------------------------------------
# Scoring and Advisory endpoints
# ---------------------------------------------------------------------------


@router.get("/programs/{program_name}/stats", response_model=ProgramStatsResponse)
async def get_program_stats(program_name: str, request: Request) -> dict[str, Any]:
    """Get execution statistics for a program."""
    scoring = _get_scoring(request)
    stats = await scoring.get_program_stats(program_name)
    return {
        "program_name": stats.program_name,
        "total_runs": stats.total_runs,
        "successes": stats.successes,
        "failures": stats.failures,
        "zero_outputs": stats.zero_outputs,
        "timeouts": stats.timeouts,
        "success_rate": stats.success_rate,
        "zero_output_rate": stats.zero_output_rate,
        "failure_rate": stats.failure_rate,
        "avg_duration_seconds": stats.avg_duration_seconds,
    }


@router.get("/programs/{program_name}/advisories")
async def get_program_advisories(
    program_name: str, request: Request
) -> list[dict[str, Any]]:
    """Get parameter change recommendations for a program."""
    scoring = _get_scoring(request)
    advisories = await scoring.get_advisories(program_name)
    return [
        {
            "program_name": a.program_name,
            "severity": a.severity.value,
            "title": a.title,
            "message": a.message,
            "suggested_changes": a.suggested_changes,
            "based_on_runs": a.based_on_runs,
            "confidence": a.confidence,
        }
        for a in advisories
    ]


@router.get("/advisories")
async def get_all_advisories(request: Request) -> dict[str, list[dict[str, Any]]]:
    """Get advisories across all programs."""
    scoring = _get_scoring(request)
    all_advisories = await scoring.get_all_advisories()
    return {
        program_name: [
            {
                "severity": a.severity.value,
                "title": a.title,
                "message": a.message,
                "suggested_changes": a.suggested_changes,
                "based_on_runs": a.based_on_runs,
                "confidence": a.confidence,
            }
            for a in advisories
        ]
        for program_name, advisories in all_advisories.items()
    }


@router.get("/programs/{program_name}/param-sets")
async def get_param_set_stats(
    program_name: str, request: Request
) -> list[dict[str, Any]]:
    """Get per-parameter-set execution statistics."""
    scoring = _get_scoring(request)
    stats = await scoring.get_param_set_stats(program_name)
    return [
        {
            "parameters_hash": s.parameters_hash,
            "parameters": s.parameters,
            "total_runs": s.total_runs,
            "successes": s.successes,
            "zero_outputs": s.zero_outputs,
            "failures": s.failures,
            "success_rate": s.success_rate,
            "avg_duration_seconds": s.avg_duration_seconds,
        }
        for s in stats
    ]
