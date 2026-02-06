"""Database setup and SQLAlchemy table definitions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    event,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid_str() -> str:
    return str(uuid4())


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


class PipelineTemplateRow(Base):
    __tablename__ = "pipeline_templates"

    id = Column(String(36), primary_key=True, default=_uuid_str)
    name = Column(String(255), nullable=False)
    description = Column(Text, default="")
    steps_definition = Column(JSON, nullable=False)
    version = Column(Integer, default=1, nullable=False)
    created_by = Column(String(50), default="manual")
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class PipelineRow(Base):
    __tablename__ = "pipelines"

    id = Column(String(36), primary_key=True, default=_uuid_str)
    template_id = Column(
        String(36), ForeignKey("pipeline_templates.id"), nullable=True
    )
    name = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    inputs = Column(JSON, default=dict)
    context = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    started_at = Column(DateTime(timezone=True), nullable=True)
    resumed_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    steps = relationship("PipelineStepRow", back_populates="pipeline", lazy="selectin")

    __table_args__ = (Index("idx_pipeline_status", "status"),)


class PipelineStepRow(Base):
    __tablename__ = "pipeline_steps"

    id = Column(String(36), primary_key=True, default=_uuid_str)
    pipeline_id = Column(String(36), ForeignKey("pipelines.id"), nullable=False)
    order = Column(Integer, nullable=False)
    name = Column(String(255), nullable=False)
    command_type = Column(String(20), nullable=False)
    command_template = Column(Text, nullable=False)
    condition = Column(Text, nullable=True)
    input_mappings = Column(JSON, default=dict)
    fan_out_on = Column(String(255), nullable=True)
    depends_on = Column(JSON, default=list)
    resource_requirements = Column(JSON, default=list)
    timeout = Column(Integer, default=3600)
    retry_max = Column(Integer, default=0)
    retry_count = Column(Integer, default=0)
    status = Column(String(30), nullable=False, default="pending")

    pipeline = relationship("PipelineRow", back_populates="steps")
    executions = relationship(
        "StepExecutionRow", back_populates="step", lazy="selectin"
    )

    __table_args__ = (Index("idx_step_pipeline_order", "pipeline_id", "order"),)


class StepExecutionRow(Base):
    __tablename__ = "step_executions"

    id = Column(String(36), primary_key=True, default=_uuid_str)
    step_id = Column(String(36), ForeignKey("pipeline_steps.id"), nullable=False)
    index = Column(Integer, nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)

    step = relationship("PipelineStepRow", back_populates="executions")

    __table_args__ = (Index("idx_exec_step_status", "step_id", "status"),)


class ArtifactRow(Base):
    __tablename__ = "artifacts"

    id = Column(String(36), primary_key=True, default=_uuid_str)
    pipeline_id = Column(String(36), ForeignKey("pipelines.id"), nullable=False)
    step_id = Column(String(36), ForeignKey("pipeline_steps.id"), nullable=False)
    execution_id = Column(
        String(36), ForeignKey("step_executions.id"), nullable=True
    )
    key = Column(String(255), nullable=False)
    value = Column(JSON, nullable=True)
    file_path = Column(String(1024), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("idx_artifact_pipeline_step_key", "pipeline_id", "step_id", "key"),
    )


class QueueRow(Base):
    __tablename__ = "queues"

    name = Column(String(255), primary_key=True)
    type = Column(String(50), nullable=False, default="priority")
    workers = Column(Integer, nullable=False, default=2)
    priority = Column(Integer, default=0)
    status = Column(String(20), nullable=False, default="active")
    config = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class ExecutionLogRow(Base):
    __tablename__ = "execution_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pipeline_id = Column(String(36), ForeignKey("pipelines.id"), nullable=True)
    step_id = Column(String(36), ForeignKey("pipeline_steps.id"), nullable=True)
    task_id = Column(String(36), nullable=True)
    timestamp = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    level = Column(String(20), nullable=False)
    message = Column(Text, nullable=False)
    context = Column(JSON, nullable=True)

    __table_args__ = (
        Index("idx_log_pipeline_time", "pipeline_id", "timestamp"),
        Index("idx_log_step_time", "step_id", "timestamp"),
    )


# ---------------------------------------------------------------------------
# Engine / Session helpers
# ---------------------------------------------------------------------------


class Database:
    """Async database manager."""

    def __init__(self, url: str, pool_size: int = 5) -> None:
        connect_args: dict[str, Any] = {}
        if "sqlite" in url:
            connect_args["check_same_thread"] = False

        self.engine = create_async_engine(
            url,
            echo=False,
            pool_pre_ping=True,
            **({"connect_args": connect_args} if connect_args else {}),
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def create_tables(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def drop_tables(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    def session(self) -> AsyncSession:
        return self.session_factory()

    async def close(self) -> None:
        await self.engine.dispose()


# Enable WAL mode for SQLite connections
@event.listens_for(Base.metadata, "after_create")
def _set_sqlite_pragma(target: Any, connection: Any, **kwargs: Any) -> None:
    if connection.dialect.name == "sqlite":
        cursor = connection.connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()
