"""Pure geometry builders for the linear and circular sketch slot tools.

Builders return a closed contour made of line and exact circular-arc edges.
Points are ``(x, y)`` sketch coordinates; arc angles and sweeps are radians.
No document objects or identifiers are created here.
"""

from __future__ import annotations

import math
from typing import Iterable
from uuid import UUID, uuid4

from .errors import ValidationError

Point = tuple[float, float]


def _point(value: Iterable[float], label: str) -> Point:
    try:
        items = tuple(value)
    except TypeError as exc:
        raise ValidationError(f"{label} must contain two finite coordinates.") from exc
    if len(items) != 2:
        raise ValidationError(f"{label} must contain two finite coordinates.")
    result = tuple(_number(v, label) for v in items)
    return result[0], result[1]


def _number(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{label} must be a finite number.")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValidationError(f"{label} is out of range.") from exc
    if not math.isfinite(result):
        raise ValidationError(f"{label} must be a finite number.")
    return result


def _distance(a: Point, b: Point) -> float:
    result = math.hypot(b[0] - a[0], b[1] - a[1])
    if not math.isfinite(result):
        raise ValidationError("Slot coordinates are numerically out of range.")
    return result


def _slot_width(width: float) -> float:
    result = _number(width, "Slot width")
    if result <= 1e-7:
        raise ValidationError("Slot width must be greater than 1e-7.")
    return result


def _line(start: Point, end: Point) -> dict:
    return {"type": "line", "start": start, "end": end}


def _arc(center: Point, radius: float, start_angle: float, sweep: float) -> dict:
    end_angle = start_angle + sweep
    start = (center[0] + radius * math.cos(start_angle), center[1] + radius * math.sin(start_angle))
    end = (center[0] + radius * math.cos(end_angle), center[1] + radius * math.sin(end_angle))
    if not all(math.isfinite(value) for value in (*start, *end, end_angle)):
        raise ValidationError("Slot arc coordinates must remain finite.")
    return {"type": "arc", "center": center, "radius": radius,
            "start_angle_radians": start_angle, "sweep_radians": sweep,
            "start": start, "end": end}


def _contour(edges: list[dict], centerline: dict) -> dict:
    return {"closed": True, "edges": edges, "centerline": centerline}


def contour_to_sketch_objects(contour: dict, sketch_id: str) -> list[dict]:
    """Convert a built contour into ordinary CADYX point/line/circular-arc records.

    The returned records can be appended to a document containing ``sketch_id``;
    they use the same references consumed by sketch validation and profile extraction.
    """
    if not isinstance(sketch_id, str) or not sketch_id.startswith("sketch:"):
        raise ValidationError("A CADYX sketch identifier is required.")
    try:
        parsed_sketch_uuid = UUID(sketch_id[len("sketch:"):])
    except ValueError as exc:
        raise ValidationError("A canonical CADYX sketch identifier is required.") from exc
    if str(parsed_sketch_uuid) != sketch_id[len("sketch:"):]:
        raise ValidationError("A canonical CADYX sketch identifier is required.")
    edges = contour.get("edges") if isinstance(contour, dict) else None
    if not isinstance(contour, dict) or contour.get("closed") is not True or not isinstance(edges, list) or len(edges) < 3:
        raise ValidationError("A closed slot contour with at least three edges is required.")
    for edge in edges:
        if not isinstance(edge, dict) or not isinstance(edge.get("type"), str) or edge["type"] not in {"line", "arc"}:
            raise ValidationError("A slot contour may contain only line and circular-arc edges.")
        start = _point(edge.get("start", ()), "Contour endpoint")
        end = _point(edge.get("end", ()), "Contour endpoint")
        if _distance(start, end) <= 1e-7:
            raise ValidationError("Contour edges must have distinct endpoints.")
        if edge["type"] == "arc":
            center = _point(edge.get("center", ()), "Arc center")
            radius = _number(edge.get("radius"), "Arc radius")
            sweep = _number(edge.get("sweep_radians"), "Arc sweep")
            if radius <= 1e-7 or abs(sweep) <= 1e-7 or abs(sweep) >= math.tau - 1e-7:
                raise ValidationError("Contour arc radius and sweep must define a non-degenerate arc.")
            if any(abs(_distance(center, point) - radius) > 1e-7 for point in (start, end)):
                raise ValidationError("Contour arc endpoints must lie on its center radius.")
            start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
            end_angle = math.atan2(end[1] - center[1], end[0] - center[0])
            angular_error = math.atan2(math.sin(end_angle - start_angle - sweep),
                                       math.cos(end_angle - start_angle - sweep))
            if abs(angular_error) > 1e-7:
                raise ValidationError("Contour arc sweep must reach its end point.")

    for current, following in zip(edges, edges[1:] + edges[:1]):
        if _distance(_point(current["end"], "Contour endpoint"),
                     _point(following["start"], "Contour endpoint")) > 1e-7:
            raise ValidationError("Contour edges must form one continuous closed loop.")

    ids = [f"point:{uuid4()}" for _ in edges]
    records: list[dict] = []
    for index, edge in enumerate(edges):
        kind = edge["type"]
        start = _point(edge["start"], "Contour endpoint")
        end = _point(edge["end"], "Contour endpoint")
        # Reuse the next edge's starting point id to make topology exact by reference.
        end_id = ids[(index + 1) % len(edges)]
        records.append({"id": ids[index], "type": "point", "sketch_id": sketch_id,
                        "x": start[0], "y": start[1]})
        edge_id = f"{'line' if kind == 'line' else 'circle'}:{uuid4()}"
        if kind == "line":
            records.append({"id": edge_id, "type": "line", "sketch_id": sketch_id,
                            "start_id": ids[index], "end_id": end_id})
        else:
            center = _point(edge.get("center", ()), "Arc center")
            center_id = f"point:{uuid4()}"
            records.append({"id": center_id, "type": "point", "sketch_id": sketch_id,
                            "x": center[0], "y": center[1], "construction": True})
            records.append({"id": edge_id, "type": "circle", "sketch_id": sketch_id,
                            "center_id": center_id, "radius": _number(edge.get("radius"), "Arc radius"),
                            "start_id": ids[index], "end_id": end_id,
                            "sweep_radians": _number(edge.get("sweep_radians"), "Arc sweep")})
    return records


def build_straight_slot(start: Iterable[float], end: Iterable[float], width: float) -> dict:
    """Build a capsule slot from its two semicircle centers and width."""
    a, b = _point(start, "Slot start"), _point(end, "Slot end")
    width = _slot_width(width)
    length = _distance(a, b)
    if length <= 1e-7:
        raise ValidationError("Straight slot center points must be distinct.")
    angle = math.atan2(b[1] - a[1], b[0] - a[0])
    half = width / 2
    normal = (-math.sin(angle) * half, math.cos(angle) * half)
    left_a = (a[0] + normal[0], a[1] + normal[1])
    left_b = (b[0] + normal[0], b[1] + normal[1])
    right_a = (a[0] - normal[0], a[1] - normal[1])
    right_b = (b[0] - normal[0], b[1] - normal[1])
    edges = [_line(left_a, left_b),
             _arc(b, half, angle + math.pi / 2, -math.pi),
             _line(right_b, right_a),
             _arc(a, half, angle - math.pi / 2, -math.pi)]
    return _contour(edges, {"type": "line", "start": a, "end": b})


def build_slot_by_centers(start_center: Iterable[float], end_center: Iterable[float], width: float) -> dict:
    """Build a straight slot from the centers of its rounded ends (SK-048)."""
    return build_straight_slot(start_center, end_center, width)


def build_slot_by_total_length(center: Iterable[float], length: float, width: float,
                               angle_degrees: float = 0.0) -> dict:
    """Build a straight slot from overall length, width, midpoint and axis angle (SK-049)."""
    midpoint = _point(center, "Slot center")
    length, width = _number(length, "Slot length"), _slot_width(width)
    angle = math.radians(_number(angle_degrees, "Slot angle"))
    if length <= width + 1e-7:
        raise ValidationError("Slot total length must exceed its width by more than 1e-7.")
    if not math.isfinite(angle):
        raise ValidationError("Slot angle must remain finite in radians.")
    radius = width / 2
    center_distance = length - width
    dx, dy = math.cos(angle) * center_distance / 2, math.sin(angle) * center_distance / 2
    return build_straight_slot((midpoint[0] - dx, midpoint[1] - dy),
                               (midpoint[0] + dx, midpoint[1] + dy), width)


def build_slot_from_center(center: Iterable[float], length: float, width: float,
                           angle_degrees: float = 0.0) -> dict:
    """Build a slot centered at a point, with total length and width (SK-050)."""
    return build_slot_by_total_length(center, length, width, angle_degrees)


def _three_point_circle(start: Point, through: Point, end: Point) -> tuple[Point, float, float, float]:
    ax, ay = start
    bx, by = through[0] - ax, through[1] - ay
    cx, cy = end[0] - ax, end[1] - ay
    determinant = 2 * (bx * cy - by * cx)
    scale = max(_distance(start, through), _distance(through, end), _distance(end, start), 1.0)
    if abs(determinant) <= 1e-12 * scale * scale:
        raise ValidationError("Arc slot centerline points must be distinct and non-collinear.")
    bb, cc = bx * bx + by * by, cx * cx + cy * cy
    offset = ((bb * cy - cc * by) / determinant, (bx * cc - cx * bb) / determinant)
    center = (ax + offset[0], ay + offset[1])
    if not all(math.isfinite(value) for value in center):
        raise ValidationError("Arc slot center must remain finite.")
    radius = _distance(center, start)
    a0, am, a1 = (math.atan2(p[1] - center[1], p[0] - center[0]) for p in (start, through, end))
    ccw_total = (a1 - a0) % math.tau
    ccw_mid = (am - a0) % math.tau
    sweep = ccw_total if ccw_mid <= ccw_total + 1e-10 else ccw_total - math.tau
    if abs(sweep) <= 1e-7 or abs(sweep) >= math.tau - 1e-7:
        raise ValidationError("Arc slot centerline sweep must be non-zero and less than one revolution.")
    return center, radius, a0, sweep


def _arc_slot(center: Point, radius: float, start_angle: float, sweep: float, width: float) -> dict:
    width = _slot_width(width)
    half = width / 2
    if radius <= half + 1e-7:
        raise ValidationError("Arc slot centerline radius must exceed half its width.")
    # The end caps have radius width / 2 and are centered at the centerline
    # endpoints. Near a full turn those centers approach each other; their
    # semicircles then overlap and the nominally closed profile self-intersects.
    closing_gap = math.tau - abs(sweep)
    endpoint_distance = 2 * radius * math.sin(closing_gap / 2)
    if not math.isfinite(endpoint_distance):
        raise ValidationError("Arc slot endpoints must remain finite.")
    if endpoint_distance <= width + 1e-7 * max(1.0, radius, width):
        raise ValidationError("Arc slot end caps must remain separated by at least the slot width.")
    direction = 1.0 if sweep > 0 else -1.0
    outer, inner = radius + half, radius - half
    end_angle = start_angle + sweep
    # Positive sweep follows the outer boundary first; negative sweep mirrors the edge order.
    if direction > 0:
        edges = [_arc(center, outer, start_angle, sweep),
                 _arc(_polar(center, radius, end_angle), half, end_angle, math.pi),
                 _arc(center, inner, end_angle, -sweep),
                 _arc(_polar(center, radius, start_angle), half, start_angle + math.pi, math.pi)]
    else:
        edges = [_arc(center, outer, start_angle, sweep),
                 _arc(_polar(center, radius, end_angle), half, end_angle, -math.pi),
                 _arc(center, inner, end_angle, -sweep),
                 _arc(_polar(center, radius, start_angle), half, start_angle - math.pi, -math.pi)]
    return _contour(edges, {"type": "arc", "center": center, "radius": radius,
                            "start_angle_radians": start_angle, "sweep_radians": sweep})


def _polar(center: Point, radius: float, angle: float) -> Point:
    return center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle)


def build_arc_slot_three_points(start: Iterable[float], through: Iterable[float], end: Iterable[float],
                                width: float) -> dict:
    """Build an annular slot whose centerline arc passes through three points (SK-051)."""
    a, m, b = _point(start, "Arc slot start"), _point(through, "Arc slot through"), _point(end, "Arc slot end")
    center, radius, angle, sweep = _three_point_circle(a, m, b)
    return _arc_slot(center, radius, angle, sweep, width)


def build_arc_slot_from_center(center: Iterable[float], radius: float, start_angle_degrees: float,
                               sweep_degrees: float, width: float) -> dict:
    """Build an annular slot from centerline center/radius and angular extent (SK-052)."""
    center = _point(center, "Arc slot center")
    radius = _number(radius, "Arc slot centerline radius")
    start_angle = math.radians(_number(start_angle_degrees, "Arc slot start angle"))
    sweep = math.radians(_number(sweep_degrees, "Arc slot sweep"))
    if not math.isfinite(start_angle) or not math.isfinite(sweep):
        raise ValidationError("Arc slot angles must remain finite in radians.")
    if abs(sweep) <= 1e-7 or abs(sweep) >= math.tau - 1e-7:
        raise ValidationError("Arc slot sweep must be non-zero and less than 360 degrees.")
    return _arc_slot(center, radius, start_angle, sweep, width)


def build_tangent_arc_slot(start: Iterable[float], tangent: Iterable[float], radius: float,
                           sweep_degrees: float, width: float, side: int = 1) -> dict:
    """Build an arc slot tangent to a directed line at start (SK-053).

    ``side`` selects the center side relative to the tangent. The signed sweep
    follows the same convention as CADYX tangent arcs; its initial traversal
    agrees with the supplied tangent when ``side * sweep_degrees > 0``.
    """
    start, tangent = _point(start, "Arc slot start"), _point(tangent, "Arc slot tangent")
    radius = _number(radius, "Arc slot centerline radius")
    sweep = math.radians(_number(sweep_degrees, "Arc slot sweep"))
    width = _slot_width(width)
    if type(side) is not int or side not in (-1, 1):
        raise ValidationError("Arc slot side must be 1 or -1.")
    tangent_length = math.hypot(*tangent)
    if tangent_length <= 1e-7:
        raise ValidationError("Arc slot tangent direction must be non-zero.")
    if abs(sweep) <= 1e-7 or abs(sweep) >= math.tau - 1e-7:
        raise ValidationError("Arc slot sweep must be non-zero and less than 360 degrees.")
    ux, uy = tangent[0] / tangent_length, tangent[1] / tangent_length
    center = (start[0] - side * uy * radius, start[1] + side * ux * radius)
    start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
    return _arc_slot(center, radius, start_angle, sweep, width)
