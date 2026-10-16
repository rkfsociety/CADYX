"""Sampled mesh deviation helpers for CADYX scan reverse engineering."""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any

from .errors import ValidationError
from .scan_reverse_common import _EPS, _dot, _sample_points

def _bbox_distance_sq(point: list[float], low: tuple[float, float, float], high: tuple[float, float, float]) -> float:
    total = 0.0
    for axis in range(3):
        if point[axis] < low[axis]:
            total += (low[axis] - point[axis]) ** 2
        elif point[axis] > high[axis]:
            total += (point[axis] - high[axis]) ** 2
    return total

def _point_triangle_distance_sq(point: list[float], a: tuple[float, float, float],
                                b: tuple[float, float, float], c: tuple[float, float, float]) -> float:
    ab = [b[i] - a[i] for i in range(3)]
    ac = [c[i] - a[i] for i in range(3)]
    ap = [point[i] - a[i] for i in range(3)]
    d1, d2 = _dot(ab, ap), _dot(ac, ap)
    if d1 <= 0.0 and d2 <= 0.0:
        return sum(value * value for value in ap)
    bp = [point[i] - b[i] for i in range(3)]
    d3, d4 = _dot(ab, bp), _dot(ac, bp)
    if d3 >= 0.0 and d4 <= d3:
        return sum(value * value for value in bp)
    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        v = d1 / (d1 - d3)
        projection = [a[i] + v * ab[i] for i in range(3)]
        return sum((point[i] - projection[i]) ** 2 for i in range(3))
    cp = [point[i] - c[i] for i in range(3)]
    d5, d6 = _dot(ab, cp), _dot(ac, cp)
    if d6 >= 0.0 and d5 <= d6:
        return sum(value * value for value in cp)
    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        w = d2 / (d2 - d6)
        projection = [a[i] + w * ac[i] for i in range(3)]
        return sum((point[i] - projection[i]) ** 2 for i in range(3))
    va = d3 * d6 - d5 * d4
    if va <= 0.0 and (d4 - d3) >= 0.0 and (d5 - d6) >= 0.0:
        bc = [c[i] - b[i] for i in range(3)]
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        projection = [b[i] + w * bc[i] for i in range(3)]
        return sum((point[i] - projection[i]) ** 2 for i in range(3))
    denominator = va + vb + vc
    if abs(denominator) <= _EPS:
        return min(sum((point[i] - vertex[i]) ** 2 for i in range(3)) for vertex in (a, b, c))
    inverse = 1.0 / denominator
    v, w = vb * inverse, vc * inverse
    projection = [a[i] + ab[i] * v + ac[i] * w for i in range(3)]
    return sum((point[i] - projection[i]) ** 2 for i in range(3))

@dataclass(slots=True)
class _TriangleRecord:
    offset: int
    low: tuple[float, float, float]
    high: tuple[float, float, float]
    center: tuple[float, float, float]

@dataclass(slots=True)
class _BVHNode:
    low: tuple[float, float, float]
    high: tuple[float, float, float]
    left: "_BVHNode | None" = None
    right: "_BVHNode | None" = None
    triangles: list[_TriangleRecord] | None = None

def _triangle_records(mesh: dict[str, list[float] | list[int]]) -> list[_TriangleRecord]:
    positions = mesh["positions"]
    triangles = mesh["triangles"]
    records: list[_TriangleRecord] = []
    assert isinstance(positions, list) and isinstance(triangles, list)
    for offset in range(0, len(triangles), 3):
        vertices = []
        for corner in range(3):
            index = triangles[offset + corner] * 3
            vertices.append((float(positions[index]), float(positions[index + 1]), float(positions[index + 2])))
        low = tuple(min(vertex[axis] for vertex in vertices) for axis in range(3))
        high = tuple(max(vertex[axis] for vertex in vertices) for axis in range(3))
        center = tuple(sum(vertex[axis] for vertex in vertices) / 3.0 for axis in range(3))
        records.append(_TriangleRecord(offset, low, high, center))
    if not records:
        raise ValidationError("Comparison target has no triangles.")
    return records

def _select_median(records: list[_TriangleRecord], start: int, end: int, axis: int) -> int:
    middle = start + (end - start) // 2
    low, high = start, end
    while low < high:
        pivot_index = low + (high - low) // 2
        pivot = sorted((records[low].center[axis], records[pivot_index].center[axis],
                        records[high - 1].center[axis]))[1]
        less, current, greater = low, low, high
        while current < greater:
            center = records[current].center[axis]
            if center < pivot:
                records[less], records[current] = records[current], records[less]
                less += 1
                current += 1
            elif center > pivot:
                greater -= 1
                records[current], records[greater] = records[greater], records[current]
            else:
                current += 1
        if middle < less:
            high = less
        elif middle >= greater:
            low = greater
        else:
            break
    return middle


def _build_bvh(records: list[_TriangleRecord], start: int = 0, end: int | None = None) -> _BVHNode:
    if end is None:
        end = len(records)
    count = end - start
    low = tuple(min(records[index].low[axis] for index in range(start, end)) for axis in range(3))
    high = tuple(max(records[index].high[axis] for index in range(start, end)) for axis in range(3))
    if count <= 12:
        return _BVHNode(low, high, triangles=records[start:end])
    spans = [high[axis] - low[axis] for axis in range(3)]
    axis = max(range(3), key=spans.__getitem__)
    middle = _select_median(records, start, end, axis)
    return _BVHNode(
        low, high,
        left=_build_bvh(records, start, middle),
        right=_build_bvh(records, middle, end),
    )

def _nearest_sq(node: _BVHNode, point: list[float], positions: list[float], triangles: list[int],
                best: float = math.inf) -> float:
    if _bbox_distance_sq(point, node.low, node.high) >= best:
        return best
    if node.triangles is not None:
        for record in node.triangles:
            if _bbox_distance_sq(point, record.low, record.high) < best:
                offset = record.offset
                a_index, b_index, c_index = (triangles[offset + corner] * 3 for corner in range(3))
                a = (float(positions[a_index]), float(positions[a_index + 1]), float(positions[a_index + 2]))
                b = (float(positions[b_index]), float(positions[b_index + 1]), float(positions[b_index + 2]))
                c = (float(positions[c_index]), float(positions[c_index + 1]), float(positions[c_index + 2]))
                best = min(best, _point_triangle_distance_sq(point, a, b, c))
        return best
    assert node.left is not None and node.right is not None
    left_distance = _bbox_distance_sq(point, node.left.low, node.left.high)
    right_distance = _bbox_distance_sq(point, node.right.low, node.right.high)
    first, second = (node.left, node.right) if left_distance <= right_distance else (node.right, node.left)
    best = _nearest_sq(first, point, positions, triangles, best)
    return _nearest_sq(second, point, positions, triangles, best)

def _deviation(source_positions: list[float], target_mesh: dict[str, list[float] | list[int]],
               max_samples: int, tolerance: float | None) -> dict[str, Any]:
    points = _sample_points(source_positions, max_samples)
    bvh = _build_bvh(_triangle_records(target_mesh))
    positions = target_mesh["positions"]
    triangles = target_mesh["triangles"]
    assert isinstance(positions, list) and isinstance(triangles, list)
    distances = [math.sqrt(max(0.0, _nearest_sq(bvh, point, positions, triangles))) for point in points]
    ordered = sorted(distances)
    p95 = ordered[min(len(ordered) - 1, max(0, math.ceil(len(ordered) * 0.95) - 1))]
    result: dict[str, Any] = {
        "sample_count": len(distances),
        "mean": statistics.fmean(distances),
        "rms": math.sqrt(statistics.fmean(distance * distance for distance in distances)),
        "p95": p95,
        "max": ordered[-1],
    }
    worst = sorted(zip(distances, points), key=lambda item: item[0], reverse=True)[:10]
    result["worst_samples"] = [{"distance": distance, "point": point} for distance, point in worst]
    if tolerance is not None:
        within = sum(distance <= tolerance for distance in distances)
        result.update({"tolerance": tolerance, "within_tolerance": within,
                       "within_tolerance_percent": within * 100.0 / len(distances)})
    return result
