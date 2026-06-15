"""
Custom scoring rules: a tiny, safe arithmetic expression evaluator.

MeOS lets an organiser define their own time/score calculation with a built-in
mini-language. We don't need a whole language -- almost every real rule is a
single arithmetic expression over a handful of variables (controls hit, time
taken, base points). This module evaluates such an expression with a strict
AST whitelist so an operator-supplied formula can never run arbitrary code.

Example formulas (score courses):
    ``controls * 10``                         10 points per control
    ``points - over_minutes * 5``             base points, 5/min over the limit
    ``max(0, controls * 25 - over_minutes)``  clamp at zero

Available variables are supplied by the caller (see ``results.build_result``):
``controls`` (valid controls hit), ``points`` (base summed points),
``seconds``/``minutes`` (elapsed time), ``limit`` (time limit, minutes) and
``over_minutes`` (started minutes over the limit, 0 if under). Allowed calls:
``min``, ``max``, ``abs``, ``round``, ``int``.
"""

from __future__ import annotations

import ast
import operator

__all__ = ["RuleError", "evaluate_formula", "validate_formula"]


class RuleError(ValueError):
    """A formula was malformed or used something not on the whitelist."""


_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_COMPARE_OPS = {
    ast.Lt: operator.lt, ast.LtE: operator.le,
    ast.Gt: operator.gt, ast.GtE: operator.ge,
    ast.Eq: operator.eq, ast.NotEq: operator.ne,
}
_FUNCS = {"min": min, "max": max, "abs": abs, "round": round, "int": int}


def _eval(node: ast.AST, variables: dict) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body, variables)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise RuleError("only numbers are allowed as literals")
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in variables:
            raise RuleError(f"unknown variable {node.id!r}")
        return variables[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval(node.left, variables),
                                       _eval(node.right, variables))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval(node.operand, variables))
    if isinstance(node, ast.BoolOp):  # `and` / `or` chains
        values = [_eval(v, variables) for v in node.values]
        result = values[0]
        for v in values[1:]:
            result = (result and v) if isinstance(node.op, ast.And) else (result or v)
        return result
    if isinstance(node, ast.Compare) and len(node.ops) == 1 \
            and type(node.ops[0]) in _COMPARE_OPS:
        return _COMPARE_OPS[type(node.ops[0])](
            _eval(node.left, variables), _eval(node.comparators[0], variables))
    if isinstance(node, ast.IfExp):  # `a if cond else b`
        return _eval(node.body, variables) if _eval(node.test, variables) \
            else _eval(node.orelse, variables)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in _FUNCS and not node.keywords:
        return _FUNCS[node.func.id](*[_eval(a, variables) for a in node.args])
    raise RuleError("that expression isn't allowed in a scoring formula")


def evaluate_formula(expr: str, variables: dict) -> float:
    """
    Evaluate ``expr`` against ``variables`` and return the numeric result.

    Raises :class:`RuleError` for a syntax error or any construct outside the
    whitelist (attribute access, comprehensions, lambdas, arbitrary calls, ...).
    """
    if not expr or not expr.strip():
        raise RuleError("empty formula")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as err:
        raise RuleError(f"syntax error: {err.msg}")
    return _eval(tree, variables)


# Variables a formula may reference, with sample values, used to validate a
# formula at the point the operator saves it (so bad formulas are rejected early).
_SAMPLE = {"controls": 5, "points": 150, "seconds": 3000, "minutes": 50,
           "limit": 60, "over_minutes": 0}


def validate_formula(expr: str) -> str:
    """
    Check a formula parses and only uses known variables/operations.

    Returns the trimmed formula on success; raises :class:`RuleError` otherwise.
    Run a sample evaluation so unknown names are caught at save time, not mid-event.
    """
    expr = (expr or "").strip()
    evaluate_formula(expr, dict(_SAMPLE))
    return expr
