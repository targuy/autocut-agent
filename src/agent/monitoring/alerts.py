"""AlertManager — sends alerts via email, webhook, Slack, Discord."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

from agent.core.config import AlertConfig

logger = structlog.get_logger()


class AlertManager:
    """Manages alert dispatching with throttling and deduplication."""

    def __init__(self, configs: list[AlertConfig]) -> None:
        self._configs = configs
        self._last_sent: dict[str, datetime] = {}
        self._throttle_seconds = 60

    async def send(
        self,
        event_type: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Send alert to all configured channels that match the event type."""
        for config in self._configs:
            if config.on_events and event_type not in config.on_events:
                continue

            throttle_key = f"{config.type}:{event_type}"
            now = datetime.now(timezone.utc)
            last = self._last_sent.get(throttle_key)
            if last and (now - last).total_seconds() < self._throttle_seconds:
                continue

            try:
                match config.type:
                    case "webhook" | "slack" | "discord":
                        await self._send_webhook(config, event_type, message, context)
                    case "email":
                        logger.info(
                            "alert.email.skipped",
                            reason="SMTP not yet implemented",
                            event=event_type,
                        )
                    case _:
                        logger.warning("alert.unknown_type", type=config.type)
                self._last_sent[throttle_key] = now
            except Exception:
                logger.exception("alert.send_failed", type=config.type, event=event_type)

    async def _send_webhook(
        self,
        config: AlertConfig,
        event_type: str,
        message: str,
        context: dict[str, Any] | None,
    ) -> None:
        if not config.webhook_url:
            return

        payload: dict[str, Any] = {
            "event": event_type,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if context:
            payload["context"] = context

        # Slack / Discord use slightly different payload shapes
        if config.type in {"slack", "discord"}:
            text = f"[{event_type}] {message}"
            if config.type == "slack":
                payload = {"text": text}
            else:
                payload = {"content": text}

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(config.webhook_url, json=payload)
            resp.raise_for_status()

        logger.info("alert.sent", type=config.type, event=event_type)
