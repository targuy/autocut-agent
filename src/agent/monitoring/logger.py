"""Structured logging setup using structlog."""

from __future__ import annotations

import logging
import sys

import structlog

from agent.core.config import LoggingConfig


def setup_logging(config: LoggingConfig | None = None) -> None:
    """Configure structlog with JSON or text output."""
    level_name = "INFO"
    log_format = "json"

    if config:
        level_name = config.level.upper()
        log_format = config.format

    level = getattr(logging, level_name, logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if log_format == "json":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    # Quieten noisy third-party loggers
    for name in ("uvicorn.access", "watchdog", "apscheduler"):
        logging.getLogger(name).setLevel(max(level, logging.WARNING))
