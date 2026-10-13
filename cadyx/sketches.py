"""Persisted XY sketches and constraint checking; this is not a solver."""

from __future__ import annotations

import math
from typing import Any
from uuid import UUID

from .errors import ValidationError


def typed_id(value: Any, kind: str) -> None:
    if not isinstance(value, str) or not value.startswith(kind + ":"):
        raise ValidationError(f"Expected a {kind}:<uuid> identifier.")
    try:
        parsed = UUID(value[len(kind) + 1:])
    except ValueError as exc:
        raise ValidationError("Invalid object UUID.") from exc
    if str(parsed) != value[len(kind) + 1:]:
        raise ValidationError("Object UUID must use canonical lowercase form.")


def number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("Sketch coordinates and dimensions must be finite numbers.")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValidationError("Sketch number is out of range.") from exc
    if not math.isfinite(result):
        raise ValidationError("Sketch numbers must be finite.")
    return result


def validate_sketches(objects: list[dict[str, Any]]) -> None:
    by_id = {item["id"]: item for item in objects}

    def reference(value: Any, kind: str, sketch_id: str) -> dict[str, Any]:
        typed_id(value, kind)
        target = by_id.get(value)
        if target is None or target.get("type") != kind or target.get("sketch_id") != sketch_id:
            raise ValidationError("Sketch reference must resolve within its owning sketch.")
        if kind == "point":
            number(target.get("x"))
            number(target.get("y"))
        return target

    def close(left: float, right: float) -> bool:
        return math.isclose(left, right, rel_tol=0, abs_tol=1e-7)

    for item in objects:
        kind = item.get("type")
        if kind not in {"sketch", "point", "line", "circle", "constraint"}:
            continue
        typed_id(item["id"], kind)
        if kind == "sketch":
            if item.get("plane") != "XY":
                raise ValidationError("Only XY sketches are supported.")
            if not isinstance(item.get("name"), str) or not item["name"].strip() or len(item["name"]) > 120:
                raise ValidationError("Sketch name must contain 1 to 120 characters.")
            continue
        sketch_id = item.get("sketch_id")
        typed_id(sketch_id, "sketch")
        if by_id.get(sketch_id, {}).get("type") != "sketch":
            raise ValidationError("Sketch object refers to a missing sketch.")
        if kind == "point":
            number(item.get("x"))
            number(item.get("y"))
        elif kind == "line":
            start = reference(item.get("start_id"), "point", sketch_id)
            end = reference(item.get("end_id"), "point", sketch_id)
            length = math.hypot(number(start["x"]) - number(end["x"]), number(start["y"]) - number(end["y"]))
            if not math.isfinite(length) or length <= 1e-7:
                raise ValidationError("Line endpoints must be distinct.")
        elif kind == "circle":
            reference(item.get("center_id"), "point", sketch_id)
            if number(item.get("radius")) <= 0:
                raise ValidationError("Circle radius must be positive.")

    # Geometry has been checked before constraints, regardless of storage order.
    for item in objects:
        if item.get("type") != "constraint":
            continue
        sketch_id = item["sketch_id"]
        kind = item.get("kind")
        refs = item.get("references")
        expected = {"horizontal": ["line"], "vertical": ["line"],
                    "coincident": ["point", "point"], "distance": ["point", "point"],
                    "radius": ["circle"]}
        if not isinstance(kind, str) or kind not in expected:
            raise ValidationError("Unsupported sketch constraint.")
        if not isinstance(refs, list) or len(refs) != len(expected[kind]):
            raise ValidationError("Constraint reference count is invalid.")
        targets = [reference(ref, target_kind, sketch_id) for ref, target_kind in zip(refs, expected[kind])]
        if len(refs) == 2 and refs[0] == refs[1]:
            raise ValidationError("Constraint must reference two distinct points.")
        if kind in {"horizontal", "vertical"}:
            line = targets[0]
            a, b = by_id[line["start_id"]], by_id[line["end_id"]]
            axis = "y" if kind == "horizontal" else "x"
            satisfied = close(a[axis], b[axis])
        elif kind in {"coincident", "distance"}:
            a, b = targets
            distance = math.hypot(number(a["x"]) - number(b["x"]), number(a["y"]) - number(b["y"]))
            value = 0 if kind == "coincident" else number(item.get("value"))
            if value < 0:
                raise ValidationError("Distance must be non-negative.")
            satisfied = close(distance, value)
        else:
            value = number(item.get("value"))
            if value <= 0:
                raise ValidationError("Radius constraint must be positive.")
            satisfied = close(targets[0]["radius"], value)
        if not satisfied:
            raise ValidationError(f"Sketch constraint {item['id']} is not satisfied.")
