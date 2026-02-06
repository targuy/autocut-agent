"""Lock managers for resource allocation — Redis-based and local fallback."""

from __future__ import annotations

import abc
import asyncio
from typing import Any

import structlog

logger = structlog.get_logger()

# Default TTL for Redis locks (seconds).  A long-running task will need to
# extend the lock externally or complete within this window.
_DEFAULT_LOCK_TTL: int = 3600


class LockManager(abc.ABC):
    """Abstract interface for resource locking."""

    @abc.abstractmethod
    async def lock(self, resource_id: str, holder_id: str) -> bool:
        """Attempt to acquire a lock for *resource_id* on behalf of *holder_id*.

        Returns ``True`` if the lock was successfully acquired.
        """

    @abc.abstractmethod
    async def unlock(self, resource_id: str, holder_id: str) -> None:
        """Release the lock on *resource_id* held by *holder_id*.

        If *holder_id* does not hold the lock the call is a no-op.
        """

    @abc.abstractmethod
    async def is_locked(self, resource_id: str) -> bool:
        """Return ``True`` if *resource_id* is currently locked."""

    @abc.abstractmethod
    async def get_holder(self, resource_id: str) -> str | None:
        """Return the holder currently owning the lock, or ``None``."""

    async def release_all(self) -> None:  # noqa: B027
        """Release all locks managed by this instance.

        Subclasses should override this for proper cleanup.
        """

    async def close(self) -> None:  # noqa: B027
        """Clean up any underlying connections."""


# ---------------------------------------------------------------------------
# Local (in-memory) lock manager — single-node mode
# ---------------------------------------------------------------------------


class LocalLockManager(LockManager):
    """In-memory lock manager for single-node deployments.

    Thread-safe: all mutations are guarded by an ``asyncio.Lock`` so that
    concurrent coroutines cannot race on the same resource.
    """

    def __init__(self) -> None:
        self._locks: dict[str, str] = {}  # resource_id -> holder_id
        self._mu = asyncio.Lock()

    async def lock(self, resource_id: str, holder_id: str) -> bool:
        async with self._mu:
            current = self._locks.get(resource_id)
            if current is not None:
                if current == holder_id:
                    # Re-entrant: same holder already owns the lock.
                    return True
                logger.debug(
                    "lock.local.busy",
                    resource=resource_id,
                    holder=current,
                    requester=holder_id,
                )
                return False
            self._locks[resource_id] = holder_id
            logger.debug(
                "lock.local.acquired",
                resource=resource_id,
                holder=holder_id,
            )
            return True

    async def unlock(self, resource_id: str, holder_id: str) -> None:
        async with self._mu:
            current = self._locks.get(resource_id)
            if current is None:
                return
            if current != holder_id:
                logger.warning(
                    "lock.local.unlock_mismatch",
                    resource=resource_id,
                    holder=current,
                    requester=holder_id,
                )
                return
            del self._locks[resource_id]
            logger.debug(
                "lock.local.released",
                resource=resource_id,
                holder=holder_id,
            )

    async def is_locked(self, resource_id: str) -> bool:
        async with self._mu:
            return resource_id in self._locks

    async def get_holder(self, resource_id: str) -> str | None:
        async with self._mu:
            return self._locks.get(resource_id)

    async def release_all(self) -> None:
        async with self._mu:
            count = len(self._locks)
            self._locks.clear()
            if count:
                logger.info("lock.local.release_all", count=count)


# ---------------------------------------------------------------------------
# Redis-based lock manager — distributed mode
# ---------------------------------------------------------------------------


class RedisLockManager(LockManager):
    """Distributed lock manager backed by Redis.

    Uses ``SET NX`` with a TTL for lock acquisition.  Keys follow the pattern
    ``autocut:lock:{resource_id}`` and store the *holder_id* as value.

    If Redis is unavailable the manager logs a warning and returns ``False``
    from :meth:`lock` (fail-closed) rather than raising.
    """

    KEY_PREFIX = "autocut:lock:"

    def __init__(self, redis_url: str, lock_ttl: int = _DEFAULT_LOCK_TTL) -> None:
        self._redis_url = redis_url
        self._lock_ttl = lock_ttl
        self._redis: Any = None  # redis.asyncio.Redis instance

    async def _get_redis(self) -> Any:
        """Lazily create the Redis connection."""
        if self._redis is None:
            try:
                import redis.asyncio as aioredis

                self._redis = aioredis.from_url(
                    self._redis_url,
                    decode_responses=True,
                )
                # Verify connectivity.
                await self._redis.ping()
                logger.info("lock.redis.connected", url=self._redis_url)
            except Exception as exc:
                logger.warning(
                    "lock.redis.connection_failed",
                    url=self._redis_url,
                    error=str(exc),
                )
                self._redis = None
        return self._redis

    def _key(self, resource_id: str) -> str:
        return f"{self.KEY_PREFIX}{resource_id}"

    async def lock(self, resource_id: str, holder_id: str) -> bool:
        rds = await self._get_redis()
        if rds is None:
            logger.warning(
                "lock.redis.unavailable",
                resource=resource_id,
                holder=holder_id,
            )
            return False

        try:
            key = self._key(resource_id)
            # SET key holder_id NX EX ttl
            acquired = await rds.set(key, holder_id, nx=True, ex=self._lock_ttl)
            if acquired:
                logger.debug(
                    "lock.redis.acquired",
                    resource=resource_id,
                    holder=holder_id,
                    ttl=self._lock_ttl,
                )
                return True

            # Check if the current holder is re-acquiring.
            current = await rds.get(key)
            if current == holder_id:
                # Refresh TTL for the same holder.
                await rds.expire(key, self._lock_ttl)
                return True

            logger.debug(
                "lock.redis.busy",
                resource=resource_id,
                holder=current,
                requester=holder_id,
            )
            return False
        except Exception as exc:
            logger.warning(
                "lock.redis.lock_error",
                resource=resource_id,
                holder=holder_id,
                error=str(exc),
            )
            return False

    async def unlock(self, resource_id: str, holder_id: str) -> None:
        rds = await self._get_redis()
        if rds is None:
            return

        try:
            key = self._key(resource_id)
            # Only delete if the holder matches (atomic via Lua script).
            lua = """
            if redis.call("get", KEYS[1]) == ARGV[1] then
                return redis.call("del", KEYS[1])
            else
                return 0
            end
            """
            result = await rds.eval(lua, 1, key, holder_id)
            if result:
                logger.debug(
                    "lock.redis.released",
                    resource=resource_id,
                    holder=holder_id,
                )
            else:
                logger.debug(
                    "lock.redis.unlock_noop",
                    resource=resource_id,
                    holder=holder_id,
                )
        except Exception as exc:
            logger.warning(
                "lock.redis.unlock_error",
                resource=resource_id,
                holder=holder_id,
                error=str(exc),
            )

    async def is_locked(self, resource_id: str) -> bool:
        rds = await self._get_redis()
        if rds is None:
            return False
        try:
            return await rds.exists(self._key(resource_id)) > 0
        except Exception as exc:
            logger.warning(
                "lock.redis.exists_error",
                resource=resource_id,
                error=str(exc),
            )
            return False

    async def get_holder(self, resource_id: str) -> str | None:
        rds = await self._get_redis()
        if rds is None:
            return None
        try:
            return await rds.get(self._key(resource_id))
        except Exception as exc:
            logger.warning(
                "lock.redis.get_holder_error",
                resource=resource_id,
                error=str(exc),
            )
            return None

    async def release_all(self) -> None:
        rds = await self._get_redis()
        if rds is None:
            return
        try:
            cursor: int | str = 0
            count = 0
            while True:
                cursor, keys = await rds.scan(
                    cursor=cursor, match=f"{self.KEY_PREFIX}*", count=100
                )
                if keys:
                    await rds.delete(*keys)
                    count += len(keys)
                if cursor == 0:
                    break
            if count:
                logger.info("lock.redis.release_all", count=count)
        except Exception as exc:
            logger.warning("lock.redis.release_all_error", error=str(exc))

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
            logger.debug("lock.redis.closed")


def create_lock_manager(
    redis_url: str | None = None,
    lock_ttl: int = _DEFAULT_LOCK_TTL,
) -> LockManager:
    """Factory: return a ``RedisLockManager`` when *redis_url* is provided,
    otherwise fall back to ``LocalLockManager``."""
    if redis_url is not None:
        logger.info("lock.using_redis", url=redis_url)
        return RedisLockManager(redis_url=redis_url, lock_ttl=lock_ttl)
    logger.info("lock.using_local")
    return LocalLockManager()
