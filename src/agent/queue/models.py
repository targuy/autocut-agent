"""Queue and Task data models."""

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


class TaskStatus(enum.StrEnum):
    """Lifecycle states of a task."""

    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# TaskPriority is a plain int alias — higher values = higher importance.
TaskPriority = int


# ---------------------------------------------------------------------------
# Queue status
# ---------------------------------------------------------------------------


class QueueStatus(enum.StrEnum):
    """Operational states of a queue."""

    ACTIVE = "active"
    PAUSED = "paused"
    DRAINING = "draining"


# ---------------------------------------------------------------------------
# Task model
# ---------------------------------------------------------------------------


class Task(BaseModel):
    """Represents a unit of work to be executed by the agent."""

    id: uuid.UUID = Field(default_factory=_new_id)
    queue_name: str
    command: str
    command_type: str = "python"
    priority: TaskPriority = Field(default=0)
    status: TaskStatus = TaskStatus.PENDING

    # Pipeline linkage (optional)
    pipeline_id: uuid.UUID | None = None
    step_id: uuid.UUID | None = None
    execution_id: uuid.UUID | None = None

    # Execution constraints
    resource_requirements: list[str] = Field(default_factory=list)
    timeout: int = Field(default=3600, ge=1)

    # Timestamps
    created_at: datetime = Field(default_factory=_utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # Results
    result: dict[str, Any] | None = None
    error: str | None = None

    # -- helpers -------------------------------------------------------------

    def is_terminal(self) -> bool:
        """Return True if the task has reached a final state."""
        return self.status in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }

    def mark_running(self) -> None:
        """Transition the task to RUNNING state."""
        self.status = TaskStatus.RUNNING
        self.started_at = _utcnow()

    def mark_completed(self, result: dict[str, Any] | None = None) -> None:
        """Transition the task to COMPLETED state."""
        self.status = TaskStatus.COMPLETED
        self.completed_at = _utcnow()
        self.result = result

    def mark_failed(self, error: str) -> None:
        """Transition the task to FAILED state."""
        self.status = TaskStatus.FAILED
        self.completed_at = _utcnow()
        self.error = error

    def mark_cancelled(self) -> None:
        """Transition the task to CANCELLED state."""
        self.status = TaskStatus.CANCELLED
        self.completed_at = _utcnow()

    # -- ordering for PriorityQueue ------------------------------------------

    def __lt__(self, other: object) -> bool:
        """Higher priority sorts first; ties broken by creation time."""
        if not isinstance(other, Task):
            return NotImplemented
        if self.priority != other.priority:
            return self.priority > other.priority  # higher value = higher prio
        return self.created_at < other.created_at
