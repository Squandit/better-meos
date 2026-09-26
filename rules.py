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
import math
import operator

__all__ = ["RuleError", "evaluate_formula", "validate_formula"]


class RuleError(ValueError):
    """A formula was malformed or used something not on the whitelist."""


# Limits that keep a typo from hanging the server: ``9**9**9`` would otherwise
# grind for minutes while holding the event lock.
MAX_LENGTH = 300
MAX_EXPONENT = 10
MAX_MAGNITUDE = 1e12


def _pow(base, exp):
    if abs(exp) > MAX_EXPONENT:
        raise RuleError(f"powers above {MAX_EXPONENT} aren't allowed")
    return operator.pow(base, exp)


_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: _pow,
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
    if len(expr) > MAX_LENGTH:
        raise RuleError(f"formula is longer than {MAX_LENGTH} characters")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as err:
        raise RuleError(f"syntax error: {err.msg}")
    try:
        value = _eval(tree, variables)
    except RuleError:
        raise
    except ZeroDivisionError:
        raise RuleError("divides by zero")
    except (ArithmeticError, TypeError, ValueError) as err:
        # OverflowError, a bad round()/int() argument, and so on: every failure
        # surfaces as RuleError so callers only ever need to catch one thing.
        raise RuleError(f"can't be calculated: {err}")
    if isinstance(value, complex) or not math.isfinite(value) \
            or abs(value) > MAX_MAGNITUDE:
        raise RuleError("result is out of range")
    return value


# Variables a formula may reference, with sample runs (on time, late, nothing
# found) used to validate a formula when the operator saves it, so unknown names
# and divide-by-zero on an ordinary run are caught at save time, not mid-event.
_SAMPLES = [
    {"controls": 5, "points": 150, "seconds": 3000, "minutes": 50.0,
     "limit": 60, "over_minutes": 0},
    {"controls": 8, "points": 240, "seconds": 3790, "minutes": 3790 / 60,
     "limit": 60, "over_minutes": 4},
    {"controls": 0, "points": 0, "seconds": 1200, "minutes": 20.0,
     "limit": 60, "over_minutes": 0},
]


def validate_formula(expr: str) -> str:
    """
    Check a formula parses, only uses known variables/operations, and gives a
    number for typical runs.

    Returns the trimmed formula on success; raises :class:`RuleError` otherwise.
    """
    expr = (expr or "").strip()
    for sample in _SAMPLES:
        try:
            evaluate_formula(expr, dict(sample))
        except RuleError as err:
            if sample is _SAMPLES[0]:
                raise
            raise RuleError(f"{err} (for a run with {sample['controls']} controls, "
                            f"{sample['over_minutes']} min over)")
    return expr
