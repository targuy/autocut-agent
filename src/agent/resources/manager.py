"""ResourceManager — handles exclusive and shared resource allocation."""

from __future__ import annotations

from typing import Any

import structlog

from agent.core.config import ResourceConfig
from agent.resources.gpu import GPUInfo, detect_gpus, get_gpu_memory
from agent.resources.locks import LockManager, create_lock_manager

logger = structlog.get_logger()


class ResourceManager:
    """Central manager for resource discovery, locking, and status reporting.

    Depending on *redis_url* the manager delegates locking to either a
    :class:`RedisLockManager` (distributed) or a :class:`LocalLockManager`
    (single-node in-memory).
    """

    def __init__(
        self,
        resources: list[ResourceConfig],
        redis_url: str | None = None,
    ) -> None:
        self._resource_configs: dict[str, ResourceConfig] = {
            r.id: r for r in resources
        }
        self._redis_url = redis_url
        self._lock_manager: LockManager | None = None
        self._gpu_info: dict[str, GPUInfo] = {}
        self._initialized: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Detect available resources and initialise the lock manager."""
        self._lock_manager = create_lock_manager(redis_url=self._redis_url)

        # Auto-detect GPUs and register any that are referenced in config.
        gpu_configs = {
            rid: cfg
            for rid, cfg in self._resource_configs.items()
            if cfg.type == "gpu"
        }
        if gpu_configs:
            gpus = await detect_gpus()
            self._gpu_info = {g.id: g for g in gpus}

            # Warn about configured GPU resources that were not detected.
            for rid in gpu_configs:
                if rid not in self._gpu_info:
                    logger.warning(
                        "resource.gpu_not_detected",
                        resource_id=rid,
                        available=[g.id for g in gpus],
                    )

        logger.info(
            "resource.manager.initialized",
            resources=list(self._resource_configs.keys()),
            gpus=list(self._gpu_info.keys()),
            lock_backend="redis" if self._redis_url else "local",
        )
        self._initialized = True

    async def shutdown(self) -> None:
        """Release all locks and close the lock manager."""
        if self._lock_manager is not None:
            await self._lock_manager.release_all()
            await self._lock_manager.close()
            logger.info("resource.manager.shutdown")
        self._initialized = False

    # ------------------------------------------------------------------
    # Locking API
    # ------------------------------------------------------------------

    async def acquire(self, resource_id: str, task_id: str) -> bool:
        """Try to acquire the resource identified by *resource_id*.

        For **exclusive** resources this is a simple lock.  For **shared**
        resources we could extend this to count-based semaphore logic in the
        future; for now shared resources are always available unless their
        ``max_concurrent`` limit has been reached.

        Returns ``True`` if the resource was successfully acquired.
        """
        self._ensure_initialized()
        assert self._lock_manager is not None  # guaranteed after init

        cfg = self._resource_configs.get(resource_id)
        if cfg is None:
            logger.warning(
                "resource.unknown",
                resource_id=resource_id,
                task_id=task_id,
            )
            return False

        if cfg.exclusive:
            acquired = await self._lock_manager.lock(resource_id, task_id)
            if acquired:
                logger.info(
                    "resource.acquired",
                    resource=resource_id,
                    task=task_id,
                    exclusive=True,
                )
            return acquired

        # Shared resource: delegate to lock manager but allow up to
        # max_concurrent holders by using sub-slot keys.
        for slot in range(cfg.max_concurrent):
            slot_key = f"{resource_id}:slot:{slot}"
            if await self._lock_manager.lock(slot_key, task_id):
                logger.info(
                    "resource.acquired",
                    resource=resource_id,
                    task=task_id,
                    slot=slot,
                    exclusive=False,
                )
                return True

        logger.debug(
            "resource.all_slots_busy",
            resource=resource_id,
            task=task_id,
            max_concurrent=cfg.max_concurrent,
        )
        return False

    async def release(self, resource_id: str, task_id: str) -> None:
        """Release a previously acquired resource lock."""
        self._ensure_initialized()
        assert self._lock_manager is not None

        cfg = self._resource_configs.get(resource_id)
        if cfg is None:
            logger.warning(
                "resource.release_unknown",
                resource_id=resource_id,
                task_id=task_id,
            )
            return

        if cfg.exclusive:
            await self._lock_manager.unlock(resource_id, task_id)
            logger.info(
                "resource.released",
                resource=resource_id,
                task=task_id,
                exclusive=True,
            )
            return

        # Release whichever slot this task holds.
        for slot in range(cfg.max_concurrent):
            slot_key = f"{resource_id}:slot:{slot}"
            holder = await self._lock_manager.get_holder(slot_key)
            if holder == task_id:
                await self._lock_manager.unlock(slot_key, task_id)
                logger.info(
                    "resource.released",
                    resource=resource_id,
                    task=task_id,
                    slot=slot,
                    exclusive=False,
                )
                return

    async def is_available(self, resource_id: str) -> bool:
        """Check whether at least one slot for *resource_id* is free."""
        self._ensure_initialized()
        assert self._lock_manager is not None

        cfg = self._resource_configs.get(resource_id)
        if cfg is None:
            return False

        if cfg.exclusive:
            return not await self._lock_manager.is_locked(resource_id)

        for slot in range(cfg.max_concurrent):
            slot_key = f"{resource_id}:slot:{slot}"
            if not await self._lock_manager.is_locked(slot_key):
                return True
        return False

    # ------------------------------------------------------------------
    # Status / introspection
    # ------------------------------------------------------------------

    async def get_status(self) -> dict[str, Any]:
        """Return a status dict describing every managed resource."""
        self._ensure_initialized()
        assert self._lock_manager is not None

        resources: dict[str, Any] = {}
        for rid, cfg in self._resource_configs.items():
            entry: dict[str, Any] = {
                "type": cfg.type,
                "exclusive": cfg.exclusive,
                "max_concurrent": cfg.max_concurrent,
            }

            if cfg.exclusive:
                locked = await self._lock_manager.is_locked(rid)
                holder = await self._lock_manager.get_holder(rid) if locked else None
                entry["locked"] = locked
                entry["holder"] = holder
            else:
                slots: list[dict[str, Any]] = []
                for slot in range(cfg.max_concurrent):
                    slot_key = f"{rid}:slot:{slot}"
                    holder = await self._lock_manager.get_holder(slot_key)
                    slots.append({"slot": slot, "holder": holder})
                entry["slots"] = slots
                entry["available"] = sum(1 for s in slots if s["holder"] is None)

            # Attach GPU memory info when applicable.
            if cfg.type == "gpu":
                gpu = self._gpu_info.get(rid)
                if gpu is not None:
                    used, total = await get_gpu_memory(rid)
                    entry["gpu"] = {
                        "name": gpu.name,
                        "memory_total_mb": total,
                        "memory_used_mb": used,
                        "memory_free_mb": total - used,
                    }

            resources[rid] = entry

        return {
            "lock_backend": "redis" if self._redis_url else "local",
            "resources": resources,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError(
                "ResourceManager has not been initialized. "
                "Call 'await resource_manager.initialize()' first."
            )
