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
        if kind not in {"sketch", "point", "line", "circle", "ellipse", "constraint"}:
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
            if "construction" in item and type(item["construction"]) is not bool:
                raise ValidationError("Sketch point construction flag must be a boolean.")
        elif kind == "line":
            start = reference(item.get("start_id"), "point", sketch_id)
            end = reference(item.get("end_id"), "point", sketch_id)
            if "construction" in item and type(item["construction"]) is not bool:
                raise ValidationError("Sketch line construction flag must be a boolean.")
            if item.get("extent", "segment") not in {"segment", "infinite", "centerline"}:
                raise ValidationError("Sketch line extent must be segment, infinite, or centerline.")
            length = math.hypot(number(start["x"]) - number(end["x"]), number(start["y"]) - number(end["y"]))
            if not math.isfinite(length) or length <= 1e-7:
                raise ValidationError("Line endpoints must be distinct.")
        elif kind == "circle":
            reference(item.get("center_id"), "point", sketch_id)
            radius = number(item.get("radius"))
            if radius <= 0:
                raise ValidationError("Circle radius must be positive.")
            arc_fields = {"start_id", "end_id", "sweep_radians"}
            present = arc_fields.intersection(item)
            if present and present != arc_fields:
                raise ValidationError("A circular arc needs start_id, end_id and sweep_radians together.")
            if present:
                center = by_id[item["center_id"]]
                start = reference(item["start_id"], "point", sketch_id)
                end = reference(item["end_id"], "point", sketch_id)
                sweep = number(item["sweep_radians"])
                for endpoint in (start, end):
                    distance = math.hypot(number(endpoint["x"]) - number(center["x"]),
                                          number(endpoint["y"]) - number(center["y"]))
                    if not close(distance, radius):
                        raise ValidationError("Circular arc endpoints must lie on their center radius.")
                if abs(sweep) <= 1e-7 or abs(sweep) >= math.tau - 1e-7:
                    raise ValidationError("Circular arc sweep must be non-zero and less than one revolution.")
                start_angle = math.atan2(start["y"] - center["y"], start["x"] - center["x"])
                end_angle = math.atan2(end["y"] - center["y"], end["x"] - center["x"])
                angular_error = math.atan2(math.sin(end_angle - start_angle - sweep),
                                           math.cos(end_angle - start_angle - sweep))
                if abs(angular_error) > 1e-7:
                    raise ValidationError("Circular arc sweep does not reach its end point.")
        elif kind == "ellipse":
            for key in ("center_x", "center_y"):
                number(item.get(key))
            major = number(item.get("major_radius"))
            minor = number(item.get("minor_radius"))
            rotation = number(item.get("rotation_radians"))
            if minor <= 1e-7 or major < minor or major <= 1e-7:
                raise ValidationError("Ellipse radii must satisfy major_radius >= minor_radius > 1e-7.")
            if abs(rotation) > 1e12:
                raise ValidationError("Ellipse rotation is out of range.")

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
    """Return one closed line/arc loop or one sampled analytic conic from a sketch."""
    sketch = next((item for item in objects if item.get("id") == sketch_id and item.get("type") == "sketch"), None)
    if sketch is None:
        raise ValidationError("A profile operation needs an existing sketch.")
    plane = sketch["plane"]
    owned = [item for item in objects if item.get("sketch_id") == sketch_id]
    lines = [item for item in owned if item.get("type") == "line"
             and not item.get("construction", False) and item.get("extent", "segment") == "segment"]
    circles = [item for item in owned if item.get("type") == "circle"
               and not item.get("construction", False)]
    ellipses = [item for item in owned if item.get("type") == "ellipse"
                and not item.get("construction", False)]
    arcs = [item for item in circles if "sweep_radians" in item]
    circles = [item for item in circles if "sweep_radians" not in item]

    if not lines and not arcs:
        if ellipses:
            if len(ellipses) != 1 or circles:
                raise ValidationError("A profile operation needs one closed ellipse or one circle.")
            ellipse = ellipses[0]
            cosine, sine = math.cos(ellipse["rotation_radians"]), math.sin(ellipse["rotation_radians"])
            return plane, [
                {"x": ellipse["center_x"] + ellipse["major_radius"] * math.cos(math.tau * index / 128) * cosine
                       - ellipse["minor_radius"] * math.sin(math.tau * index / 128) * sine,
                 "y": ellipse["center_y"] + ellipse["major_radius"] * math.cos(math.tau * index / 128) * sine
                       + ellipse["minor_radius"] * math.sin(math.tau * index / 128) * cosine}
                for index in range(128)
            ]
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
    edges = lines + arcs
    if circles or ellipses or len(edges) < 3 or len(edges) > 4096:
        raise ValidationError("A profile operation needs one closed line/arc loop with 3 to 4096 edges.")

    by_id = {item["id"]: item for item in objects}
    adjacency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        adjacency[edge["start_id"]].append(edge)
        adjacency[edge["end_id"]].append(edge)
    if any(len(edges) != 2 for edges in adjacency.values()):
        raise ValidationError("Profile lines must form one closed loop without branches.")

    first = edges[0]
    start_id = first["start_id"]
    used = {first["id"]}
    start_point = by_id[start_id]
    profile = [{"x": number(start_point["x"]), "y": number(start_point["y"])}]

    def append_edge(edge: dict[str, Any], from_id: str, to_id: str) -> None:
        if edge["type"] == "line":
            point = by_id[to_id]
            profile.append({"x": number(point["x"]), "y": number(point["y"])})
            return
        center = by_id[edge["center_id"]]
        from_point = by_id[from_id]
        start_angle = math.atan2(from_point["y"] - center["y"], from_point["x"] - center["x"])
        sweep = number(edge["sweep_radians"])
        if from_id != edge["start_id"]:
            sweep = -sweep
        steps = max(1, math.ceil(abs(sweep) / (math.pi / 64)))
        radius = number(edge["radius"])
        for index in range(1, steps + 1):
            angle = start_angle + sweep * index / steps
            profile.append({"x": number(center["x"]) + radius * math.cos(angle),
                            "y": number(center["y"]) + radius * math.sin(angle)})

    current_id = first["end_id"]
    append_edge(first, start_id, current_id)
    while current_id != start_id:
        if len(used) >= len(edges):
            raise ValidationError("Profile lines must form one closed loop without branches.")
        next_edges = [edge for edge in adjacency[current_id] if edge["id"] not in used]
        if len(next_edges) != 1:
            raise ValidationError("Profile lines must form one closed loop without branches.")
        next_edge = next_edges[0]
        used.add(next_edge["id"])
        next_id = next_edge["end_id"] if next_edge["start_id"] == current_id else next_edge["start_id"]
        append_edge(next_edge, current_id, next_id)
        current_id = next_id
    if len(used) != len(edges):
        raise ValidationError("Profile lines must form exactly one closed loop.")
    profile.pop()  # discard the final sampled endpoint, which closes onto the first vertex
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
    lines = [item for item in owned if item.get("type") == "line"
             and not item.get("construction", False) and item.get("extent", "segment") == "segment"]
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
    point_ids = {item["id"] for item in owned
                 if item.get("type") == "point" and not item.get("construction", False)}
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
