"""Tests for state management."""

from __future__ import annotations

import pytest

from agent.core.state import AgentState, StateManager


class TestStateManager:
    def test_initial_state(self) -> None:
        sm = StateManager()
        assert sm.state == AgentState.STOPPED

    def test_valid_transitions(self) -> None:
        sm = StateManager()
        sm.transition_to(AgentState.INITIALIZING)
        assert sm.state == AgentState.INITIALIZING

        sm.transition_to(AgentState.RUNNING)
        assert sm.state == AgentState.RUNNING
        assert sm.started_at is not None

        sm.transition_to(AgentState.SHUTTING_DOWN)
        sm.transition_to(AgentState.STOPPED)
        assert sm.state == AgentState.STOPPED
        assert sm.uptime_seconds is not None
        assert sm.uptime_seconds >= 0

    def test_invalid_transition(self) -> None:
        sm = StateManager()
        with pytest.raises(ValueError, match="Invalid state transition"):
            sm.transition_to(AgentState.RUNNING)

    def test_is_running(self) -> None:
        sm = StateManager()
        assert not sm.is_running()

        sm.transition_to(AgentState.INITIALIZING)
        assert not sm.is_running()

        sm.transition_to(AgentState.RUNNING)
        assert sm.is_running()

    def test_is_accepting_work(self) -> None:
        sm = StateManager()
        assert not sm.is_accepting_work()

        sm.transition_to(AgentState.INITIALIZING)
        sm.transition_to(AgentState.RUNNING)
        assert sm.is_accepting_work()

        sm.transition_to(AgentState.PAUSED)
        assert not sm.is_accepting_work()
