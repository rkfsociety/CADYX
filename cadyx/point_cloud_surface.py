"""Local-plane cloud tools; distances are source coordinates, never inferred mm."""
from __future__ import annotations

import math
from itertools import product
from typing import Any

from .errors import ValidationError
from .scan_reverse_alignment import _jacobi_eigenvectors

MAX_SURFACE_COMPARISONS = 20_000_000


def surface_points(points: list[tuple[float, float, float]], method: str,
                   parameters: dict[str, Any]) -> list[tuple[float, float, float]]:
    """Retain one representative per coincident patch or smooth along its normal.

    Updates are synchronous. Sparse, collinear and non-planar neighborhoods are
    left untouched; neither tool connects disconnected regions or creates points.
    """
    current = list(points)
    radius = parameters["radius"]
    offsets = list(product((-1, 0, 1), repeat=3))
    comparisons = 0
    for _ in range(parameters.get("iterations", 1)):
        buckets: dict[tuple[int, ...], list[int]] = {}
        for i, point in enumerate(current):
            try:
                key = tuple(math.floor(v / radius) for v in point)
            except (ValueError, OverflowError) as exc:
                raise ValidationError("Surface radius is too small for these coordinates.") from exc
            buckets.setdefault(key, []).append(i)
        removed: set[int] = set()
        updated = list(current)
        for i, point in enumerate(current):
            if i in removed:
                continue
            key = tuple(math.floor(v / radius) for v in point)
            nearby = []
            for offset in offsets:
                for j in buckets.get(tuple(a + b for a, b in zip(key, offset)), ()):
                    comparisons += 1
                    if comparisons > MAX_SURFACE_COMPARISONS:
                        raise ValidationError("Surface processing exceeds its 20M comparison budget; reduce radius or iterations.", code="RESOURCE_LIMIT")
                    distance = math.dist(point, current[j])
                    if distance <= radius:
                        nearby.append((distance, j))
            # Bound each covariance fit while retaining deterministic spatial support.
            neighbors = [j for _, j in sorted(nearby)[:48]]
            if len(neighbors) < 6:
                continue
            center = [point[a] + math.fsum(current[j][a] - point[a] for j in neighbors) / len(neighbors) for a in range(3)]
            covariance = [[math.fsum(((current[j][a] - center[a]) / radius) * ((current[j][b] - center[b]) / radius)
                                    for j in neighbors) / len(neighbors)
                           for b in range(3)] for a in range(3)]
            values, axes = _jacobi_eigenvectors(covariance)
            if values[0] <= 0 or values[1] <= values[0] * 1e-6 or values[2] > values[1] * .25:
                continue
            normal = axes[-1]
            if method == "smooth":
                residual = sum((point[a] - center[a]) * normal[a] for a in range(3))
                updated[i] = tuple(point[a] - parameters["strength"] / 100 * residual * normal[a]
                                   for a in range(3))
            else:
                # Keep the first point. Only points coincident in the tangent plane
                # and closer than the normal threshold can be removed by it.
                for _, j in nearby:
                    if j <= i or j in removed:
                        continue
                    delta = [current[j][a] - point[a] for a in range(3)]
                    vertical = sum(delta[a] * normal[a] for a in range(3))
                    lateral = math.hypot(*(delta[a] - vertical * normal[a] for a in range(3)))
                    if abs(vertical) < parameters["distance"] and lateral <= parameters["tangential_distance"]:
                        removed.add(j)
        current = [point for i, point in enumerate(updated) if i not in removed]
    return current
