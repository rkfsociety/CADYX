"""Revision-bound, typed filtering of inspected OCCT face and edge descriptors."""
from __future__ import annotations

import math
from typing import Any

from .errors import ValidationError

_ALLOWED = {"entity", "surface", "curve", "radius", "diameter", "area", "length",
            "orientation", "normal", "position", "center_in_box", "axis", "max_results"}


def _number(value: Any, name: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValidationError(f"{name} must be finite numeric.")
    return float(value)


def _vec(value: Any, name: str, *, normalize: bool = False) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValidationError(f"{name} must be XYZ array.")
    point = [_number(v, name) for v in value]
    if normalize:
        length = math.sqrt(sum(v * v for v in point))
        if length < 1e-12:
            raise ValidationError(f"{name} must be nonzero.")
        return [v / length for v in point]
    return point


def _direction(spec: Any, name: str) -> tuple[list[float], float, str]:
    if not isinstance(spec, dict) or set(spec) - {"vector", "angle_tolerance_degrees", "mode"} or "vector" not in spec:
        raise ValidationError(f"{name} requires vector, optional mode and angle_tolerance_degrees.")
    vec = _vec(spec["vector"], name, normalize=True)
    angle = _number(spec.get("angle_tolerance_degrees", 1), f"{name}.angle_tolerance_degrees")
    mode = spec.get("mode", "parallel")
    if not 0 <= angle <= 90 or mode not in ("aligned", "parallel", "perpendicular"):
        raise ValidationError(f"{name} has invalid angle or mode.")
    return vec, math.sin(math.radians(angle)), mode


def _aligned(actual: Any, target: tuple[list[float], float, str]) -> bool:
    try:
        direction = _vec(actual, "topology direction", normalize=True)
    except ValidationError:
        return False
    vector, sine, mode = target
    dot = sum(a*b for a, b in zip(direction, vector, strict=True))
    if mode == "perpendicular":
        return abs(dot) <= sine + 1e-12
    return (dot if mode == "aligned" else abs(dot)) >= math.sqrt(max(0, 1-sine*sine)) - 1e-12


def _axis_distance(point: list[float], origin: list[float], direction: list[float]) -> float:
    delta = [a-b for a, b in zip(point, origin, strict=True)]
    projection = sum(a*b for a, b in zip(delta, direction, strict=True))
    return math.sqrt(sum((a-projection*b)**2 for a, b in zip(delta, direction, strict=True)))


def query_topology(topology: dict[str, Any], query: Any) -> dict[str, Any]:
    """Return sorted candidates, never choosing implicitly on ambiguous results."""
    if not isinstance(query, dict) or set(query) - _ALLOWED:
        raise ValidationError("Unknown or invalid topology query fields.")
    entity = query.get("entity")
    if entity not in ("face", "edge"):
        raise ValidationError("entity must be face or edge.")
    if any(key in query for key in (("curve", "length") if entity == "face" else ("surface", "area", "normal"))):
        raise ValidationError("Filter is not supported for this entity kind.")
    for key in ("surface", "curve"):
        if key in query and (not isinstance(query[key], str) or not query[key]):
            raise ValidationError(f"{key} must be a nonempty string.")
    metrics = {}
    for key in ("radius", "diameter", "area", "length"):
        if key not in query:
            continue
        spec = query[key]
        if not isinstance(spec, dict) or set(spec) != {"value", "tolerance"}:
            raise ValidationError(f"{key} requires value and tolerance.")
        value, tolerance = _number(spec["value"], key), _number(spec["tolerance"], key)
        if value < 0 or tolerance < 0:
            raise ValidationError(f"{key} must be nonnegative.")
        metrics[key] = (value, tolerance)
    orientations = {key: _direction(query[key], key) for key in ("orientation", "normal") if key in query}
    if "position" in query:
        spec = query["position"]
        if not isinstance(spec, dict) or set(spec) != {"point", "tolerance"}:
            raise ValidationError("position requires point and tolerance.")
        target_point = _vec(spec["point"], "position.point")
        point_tolerance = _number(spec["tolerance"], "position.tolerance")
        if point_tolerance < 0:
            raise ValidationError("position tolerance must be nonnegative.")
    else:
        target_point, point_tolerance = None, None
    if "center_in_box" in query:
        spec = query["center_in_box"]
        if not isinstance(spec, dict) or set(spec) != {"min", "max"}:
            raise ValidationError("center_in_box requires min and max.")
        low, high = _vec(spec["min"], "min"), _vec(spec["max"], "max")
        if any(a > b for a,b in zip(low, high, strict=True)):
            raise ValidationError("center_in_box requires ordered bounds.")
    else:
        low, high = None, None
    if "axis" in query:
        spec = query["axis"]
        if not isinstance(spec, dict) or set(spec) != {"origin", "direction", "tolerance"}:
            raise ValidationError("axis requires origin, direction, and tolerance.")
        axis_origin = _vec(spec["origin"], "axis.origin")
        axis_direction = _vec(spec["direction"], "axis.direction", normalize=True)
        axis_tolerance = _number(spec["tolerance"], "axis.tolerance")
        if axis_tolerance < 0:
            raise ValidationError("axis tolerance must be nonnegative.")
    else:
        axis_origin, axis_direction, axis_tolerance = None, None, None
    limit = query.get("max_results", 100)
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValidationError("max_results must be between 1 and 500.")
    items = topology.get("faces" if entity == "face" else "edges")
    if not isinstance(items, list):
        raise ValidationError("Topology does not expose this entity collection.")
    matches = []
    for item in items:
        if not isinstance(item, dict) or type(item.get("index")) is not int:
            continue
        if any(item.get(key) != query[key] for key in ("surface", "curve") if key in query):
            continue
        if any(type(item.get("radius" if key == "diameter" else key)) not in (int, float)
               or not math.isfinite(item["radius" if key == "diameter" else key])
               or abs(item["radius" if key == "diameter" else key] * (2 if key == "diameter" else 1) - value) > tol
               for key, (value, tol) in metrics.items()):
            continue
        if any(not _aligned(item.get("normal" if key == "normal" else
                                 ("axis_direction" if "axis_direction" in item else
                                  "normal" if entity == "face" else "direction")), target)
               for key, target in orientations.items()):
            continue
        center = item.get("center")
        center_valid = (isinstance(center, list) and len(center) == 3
                        and all(type(v) in (int, float) and math.isfinite(v) for v in center))
        if target_point is not None and (not center_valid or math.dist(center, target_point) > point_tolerance):
            continue
        if low is not None and (not center_valid or any(not a <= v <= b for v,a,b in zip(center,low,high,strict=True))):
            continue
        if axis_origin is not None:
            origin = item.get("axis_origin", center)
            direction = item.get("axis_direction", item.get("direction"))
            if (not isinstance(origin, list) or len(origin) != 3
                    or not all(type(v) in (int,float) and math.isfinite(v) for v in origin)
                    or not _aligned(direction, (axis_direction, 0.0, "parallel"))
                    or _axis_distance(origin, axis_origin, axis_direction) > axis_tolerance):
                continue
        matches.append({key: item[key] for key in (
            "index", "semantic_key", "surface", "curve", "center", "radius", "area", "length",
            "normal", "direction", "axis_origin", "axis_direction") if key in item})
    matches.sort(key=lambda item: item["index"])
    count = len(matches)
    return {"entity": entity, "status": "no_match" if count == 0 else "unique" if count == 1 else "ambiguous",
            "ambiguous": count > 1, "match_count": count, "truncated": count > limit,
            "candidates": [{**item, "confidence": "descriptor_within_tolerance"} for item in matches[:limit]]}
