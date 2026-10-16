"""Persisted XY/XZ sketches, closed-profile extraction, and constraint checking."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any
from uuid import UUID

from .errors import ValidationError
from .sketch_curve_tools import (
    evaluate_bezier, evaluate_bspline, evaluate_nurbs,
)


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


def spline_sample_points(item: dict[str, Any], by_id: dict[str, dict[str, Any]],
                        samples_per_span: int = 24) -> list[dict[str, float]]:
    """Sample persisted sketch curves using their selected interpolation mode."""
    if isinstance(samples_per_span, bool) or not isinstance(samples_per_span, int) or samples_per_span < 1:
        raise ValidationError("Spline samples per span must be a positive integer.")
    points = [(number(by_id[point_id]["x"]), number(by_id[point_id]["y"]))
              for point_id in item["point_ids"]]
    mode = item.get("mode", "interpolating")
    if mode == "control":
        return _control_spline_sample_points(points, samples_per_span)
    if mode == "bezier":
        return _bezier_sample_points(points, samples_per_span)
    if mode == "bspline":
        return _bspline_sample_points(points, samples_per_span)
    if mode == "nurbs":
        return _nurbs_sample_points(points, item, samples_per_span)
    if mode == "closed":
        return _closed_interpolating_sample_points(points, samples_per_span)
    result = [dict(x=points[0][0], y=points[0][1])]

    def mix(a: tuple[float, float], b: tuple[float, float], ratio: float) -> tuple[float, float]:
        return a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio

    def interval(a: tuple[float, float], b: tuple[float, float]) -> float:
        value = math.sqrt(math.hypot(b[0] - a[0], b[1] - a[1]))
        if not math.isfinite(value) or value <= 1e-12:
            raise ValidationError("Spline points must be distinct and numerically stable.")
        return value

    for index in range(len(points) - 1):
        p1, p2 = points[index], points[index + 1]
        p0 = points[index - 1] if index else (2 * p1[0] - p2[0], 2 * p1[1] - p2[1])
        p3 = points[index + 2] if index + 2 < len(points) else (2 * p2[0] - p1[0], 2 * p2[1] - p1[1])
        t0 = 0.0
        t1 = t0 + interval(p0, p1)
        t2 = t1 + interval(p1, p2)
        t3 = t2 + interval(p2, p3)
        for step in range(1, samples_per_span + 1):
            t = t1 + (t2 - t1) * step / samples_per_span
            a1 = mix(p0, p1, (t - t0) / (t1 - t0))
            a2 = mix(p1, p2, (t - t1) / (t2 - t1))
            a3 = mix(p2, p3, (t - t2) / (t3 - t2))
            b1 = mix(a1, a2, (t - t0) / (t2 - t0))
            b2 = mix(a2, a3, (t - t1) / (t3 - t1))
            sampled = mix(b1, b2, (t - t1) / (t2 - t1))
            if not all(math.isfinite(value) for value in sampled):
                raise ValidationError("Spline interpolation must remain finite.")
            result.append({"x": sampled[0], "y": sampled[1]})
    return result


def _closed_interpolating_sample_points(
        points: list[tuple[float, float]], samples_per_span: int) -> list[dict[str, float]]:
    """Sample a periodic centripetal Catmull-Rom curve through unique vertices."""
    result = [dict(x=points[0][0], y=points[0][1])]

    def mix(a: tuple[float, float], b: tuple[float, float], ratio: float) -> tuple[float, float]:
        return a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio

    def interval(a: tuple[float, float], b: tuple[float, float]) -> float:
        value = math.sqrt(math.hypot(b[0] - a[0], b[1] - a[1]))
        if not math.isfinite(value) or value <= 1e-12:
            raise ValidationError("Closed spline vertices must be distinct and numerically stable.")
        return value

    for segment in range(len(points)):
        p1 = points[segment]
        p2 = points[(segment + 1) % len(points)]
        p0 = points[(segment - 1) % len(points)]
        p3 = points[(segment + 2) % len(points)]
        t0 = 0.0
        t1 = t0 + interval(p0, p1)
        t2 = t1 + interval(p1, p2)
        t3 = t2 + interval(p2, p3)
        for step in range(1, samples_per_span + 1):
            if step == samples_per_span:
                sampled = p2
            else:
                t = t1 + (t2 - t1) * step / samples_per_span
                a1 = mix(p0, p1, (t - t0) / (t1 - t0))
                a2 = mix(p1, p2, (t - t1) / (t2 - t1))
                a3 = mix(p2, p3, (t - t2) / (t3 - t2))
                b1 = mix(a1, a2, (t - t0) / (t2 - t0))
                b2 = mix(a2, a3, (t - t1) / (t3 - t1))
                sampled = mix(b1, b2, (t - t1) / (t2 - t1))
            if not all(math.isfinite(value) for value in sampled):
                raise ValidationError("Closed spline interpolation must remain finite.")
            result.append({"x": sampled[0], "y": sampled[1]})
    return result


def _bezier_sample_points(points: list[tuple[float, float]], samples_per_span: int) -> list[dict[str, float]]:
    """Sample one polynomial Bezier curve defined by all ordered control points."""
    return [dict(x=x, y=y) for x, y in
            (evaluate_bezier(points, index / samples_per_span)
             for index in range(samples_per_span + 1))]


def _bspline_sample_points(points: list[tuple[float, float]], samples_per_span: int) -> list[dict[str, float]]:
    """Sample an open clamped uniform B-spline with degree min(3, point count - 1)."""
    degree = min(3, len(points) - 1)
    span_count = len(points) - degree
    knots = [0.0] * (degree + 1)
    knots.extend(index / span_count for index in range(1, span_count))
    knots.extend([1.0] * (degree + 1))
    result: list[dict[str, float]] = []
    for span in range(span_count):
        low, high = knots[degree + span], knots[degree + span + 1]
        first_step = 0 if span == 0 else 1
        for step in range(first_step, samples_per_span + 1):
            if span == span_count - 1 and step == samples_per_span:
                point = points[-1]
            else:
                parameter = low + (high - low) * step / samples_per_span
                point = evaluate_bspline(points, degree, knots, parameter)
            if not all(math.isfinite(value) for value in point):
                raise ValidationError("Spline interpolation must remain finite.")
            result.append({"x": point[0], "y": point[1]})
    return result


def _nurbs_sample_points(points: list[tuple[float, float]], item: dict[str, Any],
                         samples_per_span: int) -> list[dict[str, float]]:
    """Sample a persisted rational B-spline over each non-empty knot span."""
    degree = item["degree"]
    knots = [number(value) for value in item["knots"]]
    weights = [number(value) for value in item["weights"]]
    count = len(points)
    domain_start, domain_end = knots[degree], knots[count]
    result: list[dict[str, float]] = []
    spans = [(index, knots[index], knots[index + 1])
             for index in range(degree, count)
             if knots[index] < knots[index + 1]]
    for index, low, high in spans:
        first_step = 0 if not result else 1
        for step in range(first_step, samples_per_span + 1):
            parameter = low + (high - low) * step / samples_per_span
            if index == spans[-1][0] and step == samples_per_span:
                parameter = domain_end
            try:
                x, y = evaluate_nurbs(points, weights, degree, knots, parameter)
            except (ValueError, OverflowError, ZeroDivisionError) as exc:
                raise ValidationError("NURBS evaluation must remain finite and valid.") from exc
            if not math.isfinite(x) or not math.isfinite(y):
                raise ValidationError("NURBS evaluation must remain finite and valid.")
            result.append({"x": x, "y": y})
    if not result or spans[0][1] != domain_start or spans[-1][2] != domain_end:
        raise ValidationError("NURBS knot vector must cover its parameter domain.")
    return result


def _control_spline_sample_points(points: list[tuple[float, float]],
                                  samples_per_span: int) -> list[dict[str, float]]:
    """Sample an open clamped uniform B-spline of degree 2 or 3 by de Boor evaluation."""
    degree = min(3, len(points) - 1)
    span_count = len(points) - degree
    knots = [0.0] * (degree + 1)
    knots.extend(index / span_count for index in range(1, span_count))
    knots.extend([1.0] * (degree + 1))
    result = [dict(x=points[0][0], y=points[0][1])]
    for span in range(span_count):
        low, high = knots[degree + span], knots[degree + span + 1]
        for step in range(1, samples_per_span + 1):
            if span == span_count - 1 and step == samples_per_span:
                result.append({"x": points[-1][0], "y": points[-1][1]})
                continue
            parameter = low + (high - low) * step / samples_per_span
            knot_span = next(index for index in range(degree, len(points))
                             if knots[index] <= parameter < knots[index + 1])
            work = [points[knot_span - degree + index] for index in range(degree + 1)]
            for level in range(1, degree + 1):
                for index in range(degree, level - 1, -1):
                    knot_index = knot_span - degree + index
                    denominator = knots[knot_index + degree - level + 1] - knots[knot_index]
                    ratio = (parameter - knots[knot_index]) / denominator if denominator else 0.0
                    work[index] = (work[index - 1][0] * (1 - ratio) + work[index][0] * ratio,
                                   work[index - 1][1] * (1 - ratio) + work[index][1] * ratio)
            point = work[degree]
            if not all(math.isfinite(value) for value in point):
                raise ValidationError("Spline interpolation must remain finite.")
            result.append({"x": point[0], "y": point[1]})
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
        if kind not in {"sketch", "point", "line", "circle", "ellipse", "spline", "constraint"}:
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
            arc_fields = {"start_id", "end_id", "start_angle_radians", "sweep_radians"}
            present = arc_fields.intersection(item)
            if present and present != arc_fields:
                raise ValidationError("An elliptical arc needs endpoint references, start angle and sweep together.")
            if present:
                start = reference(item["start_id"], "point", sketch_id)
                end = reference(item["end_id"], "point", sketch_id)
                if start["id"] == end["id"]:
                    raise ValidationError("Elliptical arc endpoints must be distinct.")
                start_angle = number(item["start_angle_radians"])
                sweep = number(item["sweep_radians"])
                if abs(start_angle) > 1e12:
                    raise ValidationError("Elliptical arc start angle is out of range.")
                if abs(sweep) <= 1e-7 or abs(sweep) >= math.tau - 1e-7:
                    raise ValidationError("Elliptical arc sweep must be non-zero and less than one revolution.")
                cosine, sine = math.cos(rotation), math.sin(rotation)
                for point, angle in ((start, start_angle), (end, start_angle + sweep)):
                    local_x, local_y = major * math.cos(angle), minor * math.sin(angle)
                    expected_x = number(item["center_x"]) + local_x * cosine - local_y * sine
                    expected_y = number(item["center_y"]) + local_x * sine + local_y * cosine
                    if not close(number(point["x"]), expected_x) or not close(number(point["y"]), expected_y):
                        raise ValidationError("Elliptical arc endpoints must lie on its analytic curve.")
        elif kind == "spline":
            point_ids = item.get("point_ids")
            if (not isinstance(point_ids, list) or len(point_ids) < 3
                    or any(not isinstance(point_id, str) for point_id in point_ids)
                    or len(set(point_ids)) != len(point_ids)):
                raise ValidationError("An interpolating spline needs at least 3 unique point references.")
            mode = item.get("mode", "interpolating")
            if not isinstance(mode, str) or mode not in {
                "interpolating", "closed", "control", "bezier", "bspline", "nurbs",
            }:
                raise ValidationError("Spline mode must be interpolating, closed, control, bezier, bspline, or nurbs.")
            points = [reference(point_id, "point", sketch_id) for point_id in point_ids]
            if any(math.hypot(number(right["x"]) - number(left["x"]),
                              number(right["y"]) - number(left["y"])) <= 1e-7
                   for left, right in zip(points, points[1:])):
                raise ValidationError("Adjacent spline points must be distinct.")
            if "construction" in item and type(item["construction"]) is not bool:
                raise ValidationError("Spline construction flag must be a boolean.")
            if mode == "closed" and math.hypot(
                    number(points[0]["x"]) - number(points[-1]["x"]),
                    number(points[0]["y"]) - number(points[-1]["y"])) <= 1e-7:
                raise ValidationError("A closed spline's last vertex must differ from its first vertex.")
            if mode == "nurbs":
                degree = item.get("degree")
                if isinstance(degree, bool) or not isinstance(degree, int) or not 1 <= degree < len(point_ids):
                    raise ValidationError("NURBS degree must be an integer from 1 to control point count minus one.")
                weights = item.get("weights")
                if (not isinstance(weights, list) or len(weights) != len(point_ids)
                        or any(number(weight) <= 0 for weight in weights)):
                    raise ValidationError("NURBS needs one finite positive weight per control point.")
                knots = item.get("knots")
                if not isinstance(knots, list) or len(knots) != len(point_ids) + degree + 1:
                    raise ValidationError("NURBS knot count must equal control point count plus degree plus one.")
                knot_values = [number(value) for value in knots]
                if any(left > right for left, right in zip(knot_values, knot_values[1:])):
                    raise ValidationError("NURBS knots must be nondecreasing.")
                if not knot_values[degree] < knot_values[len(point_ids)]:
                    raise ValidationError("NURBS parameter domain must have positive length.")
                domain_start, domain_end = knot_values[degree], knot_values[len(point_ids)]
                if (any(value != domain_start for value in knot_values[:degree + 1])
                        or any(value != domain_end for value in knot_values[len(point_ids):])):
                    raise ValidationError("NURBS knots must be clamped at both endpoints.")
                multiplicity = 1
                for index, (left, right) in enumerate(zip(knot_values, knot_values[1:])):
                    multiplicity = multiplicity + 1 if left == right else 1
                    if multiplicity > degree + 1:
                        raise ValidationError("NURBS knot multiplicity cannot exceed degree plus one.")
                    if (multiplicity > degree and left not in {domain_start, domain_end}
                            and index >= degree and index < len(point_ids)):
                        raise ValidationError("Interior NURBS knot multiplicity cannot exceed the degree.")
            sampled = spline_sample_points(item, by_id)
            if mode == "closed":
                if sampled[0] != sampled[-1]:
                    raise ValidationError("A closed spline must return exactly to its first vertex.")
                area = sum(
                    point["x"] * sampled[index + 1]["y"] - sampled[index + 1]["x"] * point["y"]
                    for index, point in enumerate(sampled[:-1])
                )
                if not math.isfinite(area) or abs(area) <= 1e-12:
                    raise ValidationError("A closed spline must enclose non-zero area.")

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
    ellipse_objects = [item for item in owned if item.get("type") == "ellipse"
                       and not item.get("construction", False)]
    arcs = [item for item in circles if "sweep_radians" in item]
    circles = [item for item in circles if "sweep_radians" not in item]
    ellipse_arcs = [item for item in ellipse_objects if "sweep_radians" in item]
    ellipses = [item for item in ellipse_objects if "sweep_radians" not in item]
    splines = [item for item in owned if item.get("type") == "spline"
               and not item.get("construction", False)]

    closed_splines = [item for item in splines if item.get("mode") == "closed"]
    if closed_splines:
        if (len(closed_splines) != 1 or len(splines) != 1 or lines or arcs or circles
                or ellipse_arcs or ellipses):
            raise ValidationError("A closed spline profile must be the only non-construction sketch geometry.")
        raise ValidationError("Closed spline profiles require curve-capable geometry-kernel support.")

    if not lines and not arcs and not splines:
        if ellipse_arcs:
            raise ValidationError("An elliptical arc must be combined with other edges to form a closed profile.")
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
    edges = lines + arcs + ellipse_arcs + splines
    if circles or ellipses or len(edges) < 3:
        raise ValidationError("A profile operation needs one closed line/arc loop with at least 3 edges.")

    by_id = {item["id"]: item for item in objects}
    adjacency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        start_id, end_id = ((edge["point_ids"][0], edge["point_ids"][-1]) if edge["type"] == "spline"
                            else (edge["start_id"], edge["end_id"]))
        adjacency[start_id].append(edge)
        adjacency[end_id].append(edge)
    if any(len(edges) != 2 for edges in adjacency.values()):
        raise ValidationError("Profile lines must form one closed loop without branches.")

    first = edges[0]
    start_id = first["point_ids"][0] if first["type"] == "spline" else first["start_id"]
    used = {first["id"]}
    start_point = by_id[start_id]
    profile = [{"x": number(start_point["x"]), "y": number(start_point["y"])}]

    def append_edge(edge: dict[str, Any], from_id: str, to_id: str) -> None:
        if edge["type"] == "spline":
            sampled = spline_sample_points(edge, by_id)
            if from_id != edge["point_ids"][0]:
                sampled.reverse()
            profile.extend(sampled[1:])
            return
        if edge["type"] == "line":
            point = by_id[to_id]
            profile.append({"x": number(point["x"]), "y": number(point["y"])})
            return
        sweep = number(edge["sweep_radians"])
        if edge["type"] == "circle":
            center = by_id[edge["center_id"]]
            from_point = by_id[from_id]
            start_angle = math.atan2(from_point["y"] - center["y"], from_point["x"] - center["x"])
            if from_id != edge["start_id"]:
                sweep = -sweep
            steps = max(1, math.ceil(abs(sweep) / (math.pi / 64)))
            radius = number(edge["radius"])
            for index in range(1, steps + 1):
                angle = start_angle + sweep * index / steps
                profile.append({"x": number(center["x"]) + radius * math.cos(angle),
                                "y": number(center["y"]) + radius * math.sin(angle)})
            return
        start_angle = number(edge["start_angle_radians"])
        if from_id != edge["start_id"]:
            start_angle += sweep
            sweep = -sweep
        steps = max(1, math.ceil(abs(sweep) / (math.pi / 64)))
        rotation = number(edge["rotation_radians"])
        cosine, sine = math.cos(rotation), math.sin(rotation)
        for index in range(1, steps + 1):
            angle = start_angle + sweep * index / steps
            local_x = number(edge["major_radius"]) * math.cos(angle)
            local_y = number(edge["minor_radius"]) * math.sin(angle)
            profile.append({"x": number(edge["center_x"]) + local_x * cosine - local_y * sine,
                            "y": number(edge["center_y"]) + local_x * sine + local_y * cosine})

    current_id = first["point_ids"][-1] if first["type"] == "spline" else first["end_id"]
    append_edge(first, start_id, current_id)
    while current_id != start_id:
        if len(used) >= len(edges):
            raise ValidationError("Profile lines must form one closed loop without branches.")
        next_edges = [edge for edge in adjacency[current_id] if edge["id"] not in used]
        if len(next_edges) != 1:
            raise ValidationError("Profile lines must form one closed loop without branches.")
        next_edge = next_edges[0]
        used.add(next_edge["id"])
        next_start, next_end = ((next_edge["point_ids"][0], next_edge["point_ids"][-1])
                                if next_edge["type"] == "spline"
                                else (next_edge["start_id"], next_edge["end_id"]))
        next_id = next_end if next_start == current_id else next_start
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


def closed_spline_profile(
        objects: list[dict[str, Any]], sketch_id: str,
) -> tuple[str, list[dict[str, float]], list[dict[str, float]]] | None:
    """Return sampled geometry and source vertices for a sole periodic spline."""
    sketch = next((item for item in objects if item.get("id") == sketch_id and item.get("type") == "sketch"), None)
    if sketch is None:
        raise ValidationError("A profile operation needs an existing sketch.")
    owned = [item for item in objects if item.get("sketch_id") == sketch_id]
    splines = [item for item in owned if item.get("type") == "spline"
               and not item.get("construction", False)]
    closed_splines = [item for item in splines if item.get("mode") == "closed"]
    if not closed_splines:
        return None
    lines = [item for item in owned if item.get("type") == "line"
             and not item.get("construction", False) and item.get("extent", "segment") == "segment"]
    curves = [item for item in owned if item.get("type") in {"circle", "ellipse"}
              and not item.get("construction", False)]
    if len(closed_splines) != 1 or len(splines) != 1 or lines or curves:
        raise ValidationError("A closed spline profile must be the only non-construction sketch geometry.")
    spline = closed_splines[0]
    by_id = {item["id"]: item for item in objects}
    controls = [{"x": number(by_id[point_id]["x"]), "y": number(by_id[point_id]["y"])}
                for point_id in spline["point_ids"]]
    sampled = spline_sample_points(spline, by_id)
    if sampled[0] == sampled[-1]:
        sampled.pop()
    return sketch["plane"], sampled, controls


def open_path(objects: list[dict[str, Any]], sketch_id: str) -> tuple[str, list[dict[str, float]]]:
    """Return sampled vertices from one interpolating spline or open line chain."""
    sketch = next((item for item in objects if item.get("id") == sketch_id and item.get("type") == "sketch"), None)
    if sketch is None:
        raise ValidationError("A sweep operation needs an existing sketch.")
    owned = [item for item in objects if item.get("sketch_id") == sketch_id]
    lines = [item for item in owned if item.get("type") == "line"
             and not item.get("construction", False) and item.get("extent", "segment") == "segment"]
    splines = [item for item in owned if item.get("type") == "spline"
               and not item.get("construction", False)]
    if any(item.get("type") not in {"point", "line", "spline"} for item in owned):
        raise ValidationError("A sweep path needs at least 3 points and no other sketch geometry.")

    by_id = {item["id"]: item for item in objects}
    point_ids = {item["id"] for item in owned
                 if item.get("type") == "point" and not item.get("construction", False)}
    if not lines and len(splines) == 1:
        spline = splines[0]
        if spline.get("mode") == "closed":
            raise ValidationError("A sweep path must be open.")
        if point_ids != set(spline["point_ids"]):
            raise ValidationError("Every point in a spline sweep sketch must belong to its spline.")
        return sketch["plane"], spline_sample_points(spline, by_id, samples_per_span=4)
    if len(splines) or len(lines) < 2:
        raise ValidationError("A sweep path needs one open spline or a line chain with at least 3 points.")

    adjacency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in lines:
        adjacency[line["start_id"]].append(line)
        adjacency[line["end_id"]].append(line)
    endpoints = [point_id for point_id, edges in adjacency.items() if len(edges) == 1]
    if len(endpoints) != 2 or any(len(edges) not in {1, 2} for edges in adjacency.values()):
        raise ValidationError("A sweep path must be one open line chain without branches or loops.")
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
