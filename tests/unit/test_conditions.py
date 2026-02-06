"""Tests for condition evaluation."""

from __future__ import annotations

from agent.pipeline.conditions import evaluate_condition


class TestEvaluateCondition:
    def test_empty_condition(self) -> None:
        assert evaluate_condition("", {}) is True
        assert evaluate_condition(None, {}) is True  # type: ignore[arg-type]

    def test_boolean_literals(self) -> None:
        assert evaluate_condition("true", {}) is True
        assert evaluate_condition("false", {}) is False
        assert evaluate_condition("True", {}) is True
        assert evaluate_condition("FALSE", {}) is False

    def test_comparison(self) -> None:
        ctx = {"resolution": 720, "duration": 60}
        assert evaluate_condition("resolution == 720", ctx) is True
        assert evaluate_condition("resolution != 720", ctx) is False
        assert evaluate_condition("resolution != 1080", ctx) is True
        assert evaluate_condition("duration > 30", ctx) is True
        assert evaluate_condition("duration < 30", ctx) is False

    def test_has_function(self) -> None:
        ctx = {"timecodes": [1.0, 2.0], "empty_val": None}
        assert evaluate_condition("has(timecodes)", ctx) is True
        assert evaluate_condition("has(missing)", ctx) is False
        assert evaluate_condition("has(empty_val)", ctx) is False

    def test_not_operator(self) -> None:
        ctx = {"error": "something"}
        assert evaluate_condition("not has(error)", ctx) is False
        assert evaluate_condition("not has(missing)", ctx) is True

    def test_and_combinator(self) -> None:
        ctx = {"a": 10, "b": 20}
        assert evaluate_condition("a > 5 and b > 15", ctx) is True
        assert evaluate_condition("a > 5 and b > 25", ctx) is False

    def test_or_combinator(self) -> None:
        ctx = {"a": 10, "b": 5}
        assert evaluate_condition("a > 15 or b > 3", ctx) is True
        assert evaluate_condition("a > 15 or b > 10", ctx) is False

    def test_name_truthiness(self) -> None:
        assert evaluate_condition("flag", {"flag": True}) is True
        assert evaluate_condition("flag", {"flag": False}) is False
        assert evaluate_condition("flag", {"flag": 0}) is False
        assert evaluate_condition("flag", {}) is False

    def test_malformed_expression_defaults_to_true(self) -> None:
        assert evaluate_condition("this is not valid python %%", {}) is True
