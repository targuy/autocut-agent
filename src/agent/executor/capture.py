"""Output capture and structured artifact parsing for subprocess execution."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger()

# Pattern for structured artifact lines: ##ARTIFACT:key=value
_ARTIFACT_PATTERN = re.compile(r"^##ARTIFACT:(\w+)=(.*)$")


@dataclass
class OutputCapture:
    """Collects stdout/stderr lines and extracts structured artifacts.

    Lines matching the ``##ARTIFACT:key=value`` format are parsed into
    the :attr:`artifacts` dictionary.  All raw lines (including artifact
    lines) are retained in :attr:`stdout_lines` / :attr:`stderr_lines`.
    """

    stdout_lines: list[str] = field(default_factory=list)
    stderr_lines: list[str] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)

    @property
    def stdout(self) -> str:
        """Return all captured stdout as a single string."""
        return "\n".join(self.stdout_lines)

    @property
    def stderr(self) -> str:
        """Return all captured stderr as a single string."""
        return "\n".join(self.stderr_lines)

    def _parse_artifact(self, line: str) -> None:
        """If *line* matches the artifact pattern, store the key/value pair."""
        match = _ARTIFACT_PATTERN.match(line)
        if match:
            key, value = match.group(1), match.group(2)
            self.artifacts[key] = value
            logger.debug("capture.artifact_found", key=key, value=value)


async def _read_stream(
    stream: asyncio.StreamReader | None,
    lines: list[str],
    capture: OutputCapture,
    *,
    is_stderr: bool = False,
) -> None:
    """Read lines from *stream* until EOF, appending to *lines*.

    Artifact parsing is applied to every line regardless of which stream
    it originates from.
    """
    if stream is None:
        return

    while True:
        raw = await stream.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").rstrip("\n\r")
        lines.append(line)
        capture._parse_artifact(line)

        label = "stderr" if is_stderr else "stdout"
        logger.debug("capture.line", stream=label, line=line)


async def stream_output(
    process: asyncio.subprocess.Process,
    capture: OutputCapture,
) -> dict[str, str]:
    """Read stdout and stderr from *process* concurrently.

    Lines are appended to *capture* in real-time.  Any lines matching the
    ``##ARTIFACT:key=value`` format are extracted and stored in
    ``capture.artifacts``.

    Returns
    -------
    dict[str, str]
        The parsed artifacts dictionary (same object as
        ``capture.artifacts``).
    """
    await asyncio.gather(
        _read_stream(
            process.stdout,
            capture.stdout_lines,
            capture,
            is_stderr=False,
        ),
        _read_stream(
            process.stderr,
            capture.stderr_lines,
            capture,
            is_stderr=True,
        ),
    )

    logger.info(
        "capture.complete",
        stdout_line_count=len(capture.stdout_lines),
        stderr_line_count=len(capture.stderr_lines),
        artifact_count=len(capture.artifacts),
    )
    return capture.artifacts
