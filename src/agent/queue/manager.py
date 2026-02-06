"""QueueManager and TaskDispatcher — queue lifecycle and task routing."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from agent.core.config import QueueConfig
from agent.queue.models import QueueStatus, Task, TaskStatus
from agent.resources.manager import ResourceManager

logger = structlog.get_logger()

# Type alias for the callback that actually executes a task.
ExecuteCallback = Callable[[Task], Awaitable[Task]]

# Type alias for listeners notified on task state changes.
TaskStateListener = Callable[[Task], Awaitable[None]]


# ---------------------------------------------------------------------------
# Internal queue wrapper
# ---------------------------------------------------------------------------


class _ManagedQueue:
    """Wraps an asyncio.PriorityQueue with metadata from QueueConfig."""

    def __init__(self, config: QueueConfig) -> None:
        self.name: str = config.name
        self.queue_type: str = config.type
        self.max_workers: int = config.workers
        self.priority: int = config.priority
        self.status: QueueStatus = QueueStatus.ACTIVE
        self._queue: asyncio.PriorityQueue[Task] = asyncio.PriorityQueue()

    async def put(self, task: Task) -> None:
        await self._queue.put(task)

    async def get(self) -> Task:
        return await self._queue.get()

    def empty(self) -> bool:
        return self._queue.empty()

    @property
    def depth(self) -> int:
        return self._queue.qsize()


# ---------------------------------------------------------------------------
# TaskDispatcher
# ---------------------------------------------------------------------------


class TaskDispatcher:
    """Dispatches tasks to an executor callback, managing resource acquisition
    and release, and notifying listeners on state transitions."""

    def __init__(self, resource_manager: ResourceManager) -> None:
        self._resource_manager = resource_manager
        self._listeners: list[TaskStateListener] = []

    def add_listener(self, listener: TaskStateListener) -> None:
        """Register a callback invoked whenever a task changes state."""
        self._listeners.append(listener)

    def remove_listener(self, listener: TaskStateListener) -> None:
        self._listeners.remove(listener)

    async def _notify(self, task: Task) -> None:
        for listener in self._listeners:
            try:
                await listener(task)
            except Exception:
                logger.exception(
                    "dispatcher.listener_error", task_id=str(task.id)
                )

    async def dispatch(
        self,
        task: Task,
        execute_callback: ExecuteCallback,
    ) -> Task:
        """Acquire resources, run the callback, release resources, notify."""
        acquired_resources: list[str] = []
        try:
            # Acquire every required resource before executing.
            for resource_id in task.resource_requirements:
                ok = await self._resource_manager.acquire(
                    resource_id=resource_id, task_id=str(task.id)
                )
                if not ok:
                    # Unable to secure all resources — release those already held.
                    for rid in acquired_resources:
                        await self._resource_manager.release(
                            resource_id=rid, task_id=str(task.id)
                        )
                    logger.debug(
                        "dispatcher.resource_unavailable",
                        task_id=str(task.id),
                        resource_id=resource_id,
                    )
                    return task  # returned unchanged — caller can re-enqueue
                acquired_resources.append(resource_id)

            task.mark_running()
            await self._notify(task)
            logger.info(
                "dispatcher.task_started",
                task_id=str(task.id),
                queue=task.queue_name,
            )

            task = await execute_callback(task)

        except Exception as exc:
            task.mark_failed(str(exc))
            logger.error(
                "dispatcher.task_error",
                task_id=str(task.id),
                error=str(exc),
            )

        finally:
            # Always release every resource that was acquired.
            for rid in acquired_resources:
                await self._resource_manager.release(
                    resource_id=rid, task_id=str(task.id)
                )

        await self._notify(task)
        return task


# ---------------------------------------------------------------------------
# QueueManager
# ---------------------------------------------------------------------------


class QueueManager:
    """Owns the named queues, runs a dispatch loop, and routes tasks."""

    def __init__(
        self,
        queues: list[QueueConfig],
        resource_manager: ResourceManager,
    ) -> None:
        self._configs = {q.name: q for q in queues}
        self._queues: dict[str, _ManagedQueue] = {}
        self._dispatcher = TaskDispatcher(resource_manager)
        self._dispatch_task: asyncio.Task[None] | None = None
        self._running = False
        self._execute_callback: ExecuteCallback | None = None

    # -- public API ----------------------------------------------------------

    @property
    def dispatcher(self) -> TaskDispatcher:
        """Expose the dispatcher so callers can register listeners."""
        return self._dispatcher

    def set_execute_callback(self, callback: ExecuteCallback) -> None:
        """Set the callback used to execute dispatched tasks."""
        self._execute_callback = callback

    async def start(self) -> None:
        """Initialise queues from configuration and start the dispatch loop."""
        for name, cfg in self._configs.items():
            self._queues[name] = _ManagedQueue(cfg)
            logger.info(
                "queue.created",
                name=name,
                type=cfg.type,
                workers=cfg.workers,
            )
        self._running = True
        self._dispatch_task = asyncio.create_task(self._dispatch_loop())
        logger.info("queue_manager.started", queue_count=len(self._queues))

    async def stop(self) -> None:
        """Stop the dispatch loop gracefully."""
        self._running = False
        if self._dispatch_task is not None:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
            self._dispatch_task = None
        logger.info("queue_manager.stopped")

    async def add_task(self, task: Task) -> None:
        """Add a task to the queue indicated by task.queue_name."""
        mq = self._queues.get(task.queue_name)
        if mq is None:
            raise ValueError(f"Unknown queue: {task.queue_name!r}")
        if mq.status == QueueStatus.PAUSED:
            raise RuntimeError(
                f"Queue {task.queue_name!r} is paused — cannot accept tasks"
            )
        task.status = TaskStatus.QUEUED
        await mq.put(task)
        logger.info(
            "queue.task_added",
            task_id=str(task.id),
            queue=task.queue_name,
            depth=mq.depth,
        )

    async def get_queue_status(self, name: str) -> dict[str, Any]:
        """Return a snapshot of the named queue's status."""
        mq = self._queues.get(name)
        if mq is None:
            raise ValueError(f"Unknown queue: {name!r}")
        return {
            "name": mq.name,
            "type": mq.queue_type,
            "status": mq.status.value,
            "depth": mq.depth,
            "max_workers": mq.max_workers,
            "priority": mq.priority,
        }

    async def pause_queue(self, name: str) -> None:
        """Pause the queue so it stops dispatching but can still accept tasks."""
        mq = self._get_queue(name)
        mq.status = QueueStatus.PAUSED
        logger.info("queue.paused", name=name)

    async def resume_queue(self, name: str) -> None:
        """Resume a paused queue."""
        mq = self._get_queue(name)
        mq.status = QueueStatus.ACTIVE
        logger.info("queue.resumed", name=name)

    def list_queues(self) -> list[str]:
        """Return all registered queue names."""
        return list(self._queues.keys())

    # -- internals -----------------------------------------------------------

    def _get_queue(self, name: str) -> _ManagedQueue:
        mq = self._queues.get(name)
        if mq is None:
            raise ValueError(f"Unknown queue: {name!r}")
        return mq

    async def _dispatch_loop(self) -> None:
        """Continuously pull tasks from active queues and dispatch them.

        Tasks are pulled in priority order across queues (highest-priority
        queue first).  When no callback is registered, tasks remain in their
        queues and the loop sleeps briefly.
        """
        logger.debug("dispatch_loop.started")
        while self._running:
            dispatched_any = False
            # Sort queues by priority (highest first).
            sorted_queues = sorted(
                self._queues.values(),
                key=lambda q: q.priority,
                reverse=True,
            )
            for mq in sorted_queues:
                if mq.status != QueueStatus.ACTIVE:
                    continue
                if mq.empty():
                    continue
                if self._execute_callback is None:
                    continue

                task = await mq.get()
                dispatched_any = True
                # Fire-and-forget: the dispatcher handles resource locking.
                asyncio.create_task(
                    self._dispatcher.dispatch(task, self._execute_callback)
                )

            if not dispatched_any:
                # Avoid busy-waiting when all queues are empty or paused.
                await asyncio.sleep(0.1)
