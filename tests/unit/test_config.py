"""Tests for configuration loading and validation."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from agent.core.config import AgentConfig, LLMConfig, load_config


def _write_config(data: dict, path: Path) -> Path:
    config_path = path / "config.yaml"
    config_path.write_text(yaml.dump(data))
    return config_path


class TestAgentConfig:
    def test_default_config(self) -> None:
        config = AgentConfig()
        assert config.agent.name == "AutoCut Agent"
        assert config.agent.workers == 4
        assert config.agent.log_level == "INFO"

    def test_workers_range(self) -> None:
        config = AgentConfig(agent={"name": "Test", "workers": 1})
        assert config.agent.workers == 1

        with pytest.raises(Exception):
            AgentConfig(agent={"name": "Test", "workers": 0})

        with pytest.raises(Exception):
            AgentConfig(agent={"name": "Test", "workers": 33})

    def test_log_level_validation(self) -> None:
        config = AgentConfig(agent={"name": "Test", "log_level": "debug"})
        assert config.agent.log_level == "DEBUG"

        with pytest.raises(Exception):
            AgentConfig(agent={"name": "Test", "log_level": "invalid"})

    def test_queue_names_unique(self) -> None:
        with pytest.raises(Exception, match="unique"):
            AgentConfig(
                queues=[
                    {"name": "q1", "type": "fifo"},
                    {"name": "q1", "type": "priority"},
                ]
            )

    def test_resource_type_validation(self) -> None:
        config = AgentConfig(resources=[{"id": "gpu0", "type": "gpu"}])
        assert config.resources[0].type == "gpu"

        with pytest.raises(Exception):
            AgentConfig(resources=[{"id": "x", "type": "invalid"}])

    def test_llm_provider_validation(self) -> None:
        config = AgentConfig(llm={"provider": "anthropic", "model": "claude-3"})
        assert config.llm.provider == "anthropic"

        with pytest.raises(Exception):
            AgentConfig(llm={"provider": "invalid"})

    def test_llm_gemini_provider(self) -> None:
        config = LLMConfig(provider="gemini", model="gemini-pro")
        assert config.provider == "gemini"

    def test_llm_lmstudio_provider(self) -> None:
        config = LLMConfig(provider="lmstudio", model="local-model", base_url="http://localhost:1234/v1")
        assert config.provider == "lmstudio"
        assert config.base_url == "http://localhost:1234/v1"


class TestLoadConfig:
    def test_load_from_yaml(self, tmp_path: Path) -> None:
        data = {
            "agent": {"name": "Test Agent", "workers": 2},
            "database": {"url": "sqlite+aiosqlite:///test.db"},
        }
        config_path = _write_config(data, tmp_path)
        config = load_config(config_path)
        assert config.agent.name == "Test Agent"
        assert config.agent.workers == 2

    def test_env_var_substitution(self, tmp_path: Path) -> None:
        os.environ["TEST_SECRET_KEY"] = "my-secret"
        try:
            data = {"api": {"secret_key": "${TEST_SECRET_KEY}"}}
            config_path = _write_config(data, tmp_path)
            config = load_config(config_path)
            assert config.api.secret_key == "my-secret"
        finally:
            del os.environ["TEST_SECRET_KEY"]

    def test_missing_file(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/config.yaml")
