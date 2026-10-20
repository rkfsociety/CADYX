"""Deterministic point-only processing in source coordinates, without mesh reconstruction."""
from __future__ import annotations

import math
from itertools import product
from typing import Any

from .errors import ValidationError


def normalize_cloud_parameters(method: str, parameters: Any) -> dict[str, Any]:
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


def process_points(points: list[tuple[float, float, float]], method: str, parameters: dict[str, Any]) -> list[tuple[float, float, float]]:
    p = normalize_cloud_parameters(method, parameters)
    if method == "deduplicate":
        result = list(dict.fromkeys(points))
    elif method == "crop":
        result = [v for v in points if all(lo <= x <= hi for x, lo, hi in zip(v, p["min"], p["max"]))]
    else:
        size = p.get("radius", p.get("voxel_size"))
        buckets: dict[tuple[int, ...], list[int]] = {}
        for index, point in enumerate(points):
            try:
                key = tuple(math.floor(v / size) for v in point)
            except (OverflowError, ValueError) as exc:
                raise ValidationError("Point-cloud distance is too small for these source coordinates.") from exc
            buckets.setdefault(key, []).append(index)
        if method == "voxel_downsample":
            result = [tuple(math.fsum(points[i][axis] for i in indices) / len(indices) for axis in range(3))
                      for indices in buckets.values()]
        else:
            result = []
            offsets = list(product((-1, 0, 1), repeat=3))
            # Radius membership excludes the query point; duplicates remain distinct
            # until the explicit deduplication operation is requested.
            comparisons = 0
            for index, point in enumerate(points):
                key = tuple(math.floor(v / size) for v in point)
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
