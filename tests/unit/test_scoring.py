"""Tests for the execution scoring and advisory engine."""

from __future__ import annotations

from agent.pipeline.scoring import (
    Advisory,
    AdvisoryEngine,
    AdvisorySeverity,
    ExecutionOutcome,
    ExecutionScore,
    ParamSetStats,
    ProgramStats,
    _hash_params,
)


class TestExecutionScore:
    def test_create_score(self) -> None:
        score = ExecutionScore(
            program_name="facedetection",
            outcome=ExecutionOutcome.SUCCESS,
            parameters_used={"threshold": 0.5},
            duration_seconds=12.3,
            output_size=42,
        )
        assert score.program_name == "facedetection"
        assert score.outcome == ExecutionOutcome.SUCCESS
        assert score.parameters_hash != ""

    def test_auto_hash(self) -> None:
        s1 = ExecutionScore(
            program_name="test",
            outcome=ExecutionOutcome.SUCCESS,
            parameters_used={"a": 1, "b": 2},
        )
        s2 = ExecutionScore(
            program_name="test",
            outcome=ExecutionOutcome.FAILURE,
            parameters_used={"b": 2, "a": 1},  # same params, different order
        )
        assert s1.parameters_hash == s2.parameters_hash

    def test_different_params_different_hash(self) -> None:
        s1 = ExecutionScore(
            program_name="test",
            outcome=ExecutionOutcome.SUCCESS,
            parameters_used={"threshold": 0.5},
        )
        s2 = ExecutionScore(
            program_name="test",
            outcome=ExecutionOutcome.SUCCESS,
            parameters_used={"threshold": 0.8},
        )
        assert s1.parameters_hash != s2.parameters_hash

    def test_empty_params_no_hash(self) -> None:
        s = ExecutionScore(
            program_name="test",
            outcome=ExecutionOutcome.SUCCESS,
            parameters_used={},
        )
        assert s.parameters_hash == ""


class TestProgramStats:
    def test_success_rate(self) -> None:
        stats = ProgramStats(
            program_name="test", total_runs=10, successes=8, failures=2
        )
        assert stats.success_rate == 0.8

    def test_success_rate_zero_runs(self) -> None:
        stats = ProgramStats(program_name="test")
        assert stats.success_rate == 0.0

    def test_zero_output_rate(self) -> None:
        stats = ProgramStats(
            program_name="test", total_runs=10, zero_outputs=5
        )
        assert stats.zero_output_rate == 0.5

    def test_failure_rate(self) -> None:
        stats = ProgramStats(
            program_name="test", total_runs=10, failures=3
        )
        assert stats.failure_rate == 0.3


class TestHashParams:
    def test_deterministic(self) -> None:
        h1 = _hash_params({"a": 1, "b": "two"})
        h2 = _hash_params({"b": "two", "a": 1})
        assert h1 == h2

    def test_different(self) -> None:
        h1 = _hash_params({"a": 1})
        h2 = _hash_params({"a": 2})
        assert h1 != h2


class TestAdvisoryEngine:
    def _engine(self) -> AdvisoryEngine:
        return AdvisoryEngine()

    def test_no_advisory_insufficient_runs(self) -> None:
        engine = self._engine()
        stats = ProgramStats(program_name="test", total_runs=2, successes=2)
        advisories = engine.generate_advisories("test", stats, [])
        assert len(advisories) == 0

    def test_zero_output_critical(self) -> None:
        engine = self._engine()
        stats = ProgramStats(
            program_name="filter_strict",
            total_runs=10,
            successes=1,
            zero_outputs=9,
        )
        advisories = engine.generate_advisories("filter_strict", stats, [])
        critical = [a for a in advisories if a.severity == AdvisorySeverity.CRITICAL]
        assert len(critical) >= 1
        assert "zero output" in critical[0].title.lower()

    def test_zero_output_warning(self) -> None:
        engine = self._engine()
        stats = ProgramStats(
            program_name="filter_moderate",
            total_runs=10,
            successes=4,
            zero_outputs=6,
        )
        advisories = engine.generate_advisories("filter_moderate", stats, [])
        warnings = [a for a in advisories if a.severity == AdvisorySeverity.WARNING]
        assert len(warnings) >= 1

    def test_high_failure_rate_critical(self) -> None:
        engine = self._engine()
        stats = ProgramStats(
            program_name="broken_prog",
            total_runs=10,
            successes=3,
            failures=7,
        )
        advisories = engine.generate_advisories("broken_prog", stats, [])
        critical = [a for a in advisories if a.severity == AdvisorySeverity.CRITICAL]
        assert len(critical) >= 1
        assert "failure" in critical[0].title.lower()

    def test_high_failure_rate_warning(self) -> None:
        engine = self._engine()
        stats = ProgramStats(
            program_name="flaky_prog",
            total_runs=10,
            successes=6,
            failures=4,
        )
        advisories = engine.generate_advisories("flaky_prog", stats, [])
        warnings = [a for a in advisories if a.severity == AdvisorySeverity.WARNING]
        assert len(warnings) >= 1

    def test_timeout_warning(self) -> None:
        engine = self._engine()
        stats = ProgramStats(
            program_name="slow_prog",
            total_runs=10,
            successes=7,
            timeouts=3,
        )
        advisories = engine.generate_advisories("slow_prog", stats, [])
        timeout_advisories = [a for a in advisories if "timeout" in a.title.lower()]
        assert len(timeout_advisories) >= 1

    def test_suggest_better_params(self) -> None:
        engine = self._engine()
        stats = ProgramStats(
            program_name="parameterized",
            total_runs=20,
            successes=15,
            failures=5,
        )
        param_sets = [
            ParamSetStats(
                parameters_hash="good",
                parameters={"threshold": 0.3},
                total_runs=10,
                successes=9,
                failures=1,
                success_rate=0.9,
            ),
            ParamSetStats(
                parameters_hash="bad",
                parameters={"threshold": 0.9},
                total_runs=10,
                successes=4,
                failures=6,
                success_rate=0.4,
            ),
        ]
        advisories = engine.generate_advisories("parameterized", stats, param_sets)
        info_advisories = [a for a in advisories if a.severity == AdvisorySeverity.INFO]
        assert len(info_advisories) >= 1
        assert "better" in info_advisories[0].title.lower()
        assert info_advisories[0].suggested_changes == {"threshold": 0.3}

    def test_no_suggestion_similar_params(self) -> None:
        """No suggestion when parameter sets perform similarly."""
        engine = self._engine()
        stats = ProgramStats(
            program_name="consistent",
            total_runs=20,
            successes=18,
            failures=2,
        )
        param_sets = [
            ParamSetStats(
                parameters_hash="a",
                parameters={"x": 1},
                total_runs=10,
                successes=9,
                failures=1,
                success_rate=0.9,
            ),
            ParamSetStats(
                parameters_hash="b",
                parameters={"x": 2},
                total_runs=10,
                successes=9,
                failures=1,
                success_rate=0.9,
            ),
        ]
        advisories = engine.generate_advisories("consistent", stats, param_sets)
        info_advisories = [a for a in advisories if a.severity == AdvisorySeverity.INFO]
        assert len(info_advisories) == 0

    def test_all_success_no_advisories(self) -> None:
        engine = self._engine()
        stats = ProgramStats(
            program_name="perfect",
            total_runs=10,
            successes=10,
        )
        advisories = engine.generate_advisories("perfect", stats, [])
        assert len(advisories) == 0

    def test_confidence_scales_with_runs(self) -> None:
        engine = self._engine()
        stats = ProgramStats(
            program_name="test",
            total_runs=5,
            zero_outputs=5,
        )
        advisories = engine.generate_advisories("test", stats, [])
        assert advisories[0].confidence == 0.5  # 5/10

        stats.total_runs = 20
        stats.zero_outputs = 20
        advisories = engine.generate_advisories("test", stats, [])
        assert advisories[0].confidence == 1.0  # capped at 1.0
