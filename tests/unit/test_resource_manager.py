"""Tests for ResourceManager."""

from __future__ import annotations

import pytest

from agent.core.config import ResourceConfig
from agent.resources.manager import ResourceManager


class TestResourceManager:
    @pytest.mark.asyncio
    async def test_initialize_and_shutdown(self) -> None:
        rm = ResourceManager(resources=[], redis_url=None)
        await rm.initialize()
        await rm.shutdown()

    @pytest.mark.asyncio
    async def test_acquire_exclusive(self) -> None:
        configs = [ResourceConfig(id="gpu:0", type="gpu", exclusive=True)]
        rm = ResourceManager(resources=configs, redis_url=None)
        await rm.initialize()

        assert await rm.acquire("gpu:0", "task-1") is True
        assert await rm.is_available("gpu:0") is False

        # Second task cannot acquire the same resource
        assert await rm.acquire("gpu:0", "task-2") is False

        await rm.release("gpu:0", "task-1")
        assert await rm.is_available("gpu:0") is True
        await rm.shutdown()

    @pytest.mark.asyncio
    async def test_acquire_shared(self) -> None:
        configs = [
            ResourceConfig(id="cpu", type="cpu", exclusive=False, max_concurrent=2)
        ]
        rm = ResourceManager(resources=configs, redis_url=None)
        await rm.initialize()

        assert await rm.acquire("cpu", "task-1") is True
        assert await rm.acquire("cpu", "task-2") is True

        # Third task exceeds max_concurrent
        assert await rm.acquire("cpu", "task-3") is False

        await rm.release("cpu", "task-1")
        assert await rm.acquire("cpu", "task-3") is True

        await rm.shutdown()

    @pytest.mark.asyncio
    async def test_acquire_unknown_resource(self) -> None:
        rm = ResourceManager(resources=[], redis_url=None)
        await rm.initialize()

        assert await rm.acquire("unknown", "task-1") is False
        await rm.shutdown()

    @pytest.mark.asyncio
    async def test_not_initialized_raises(self) -> None:
        rm = ResourceManager(resources=[], redis_url=None)
        with pytest.raises(RuntimeError, match="not been initialized"):
            await rm.acquire("x", "t")

    @pytest.mark.asyncio
    async def test_get_status(self) -> None:
        configs = [ResourceConfig(id="gpu:0", type="gpu", exclusive=True)]
        rm = ResourceManager(resources=configs, redis_url=None)
        await rm.initialize()

        status = await rm.get_status()
        assert status["lock_backend"] == "local"
        assert "gpu:0" in status["resources"]
        await rm.shutdown()
