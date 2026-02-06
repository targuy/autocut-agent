"""Execution scoring and adaptive advisory for program parameters.

Tracks per-program, per-parameter-set execution outcomes and provides
recommendations for parameter adjustments based on historical success
rates and failure patterns.

Key concepts:
- **ExecutionScore**: a single recorded outcome (success/failure/zero-output)
- **ProgramStats**: aggregated statistics for a program across all runs
- **Advisory**: a recommendation for parameter change with severity and reasoning
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.core.database import ProgramScoreRow

logger = structlog.get_logger()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> uuid.UUID:
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# Execution outcome
# ---------------------------------------------------------------------------


class ExecutionOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    ZERO_OUTPUT = "zero_output"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


class ExecutionScore(BaseModel):
    """A single recorded execution outcome for a program."""

    id: uuid.UUID = Field(default_factory=_new_id)
    program_name: str
    pipeline_id: uuid.UUID | None = None
    step_id: uuid.UUID | None = None

    outcome: ExecutionOutcome
    parameters_used: dict[str, Any] = Field(default_factory=dict)
    parameters_hash: str = ""  # deterministic hash of params for grouping

    duration_seconds: float = 0.0
    output_size: int = 0  # byte count or item count of output
    error_message: str = ""

    recorded_at: datetime = Field(default_factory=_utcnow)

    def model_post_init(self, __context: Any) -> None:
        if not self.parameters_hash and self.parameters_used:
            self.parameters_hash = _hash_params(self.parameters_used)


def _hash_params(params: dict[str, Any]) -> str:
    """Create a deterministic hash of parameter values for grouping."""
    canonical = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Aggregated statistics
# ---------------------------------------------------------------------------


class ProgramStats(BaseModel):
    """Aggregated execution statistics for a program."""

    program_name: str
    total_runs: int = 0
    successes: int = 0
    failures: int = 0
    zero_outputs: int = 0
    timeouts: int = 0

    avg_duration_seconds: float = 0.0
    avg_output_size: float = 0.0

    last_run_at: datetime | None = None
    last_outcome: ExecutionOutcome | None = None

    @property
    def success_rate(self) -> float:
        """Fraction of runs that succeeded (0.0 to 1.0)."""
        if self.total_runs == 0:
            return 0.0
        return self.successes / self.total_runs

    @property
    def zero_output_rate(self) -> float:
        """Fraction of runs that produced zero output."""
        if self.total_runs == 0:
            return 0.0
        return self.zero_outputs / self.total_runs

    @property
    def failure_rate(self) -> float:
        if self.total_runs == 0:
            return 0.0
        return self.failures / self.total_runs


class ParamSetStats(BaseModel):
    """Statistics for a specific parameter combination."""

    parameters_hash: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    total_runs: int = 0
    successes: int = 0
    zero_outputs: int = 0
    failures: int = 0
    avg_duration_seconds: float = 0.0
    success_rate: float = 0.0


# ---------------------------------------------------------------------------
# Advisory system
# ---------------------------------------------------------------------------


class AdvisorySeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Advisory(BaseModel):
    """A recommendation for a program parameter or usage change."""

    program_name: str
    severity: AdvisorySeverity
    title: str
    message: str
    suggested_changes: dict[str, Any] = Field(default_factory=dict)
    based_on_runs: int = 0
    confidence: float = 0.0  # 0.0 to 1.0


class AdvisoryEngine:
    """Analyzes execution scores and generates parameter recommendations.

    Detects patterns like:
    - Zero-output runs (filter criteria too aggressive)
    - High failure rates (misconfiguration, version mismatch)
    - Consistently slow execution (timeout risk)
    - Better-performing parameter sets to suggest switching to
    """

    # Thresholds (configurable)
    MIN_RUNS_FOR_ADVISORY = 3
    ZERO_OUTPUT_WARNING_RATE = 0.5
    ZERO_OUTPUT_CRITICAL_RATE = 0.8
    FAILURE_WARNING_RATE = 0.3
    FAILURE_CRITICAL_RATE = 0.6
    TIMEOUT_WARNING_RATE = 0.2
    SLOW_DURATION_PERCENTILE = 0.9  # top 10% slowest

    def generate_advisories(
        self,
        program_name: str,
        stats: ProgramStats,
        param_set_stats: list[ParamSetStats],
    ) -> list[Advisory]:
        """Generate all applicable advisories for a program."""
        advisories: list[Advisory] = []

        if stats.total_runs < self.MIN_RUNS_FOR_ADVISORY:
            return advisories

        advisories.extend(self._check_zero_output(program_name, stats, param_set_stats))
        advisories.extend(self._check_failure_rate(program_name, stats, param_set_stats))
        advisories.extend(self._check_timeout_rate(program_name, stats))
        advisories.extend(self._suggest_better_params(program_name, param_set_stats))

        return advisories

    def _check_zero_output(
        self,
        program_name: str,
        stats: ProgramStats,
        param_set_stats: list[ParamSetStats],
    ) -> list[Advisory]:
        """Detect programs producing zero output too often."""
        advisories: list[Advisory] = []

        if stats.zero_output_rate >= self.ZERO_OUTPUT_CRITICAL_RATE:
            # Find which parameters are causing it
            worst_params = self._find_worst_param_sets(param_set_stats, "zero_output")
            suggested = {}
            if worst_params:
                suggested = {
                    "problematic_parameter_sets": [
                        p.parameters for p in worst_params[:3]
                    ]
                }

            advisories.append(Advisory(
                program_name=program_name,
                severity=AdvisorySeverity.CRITICAL,
                title="Consistently producing zero output",
                message=(
                    f"'{program_name}' produced zero output in "
                    f"{stats.zero_outputs}/{stats.total_runs} runs "
                    f"({stats.zero_output_rate:.0%}). "
                    "Its filter criteria or parameters are likely too restrictive. "
                    "Consider relaxing thresholds or adjusting input requirements."
                ),
                suggested_changes=suggested,
                based_on_runs=stats.total_runs,
                confidence=min(stats.total_runs / 10, 1.0),
            ))
        elif stats.zero_output_rate >= self.ZERO_OUTPUT_WARNING_RATE:
            advisories.append(Advisory(
                program_name=program_name,
                severity=AdvisorySeverity.WARNING,
                title="Frequent zero-output runs",
                message=(
                    f"'{program_name}' produced zero output in "
                    f"{stats.zero_outputs}/{stats.total_runs} runs "
                    f"({stats.zero_output_rate:.0%}). "
                    "Review filter parameters — they may be too strict for some inputs."
                ),
                based_on_runs=stats.total_runs,
                confidence=min(stats.total_runs / 10, 1.0),
            ))

        return advisories

    def _check_failure_rate(
        self,
        program_name: str,
        stats: ProgramStats,
        param_set_stats: list[ParamSetStats],
    ) -> list[Advisory]:
        """Detect programs with high failure rates."""
        advisories: list[Advisory] = []

        if stats.failure_rate >= self.FAILURE_CRITICAL_RATE:
            advisories.append(Advisory(
                program_name=program_name,
                severity=AdvisorySeverity.CRITICAL,
                title="Very high failure rate",
                message=(
                    f"'{program_name}' failed in {stats.failures}/{stats.total_runs} runs "
                    f"({stats.failure_rate:.0%}). "
                    "Check that the program exists, dependencies are installed, "
                    "and command template is correct."
                ),
                based_on_runs=stats.total_runs,
                confidence=min(stats.total_runs / 10, 1.0),
            ))
        elif stats.failure_rate >= self.FAILURE_WARNING_RATE:
            advisories.append(Advisory(
                program_name=program_name,
                severity=AdvisorySeverity.WARNING,
                title="Elevated failure rate",
                message=(
                    f"'{program_name}' failed in {stats.failures}/{stats.total_runs} runs "
                    f"({stats.failure_rate:.0%}). "
                    "Some parameter combinations may be causing errors."
                ),
                based_on_runs=stats.total_runs,
                confidence=min(stats.total_runs / 10, 1.0),
            ))

        return advisories

    def _check_timeout_rate(
        self,
        program_name: str,
        stats: ProgramStats,
    ) -> list[Advisory]:
        """Detect programs that timeout frequently."""
        if stats.total_runs == 0:
            return []

        timeout_rate = stats.timeouts / stats.total_runs
        if timeout_rate >= self.TIMEOUT_WARNING_RATE:
            return [Advisory(
                program_name=program_name,
                severity=AdvisorySeverity.WARNING,
                title="Frequent timeouts",
                message=(
                    f"'{program_name}' timed out in {stats.timeouts}/{stats.total_runs} runs "
                    f"({timeout_rate:.0%}). Consider increasing the timeout or optimizing "
                    "the program."
                ),
                based_on_runs=stats.total_runs,
                confidence=min(stats.total_runs / 10, 1.0),
            )]
        return []

    def _suggest_better_params(
        self,
        program_name: str,
        param_set_stats: list[ParamSetStats],
    ) -> list[Advisory]:
        """If some parameter sets perform better, suggest switching."""
        if len(param_set_stats) < 2:
            return []

        # Sort by success rate descending
        sorted_sets = sorted(param_set_stats, key=lambda s: s.success_rate, reverse=True)
        best = sorted_sets[0]
        worst = sorted_sets[-1]

        # Only suggest if there's a meaningful difference and enough data
        if (
            best.total_runs >= self.MIN_RUNS_FOR_ADVISORY
            and worst.total_runs >= self.MIN_RUNS_FOR_ADVISORY
            and best.success_rate - worst.success_rate >= 0.3
        ):
            return [Advisory(
                program_name=program_name,
                severity=AdvisorySeverity.INFO,
                title="Better parameter set available",
                message=(
                    f"Parameter set with success rate {best.success_rate:.0%} "
                    f"({best.total_runs} runs) outperforms current worst at "
                    f"{worst.success_rate:.0%} ({worst.total_runs} runs). "
                    f"Consider switching to the better-performing parameters."
                ),
                suggested_changes=best.parameters,
                based_on_runs=best.total_runs + worst.total_runs,
                confidence=min((best.total_runs + worst.total_runs) / 20, 1.0),
            )]

        return []

    @staticmethod
    def _find_worst_param_sets(
        param_set_stats: list[ParamSetStats],
        metric: str,
    ) -> list[ParamSetStats]:
        """Find parameter sets with the worst performance on a given metric."""
        if metric == "zero_output":
            return sorted(
                [s for s in param_set_stats if s.zero_outputs > 0],
                key=lambda s: s.zero_outputs / max(s.total_runs, 1),
                reverse=True,
            )
        if metric == "failure":
            return sorted(
                [s for s in param_set_stats if s.failures > 0],
                key=lambda s: s.failures / max(s.total_runs, 1),
                reverse=True,
            )
        return []


# ---------------------------------------------------------------------------
# Scoring manager (persistence)
# ---------------------------------------------------------------------------


class ScoringManager:
    """Persists execution scores and computes statistics."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory
        self._advisory_engine = AdvisoryEngine()

    async def record_score(self, score: ExecutionScore) -> None:
        """Record an execution outcome."""
        async with self._session_factory() as session:
            row = ProgramScoreRow(
                id=str(score.id),
                program_name=score.program_name,
                pipeline_id=str(score.pipeline_id) if score.pipeline_id else None,
                step_id=str(score.step_id) if score.step_id else None,
                outcome=score.outcome.value,
                parameters_used=score.parameters_used,
                parameters_hash=score.parameters_hash,
                duration_seconds=score.duration_seconds,
                output_size=score.output_size,
                error_message=score.error_message,
                recorded_at=score.recorded_at,
            )
            session.add(row)
            await session.commit()

        logger.debug(
            "scoring.recorded",
            program=score.program_name,
            outcome=score.outcome.value,
        )

    async def get_program_stats(self, program_name: str) -> ProgramStats:
        """Compute aggregated statistics for a program."""
        async with self._session_factory() as session:
            rows = await session.execute(
                select(ProgramScoreRow)
                .where(ProgramScoreRow.program_name == program_name)
                .order_by(ProgramScoreRow.recorded_at.desc())
            )
            all_rows = rows.scalars().all()

        if not all_rows:
            return ProgramStats(program_name=program_name)

        stats = ProgramStats(program_name=program_name)
        total_duration = 0.0
        total_output = 0

        for row in all_rows:
            stats.total_runs += 1
            total_duration += row.duration_seconds or 0.0
            total_output += row.output_size or 0

            match row.outcome:
                case "success":
                    stats.successes += 1
                case "failure":
                    stats.failures += 1
                case "zero_output":
                    stats.zero_outputs += 1
                case "timeout":
                    stats.timeouts += 1

        stats.avg_duration_seconds = total_duration / stats.total_runs
        stats.avg_output_size = total_output / stats.total_runs
        stats.last_run_at = all_rows[0].recorded_at
        stats.last_outcome = ExecutionOutcome(all_rows[0].outcome)

        return stats

    async def get_param_set_stats(self, program_name: str) -> list[ParamSetStats]:
        """Compute per-parameter-set statistics."""
        async with self._session_factory() as session:
            rows = await session.execute(
                select(ProgramScoreRow)
                .where(ProgramScoreRow.program_name == program_name)
            )
            all_rows = rows.scalars().all()

        # Group by parameters_hash
        groups: dict[str, list[ProgramScoreRow]] = {}
        for row in all_rows:
            h = row.parameters_hash or "default"
            groups.setdefault(h, []).append(row)

        result: list[ParamSetStats] = []
        for param_hash, group_rows in groups.items():
            ps = ParamSetStats(
                parameters_hash=param_hash,
                parameters=group_rows[0].parameters_used or {},
            )
            total_dur = 0.0
            for row in group_rows:
                ps.total_runs += 1
                total_dur += row.duration_seconds or 0.0
                match row.outcome:
                    case "success":
                        ps.successes += 1
                    case "zero_output":
                        ps.zero_outputs += 1
                    case "failure" | "timeout":
                        ps.failures += 1

            ps.avg_duration_seconds = total_dur / ps.total_runs if ps.total_runs else 0
            ps.success_rate = ps.successes / ps.total_runs if ps.total_runs else 0
            result.append(ps)

        return result

    async def get_advisories(self, program_name: str) -> list[Advisory]:
        """Generate advisories for a program based on its execution history."""
        stats = await self.get_program_stats(program_name)
        param_stats = await self.get_param_set_stats(program_name)
        return self._advisory_engine.generate_advisories(
            program_name, stats, param_stats
        )

    async def get_all_advisories(self) -> dict[str, list[Advisory]]:
        """Generate advisories for all programs that have execution history."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(ProgramScoreRow.program_name).distinct()
            )
            names = [row[0] for row in result.all()]

        advisories: dict[str, list[Advisory]] = {}
        for name in names:
            program_advisories = await self.get_advisories(name)
            if program_advisories:
                advisories[name] = program_advisories

        return advisories

    async def clear_scores(self, program_name: str) -> int:
        """Delete all scores for a program. Returns count deleted."""
        async with self._session_factory() as session:
            result = await session.execute(
                delete(ProgramScoreRow).where(
                    ProgramScoreRow.program_name == program_name
                )
            )
            await session.commit()
            return result.rowcount
