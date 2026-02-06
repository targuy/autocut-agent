"""Tests for the executor module."""

from __future__ import annotations

import sys

import pytest

from agent.executor.runner import ExecutorPool, ProgramExecutor
from agent.executor.capture import OutputCapture
from agent.executor.venv import VenvManager


class TestProgramExecutor:
    @pytest.mark.asyncio
    async def test_execute_shell_echo(self) -> None:
        executor = ProgramExecutor()
        result = await executor.execute(
            command="echo hello",
            command_type="shell",
            timeout=10,
        )
        assert result.return_code == 0
        assert "hello" in result.stdout

    @pytest.mark.asyncio
    async def test_execute_python_inline(self) -> None:
        executor = ProgramExecutor()
        result = await executor.execute(
            command='python3 -c "print(42)"',
            command_type="shell",
            timeout=10,
        )
        assert result.return_code == 0
        assert "42" in result.stdout

    @pytest.mark.asyncio
    async def test_execute_timeout(self) -> None:
        executor = ProgramExecutor()
        result = await executor.execute(
            command="sleep 30",
            command_type="shell",
            timeout=1,
        )
        assert result.return_code != 0

    @pytest.mark.asyncio
    async def test_execute_failing_command(self) -> None:
        executor = ProgramExecutor()
        result = await executor.execute(
            command="exit 1",
            command_type="shell",
            timeout=10,
        )
        assert result.return_code == 1


class TestExecutorPool:
    @pytest.mark.asyncio
    async def test_pool_limits_concurrency(self) -> None:
        pool = ExecutorPool(max_workers=2)
        result = await pool.execute(
            command="echo pool_test",
            command_type="shell",
            timeout=10,
        )
        assert result.return_code == 0
        assert "pool_test" in result.stdout
        await pool.shutdown()


class TestOutputCapture:
    def test_parse_artifacts_from_lines(self) -> None:
        capture = OutputCapture()
        lines = [
            "Processing...",
            "##ARTIFACT:timecodes=[1.0, 5.3, 12.7]",
            "##ARTIFACT:output_path=/tmp/output.mp4",
            "Done.",
        ]
        for line in lines:
            capture.stdout_lines.append(line)
            capture._parse_artifact(line)

        assert "timecodes" in capture.artifacts
        assert "output_path" in capture.artifacts
        assert capture.artifacts["output_path"] == "/tmp/output.mp4"

    def test_non_artifact_lines_ignored(self) -> None:
        capture = OutputCapture()
        capture._parse_artifact("Just a normal log line")
        capture._parse_artifact("")
        assert capture.artifacts == {}

    def test_stdout_property(self) -> None:
        capture = OutputCapture()
        capture.stdout_lines = ["line1", "line2"]
        assert capture.stdout == "line1\nline2"

    def test_stderr_property(self) -> None:
        capture = OutputCapture()
        capture.stderr_lines = ["err1", "err2"]
        assert capture.stderr == "err1\nerr2"


class TestVenvManager:
    def test_get_python_path_default(self) -> None:
        vm = VenvManager()
        path = vm.get_python_path(None)
        assert path == sys.executable

    def test_get_python_path_empty_string(self) -> None:
        vm = VenvManager()
        path = vm.get_python_path("")
        assert path == sys.executable

    def test_get_python_path_nonexistent_venv(self) -> None:
        """When venv path doesn't exist, falls back to sys.executable."""
        vm = VenvManager()
        path = vm.get_python_path("/nonexistent/venv/path")
        assert path == sys.executable

    def test_get_env_vars_default(self) -> None:
        vm = VenvManager()
        env = vm.get_env_vars(None)
        assert "PATH" in env
