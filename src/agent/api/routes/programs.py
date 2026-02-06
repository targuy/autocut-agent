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


class UpdateProgramRequest(BaseModel):
    """Update fields on an existing program. Only non-None fields are applied."""

    description: str | None = None
    purpose: str | None = None
    command_type: str | None = None
    command_template: str | None = None
    required_inputs: list[str] | None = None
    expected_outputs: list[str] | None = None
    tags: list[str] | None = None
    version: str | None = None


class UpdateParametersRequest(BaseModel):
    parameters: dict[str, Any]


class AddParameterRequest(BaseModel):
    """Add a single new parameter to a program."""

    name: str
    type: str = "string"  # string/int/float/bool/enum/path
    description: str = ""
    default: Any = None
    required: bool = False
    min_value: float | None = None
    max_value: float | None = None
    allowed_values: list[str] = Field(default_factory=list)


class InjectScoreRequest(BaseModel):
    """Manually inject an execution score."""

    outcome: str  # success/failure/zero_output/timeout
    parameters_used: dict[str, Any] = Field(default_factory=dict)
    duration_seconds: float = 0.0
    output_size: int = 0
    error_message: str = ""


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
# Bulk import / export endpoints (placed before parameterized routes)
# ---------------------------------------------------------------------------


@router.post("/programs/import")
async def bulk_import_programs(
    body: list[RegisterProgramRequest], request: Request
) -> dict[str, Any]:
    """Bulk import multiple program definitions at once."""
    from agent.pipeline.models import CommandType
    from agent.pipeline.registry import ParameterDef, ProgramEntry

    registry = _get_registry(request)

    imported: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for item in body:
        try:
            cmd_type = CommandType(item.command_type)
        except ValueError:
            errors.append({
                "program": item.name,
                "error": f"Invalid command_type: {item.command_type}",
            })
            continue

        try:
            params = [ParameterDef(**p) for p in item.parameters]
        except Exception as exc:
            errors.append({
                "program": item.name,
                "error": f"Invalid parameter definition: {exc}",
            })
            continue

        entry = ProgramEntry(
            name=item.name,
            description=item.description,
            purpose=item.purpose,
            command_type=cmd_type,
            command_template=item.command_template,
            required_inputs=item.required_inputs,
            expected_outputs=item.expected_outputs,
            parameters=params,
            tags=item.tags,
            version=item.version,
            registered_by="api",
        )

        try:
            entry = await registry.register(entry)
            imported.append(_entry_to_response(entry))
        except Exception as exc:
            errors.append({"program": item.name, "error": str(exc)})

    return {
        "imported": len(imported),
        "errors": errors,
        "programs": imported,
    }


@router.get("/programs/export")
async def export_programs(request: Request) -> list[dict[str, Any]]:
    """Export all program definitions for backup or sharing."""
    registry = _get_registry(request)
    programs = await registry.list_all(active_only=False)
    return [_entry_to_response(p) for p in programs]


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


@router.put("/programs/{program_name}")
async def update_program(
    program_name: str, body: UpdateProgramRequest, request: Request
) -> dict[str, Any]:
    """Update fields of an existing program. Only non-None fields are applied."""
    from agent.pipeline.models import CommandType

    registry = _get_registry(request)
    entry = await registry.get_by_name(program_name)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Program '{program_name}' not found")

    if body.description is not None:
        entry.description = body.description
    if body.purpose is not None:
        entry.purpose = body.purpose
    if body.command_type is not None:
        try:
            entry.command_type = CommandType(body.command_type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid command_type: {body.command_type}",
            )
    if body.command_template is not None:
        entry.command_template = body.command_template
    if body.required_inputs is not None:
        entry.required_inputs = body.required_inputs
    if body.expected_outputs is not None:
        entry.expected_outputs = body.expected_outputs
    if body.tags is not None:
        entry.tags = body.tags
    if body.version is not None:
        entry.version = body.version

    entry = await registry.register(entry)
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


@router.post("/programs/{program_name}/parameters")
async def add_parameter(
    program_name: str, body: AddParameterRequest, request: Request
) -> dict[str, Any]:
    """Add a single new parameter to a program."""
    from agent.pipeline.registry import ParameterDef

    registry = _get_registry(request)
    entry = await registry.get_by_name(program_name)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Program '{program_name}' not found")

    # Check for duplicate parameter name
    if entry.get_parameter(body.name) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Parameter '{body.name}' already exists on program '{program_name}'",
        )

    param = ParameterDef(
        name=body.name,
        type=body.type,
        description=body.description,
        default=body.default,
        required=body.required,
        min_value=body.min_value,
        max_value=body.max_value,
        allowed_values=body.allowed_values,
    )

    entry.parameters.append(param)
    entry = await registry.register(entry)
    return _entry_to_response(entry)


@router.delete("/programs/{program_name}/parameters/{param_name}")
async def delete_parameter(
    program_name: str, param_name: str, request: Request
) -> dict[str, Any]:
    """Remove a parameter from a program."""
    registry = _get_registry(request)
    entry = await registry.get_by_name(program_name)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Program '{program_name}' not found")

    param = entry.get_parameter(param_name)
    if param is None:
        raise HTTPException(
            status_code=404,
            detail=f"Parameter '{param_name}' not found on program '{program_name}'",
        )

    entry.parameters = [p for p in entry.parameters if p.name != param_name]
    entry = await registry.register(entry)
    return _entry_to_response(entry)


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


@router.post("/programs/{program_name}/scores")
async def inject_score(
    program_name: str, body: InjectScoreRequest, request: Request
) -> dict[str, Any]:
    """Manually inject a single execution score for a program."""
    from agent.pipeline.scoring import ExecutionOutcome, ExecutionScore

    scoring = _get_scoring(request)

    try:
        outcome = ExecutionOutcome(body.outcome)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid outcome: {body.outcome!r}. "
                f"Must be one of: success, failure, zero_output, timeout, skipped"
            ),
        )

    score = ExecutionScore(
        program_name=program_name,
        outcome=outcome,
        parameters_used=body.parameters_used,
        duration_seconds=body.duration_seconds,
        output_size=body.output_size,
        error_message=body.error_message,
    )
    await scoring.record_score(score)

    return {
        "status": "recorded",
        "score_id": str(score.id),
        "program_name": program_name,
        "outcome": outcome.value,
    }


@router.post("/programs/{program_name}/scores/batch")
async def batch_inject_scores(
    program_name: str, body: list[InjectScoreRequest], request: Request
) -> dict[str, Any]:
    """Batch inject multiple execution scores for a program."""
    from agent.pipeline.scoring import ExecutionOutcome, ExecutionScore

    scoring = _get_scoring(request)

    recorded: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []

    for idx, item in enumerate(body):
        try:
            outcome = ExecutionOutcome(item.outcome)
        except ValueError:
            errors.append({
                "index": str(idx),
                "error": f"Invalid outcome: {item.outcome!r}",
            })
            continue

        score = ExecutionScore(
            program_name=program_name,
            outcome=outcome,
            parameters_used=item.parameters_used,
            duration_seconds=item.duration_seconds,
            output_size=item.output_size,
            error_message=item.error_message,
        )
        try:
            await scoring.record_score(score)
            recorded.append({
                "score_id": str(score.id),
                "outcome": outcome.value,
            })
        except Exception as exc:
            errors.append({"index": str(idx), "error": str(exc)})

    return {
        "recorded": len(recorded),
        "errors": errors,
        "scores": recorded,
    }


@router.delete("/programs/{program_name}/scores")
async def clear_scores(
    program_name: str, request: Request
) -> dict[str, Any]:
    """Delete all execution scores for a program."""
    scoring = _get_scoring(request)
    deleted_count = await scoring.clear_scores(program_name)
    return {
        "status": "cleared",
        "program_name": program_name,
        "deleted_count": deleted_count,
    }


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
