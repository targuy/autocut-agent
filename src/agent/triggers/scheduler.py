"""APScheduler-based cron and interval trigger."""

from __future__ import annotations

from typing import Any, Callable, Coroutine

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = structlog.get_logger()

# Callback type: async function receiving trigger context
TriggerCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class SchedulerTrigger:
    """Manages cron and interval-based schedule triggers."""

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._callbacks: list[TriggerCallback] = []

    def on_trigger(self, callback: TriggerCallback) -> None:
        self._callbacks.append(callback)

    def add_cron(
        self,
        job_id: str,
        cron_expression: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Add a cron-based schedule."""
        parts = cron_expression.split()
        trigger = CronTrigger(
            minute=parts[0] if len(parts) > 0 else "*",
            hour=parts[1] if len(parts) > 1 else "*",
            day=parts[2] if len(parts) > 2 else "*",
            month=parts[3] if len(parts) > 3 else "*",
            day_of_week=parts[4] if len(parts) > 4 else "*",
        )
        ctx = context or {}
        self._scheduler.add_job(
            self._fire,
            trigger=trigger,
            id=job_id,
            kwargs={"job_id": job_id, "context": ctx},
            replace_existing=True,
        )
        logger.info("scheduler.cron_added", job_id=job_id, cron=cron_expression)

    def add_interval(
        self,
        job_id: str,
        seconds: int,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Add an interval-based schedule."""
        ctx = context or {}
        self._scheduler.add_job(
            self._fire,
            trigger=IntervalTrigger(seconds=seconds),
            id=job_id,
            kwargs={"job_id": job_id, "context": ctx},
            replace_existing=True,
        )
        logger.info("scheduler.interval_added", job_id=job_id, seconds=seconds)

    async def start(self) -> None:
        self._scheduler.start()
        logger.info("scheduler.started")

    async def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("scheduler.stopped")

    async def _fire(self, job_id: str, context: dict[str, Any]) -> None:
        logger.info("scheduler.fired", job_id=job_id)
        trigger_context = {"trigger_type": "schedule", "job_id": job_id, **context}
        for callback in self._callbacks:
            try:
                await callback(trigger_context)
            except Exception:
                logger.exception("scheduler.callback_error", job_id=job_id)
