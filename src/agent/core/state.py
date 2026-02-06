"""State management for the agent system."""

from __future__ import annotations

import enum
from datetime import datetime, timezone


class AgentState(enum.StrEnum):
    """Overall agent lifecycle states."""

    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    SHUTTING_DOWN = "shutting_down"
    STOPPED = "stopped"


class StateManager:
    """Manages the global agent state and tracks lifecycle events."""

    def __init__(self) -> None:
        self._state = AgentState.STOPPED
        self._started_at: datetime | None = None
        self._stopped_at: datetime | None = None

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def started_at(self) -> datetime | None:
        return self._started_at

    @property
    def uptime_seconds(self) -> float | None:
        if self._started_at is None:
            return None
        end = self._stopped_at or datetime.now(timezone.utc)
        return (end - self._started_at).total_seconds()

    def transition_to(self, new_state: AgentState) -> None:
        """Transition to a new state with validation."""
        valid_transitions: dict[AgentState, set[AgentState]] = {
            AgentState.STOPPED: {AgentState.INITIALIZING},
            AgentState.INITIALIZING: {AgentState.RUNNING, AgentState.STOPPED},
            AgentState.RUNNING: {AgentState.PAUSED, AgentState.SHUTTING_DOWN},
            AgentState.PAUSED: {AgentState.RUNNING, AgentState.SHUTTING_DOWN},
            AgentState.SHUTTING_DOWN: {AgentState.STOPPED},
        }

        allowed = valid_transitions.get(self._state, set())
        if new_state not in allowed:
            raise ValueError(
                f"Invalid state transition: {self._state} -> {new_state}. "
                f"Allowed: {allowed}"
            )

        self._state = new_state

        if new_state == AgentState.RUNNING and self._started_at is None:
            self._started_at = datetime.now(timezone.utc)
        elif new_state == AgentState.STOPPED:
            self._stopped_at = datetime.now(timezone.utc)

    def is_running(self) -> bool:
        return self._state == AgentState.RUNNING

    def is_accepting_work(self) -> bool:
        return self._state in {AgentState.RUNNING}
