"""Configuration loading and validation using Pydantic models."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


def _substitute_env_vars(data: Any) -> Any:
    """Recursively substitute ${VAR_NAME} patterns with environment variables."""
    if isinstance(data, str):
        pattern = re.compile(r"\$\{(\w+)\}")
        return pattern.sub(lambda m: os.environ.get(m.group(1), m.group(0)), data)
    if isinstance(data, dict):
        return {k: _substitute_env_vars(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_substitute_env_vars(item) for item in data]
    return data


class AgentSettings(BaseModel):
    name: str = "AutoCut Agent"
    workers: int = Field(default=4, ge=1, le=32)
    log_level: str = Field(default="INFO")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v = v.upper()
        if v not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return v


class DatabaseSettings(BaseModel):
    url: str = "sqlite+aiosqlite:///agent.db"
    pool_size: int = Field(default=5, ge=1, le=100)


class RedisSettings(BaseModel):
    url: str = "redis://localhost:6379/0"
    enabled: bool = False


class ResourceConfig(BaseModel):
    id: str
    type: str = Field(default="cpu")
    exclusive: bool = False
    max_concurrent: int = Field(default=1, ge=1)

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        allowed = {"gpu", "cpu", "custom"}
        if v not in allowed:
            raise ValueError(f"resource type must be one of {allowed}")
        return v


class QueueConfig(BaseModel):
    name: str
    type: str = Field(default="priority")
    workers: int = Field(default=2, ge=1)
    priority: int = Field(default=0)

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        allowed = {"fifo", "priority", "parallel"}
        if v not in allowed:
            raise ValueError(f"queue type must be one of {allowed}")
        return v


class ProgramConfig(BaseModel):
    id: str
    path: str
    venv: str | None = None
    timeout: int = Field(default=3600, ge=1)
    retry_attempts: int = Field(default=0, ge=0)


class TriggerConfig(BaseModel):
    type: str
    enabled: bool = True
    cron: str | None = None
    path: str | None = None
    pattern: str | None = None
    program: str | None = None
    queue: str | None = None

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        allowed = {"schedule", "file_watcher", "api", "llm"}
        if v not in allowed:
            raise ValueError(f"trigger type must be one of {allowed}")
        return v


class MetricsConfig(BaseModel):
    enabled: bool = True
    prometheus_port: int = Field(default=9090, ge=1, le=65535)


class LoggingConfig(BaseModel):
    format: str = "json"
    level: str = "INFO"
    file: str | None = None

    @field_validator("format")
    @classmethod
    def validate_format(cls, v: str) -> str:
        if v not in {"json", "text"}:
            raise ValueError("format must be 'json' or 'text'")
        return v


class AlertConfig(BaseModel):
    type: str
    smtp_server: str | None = None
    smtp_port: int | None = None
    username: str | None = None
    password: str | None = None
    recipients: list[str] = Field(default_factory=list)
    webhook_url: str | None = None
    on_events: list[str] = Field(default_factory=list)


class MonitoringConfig(BaseModel):
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    alerts: list[AlertConfig] = Field(default_factory=list)


class APIConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    secret_key: str = "change-me"


class LLMConfig(BaseModel):
    provider: str = "openai"
    model: str = "gpt-4"
    api_key: str | None = None
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        if v not in {"openai", "anthropic"}:
            raise ValueError("provider must be 'openai' or 'anthropic'")
        return v


class AgentConfig(BaseModel):
    """Root configuration model for AutoCut-Agent."""

    agent: AgentSettings = Field(default_factory=AgentSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    resources: list[ResourceConfig] = Field(default_factory=list)
    queues: list[QueueConfig] = Field(default_factory=list)
    programs: list[ProgramConfig] = Field(default_factory=list)
    triggers: list[TriggerConfig] = Field(default_factory=list)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    api: APIConfig = Field(default_factory=APIConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)

    @field_validator("queues")
    @classmethod
    def validate_queue_names_unique(cls, v: list[QueueConfig]) -> list[QueueConfig]:
        names = [q.name for q in v]
        if len(names) != len(set(names)):
            raise ValueError("Queue names must be unique")
        return v


def load_config(path: str | Path) -> AgentConfig:
    """Load and validate configuration from a YAML file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    data = _substitute_env_vars(data)
    return AgentConfig(**data)
