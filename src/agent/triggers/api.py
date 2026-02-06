"""API trigger — exposes REST endpoints that fire trigger callbacks."""

from __future__ import annotations

from typing import Any, Callable, Coroutine

import structlog

logger = structlog.get_logger()

TriggerCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class APITrigger:
    """Trigger that fires when an API request is received.

    The actual HTTP handling is done via FastAPI routes.
    This class just manages the callback chain.
    """

    def __init__(self) -> None:
        self._callbacks: list[TriggerCallback] = []

    def on_trigger(self, callback: TriggerCallback) -> None:
        self._callbacks.append(callback)

    async def fire(self, context: dict[str, Any]) -> None:
        """Fire all registered callbacks with the given context."""
        logger.info("api_trigger.fired", context_keys=list(context.keys()))
        trigger_context = {"trigger_type": "api", **context}
        for callback in self._callbacks:
            try:
                await callback(trigger_context)
            except Exception:
                logger.exception("api_trigger.callback_error")
