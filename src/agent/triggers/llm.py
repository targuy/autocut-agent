"""LLM trigger — natural language command parsing via LangChain."""

from __future__ import annotations

from typing import Any, Callable, Coroutine

import structlog

logger = structlog.get_logger()

TriggerCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class LLMTrigger:
    """Trigger that accepts natural language and delegates to the pipeline compiler.

    This is the entry point for LLM chat: receives user text,
    passes it through the pipeline compiler to create a pipeline,
    then fires the trigger callback with the compiled result.
    """

    def __init__(self) -> None:
        self._callbacks: list[TriggerCallback] = []

    def on_trigger(self, callback: TriggerCallback) -> None:
        self._callbacks.append(callback)

    async def process_message(self, message: str) -> dict[str, Any]:
        """Process a natural language message.

        Returns the trigger context that was dispatched.
        The actual LLM compilation happens in the pipeline compiler;
        this trigger just wraps the message and fires callbacks.
        """
        logger.info("llm_trigger.received", message_length=len(message))

        trigger_context: dict[str, Any] = {
            "trigger_type": "llm",
            "message": message,
        }

        for callback in self._callbacks:
            try:
                await callback(trigger_context)
            except Exception:
                logger.exception("llm_trigger.callback_error")

        return trigger_context
