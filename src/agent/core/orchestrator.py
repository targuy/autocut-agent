"""AgentOrchestrator — central coordination class that owns all subsystems."""

from __future__ import annotations

import structlog

from agent.core.config import AgentConfig
from agent.core.database import Database
from agent.core.state import AgentState, StateManager
from agent.executor.runner import ExecutorPool
from agent.monitoring.logger import setup_logging
from agent.pipeline.engine import PipelineEngine
from agent.queue.manager import QueueManager
from agent.resources.manager import ResourceManager

logger = structlog.get_logger()


class AgentOrchestrator:
    """Central orchestrator that initialises and coordinates all subsystems."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.state = StateManager()
        self.db = Database(
            url=config.database.url,
            pool_size=config.database.pool_size,
        )
        self.resource_manager = ResourceManager(
            resources=config.resources,
            redis_url=config.redis.url if config.redis.enabled else None,
        )
        self.queue_manager = QueueManager(
            queues=config.queues,
            resource_manager=self.resource_manager,
        )
        self.executor_pool = ExecutorPool(max_workers=config.agent.workers)
        self.pipeline_engine = PipelineEngine(
            db=self.db,
            queue_manager=self.queue_manager,
            executor_pool=self.executor_pool,
            resource_manager=self.resource_manager,
        )

    async def start(self) -> None:
        """Start all subsystems in order."""
        self.state.transition_to(AgentState.INITIALIZING)
        logger.info("agent.starting", name=self.config.agent.name)

        setup_logging(self.config.monitoring.logging)

        await self.db.create_tables()
        logger.info("database.ready")

        await self.resource_manager.initialize()
        logger.info("resources.ready")

        await self.queue_manager.start()
        logger.info("queues.ready")

        self.state.transition_to(AgentState.RUNNING)
        logger.info(
            "agent.started",
            name=self.config.agent.name,
            workers=self.config.agent.workers,
        )

    async def stop(self) -> None:
        """Graceful shutdown of all subsystems."""
        if self.state.state == AgentState.STOPPED:
            return

        self.state.transition_to(AgentState.SHUTTING_DOWN)
        logger.info("agent.stopping")

        await self.queue_manager.stop()
        await self.executor_pool.shutdown()
        await self.resource_manager.shutdown()
        await self.db.close()

        self.state.transition_to(AgentState.STOPPED)
        logger.info(
            "agent.stopped", uptime_seconds=self.state.uptime_seconds
        )
