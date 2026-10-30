"""Deterministic point-only processing in source coordinates, without mesh reconstruction."""
from __future__ import annotations

import math
from itertools import product
from typing import Any

from .errors import ValidationError


def _mean_coordinate(values: list[float]) -> float:
    """Preserve subnormal centroids; scale first only if the sum overflows."""
    try:
        return math.fsum(values) / len(values)
    except OverflowError:
        return math.fsum(value / len(values) for value in values)


def _voxel_index(value: float, size: float) -> int:
    """Keep finite coordinates addressable when their float quotient overflows."""
    quotient = value / size
    if math.isfinite(quotient):
        return math.floor(quotient)
    if not math.isfinite(value):
        raise ValueError("Non-finite point coordinate.")
    # Float division can overflow even though both operands are finite.
    # Exact integer ratios preserve the world-origin-aligned voxel boundary.
    numerator, denominator = value.as_integer_ratio()
    size_numerator, size_denominator = size.as_integer_ratio()
    return (numerator * size_denominator) // (denominator * size_numerator)


def normalize_cloud_parameters(method: str, parameters: Any) -> dict[str, Any]:
    if method == "simplify":
        defaults = {"strategy": "uniform", "ratio": 40.}
        if not isinstance(parameters, dict) or set(parameters) - defaults.keys():
            raise ValidationError("Simplification accepts only strategy and ratio.")
        p = {**defaults, **parameters}
        if not isinstance(p["strategy"], str) or p["strategy"] not in {"uniform", "geometric"}:
            raise ValidationError("Simplification strategy must be uniform or geometric.")
        if type(p["ratio"]) not in (int, float) or not math.isfinite(p["ratio"]) or not 0 < p["ratio"] <= 95:
            raise ValidationError("Simplification ratio must be in (0,95].")
        return p
    if method == "isolation_filter":
        if not isinstance(parameters, dict) or set(parameters) - {"rate", "component_factor"}:
            raise ValidationError("Isolation filtering accepts only rate and component_factor.")
        rate = parameters.get("rate", 15.)
        if type(rate) not in (int, float) or not math.isfinite(rate) or not 0 < rate <= 50:
            raise ValidationError("Isolation rate must be a positive percentage at most 50.")
        component_factor = parameters.get("component_factor", 3.)
        if (type(component_factor) not in (int, float) or not math.isfinite(component_factor)
                or not 2 <= component_factor <= 12):
            raise ValidationError("Isolation component_factor must be between 2 and 12 median neighbor spacings.")
        return {"rate": rate, "component_factor": component_factor}
    if isinstance(method, str) and method in {"remove_overlaps", "smooth"}:
        defaults = ({"distance": .56, "radius": 2., "tangential_distance": .1}
                    if method == "remove_overlaps" else {"strength": 10., "iterations": 10, "radius": 1.})
        if not isinstance(parameters, dict) or set(parameters) - defaults.keys():
            raise ValidationError("Unknown local-surface point-cloud parameters.")
        p = {**defaults, **parameters}
        for key, value in p.items():
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValidationError("Surface parameters must be positive finite numbers.")
        if method == "smooth":
            if type(p["iterations"]) is not int or not 1 <= p["iterations"] <= 100 or p["strength"] > 100:
                raise ValidationError("Smoothing requires strength in (0,100] and integer iterations in 1..100.")
        elif p["radius"] <= max(p["distance"], p["tangential_distance"]):
            raise ValidationError("Surface radius must exceed both overlap tolerances.")
        return p
    allowed = {"deduplicate": set(), "radius_filter": {"radius", "min_neighbors"},
               "voxel_downsample": {"voxel_size"}, "crop": {"min", "max"}}
    if not isinstance(method, str) or method not in allowed or not isinstance(parameters, dict) or set(parameters) != allowed[method]:
        raise ValidationError("Point-cloud method and its exact parameters are required.")
    p = dict(parameters)
    for key in ("radius", "voxel_size"):
        if key in p and (type(p[key]) not in (int, float) or not math.isfinite(p[key]) or p[key] <= 0):
            raise ValidationError("Point-cloud distance must be positive and finite.")
    if method == "radius_filter" and (type(p["min_neighbors"]) is not int or not 1 <= p["min_neighbors"] <= 100):
        raise ValidationError("min_neighbors must be an integer from 1 to 100, excluding the point itself.")
    if method == "crop":
        for key in ("min", "max"):
            if not isinstance(p[key], list) or len(p[key]) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in p[key]):
                raise ValidationError("Crop bounds must contain three finite source coordinates.")
        if any(a >= b for a, b in zip(p["min"], p["max"])):
            raise ValidationError("Crop minimum must be below maximum on every axis.")
    return p


def process_points(points: list[tuple[float, float, float]], method: str, parameters: dict[str, Any],
                   *, diagnostics: dict[str, Any] | None = None) -> list[tuple[float, float, float]]:
    p = normalize_cloud_parameters(method, parameters)
    if method == "simplify":
        target = max(3, math.floor(len(points) * (100 - p["ratio"]) / 100))
        if p["strategy"] == "uniform":
            result = [points[(i * len(points)) // target] for i in range(target)]
        else:
            bounds = [max(point[a] for point in points) - min(point[a] for point in points) for a in range(3)]
            high = math.hypot(*bounds) or 1.
            # Finite coordinates can span more than the float range. Normalize
            # only the voxel keys, retaining original coordinates for centroids.
            bucket_points = points
            if not math.isfinite(high):
                scale = max(abs(value) for point in points for value in point)
                bucket_points = [tuple(value / scale for value in point) for point in points]
                bounds = [max(point[a] for point in bucket_points) - min(point[a] for point in bucket_points)
                          for a in range(3)]
                high = math.hypot(*bounds) or 1.
            low = high / (len(points) ** (1 / 3) * 4)
            best: list[tuple[float, float, float]] | None = None
            best_gap = math.inf
            for _ in range(18):
                size = low + (high - low) / 2
                buckets: dict[tuple[int, int, int], list[tuple[float, float, float]]] = {}
                for point, bucket_point in zip(points, bucket_points):
                    key = tuple(math.floor(value / size) for value in bucket_point)
                    buckets.setdefault(key, []).append(point)
                candidate = [tuple(_mean_coordinate([point[a] for point in group]) for a in range(3))
                             for group in buckets.values()]
                if len(candidate) >= 3 and abs(len(candidate) - target) < best_gap:
                    best, best_gap = candidate, abs(len(candidate) - target)
                if len(candidate) > target:
                    low = size
                else:
                    high = size
            if best is None:
                raise ValidationError("Geometric simplification must retain at least three voxels.")
            result = best
    elif method in {"remove_overlaps", "smooth", "isolation_filter"}:
        from .point_cloud_surface import isolation_points, surface_points
        result = (isolation_points(points, p["rate"], p["component_factor"], diagnostics)
                  if method == "isolation_filter" else surface_points(points, method, p))
    elif method == "deduplicate":
        result = list(dict.fromkeys(points))
    elif method == "crop":
        result = [v for v in points if all(lo <= x <= hi for x, lo, hi in zip(v, p["min"], p["max"]))]
    else:
        size = p.get("radius", p.get("voxel_size"))
        buckets: dict[tuple[int, ...], list[int]] = {}
        for index, point in enumerate(points):
            try:
                key = tuple(_voxel_index(v, size) for v in point)
            except (OverflowError, ValueError) as exc:
                raise ValidationError("Point-cloud distance is too small for these source coordinates.") from exc
            buckets.setdefault(key, []).append(index)
        if method == "voxel_downsample":
            result = [tuple(_mean_coordinate([points[i][axis] for i in indices]) for axis in range(3))
                      for indices in buckets.values()]
        else:
            result = []
            offsets = list(product((-1, 0, 1), repeat=3))
            # Radius membership excludes the query point; duplicates remain distinct
            # until the explicit deduplication operation is requested.
            comparisons = 0
            for index, point in enumerate(points):
                key = tuple(_voxel_index(v, size) for v in point)
                count = 0
                for offset in offsets:
                    for other in buckets.get(tuple(a + b for a, b in zip(key, offset)), ()):
                        comparisons += 1
                        if comparisons > 20_000_000:
                            raise ValidationError("Radius filtering exceeds its 20M comparison budget; reduce the radius or downsample first.", code="RESOURCE_LIMIT")
                        if other != index and math.dist(point, points[other]) <= size:
                            count += 1
                            if count >= p["min_neighbors"]:
                                break
                    if count >= p["min_neighbors"]:
                        break
                if count >= p["min_neighbors"]:
                    result.append(point)
    if len(result) < 3:
        raise ValidationError("Point-cloud processing must retain at least three points; no change was saved.")
    return result
