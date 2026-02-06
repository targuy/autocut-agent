"""Command-line interface entry point."""

from __future__ import annotations

import asyncio
import signal
import sys

import click
import structlog

logger = structlog.get_logger()


@click.group()
@click.version_option(version="0.1.0", prog_name="autocut-agent")
def cli() -> None:
    """AutoCut-Agent — pipeline-oriented task orchestration system."""


@cli.command()
@click.option(
    "--config",
    "config_path",
    default="configs/default.yaml",
    help="Path to YAML configuration file.",
    type=click.Path(exists=True),
)
@click.option(
    "--dev",
    is_flag=True,
    default=False,
    help="Run in development mode (auto-reload, debug logging).",
)
@click.option(
    "--log-level",
    default=None,
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]),
    help="Override log level.",
)
def start(config_path: str, dev: bool, log_level: str | None) -> None:
    """Start the AutoCut-Agent orchestrator and API server."""
    from agent.core.config import load_config
    from agent.core.orchestrator import AgentOrchestrator
    from agent.monitoring.logger import setup_logging

    config = load_config(config_path)

    if dev:
        config.agent.log_level = "DEBUG"
        config.monitoring.logging.level = "DEBUG"
        config.monitoring.logging.format = "text"

    if log_level:
        config.agent.log_level = log_level
        config.monitoring.logging.level = log_level

    setup_logging(config.monitoring.logging)

    orchestrator = AgentOrchestrator(config)

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        shutdown_event = asyncio.Event()

        def _signal_handler() -> None:
            logger.info("signal.received", signal="SIGINT/SIGTERM")
            shutdown_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

        await orchestrator.start()

        # Start the API server in the background
        import uvicorn

        from agent.api.main import create_app

        app = create_app(orchestrator)
        uv_config = uvicorn.Config(
            app,
            host=config.api.host,
            port=config.api.port,
            log_level=config.agent.log_level.lower(),
            access_log=False,
        )
        server = uvicorn.Server(uv_config)
        server_task = asyncio.create_task(server.serve())

        logger.info(
            "api.listening",
            host=config.api.host,
            port=config.api.port,
        )

        await shutdown_event.wait()

        server.should_exit = True
        await server_task
        await orchestrator.stop()

    asyncio.run(_run())


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
