"""Exact line, circular-arc and elliptical-arc wires for sketch-driven solids."""
from __future__ import annotations
import math
from .errors import ValidationError
from .sketches import closed_profile, _profile_endpoint_aliases


def _ellipse_point(cx, cy, major, minor, rotation, angle):
    cosine, sine = math.cos(rotation), math.sin(rotation)
    return (cx + major * math.cos(angle) * cosine - minor * math.sin(angle) * sine,
            cy + major * math.cos(angle) * sine + minor * math.sin(angle) * cosine)


def _ellipse_parameter(x, y, cx, cy, major, minor, rotation):
    cosine, sine = math.cos(rotation), math.sin(rotation)
    dx, dy = x - cx, y - cy
    local_x = cosine * dx + sine * dy
    local_y = -sine * dx + cosine * dy
    return math.atan2(local_y / minor, local_x / major)


def sketch_segments(objects, sketch_id):
    owned = [item for item in objects if item.get("sketch_id") == sketch_id
             and not item.get("construction", False) and item.get("type") != "point"]
    arc_types = {"circle", "ellipse"}
    if not any(item.get("type") in arc_types and "sweep_radians" in item for item in owned):
        return None
    if any(not (item.get("type") == "line" and item.get("extent", "segment") == "segment"
                or item.get("type") in arc_types and "sweep_radians" in item) for item in owned):
        return None
    closed_profile(objects, sketch_id)
    edges = _profile_endpoint_aliases(objects, owned)
    by_id = {item["id"]: item for item in objects}
    current = edges[0]["start_id"]
    used, result = set(), []
    while len(used) < len(edges):
        edge = next(item for item in edges if item["id"] not in used
                    and current in (item["start_id"], item["end_id"]))
        forward = current == edge["start_id"]
        end = edge["end_id"] if forward else edge["start_id"]
        start_point, end_point = by_id[current], by_id[end]
        x, y, ex, ey = (float(start_point["x"]), float(start_point["y"]),
                         float(end_point["x"]), float(end_point["y"]))
        if edge.get("type") == "circle":
            center = by_id[edge["center_id"]]
            sweep = edge["sweep_radians"] * (1 if forward else -1)
            start_angle = math.atan2(y - center["y"], x - center["x"])
            angle = start_angle + sweep / 2
            mx = center["x"] + edge["radius"] * math.cos(angle)
            my = center["y"] + edge["radius"] * math.sin(angle)
            result.append([1, x, y, mx, my, ex, ey])
        elif edge.get("type") == "ellipse":
            sweep = edge["sweep_radians"] * (1 if forward else -1)
            rotation = edge["rotation_radians"]
            start_angle = _ellipse_parameter(x, y, edge["center_x"], edge["center_y"],
                                             edge["major_radius"], edge["minor_radius"], rotation)
            if not forward:
                start_angle = _ellipse_parameter(x, y, edge["center_x"], edge["center_y"],
                                                 edge["major_radius"], edge["minor_radius"], rotation)
            midpoint = _ellipse_point(edge["center_x"], edge["center_y"], edge["major_radius"],
                                      edge["minor_radius"], rotation, start_angle + sweep / 2)
            result.append([2, x, y, midpoint[0], midpoint[1], ex, ey,
                           edge["center_x"], edge["center_y"], edge["major_radius"],
                           edge["minor_radius"], rotation, start_angle, sweep])
        else:
            result.append([0, x, y, (x + ex) / 2, (y + ey) / 2, ex, ey])
        used.add(edge["id"])
        current = end
    return segment_values(result)


def segment_values(segments):
    if not isinstance(segments, list) or len(segments) < 3:
        raise ValidationError("An analytic profile needs at least three connected segments.")
    normalized = []
    for segment in segments:
        if (not isinstance(segment, list) or not segment or type(segment[0]) is not int
                or segment[0] not in (0, 1, 2)
                or len(segment) != (14 if segment[0] == 2 else 7)
                or any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or not math.isfinite(value) for value in segment[1:])):
            raise ValidationError("Analytic profile segments need a line/arc tag and finite coordinates.")
        item = [segment[0], *(float(value) for value in segment[1:])]
        if item[0] == 2:
            _, x, y, mx, my, ex, ey, cx, cy, major, minor, rotation, start, sweep = item
            if major < minor or minor <= 1e-7 or abs(sweep) <= 1e-10 or abs(sweep) > math.tau + 1e-9:
                raise ValidationError("Elliptical profile arcs need valid radii and a nonzero sweep up to one turn.")
            tolerance = 1e-7 * max(1.0, major)
            for actual, expected in zip((x, y), _ellipse_point(cx, cy, major, minor, rotation, start)):
                if abs(actual - expected) > tolerance:
                    raise ValidationError("Elliptical arc start must lie on its analytic curve.")
            for actual, expected in zip((ex, ey), _ellipse_point(cx, cy, major, minor, rotation, start + sweep)):
                if abs(actual - expected) > tolerance:
                    raise ValidationError("Elliptical arc end must lie on its analytic curve.")
            for actual, expected in zip((mx, my), _ellipse_point(cx, cy, major, minor, rotation, start + sweep / 2)):
                if abs(actual - expected) > tolerance:
                    raise ValidationError("Elliptical arc midpoint must lie on its analytic curve.")
        normalized.append(item)
    for segment, following in zip(normalized, normalized[1:] + normalized[:1]):
        if segment[5:7] != following[1:3]:
            raise ValidationError("Analytic profile segments must form one exactly connected closed wire.")
    return normalized
