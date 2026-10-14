"""Persisted XY/XZ sketches, closed-profile extraction, and constraint checking."""

from __future__ import annotations

import math
from collections import defaultdict
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
            if item.get("plane") not in {"XY", "XZ"}:
                raise ValidationError("Only XY and XZ sketches are supported.")
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


def closed_profile(objects: list[dict[str, Any]], sketch_id: str) -> tuple[str, list[dict[str, float]]]:
    """Return one closed line loop or one sampled circle from a sketch."""
    sketch = next((item for item in objects if item.get("id") == sketch_id and item.get("type") == "sketch"), None)
    if sketch is None:
        raise ValidationError("A profile operation needs an existing sketch.")
    plane = sketch["plane"]
    owned = [item for item in objects if item.get("sketch_id") == sketch_id]
    lines = [item for item in owned if item.get("type") == "line"]
    circles = [item for item in owned if item.get("type") == "circle"]

    if not lines:
        if len(circles) != 1:
            raise ValidationError("A profile operation needs one closed line loop or one circle.")
        by_id = {item["id"]: item for item in objects}
        circle = circles[0]
        center = by_id[circle["center_id"]]
        radius = number(circle["radius"])
        return plane, [
            {"x": number(center["x"]) + radius * math.cos(math.tau * index / 128),
             "y": number(center["y"]) + radius * math.sin(math.tau * index / 128)}
            for index in range(128)
        ]
    if circles or len(lines) < 3 or len(lines) > 4096:
        raise ValidationError("A profile operation needs one closed line loop with 3 to 4096 edges.")

    by_id = {item["id"]: item for item in objects}
    adjacency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in lines:
        adjacency[line["start_id"]].append(line)
        adjacency[line["end_id"]].append(line)
    if any(len(edges) != 2 for edges in adjacency.values()):
        raise ValidationError("Profile lines must form one closed loop without branches.")

    first = lines[0]
    start_id = first["start_id"]
    current_id = first["end_id"]
    used = {first["id"]}
    ordered_ids = [start_id]
    while current_id != start_id:
        if current_id in ordered_ids or len(used) >= len(lines):
            raise ValidationError("Profile lines must form one closed loop without branches.")
        ordered_ids.append(current_id)
        next_lines = [line for line in adjacency[current_id] if line["id"] not in used]
        if len(next_lines) != 1:
            raise ValidationError("Profile lines must form one closed loop without branches.")
        next_line = next_lines[0]
        used.add(next_line["id"])
        current_id = next_line["end_id"] if next_line["start_id"] == current_id else next_line["start_id"]
    if len(used) != len(lines) or len(ordered_ids) != len(lines):
        raise ValidationError("Profile lines must form exactly one closed loop.")
    profile = [{"x": number(by_id[point_id]["x"]), "y": number(by_id[point_id]["y"])}
               for point_id in ordered_ids]
    area = sum(
        point["x"] * profile[(index + 1) % len(profile)]["y"]
        - profile[(index + 1) % len(profile)]["x"] * point["y"]
        for index, point in enumerate(profile)
    )
    if not math.isfinite(area) or abs(area) <= 1e-12:
        raise ValidationError("Profile area must be non-zero.")
    return plane, profile


def open_path(objects: list[dict[str, Any]], sketch_id: str) -> tuple[str, list[dict[str, float]]]:
    """Return the ordered vertices of one connected, non-branching open line chain."""
    sketch = next((item for item in objects if item.get("id") == sketch_id and item.get("type") == "sketch"), None)
    if sketch is None:
        raise ValidationError("A sweep operation needs an existing sketch.")
    owned = [item for item in objects if item.get("sketch_id") == sketch_id]
    lines = [item for item in owned if item.get("type") == "line"]
    if not 2 <= len(lines) <= 511 or any(item.get("type") not in {"point", "line"} for item in owned):
        raise ValidationError("A sweep path needs 3 to 512 points and no other sketch geometry.")

    by_id = {item["id"]: item for item in objects}
    adjacency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in lines:
        adjacency[line["start_id"]].append(line)
        adjacency[line["end_id"]].append(line)
    endpoints = [point_id for point_id, edges in adjacency.items() if len(edges) == 1]
    if len(endpoints) != 2 or any(len(edges) not in {1, 2} for edges in adjacency.values()):
        raise ValidationError("A sweep path must be one open line chain without branches or loops.")
    point_ids = {item["id"] for item in owned if item.get("type") == "point"}
    if set(adjacency) != point_ids:
        raise ValidationError("Every point in a sweep sketch must belong to its open line chain.")

    current_id = endpoints[0]
    ordered_ids = [current_id]
    used: set[str] = set()
    while True:
        next_lines = [line for line in adjacency[current_id] if line["id"] not in used]
        if not next_lines:
            break
        if len(next_lines) != 1:
            raise ValidationError("A sweep path must be one open line chain without branches or loops.")
        line = next_lines[0]
        used.add(line["id"])
        current_id = line["end_id"] if line["start_id"] == current_id else line["start_id"]
        ordered_ids.append(current_id)
    if len(used) != len(lines) or len(ordered_ids) != len(lines) + 1:
        raise ValidationError("A sweep path must be one connected open line chain.")
    return sketch["plane"], [
        {"x": number(by_id[point_id]["x"]), "y": number(by_id[point_id]["y"])}
        for point_id in ordered_ids
    ]
