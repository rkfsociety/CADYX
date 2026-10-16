"""Safe arithmetic expressions used by named model parameters."""

from __future__ import annotations

import ast
import math
import operator
import re
from collections.abc import Mapping
from typing import Any

from .errors import ValidationError


_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BINARY: dict[type[ast.operator], Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_UNARY: dict[type[ast.unaryop], Any] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def evaluate_expression(expression: str, values: Mapping[str, Any]) -> float:
    """Evaluate a bounded numeric expression against named model parameters."""
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 256:
        raise ValidationError("Parameter expression must be a non-empty string of at most 256 characters.")
    try:
        root = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError) as exc:
        raise ValidationError("Parameter expression is not valid arithmetic.") from exc
    nodes = list(ast.walk(root))
    if len(nodes) > 64:
        raise ValidationError("Parameter expression contains too many operations.")

    def visit(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ValidationError("Parameter expressions can contain only numbers.")
            result = float(node.value)
        elif isinstance(node, ast.Name):
            if not _NAME.fullmatch(node.id) or node.id not in values:
                raise ValidationError(f"Unknown model parameter: {node.id}.")
            value = values[node.id]
            if isinstance(value, dict):
                value = value.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationError(f"Model parameter {node.id} is not numeric.")
            result = float(value)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "min" and 2 <= len(node.args) <= 8 and not node.keywords):
            result = min(visit(argument) for argument in node.args)
        elif isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Div) and right == 0:
                raise ValidationError("Parameter expression cannot divide by zero.")
            try:
                result = _BINARY[type(node.op)](left, right)
            except (ArithmeticError, OverflowError) as exc:
                raise ValidationError("Parameter expression is outside the numeric range.") from exc
        elif isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            result = _UNARY[type(node.op)](visit(node.operand))
        else:
            raise ValidationError("Parameter expressions support arithmetic, parentheses, parameter names, and min().")
        if not math.isfinite(result):
            raise ValidationError("Parameter expression result must be finite.")
        return float(result)

    return visit(root)


def property_location(target: dict[str, Any], path: str) -> tuple[Any, str | int]:
    """Return the parent container and final key for a supported numeric property."""
    if not isinstance(path, str) or not path or len(path) > 160 or "__" in path:
        raise ValidationError("Parameter binding path is invalid.")
    parts: list[str | int] = []
    for part in path.split("."):
        if not part:
            raise ValidationError("Parameter binding path is invalid.")
        parts.append(int(part) if part.isdecimal() else part)
    kind = target.get("type")
    if kind == "feature":
        if len(parts) < 2 or parts[0] != "parameters":
            raise ValidationError("Feature bindings must target a parameter value.")
    elif kind == "sketch":
        if parts != ["section_offset"]:
            raise ValidationError("Sketch bindings can target only section_offset.")
    elif kind == "point":
        if parts not in (["x"], ["y"]):
            raise ValidationError("Point bindings can target only x or y.")
    elif kind == "circle":
        if parts not in (["x"], ["y"], ["radius"]):
            raise ValidationError("Circle bindings can target only x, y, or radius.")
    elif kind == "ellipse":
        if len(parts) != 1 or parts[0] not in {
            "center_x", "center_y", "major_radius", "minor_radius", "rotation_radians",
        }:
            raise ValidationError("Ellipse bindings must target a numeric ellipse parameter.")
    else:
        raise ValidationError("Parameter bindings must target a feature, sketch, or sketch element.")

    parent: Any = target
    for part in parts[:-1]:
        if isinstance(part, int):
            if not isinstance(parent, list) or part >= len(parent):
                raise ValidationError("Parameter binding path does not exist.")
            parent = parent[part]
        else:
            if not isinstance(parent, dict) or part not in parent:
                raise ValidationError("Parameter binding path does not exist.")
            parent = parent[part]
    final = parts[-1]
    if isinstance(final, int):
        if not isinstance(parent, list) or final >= len(parent):
            raise ValidationError("Parameter binding path does not exist.")
        current = parent[final]
    else:
        if not isinstance(parent, dict) or final not in parent:
            raise ValidationError("Parameter binding path does not exist.")
        current = parent[final]
    if isinstance(current, bool) or not isinstance(current, (int, float)) or not math.isfinite(current):
        raise ValidationError("Parameter bindings must target a finite numeric value.")
    return parent, final


def assign_property(target: dict[str, Any], path: str, value: float) -> None:
    parent, final = property_location(target, path)
    parent[final] = float(value)
