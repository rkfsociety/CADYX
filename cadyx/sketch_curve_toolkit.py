"""Isolated evaluators for equation-driven sketch curves (SK-054, SK-056, SK-057).

These functions return sampled XY coordinates only. They do not persist document
objects, select a kernel curve type, or define sketch command/UI contracts.
"""

from __future__ import annotations

import math
import ast
import operator
from collections.abc import Callable, Sequence

from .sketch_curve_tools import evaluate_interpolating_spline

Point2 = tuple[float, float]

_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.BitXor: operator.pow,
}
_UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "sqrt": math.sqrt, "exp": math.exp, "log": math.log,
    "abs": abs,
}


def _evaluate_expression(node: ast.AST, parameter: float) -> float:
    if isinstance(node, ast.Expression):
        return _evaluate_expression(node.body, parameter)
    if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
        return _finite(node.value, "expression constant")
    if isinstance(node, ast.Name):
        if node.id == "t":
            return parameter
        if node.id == "pi":
            return math.pi
        if node.id == "e":
            return math.e
        raise ValueError(f"unsupported expression name: {node.id}")
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _evaluate_expression(node.left, parameter)
        right = _evaluate_expression(node.right, parameter)
        if isinstance(node.op, (ast.Pow, ast.BitXor)) and abs(right) > 128:
            raise ValueError("expression exponent is out of range")
        try:
            result = _BINARY_OPERATORS[type(node.op)](left, right)
        except (ArithmeticError, OverflowError) as exc:
            raise ValueError("expression could not be evaluated") from exc
        return _finite(result, "expression result")
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        return _finite(_UNARY_OPERATORS[type(node.op)](_evaluate_expression(node.operand, parameter)),
                       "expression result")
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in _FUNCTIONS and not node.keywords and len(node.args) == 1):
        argument = _evaluate_expression(node.args[0], parameter)
        try:
            result = _FUNCTIONS[node.func.id](argument)
        except (ArithmeticError, OverflowError) as exc:
            raise ValueError("expression could not be evaluated") from exc
        return _finite(result, "expression result")
    raise ValueError("expression supports t, pi, e, arithmetic and one-argument math functions only")


def evaluate_parametric_expression(expression: str, parameter: float) -> float:
    """Evaluate a bounded scalar expression using t and basic math functions."""
    if not isinstance(expression, str) or not expression or len(expression) > 256:
        raise ValueError("expression must be a non-empty string of at most 256 characters")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("expression is not valid mathematical syntax") from exc
    if sum(1 for _ in ast.walk(tree)) > 64:
        raise ValueError("expression is too complex")
    return _evaluate_expression(tree, _finite(parameter, "parameter"))


def sample_parametric_expressions(x_expression: str, y_expression: str,
                                  start: float, end: float,
                                  samples: int) -> tuple[Point2, ...]:
    """Uniformly sample serializable x(t), y(t) expressions over a closed range."""
    start, end = _finite(start, "start"), _finite(end, "end")
    count = _sample_count(samples)
    result = []
    for index in range(count):
        parameter = start + (end - start) * index / (count - 1)
        result.append((evaluate_parametric_expression(x_expression, parameter),
                       evaluate_parametric_expression(y_expression, parameter)))
    return tuple(result)


def _finite(value: float, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _sample_count(samples: int) -> int:
    if isinstance(samples, bool) or not isinstance(samples, int) or samples < 2:
        raise ValueError("samples must be an integer >= 2")
    return samples


def evaluate_archimedean_spiral(center: Sequence[float], initial_radius: float,
                                pitch_per_turn: float, angle: float,
                                *, clockwise: bool = False) -> Point2:
    """Evaluate an Archimedean spiral, r = r0 + pitch*|theta|/(2*pi).

    ``angle`` is measured in radians from +X; ``clockwise`` reverses angular
    orientation. The radius must stay nonnegative at the requested angle.
    """
    if len(center) != 2:
        raise ValueError("center must have two coordinates")
    cx, cy = (_finite(v, "center coordinate") for v in center)
    r0 = _finite(initial_radius, "initial radius")
    pitch = _finite(pitch_per_turn, "pitch")
    theta = _finite(angle, "angle")
    if r0 < 0 or pitch < 0 or not isinstance(clockwise, bool):
        raise ValueError("radius and pitch must be nonnegative; clockwise must be boolean")
    theta *= -1 if clockwise else 1
    radius = r0 + pitch * abs(theta) / (2 * math.pi)
    point = (cx + radius * math.cos(theta), cy + radius * math.sin(theta))
    if not all(math.isfinite(v) for v in point):
        raise ValueError("spiral coordinate is out of range")
    return point


def sample_parametric_curve(x_of_t: Callable[[float], float],
                            y_of_t: Callable[[float], float],
                            start: float, end: float, samples: int) -> tuple[Point2, ...]:
    """Uniformly sample user-supplied scalar equations x(t), y(t), inclusive.

    Function callbacks are an in-process computational contract, not a
    serializable CADYX command/schema. Callback errors propagate to the caller.
    """
    if not callable(x_of_t) or not callable(y_of_t):
        raise ValueError("x_of_t and y_of_t must be callable")
    start, end = _finite(start, "start"), _finite(end, "end")
    count = _sample_count(samples)
    result: list[Point2] = []
    for i in range(count):
        t = start + (end - start) * i / (count - 1)
        point = (_finite(x_of_t(t), "x(t)"), _finite(y_of_t(t), "y(t)"))
        result.append(point)
    return tuple(result)


def sample_coordinate_curve(points: Sequence[Sequence[float]],
                            samples_per_span: int = 24,
                            *, closed: bool = False) -> tuple[Point2, ...]:
    """Sample a uniform Catmull-Rom curve interpolating an ordered XY coordinate list.

    For a closed curve repeat the first point as the last input point. Output
    includes the first point and each later span endpoint exactly once.
    """
    count = _sample_count(samples_per_span)
    try:
        coordinates = tuple(tuple(_finite(value, "coordinate") for value in point)
                            for point in points)
    except TypeError as exc:
        raise ValueError("points must be an iterable of XY coordinate pairs") from exc
    if len(coordinates) < 3 or any(len(point) != 2 for point in coordinates):
        raise ValueError("at least three XY coordinate pairs are required")
    if closed and coordinates[0] != coordinates[-1]:
        raise ValueError("closed curves must repeat the first point at the end")
    segment_count = len(coordinates) - 1
    return tuple(evaluate_interpolating_spline(coordinates, segment + step / count, closed=closed)
                 for segment in range(segment_count)
                 for step in range(count)) + (tuple(map(float, coordinates[-1])),)
