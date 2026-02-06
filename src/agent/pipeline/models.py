"""Pipeline data models: Pipeline, Step, Execution, Artifact, Template."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> uuid.UUID:
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PipelineStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    SKIPPED = "skipped"
    INTERRUPTED = "interrupted"


class ExecutionStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class CommandType(enum.StrEnum):
    PYTHON = "python"
    FFMPEG = "ffmpeg"
    SHELL = "shell"


# ---------------------------------------------------------------------------
# Step definition (used in templates — no runtime state)
# ---------------------------------------------------------------------------


class PipelineStepDef(BaseModel):
    """Step definition as stored in a template. No runtime state."""

    name: str
    command_type: CommandType
    command_template: str
    condition: str | None = None
    input_mappings: dict[str, str] = Field(default_factory=dict)
    fan_out_on: str | None = None
    depends_on_names: list[str] = Field(default_factory=list)
    resource_requirements: list[str] = Field(default_factory=list)
    timeout: int = Field(default=3600, ge=1)
    retry_max: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------


class PipelineTemplate(BaseModel):
    id: uuid.UUID = Field(default_factory=_new_id)
    name: str
    description: str = ""
    steps: list[PipelineStepDef] = Field(default_factory=list)
    version: int = 1
    created_by: str = "manual"
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Runtime pipeline instance
# ---------------------------------------------------------------------------


class Pipeline(BaseModel):
    id: uuid.UUID = Field(default_factory=_new_id)
    template_id: uuid.UUID | None = None
    name: str
    status: PipelineStatus = PipelineStatus.PENDING
    inputs: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)
    started_at: datetime | None = None
    resumed_at: datetime | None = None
    completed_at: datetime | None = None

    def is_terminal(self) -> bool:
        return self.status in {
            PipelineStatus.COMPLETED,
            PipelineStatus.FAILED,
            PipelineStatus.CANCELLED,
        }


class PipelineStep(BaseModel):
    id: uuid.UUID = Field(default_factory=_new_id)
    pipeline_id: uuid.UUID
    order: int
    name: str
    command_type: CommandType
    command_template: str
    condition: str | None = None
    input_mappings: dict[str, str] = Field(default_factory=dict)
    fan_out_on: str | None = None
    depends_on: list[uuid.UUID] = Field(default_factory=list)
    resource_requirements: list[str] = Field(default_factory=list)
    timeout: int = 3600
    retry_max: int = 0
    retry_count: int = 0
    status: StepStatus = StepStatus.PENDING

    def is_ready(self, completed_step_ids: set[uuid.UUID]) -> bool:
        """Check if all dependencies are satisfied."""
        return all(dep in completed_step_ids for dep in self.depends_on)

    def is_terminal(self) -> bool:
        return self.status in {
            StepStatus.COMPLETED,
            StepStatus.FAILED_TERMINAL,
            StepStatus.SKIPPED,
        }

    def can_retry(self) -> bool:
        return (
            self.status == StepStatus.FAILED_RETRYABLE
            and self.retry_count < self.retry_max
        )


class StepExecution(BaseModel):
    id: uuid.UUID = Field(default_factory=_new_id)
    step_id: uuid.UUID
    index: int | None = None
    status: ExecutionStatus = ExecutionStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class Artifact(BaseModel):
    id: uuid.UUID = Field(default_factory=_new_id)
    pipeline_id: uuid.UUID
    step_id: uuid.UUID
    execution_id: uuid.UUID | None = None
    key: str
    value: Any = None
    file_path: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
