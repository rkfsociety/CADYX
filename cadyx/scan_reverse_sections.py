"""Triangle-mesh section extraction for CADYX scan reverse engineering."""
from __future__ import annotations

import math
import statistics
from typing import Any

from .errors import ValidationError
from .scan_reverse_common import _EPS, _bounds, _finite, _positive, _span

def _axis_index(axis: str) -> int:
    return {"X": 0, "Y": 1, "Z": 2}[axis]

def _point_key(point: list[float], tolerance: float) -> tuple[int, int, int]:
    return tuple(int(round(value / tolerance)) for value in point)  # type: ignore[return-value]

def _intersections_for_triangle(points: list[list[float]], axis: int, offset: float, epsilon: float) -> list[list[float]]:
    hits: list[list[float]] = []
    for first, second in ((0, 1), (1, 2), (2, 0)):
        a, b = points[first], points[second]
        da, db = a[axis] - offset, b[axis] - offset
        if abs(da) <= epsilon and abs(db) <= epsilon:
            continue
        if abs(da) <= epsilon:
            candidate = a[:]
        elif abs(db) <= epsilon:
            candidate = b[:]
        elif da * db < 0:
            ratio = da / (da - db)
            candidate = [a[index] + ratio * (b[index] - a[index]) for index in range(3)]
        else:
            continue
        if not any(math.dist(candidate, existing) <= epsilon for existing in hits):
            hits.append(candidate)
    if len(hits) <= 2:
        return hits
    pair = max(((i, j) for i in range(len(hits)) for j in range(i + 1, len(hits))),
               key=lambda indices: math.dist(hits[indices[0]], hits[indices[1]]))
    return [hits[pair[0]], hits[pair[1]]]

def _slice_mesh(positions: list[float], triangles: list[int], axis: int, offset: float,
                tolerance: float) -> tuple[list[list[list[float]]], int]:
    representatives: dict[tuple[int, int, int], list[float]] = {}
    counts: dict[tuple[int, int, int], int] = {}
    edges: set[tuple[tuple[int, int, int], tuple[int, int, int]]] = set()
    epsilon = max(tolerance * 0.25, 1e-9)
    for index in range(0, len(triangles), 3):
        triangle_points = [positions[triangles[index + corner] * 3: triangles[index + corner] * 3 + 3]
                           for corner in range(3)]
        values = [point[axis] for point in triangle_points]
        if offset < min(values) - epsilon or offset > max(values) + epsilon:
            continue
        hits = _intersections_for_triangle(triangle_points, axis, offset, epsilon)
        if len(hits) != 2 or math.dist(hits[0], hits[1]) <= epsilon:
            continue
        keys = [_point_key(point, tolerance) for point in hits]
        if keys[0] == keys[1]:
            continue
        for key, point in zip(keys, hits, strict=True):
            if key not in representatives:
                representatives[key] = point[:]
                counts[key] = 1
            else:
                counts[key] += 1
                count = counts[key]
                representatives[key] = [representatives[key][axis_index] + (point[axis_index] - representatives[key][axis_index]) / count
                                        for axis_index in range(3)]
        edge = tuple(sorted(keys))
        edges.add(edge)  # type: ignore[arg-type]

    adjacency: dict[tuple[int, int, int], set[tuple[int, int, int]]] = {}
    for first, second in edges:
        adjacency.setdefault(first, set()).add(second)
        adjacency.setdefault(second, set()).add(first)

    remaining = set(adjacency)
    loops: list[list[list[float]]] = []
    rejected = 0
    while remaining:
        seed = next(iter(remaining))
        stack, component = [seed], set()
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            stack.extend(adjacency.get(node, ()))
        remaining.difference_update(component)
        if len(component) < 3 or any(len(adjacency[node]) != 2 for node in component):
            rejected += 1
            continue
        start = min(component)
        ordered = [start]
        previous = None
        current = start
        for _ in range(len(component) + 1):
            neighbors = sorted(adjacency[current])
            candidate = neighbors[0] if neighbors[0] != previous else neighbors[1]
            if candidate == start:
                break
            if candidate in ordered:
                ordered = []
                break
            ordered.append(candidate)
            previous, current = current, candidate
        if len(ordered) != len(component):
            rejected += 1
            continue
        loops.append([representatives[key] for key in ordered])
    return loops, rejected

def _project(point: list[float], axis: str) -> tuple[float, float]:
    if axis == "Y":
        return point[0], point[2]
    if axis == "Z":
        return point[0], point[1]
    raise ValidationError("Scan section normal_axis currently supports Y or Z.")

def _point_segment_distance(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    denominator = dx * dx + dy * dy
    if denominator <= _EPS:
        return math.dist(point, start)
    ratio = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / denominator))
    projection = (start[0] + ratio * dx, start[1] + ratio * dy)
    return math.dist(point, projection)

def _rdp(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    if len(points) <= 2:
        return points[:]
    maximum, split = -1.0, 0
    for index in range(1, len(points) - 1):
        distance = _point_segment_distance(points[index], points[0], points[-1])
        if distance > maximum:
            maximum, split = distance, index
    if maximum <= tolerance:
        return [points[0], points[-1]]
    return _rdp(points[:split + 1], tolerance)[:-1] + _rdp(points[split:], tolerance)

def _simplify_closed(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    if tolerance <= 0 or len(points) <= 4:
        return points[:]
    anchor = max(range(1, len(points)), key=lambda index: math.dist(points[0], points[index]))
    first = _rdp(points[:anchor + 1], tolerance)
    second = _rdp(points[anchor:] + [points[0]], tolerance)
    merged = first[:-1] + second[:-1]
    return merged if len(merged) >= 3 else points[:]

def _resample_closed(points: list[tuple[float, float]], count: int) -> list[tuple[float, float]]:
    if len(points) <= count:
        return points
    lengths = [math.dist(points[index], points[(index + 1) % len(points)]) for index in range(len(points))]
    total = sum(lengths)
    if total <= 1e-12:
        raise ValidationError("Scan section contour has zero perimeter.")
    cumulative = [0.0]
    for length in lengths:
        cumulative.append(cumulative[-1] + length)
    result: list[tuple[float, float]] = []
    segment = 0
    for sample in range(count):
        target = total * sample / count
        while segment + 1 < len(cumulative) and cumulative[segment + 1] < target:
            segment += 1
        start, end = points[segment], points[(segment + 1) % len(points)]
        length = lengths[segment]
        ratio = 0.0 if length <= _EPS else (target - cumulative[segment]) / length
        result.append((start[0] + ratio * (end[0] - start[0]), start[1] + ratio * (end[1] - start[1])))
    return result

def _contour_metrics(points: list[tuple[float, float]]) -> dict[str, Any]:
    signed_twice_area = 0.0
    perimeter = 0.0
    min_x = min(point[0] for point in points)
    max_x = max(point[0] for point in points)
    min_y = min(point[1] for point in points)
    max_y = max(point[1] for point in points)
    centroid_x = centroid_y = 0.0
    for index, first in enumerate(points):
        second = points[(index + 1) % len(points)]
        cross = first[0] * second[1] - second[0] * first[1]
        signed_twice_area += cross
        centroid_x += (first[0] + second[0]) * cross
        centroid_y += (first[1] + second[1]) * cross
        perimeter += math.dist(first, second)
    area = signed_twice_area / 2.0
    if abs(signed_twice_area) > 1e-15:
        centroid = [centroid_x / (3.0 * signed_twice_area), centroid_y / (3.0 * signed_twice_area)]
    else:
        centroid = [statistics.fmean(point[0] for point in points), statistics.fmean(point[1] for point in points)]
    return {
        "area": abs(area),
        "signed_area": area,
        "perimeter": perimeter,
        "centroid": centroid,
        "bounds": {"min": [min_x, min_y], "max": [max_x, max_y]},
        "point_count": len(points),
    }

def _section_options(command: dict[str, Any], positions: list[float]) -> dict[str, Any]:
    axis = command.get("normal_axis", "Y")
    if not isinstance(axis, str) or axis.upper() not in {"Y", "Z"}:
        raise ValidationError("normal_axis must be Y or Z for editable scan sections.")
    axis = axis.upper()
    mesh_bounds = _bounds(positions)
    diagonal = math.sqrt(sum(value * value for value in _span(mesh_bounds)))
    default_join = max(diagonal * 1e-5, 1e-6)
    join = _positive(command.get("join_tolerance", default_join), "join_tolerance")
    simplify = _finite(command.get("simplify_tolerance", 0.0), "simplify_tolerance")
    if simplify < 0:
        raise ValidationError("simplify_tolerance cannot be negative.")
    max_points = command.get("max_points", 512)
    max_contours = command.get("max_contours", 16)
    if type(max_points) is not int or not 16 <= max_points <= 4096:
        raise ValidationError("max_points must be an integer from 16 to 4096.")
    if type(max_contours) is not int or not 1 <= max_contours <= 16:
        raise ValidationError("max_contours must be an integer from 1 to 16.")
    min_area = _finite(command.get("min_area", 0.0), "min_area")
    min_perimeter = _finite(command.get("min_perimeter", 0.0), "min_perimeter")
    if min_area < 0 or min_perimeter < 0:
        raise ValidationError("min_area and min_perimeter cannot be negative.")
    return {
        "axis": axis, "join_tolerance": join, "simplify_tolerance": simplify,
        "max_points": max_points, "max_contours": max_contours,
        "min_area": min_area, "min_perimeter": min_perimeter,
    }
