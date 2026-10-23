"""Local-plane cloud tools; distances are source coordinates, never inferred mm."""
from __future__ import annotations

import math
import heapq
from typing import Any

from .errors import ValidationError
from .scan_reverse_alignment import _jacobi_eigenvectors

MAX_SURFACE_COMPARISONS = 20_000_000
_KD_LEAF_SIZE = 8

_KDNode = tuple[
    tuple[float, float, float], tuple[float, float, float], int,
    list[int] | None, Any, Any,
]


def _build_kd_tree(points: list[tuple[float, float, float]], indices: list[int]) -> _KDNode:
    minimum = tuple(min(points[index][axis] for index in indices) for axis in range(3))
    maximum = tuple(max(points[index][axis] for index in indices) for axis in range(3))
    if len(indices) <= _KD_LEAF_SIZE:
        return minimum, maximum, -1, indices, None, None
    axis = max(range(3), key=lambda value: maximum[value] - minimum[value])
    indices.sort(key=lambda index: points[index][axis])
    middle = len(indices) // 2
    left = _build_kd_tree(points, indices[:middle])
    right = _build_kd_tree(points, indices[middle:])
    return minimum, maximum, axis, None, left, right


def _box_distance_squared(node: _KDNode, point: tuple[float, float, float]) -> float:
    minimum, maximum = node[0], node[1]
    dx = max(minimum[0] - point[0], point[0] - maximum[0], 0.0)
    dy = max(minimum[1] - point[1], point[1] - maximum[1], 0.0)
    dz = max(minimum[2] - point[2], point[2] - maximum[2], 0.0)
    return dx * dx + dy * dy + dz * dz


def _nearest_points(points: list[tuple[float, float, float]], tree: _KDNode,
                    point: tuple[float, float, float], radius_squared: float, limit: int,
                    comparisons: list[int], *, budget_message: str) -> list[tuple[float, int]]:
    nearest: list[tuple[float, int]] = []
    pending = [(_box_distance_squared(tree, point), 0, tree)]
    sequence = 1
    while pending:
        node_distance_squared, _, node = heapq.heappop(pending)
        search_radius_squared = min(radius_squared, -nearest[0][0]) if len(nearest) == limit else radius_squared
        if node_distance_squared > search_radius_squared:
            break
        _, _, _, indices, left, right = node
        if indices is not None:
            for index in indices:
                comparisons[0] += 1
                if comparisons[0] > MAX_SURFACE_COMPARISONS:
                    raise ValidationError(
                        budget_message,
                        code="RESOURCE_LIMIT",
                    )
                candidate = points[index]
                dx = point[0] - candidate[0]
                dy = point[1] - candidate[1]
                dz = point[2] - candidate[2]
                squared_distance = dx * dx + dy * dy + dz * dz
                if squared_distance <= radius_squared:
                    entry = (-squared_distance, -index)
                    if len(nearest) < limit:
                        heapq.heappush(nearest, entry)
                    elif squared_distance < -nearest[0][0] or (
                            squared_distance == -nearest[0][0] and index < -nearest[0][1]):
                        heapq.heapreplace(nearest, entry)
            continue

        if left is not None:
            distance_squared = _box_distance_squared(left, point)
            if distance_squared <= search_radius_squared:
                heapq.heappush(pending, (distance_squared, sequence, left))
                sequence += 1
        if right is not None:
            distance_squared = _box_distance_squared(right, point)
            if distance_squared <= search_radius_squared:
                heapq.heappush(pending, (distance_squared, sequence, right))
                sequence += 1
    return sorted((math.sqrt(-distance), -index) for distance, index in nearest)


def _points_in_box(points: list[tuple[float, float, float]], tree: _KDNode,
                   minimum: tuple[float, float, float],
                   maximum: tuple[float, float, float]) -> list[int]:
    found: list[int] = []
    pending = [tree]
    while pending:
        node = pending.pop()
        node_minimum, node_maximum, _, indices, left, right = node
        if any(node_maximum[axis] < minimum[axis] or node_minimum[axis] > maximum[axis]
               for axis in range(3)):
            continue
        if indices is not None:
            found.extend(index for index in indices
                         if all(minimum[axis] <= points[index][axis] <= maximum[axis]
                                for axis in range(3)))
            continue
        if left is not None:
            pending.append(left)
        if right is not None:
            pending.append(right)
    return found


def surface_points(points: list[tuple[float, float, float]], method: str,
                   parameters: dict[str, Any]) -> list[tuple[float, float, float]]:
    """Retain one representative per coincident patch or smooth along its normal.

    Updates are synchronous. Sparse, collinear and non-planar neighborhoods are
    left untouched; neither tool connects disconnected regions or creates points.
    """
    current = list(points)
    radius = parameters["radius"]
    comparisons = [0]
    for _ in range(parameters.get("iterations", 1)):
        tree = _build_kd_tree(current, list(range(len(current))))
        removed: set[int] = set()
        updated = list(current)
        for i, point in enumerate(current):
            if i in removed:
                continue
            nearest = _nearest_points(
                current, tree, point, radius * radius, 48, comparisons,
                budget_message="Surface processing exceeds its 20M comparison budget; reduce radius or iterations.",
            )
            neighbors = [j for _, j in nearest]
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
                tangent = parameters["tangential_distance"]
                extent = tuple(abs(normal[axis]) * parameters["distance"]
                               + math.sqrt(max(0.0, 1.0 - normal[axis] ** 2)) * tangent
                               for axis in range(3))
                minimum = tuple(point[axis] - extent[axis] for axis in range(3))
                maximum = tuple(point[axis] + extent[axis] for axis in range(3))
                candidates = _points_in_box(current, tree, minimum, maximum)
                for j in candidates:
                    if j <= i or j in removed:
                        continue
                    comparisons[0] += 1
                    if comparisons[0] > MAX_SURFACE_COMPARISONS:
                        raise ValidationError(
                            "Surface processing exceeds its 20M comparison budget; reduce radius or iterations.",
                            code="RESOURCE_LIMIT",
                        )
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
    tree = _build_kd_tree(points, list(range(len(points))))
    nearest = [math.inf] * len(points)
    comparisons = [0]
    for i, point in enumerate(points):
        candidates = _nearest_points(
            points, tree, point, math.inf, 2, comparisons,
            budget_message="Isolation detection exceeds its 20M comparison budget; downsample first.",
        )
        nearest[i] = next((distance for distance, index in candidates if index != i), math.inf)
    finite = sorted(distance for distance in nearest if math.isfinite(distance))
    if len(finite) < 3:
        raise ValidationError("Isolation detection requires at least four points.")
    median = finite[len(finite) // 2]
    component_radius = median * component_factor
    if not math.isfinite(component_radius) or component_radius <= 1e-12:
        raise ValidationError("The cloud has no measurable nearest-neighbor spacing for main-component detection.")
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

    component_comparisons = [0]
    radius_squared = component_radius * component_radius
    for index, point in enumerate(points):
        minimum = tuple(value - component_radius for value in point)
        maximum = tuple(value + component_radius for value in point)
        for other in _points_in_box(points, tree, minimum, maximum):
            if other <= index:
                continue
            component_comparisons[0] += 1
            if comparisons[0] + component_comparisons[0] > MAX_SURFACE_COMPARISONS:
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
