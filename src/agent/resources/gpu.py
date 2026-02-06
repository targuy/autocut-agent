"""GPU detection and VRAM monitoring via nvidia-smi."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()


@dataclass(frozen=True)
class GPUInfo:
    """Information about a single GPU device."""

    id: str
    name: str
    memory_total_mb: int
    memory_used_mb: int

    @property
    def memory_free_mb(self) -> int:
        return self.memory_total_mb - self.memory_used_mb

    @property
    def memory_utilization(self) -> float:
        """Return memory utilization as a fraction (0.0 to 1.0)."""
        if self.memory_total_mb == 0:
            return 0.0
        return self.memory_used_mb / self.memory_total_mb


async def _run_nvidia_smi(*args: str) -> str | None:
    """Run nvidia-smi with the given arguments, returning stdout or None on failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            logger.debug(
                "gpu.nvidia_smi_failed",
                returncode=proc.returncode,
                stderr=stderr.decode(errors="replace").strip(),
            )
            return None

        return stdout.decode(errors="replace").strip()
    except FileNotFoundError:
        logger.debug("gpu.nvidia_smi_not_found")
        return None
    except OSError as exc:
        logger.debug("gpu.nvidia_smi_error", error=str(exc))
        return None


async def detect_gpus() -> list[GPUInfo]:
    """Detect available NVIDIA GPUs using nvidia-smi.

    Returns an empty list when no NVIDIA driver is installed or nvidia-smi
    is not available.
    """
    output = await _run_nvidia_smi(
        "--query-gpu=index,name,memory.total,memory.used",
        "--format=csv,noheader,nounits",
    )
    if output is None:
        return []

    gpus: list[GPUInfo] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            logger.warning("gpu.unexpected_csv_line", line=line)
            continue
        try:
            gpu = GPUInfo(
                id=f"cuda:{parts[0]}",
                name=parts[1],
                memory_total_mb=int(float(parts[2])),
                memory_used_mb=int(float(parts[3])),
            )
            gpus.append(gpu)
        except (ValueError, IndexError) as exc:
            logger.warning("gpu.parse_error", line=line, error=str(exc))
            continue

    logger.info("gpu.detected", count=len(gpus), devices=[g.id for g in gpus])
    return gpus


async def get_gpu_memory(device_id: str) -> tuple[int, int]:
    """Return ``(used_mb, total_mb)`` for the given GPU device.

    *device_id* should be in the form ``cuda:N`` where N is the GPU index.
    Returns ``(0, 0)`` if the device cannot be queried.
    """
    # Extract numeric index from device_id (e.g. "cuda:0" -> "0")
    index = device_id.split(":")[-1] if ":" in device_id else device_id

    output = await _run_nvidia_smi(
        f"--id={index}",
        "--query-gpu=memory.used,memory.total",
        "--format=csv,noheader,nounits",
    )
    if output is None:
        return (0, 0)

    line = output.splitlines()[0].strip() if output.splitlines() else ""
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 2:
        logger.warning("gpu.memory_parse_error", device=device_id, line=line)
        return (0, 0)

    try:
        used = int(float(parts[0]))
        total = int(float(parts[1]))
        return (used, total)
    except (ValueError, IndexError) as exc:
        logger.warning(
            "gpu.memory_parse_error", device=device_id, line=line, error=str(exc)
        )
        return (0, 0)
