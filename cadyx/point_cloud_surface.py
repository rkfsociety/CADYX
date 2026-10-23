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


def isolation_points(points: list[tuple[float, float, float]], rate: float,
                     component_factor: float = 3.,
                     diagnostics: dict[str, Any] | None = None) -> list[tuple[float, float, float]]:
    """Keep the largest scan component and remove sparse points within the rate cap."""
    bounds = [max(p[a] for p in points) - min(p[a] for p in points) for a in range(3)]
    # Captured clouds are predominantly sampled surfaces. Surface-density cells
    # reduce candidate comparisons; shell expansion still gives exact 3-D nearest
    # neighbors for volumetric inputs and sparse outliers.
    cell_size = max(bounds) / max(1., len(points) ** .5)
    if not math.isfinite(cell_size) or cell_size <= 1e-12:
        cell_size = 1.
    buckets: dict[tuple[int, int, int], list[int]] = {}
    keys = []
    for i, point in enumerate(points):
        try:
            key = tuple(math.floor(value / cell_size) for value in point)
        except (ValueError, OverflowError) as exc:
            raise ValidationError("Isolation grid is too fine for these coordinates.") from exc
        keys.append(key)
        buckets.setdefault(key, []).append(i)
    nearest = [math.inf] * len(points)
    comparisons = 0
    for i, point in enumerate(points):
        key = keys[i]
        best = math.inf
        shell = 0
        while shell * cell_size - cell_size < best:
            offsets = ((0, 0, 0),) if shell == 0 else (
                (x, y, z) for x in range(-shell, shell + 1)
                for y in range(-shell, shell + 1) for z in range(-shell, shell + 1)
                if max(abs(x), abs(y), abs(z)) == shell
            )
            for offset in offsets:
                neighbor_key = tuple(key[a] + offset[a] for a in range(3))
                for j in buckets.get(neighbor_key, ()):
                    if i == j:
                        continue
                    comparisons += 1
                    if comparisons > MAX_SURFACE_COMPARISONS:
                        raise ValidationError("Isolation detection exceeds its 20M comparison budget; downsample first.", code="RESOURCE_LIMIT")
                    best = min(best, math.dist(point, points[j]))
            shell += 1
            if shell > 10_000:
                raise ValidationError("Isolation search exceeded its spatial range budget.", code="RESOURCE_LIMIT")
        nearest[i] = best
    finite = sorted(distance for distance in nearest if math.isfinite(distance))
    if len(finite) < 3:
        raise ValidationError("Isolation detection requires at least four points.")
    median = finite[len(finite) // 2]
    component_radius = median * component_factor
    if not math.isfinite(component_radius) or component_radius <= 1e-12:
        raise ValidationError("The cloud has no measurable nearest-neighbor spacing for main-component detection.")
    cell_size = component_radius
    buckets: dict[tuple[int, int, int], list[int]] = {}
    keys: list[tuple[int, int, int]] = []
    for index, point in enumerate(points):
        try:
            key = tuple(math.floor(value / cell_size) for value in point)
        except (ValueError, OverflowError) as exc:
            raise ValidationError("Isolation component grid is too fine for these coordinates.") from exc
        keys.append(key)
        buckets.setdefault(key, []).append(index)

    parent = list(range(len(points)))
    sizes = [1] * len(points)

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(first: int, second: int) -> None:
        first_root, second_root = find(first), find(second)
        if first_root == second_root:
            return
        if sizes[first_root] < sizes[second_root] or (
                sizes[first_root] == sizes[second_root] and first_root > second_root):
            first_root, second_root = second_root, first_root
        parent[second_root] = first_root
        sizes[first_root] += sizes[second_root]

    component_comparisons = 0
    offsets = list(product((-1, 0, 1), repeat=3))
    radius_squared = component_radius * component_radius
    for index, point in enumerate(points):
        key = keys[index]
        for offset in offsets:
            neighbor_key = tuple(key[axis] + offset[axis] for axis in range(3))
            for other in buckets.get(neighbor_key, ()):
                if other <= index:
                    continue
                component_comparisons += 1
                if comparisons + component_comparisons > MAX_SURFACE_COMPARISONS:
                    raise ValidationError("Isolation detection exceeds its 20M comparison budget; downsample first.", code="RESOURCE_LIMIT")
                delta = [point[axis] - points[other][axis] for axis in range(3)]
                if math.fsum(value * value for value in delta) <= radius_squared:
                    union(index, other)

    roots = [find(index) for index in range(len(points))]
    component_sizes: dict[int, int] = {}
    for root in roots:
        component_sizes[root] = component_sizes.get(root, 0) + 1
    main_root = min(component_sizes, key=lambda root: (-component_sizes[root], root))
    spacing_candidates = {index for index, distance in enumerate(nearest) if distance > median * 1.5}
    component_candidates = {index for index, root in enumerate(roots) if root != main_root}
    candidates = spacing_candidates | component_candidates
    cap = math.floor(len(points) * rate / 100)
    ordered_candidates = sorted(
        candidates,
        key=lambda index: (
            roots[index] != main_root,
            -component_sizes[roots[index]] if roots[index] != main_root else 0,
            nearest[index],
            -index,
        ),
        reverse=True,
    )
    removed = set(ordered_candidates[:cap])
    if diagnostics is not None:
        diagnostics.update({
            "nearest_spacing_median": median,
            "component_radius": component_radius,
            "component_count": len(component_sizes),
            "main_component_points": component_sizes[main_root],
            "outside_main_points": len(points) - component_sizes[main_root],
            "spacing_candidate_points": len(spacing_candidates),
            "removal_candidate_points": len(candidates),
            "removal_cap_points": cap,
        })
    return [point for i, point in enumerate(points) if i not in removed]
