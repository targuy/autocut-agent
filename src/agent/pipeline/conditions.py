"""Conditional branch evaluation for pipeline steps.

Conditions are simple expressions compiled at pipeline creation time.
They are evaluated against the pipeline context and step artifacts
without calling the LLM at runtime.

Supported expressions:
  - Comparison: "resolution != 720"  "duration > 60"
  - Existence:  "has(timecodes)"  "not has(error)"
  - Boolean:    "true"  "false"
  - Combined:   "has(timecodes) and duration > 0"
"""

from __future__ import annotations

import ast
import operator
from typing import Any


_OPERATORS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


def evaluate_condition(expression: str, context: dict[str, Any]) -> bool:
    """Evaluate a condition expression against a context dict.

    Returns True if the condition is met (step should execute),
    False if the step should be skipped.
    Returns True for empty/None expressions (unconditional step).
    """
    if not expression or not expression.strip():
        return True

    expression = expression.strip()

    if expression.lower() == "true":
        return True
    if expression.lower() == "false":
        return False

    try:
        return _eval_expr(expression, context)
    except Exception:
        # If we can't evaluate, default to running the step
        return True


def _eval_expr(expression: str, context: dict[str, Any]) -> bool:
    """Safely evaluate an expression using AST parsing."""
    # Handle 'and' / 'or' combinators
    if " and " in expression:
        parts = expression.split(" and ")
        return all(_eval_expr(p.strip(), context) for p in parts)
    if " or " in expression:
        parts = expression.split(" or ")
        return any(_eval_expr(p.strip(), context) for p in parts)

    # Handle 'not' prefix
    if expression.startswith("not "):
        return not _eval_expr(expression[4:].strip(), context)

    # Handle has() function
    if expression.startswith("has(") and expression.endswith(")"):
        key = expression[4:-1].strip().strip("'\"")
        return key in context and context[key] is not None

    # Handle comparison expressions
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return True

    if isinstance(tree.body, ast.Compare):
        return _eval_compare(tree.body, context)

    if isinstance(tree.body, ast.Name):
        val = context.get(tree.body.id)
        return bool(val)

    return True


def _eval_compare(node: ast.Compare, context: dict[str, Any]) -> bool:
    """Evaluate a comparison AST node."""
    left = _resolve_value(node.left, context)

    for op_node, comparator in zip(node.ops, node.comparators):
        right = _resolve_value(comparator, context)
        op_func = _OPERATORS.get(type(op_node))
        if op_func is None:
            return True
        try:
            if not op_func(left, right):
                return False
        except TypeError:
            return True
        left = right

    return True


def _resolve_value(node: ast.expr, context: dict[str, Any]) -> Any:
    """Resolve an AST node to a Python value."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return context.get(node.id)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        val = _resolve_value(node.operand, context)
        return -val if isinstance(val, (int, float)) else val
    return None
