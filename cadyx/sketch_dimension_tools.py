"""Exact, read-only measurements for persisted 2D sketch geometry.

These functions calculate values only. They do not add document constraints,
create dimension annotations, or move geometry to satisfy a requested value.
Coordinates and lengths use the document's coordinate units; arc angles use
the signed radians stored by the document model.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from .errors import ValidationError

Point2D = Sequence[float]


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{label} must be a finite number.")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValidationError(f"{label} is out of range.") from exc
    if not math.isfinite(result):
        raise ValidationError(f"{label} must be a finite number.")
    return result


def _point(value: Point2D, label: str) -> tuple[float, float]:
    if isinstance(value, (str, bytes)):
        raise ValidationError(f"{label} must contain two finite coordinates.")
    try:
        coordinates = tuple(value)
    except TypeError as exc:
        raise ValidationError(f"{label} must contain two finite coordinates.") from exc
    if len(coordinates) != 2:
        raise ValidationError(f"{label} must contain two finite coordinates.")
    return (_finite_number(coordinates[0], f"{label} x"),
            _finite_number(coordinates[1], f"{label} y"))


def measure_point_distance(first: Point2D, second: Point2D) -> float:
    """Return Euclidean distance between two 2D points (SK-095)."""
    ax, ay = _point(first, "First point")
    bx, by = _point(second, "Second point")
    distance = math.hypot(bx - ax, by - ay)
    if not math.isfinite(distance):
        raise ValidationError("Point distance must remain finite.")
    return distance


def measure_line_length(start: Point2D, end: Point2D) -> float:
    """Return endpoint distance for a valid finite segment (SK-097)."""
    length = measure_point_distance(start, end)
    if length <= 1e-7:
        raise ValidationError("Line endpoints must be distinct.")
    return length


def measure_circle_radius(radius: float) -> float:
    """Return the stored positive circle radius (SK-098)."""
    result = _finite_number(radius, "Circle radius")
    if result <= 0:
        raise ValidationError("Circle radius must be positive.")
    return result


def measure_circle_diameter(radius: float) -> float:
    """Return twice the stored positive circle radius (SK-099)."""
    diameter = 2 * measure_circle_radius(radius)
    if not math.isfinite(diameter):
        raise ValidationError("Circle diameter must remain finite.")
    return diameter


def measure_arc_angle(sweep_radians: float) -> float:
    """Return the signed sweep angle in document/API radians (SK-102)."""
    sweep = _finite_number(sweep_radians, "Arc sweep")
    if abs(sweep) <= 1e-7 or abs(sweep) >= math.tau - 1e-7:
        raise ValidationError("Arc sweep must be non-zero and less than one revolution.")
    return sweep


def measure_arc_length(radius: float, sweep_radians: float) -> float:
    """Return exact circular arc length r*|sweep| in coordinate units (SK-107)."""
    arc_length = measure_circle_radius(radius) * abs(measure_arc_angle(sweep_radians))
    if not math.isfinite(arc_length):
        raise ValidationError("Arc length must remain finite.")
    return arc_length
