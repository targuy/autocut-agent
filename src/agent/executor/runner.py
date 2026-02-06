"""ProgramExecutor and ExecutorPool — runs Python, ffmpeg, and shell commands."""

from __future__ import annotations

import asyncio
import shlex
import time
from dataclasses import dataclass, field

import structlog

from agent.executor.capture import OutputCapture, stream_output

logger = structlog.get_logger()


@dataclass
class ExecutionResult:
    """Result of a single subprocess execution."""

    stdout: str = ""
    stderr: str = ""
    return_code: int = -1
    output_files: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    artifacts: dict[str, str] = field(default_factory=dict)


class ProgramExecutor:
    """Execute Python scripts, ffmpeg commands, or arbitrary shell commands.

    Each invocation runs the command in its own subprocess with real-time
    output capture, timeout enforcement, and structured artifact extraction.
    """

    async def execute(
        self,
        command: str,
        command_type: str,
        *,
        timeout: int = 3600,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> ExecutionResult:
        """Run *command* and return an :class:`ExecutionResult`.

        Parameters
        ----------
        command:
            The command string.  Interpretation depends on *command_type*.
        command_type:
            One of ``"python"``, ``"ffmpeg"``, or ``"shell"``.
        timeout:
            Maximum wall-clock seconds before the process is killed.
        env:
            Optional environment variable mapping passed to the subprocess.
        cwd:
            Optional working directory for the subprocess.

        Returns
        -------
        ExecutionResult
            Captured output, return code, duration, and any artifacts.
        """
        log = logger.bind(command_type=command_type, command=command)
        log.info("executor.starting")

        args, use_shell = self._build_args(command, command_type)

        start = time.monotonic()
        capture = OutputCapture()
        result = ExecutionResult()

        try:
            if use_shell:
                process = await asyncio.create_subprocess_shell(
                    args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                    cwd=cwd,
                )
            else:
                process = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                    cwd=cwd,
                )

            try:
                artifacts = await asyncio.wait_for(
                    self._run_with_capture(process, capture),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                log.warning("executor.timeout", timeout=timeout, pid=process.pid)
                process.kill()
                await process.wait()
                result.return_code = -9
                result.stderr = capture.stderr + "\n[TIMEOUT] Process killed after "
                result.stderr += f"{timeout}s"
                result.stdout = capture.stdout
                result.duration_seconds = time.monotonic() - start
                return result

            result.return_code = process.returncode if process.returncode is not None else -1
            result.stdout = capture.stdout
            result.stderr = capture.stderr
            result.artifacts = artifacts
            result.output_files = list(artifacts.values())

        except OSError as exc:
            log.error("executor.os_error", error=str(exc))
            result.return_code = -1
            result.stderr = f"OSError: {exc}"
        except Exception as exc:  # noqa: BLE001
            log.error("executor.unexpected_error", error=str(exc))
            result.return_code = -1
            result.stderr = f"Unexpected error: {exc}"
        finally:
            result.duration_seconds = time.monotonic() - start

        log.info(
            "executor.finished",
            return_code=result.return_code,
            duration_seconds=round(result.duration_seconds, 3),
        )
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_args(
        command: str,
        command_type: str,
    ) -> tuple[str | list[str], bool]:
        """Translate *command* and *command_type* into subprocess arguments.

        Returns a tuple of ``(args, use_shell)``.
        """
        if command_type == "python":
            # If the command looks like a file path, run it; otherwise
            # treat it as inline code via ``python -c``.
            stripped = command.strip()
            if stripped.endswith(".py") or " " not in stripped:
                return ["python", stripped], False
            return ["python", "-c", stripped], False

        if command_type == "ffmpeg":
            # Split into tokens so create_subprocess_exec gets a proper
            # argv.  Prepend ``ffmpeg`` if the user didn't include it.
            tokens = shlex.split(command)
            if tokens and tokens[0] != "ffmpeg":
                tokens = ["ffmpeg"] + tokens
            return tokens, False

        if command_type == "shell":
            return command, True

        raise ValueError(f"Unsupported command_type: {command_type!r}")

    @staticmethod
    async def _run_with_capture(
        process: asyncio.subprocess.Process,
        capture: OutputCapture,
    ) -> dict[str, str]:
        """Stream output and wait for process exit."""
        artifacts = await stream_output(process, capture)
        await process.wait()
        return artifacts


class ExecutorPool:
    """Concurrency-limited pool that delegates work to :class:`ProgramExecutor`.

    Uses an :class:`asyncio.Semaphore` so that at most *max_workers*
    executions run simultaneously.
    """

    def __init__(self, max_workers: int = 4) -> None:
        self._max_workers = max_workers
        self._semaphore = asyncio.Semaphore(max_workers)
        self._executor = ProgramExecutor()
        self._active: int = 0
        self._shutting_down: bool = False
        logger.info("executor_pool.init", max_workers=max_workers)

    @property
    def max_workers(self) -> int:
        return self._max_workers

    @property
    def active_count(self) -> int:
        return self._active

    async def execute(
        self,
        command: str,
        command_type: str,
        *,
        timeout: int = 3600,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> ExecutionResult:
        """Execute a command, blocking until a worker slot is available.

        Raises :class:`RuntimeError` if the pool has been shut down.
        """
        if self._shutting_down:
            raise RuntimeError("ExecutorPool is shutting down")

        async with self._semaphore:
            self._active += 1
            try:
                return await self._executor.execute(
                    command,
                    command_type,
                    timeout=timeout,
                    env=env,
                    cwd=cwd,
                )
            finally:
                self._active -= 1

    async def shutdown(self) -> None:
        """Signal the pool to reject new work and wait for running tasks.

        Existing in-progress executions are allowed to finish; new calls
        to :meth:`execute` will raise :class:`RuntimeError`.
        """
        self._shutting_down = True
        logger.info("executor_pool.shutting_down", active=self._active)

        # Wait until all active tasks complete.
        while self._active > 0:
            await asyncio.sleep(0.1)

        logger.info("executor_pool.stopped")
