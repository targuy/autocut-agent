"""Worker implementation — executes a single Task and returns the updated result."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from agent.queue.models import Task, TaskStatus

logger = structlog.get_logger()

# The executor callback receives a task and returns a result dict (or None).
ExecutorCallback = Callable[[Task], Awaitable[dict[str, Any] | None]]


class Worker:
    """Runs a single task through an executor callback.

    Responsibilities:
      - Enforce the task timeout.
      - Catch and record exceptions.
      - Transition the task to its terminal status (COMPLETED / FAILED).
    """

    def __init__(
        self,
        worker_id: str,
        executor_callback: ExecutorCallback,
    ) -> None:
        self.worker_id = worker_id
        self._executor_callback = executor_callback
        self._current_task: Task | None = None

    @property
    def busy(self) -> bool:
        """Return True while the worker is executing a task."""
        return self._current_task is not None

    async def run(self, task: Task) -> Task:
        """Execute *task* and return the updated task with result or error.

        The task is transitioned to RUNNING before execution, and to
        COMPLETED or FAILED when finished.  A timeout (``task.timeout``
        seconds) is enforced around the executor callback.
        """
        self._current_task = task
        log = logger.bind(
            worker_id=self.worker_id,
            task_id=str(task.id),
            queue=task.queue_name,
        )

        try:
            task.mark_running()
            log.info("worker.task_started")

            result = await asyncio.wait_for(
                self._executor_callback(task),
                timeout=task.timeout,
            )

            task.mark_completed(result)
            log.info("worker.task_completed")

        except asyncio.TimeoutError:
            task.mark_failed(
                f"Task timed out after {task.timeout}s"
            )
            log.warning(
                "worker.task_timeout",
                timeout=task.timeout,
            )

        except asyncio.CancelledError:
            task.status = TaskStatus.CANCELLED
            log.info("worker.task_cancelled")
            raise  # propagate cancellation

        except Exception as exc:
            task.mark_failed(str(exc))
            log.error(
                "worker.task_failed",
                error=str(exc),
                exc_info=True,
            )

        finally:
            self._current_task = None

        return task
