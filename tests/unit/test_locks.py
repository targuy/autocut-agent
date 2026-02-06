"""Tests for lock managers."""

from __future__ import annotations

import pytest

from agent.resources.locks import LocalLockManager


class TestLocalLockManager:
    @pytest.mark.asyncio
    async def test_lock_and_unlock(self) -> None:
        lm = LocalLockManager()

        assert await lm.lock("gpu:0", "task-1") is True
        assert await lm.is_locked("gpu:0") is True
        assert await lm.get_holder("gpu:0") == "task-1"

        await lm.unlock("gpu:0", "task-1")
        assert await lm.is_locked("gpu:0") is False

    @pytest.mark.asyncio
    async def test_lock_exclusive(self) -> None:
        lm = LocalLockManager()

        assert await lm.lock("gpu:0", "task-1") is True
        assert await lm.lock("gpu:0", "task-2") is False
        assert await lm.get_holder("gpu:0") == "task-1"

    @pytest.mark.asyncio
    async def test_unlock_wrong_holder(self) -> None:
        lm = LocalLockManager()

        await lm.lock("gpu:0", "task-1")
        await lm.unlock("gpu:0", "task-2")  # should not release
        assert await lm.is_locked("gpu:0") is True

    @pytest.mark.asyncio
    async def test_multiple_resources(self) -> None:
        lm = LocalLockManager()

        await lm.lock("gpu:0", "task-1")
        await lm.lock("gpu:1", "task-2")

        assert await lm.get_holder("gpu:0") == "task-1"
        assert await lm.get_holder("gpu:1") == "task-2"

    @pytest.mark.asyncio
    async def test_unlocked_resource(self) -> None:
        lm = LocalLockManager()
        assert await lm.is_locked("nonexistent") is False
        assert await lm.get_holder("nonexistent") is None
