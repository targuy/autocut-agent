"""Watchdog-based file system monitoring trigger."""

from __future__ import annotations

import asyncio
import fnmatch
import time
from pathlib import Path
from typing import Any, Callable, Coroutine

import structlog
from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = structlog.get_logger()

TriggerCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class _DebouncedHandler(FileSystemEventHandler):
    """Watchdog handler with debouncing and pattern matching."""

    def __init__(
        self,
        pattern: str,
        callback: Callable[[str], None],
        debounce_seconds: float = 1.0,
    ) -> None:
        super().__init__()
        self._pattern = pattern
        self._callback = callback
        self._debounce = debounce_seconds
        self._last_fired: dict[str, float] = {}

    def on_created(self, event: FileCreatedEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        path = event.src_path
        if not fnmatch.fnmatch(Path(path).name, self._pattern):
            return

        now = time.monotonic()
        last = self._last_fired.get(path, 0)
        if now - last < self._debounce:
            return

        self._last_fired[path] = now
        self._callback(path)


class FileWatcherTrigger:
    """Monitors directories for file changes and fires callbacks."""

    def __init__(self) -> None:
        self._observer = Observer()
        self._callbacks: list[TriggerCallback] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def on_trigger(self, callback: TriggerCallback) -> None:
        self._callbacks.append(callback)

    def watch(
        self,
        path: str,
        pattern: str = "*",
        recursive: bool = False,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Add a directory to watch."""
        ctx = context or {}

        def _sync_callback(file_path: str) -> None:
            if self._loop is None:
                return
            asyncio.run_coroutine_threadsafe(
                self._fire(file_path, ctx), self._loop
            )

        handler = _DebouncedHandler(pattern=pattern, callback=_sync_callback)
        self._observer.schedule(handler, path, recursive=recursive)
        logger.info(
            "watcher.added",
            path=path,
            pattern=pattern,
            recursive=recursive,
        )

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._observer.start()
        logger.info("watcher.started")

    async def stop(self) -> None:
        self._observer.stop()
        self._observer.join(timeout=5)
        logger.info("watcher.stopped")

    async def _fire(self, file_path: str, context: dict[str, Any]) -> None:
        logger.info("watcher.fired", file_path=file_path)
        trigger_context = {
            "trigger_type": "file_watcher",
            "file_path": file_path,
            **context,
        }
        for callback in self._callbacks:
            try:
                await callback(trigger_context)
            except Exception:
                logger.exception("watcher.callback_error", file_path=file_path)
