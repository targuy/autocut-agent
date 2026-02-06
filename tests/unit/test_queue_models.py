"""Tests for queue data models."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from agent.queue.models import Task, TaskStatus


class TestTask:
    def test_create_task(self) -> None:
        task = Task(queue_name="default", command="echo test")
        assert task.queue_name == "default"
        assert task.command == "echo test"
        assert task.status == TaskStatus.PENDING
        assert isinstance(task.id, uuid.UUID)
        assert task.priority == 0

    def test_mark_running(self) -> None:
        task = Task(queue_name="default", command="echo test")
        assert task.started_at is None

        task.mark_running()
        assert task.status == TaskStatus.RUNNING
        assert task.started_at is not None

    def test_mark_completed(self) -> None:
        task = Task(queue_name="default", command="echo test")
        task.mark_running()
        task.mark_completed(result={"output": "done"})

        assert task.status == TaskStatus.COMPLETED
        assert task.completed_at is not None
        assert task.result == {"output": "done"}

    def test_mark_failed(self) -> None:
        task = Task(queue_name="default", command="echo test")
        task.mark_running()
        task.mark_failed("Something broke")

        assert task.status == TaskStatus.FAILED
        assert task.error == "Something broke"
        assert task.completed_at is not None

    def test_mark_cancelled(self) -> None:
        task = Task(queue_name="default", command="echo test")
        task.mark_cancelled()

        assert task.status == TaskStatus.CANCELLED
        assert task.completed_at is not None

    def test_is_terminal(self) -> None:
        task = Task(queue_name="default", command="echo test")
        assert not task.is_terminal()

        task.status = TaskStatus.RUNNING
        assert not task.is_terminal()

        task.status = TaskStatus.COMPLETED
        assert task.is_terminal()

        task.status = TaskStatus.FAILED
        assert task.is_terminal()

        task.status = TaskStatus.CANCELLED
        assert task.is_terminal()

    def test_task_ordering(self) -> None:
        """Higher priority sorts first (less-than means higher prio)."""
        t1 = Task(queue_name="q", command="a", priority=10)
        t2 = Task(queue_name="q", command="b", priority=5)
        assert t1 < t2  # t1 is higher priority -> sorts first
        assert not t2 < t1
