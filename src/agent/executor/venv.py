"""Virtual environment management for isolated task execution."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import structlog

logger = structlog.get_logger()


class VenvManager:
    """Manages virtual environment discovery and environment variable setup.

    Provides helpers to resolve the correct Python executable and build
    environment variable mappings so that subprocesses execute inside
    a given virtualenv.
    """

    @staticmethod
    def get_python_path(venv_path: str | None) -> str:
        """Return the Python executable for *venv_path*.

        If *venv_path* is ``None`` or empty the current interpreter
        (``sys.executable``) is returned.  Otherwise the platform-appropriate
        ``python`` binary inside the virtualenv is resolved.
        """
        if not venv_path:
            return sys.executable

        venv = Path(venv_path)
        if not venv.is_dir():
            logger.warning(
                "venv.not_found",
                venv_path=venv_path,
                fallback=sys.executable,
            )
            return sys.executable

        # Windows: Scripts/python.exe  |  POSIX: bin/python
        candidates = [
            venv / "bin" / "python",
            venv / "Scripts" / "python.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                resolved = str(candidate.resolve())
                logger.debug("venv.python_resolved", python=resolved)
                return resolved

        logger.warning(
            "venv.python_not_found",
            venv_path=venv_path,
            fallback=sys.executable,
        )
        return sys.executable

    @staticmethod
    def get_env_vars(venv_path: str | None) -> dict[str, str]:
        """Return environment variables configured for *venv_path*.

        The returned dict is a **copy** of the current ``os.environ`` with
        ``VIRTUAL_ENV`` set and the virtualenv ``bin`` (or ``Scripts``)
        directory prepended to ``PATH``.  When *venv_path* is ``None`` or
        empty, a plain copy of the current environment is returned.
        """
        env = dict(os.environ)

        if not venv_path:
            return env

        venv = Path(venv_path).resolve()

        # Determine the bin directory (POSIX vs Windows).
        bin_dir = venv / "bin"
        if not bin_dir.is_dir():
            bin_dir = venv / "Scripts"

        env["VIRTUAL_ENV"] = str(venv)
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")

        # Remove PYTHONHOME if set — it conflicts with virtualenvs.
        env.pop("PYTHONHOME", None)

        logger.debug(
            "venv.env_vars_prepared",
            virtual_env=str(venv),
            bin_dir=str(bin_dir),
        )
        return env
