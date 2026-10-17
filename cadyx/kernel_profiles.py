"""Pure profile, loft matching and numeric normalization algorithms."""
from __future__ import annotations

import math
from typing import Any
from .errors import ValidationError


def _box_dimensions(width: Any, height: Any, depth: Any) -> dict[str, float]:
    dimensions = {"width": width, "height": height, "depth": depth}
    normalized: dict[str, float] = {}
    for name, value in dimensions.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationError(f"{name} must be a number.")
        try:
            number = float(value)
        except OverflowError as exc:
            raise ValidationError(f"{name} must be a finite positive number.") from exc
        if not math.isfinite(number) or number <= 0:
            raise ValidationError(f"{name} must be a finite positive number.")
        normalized[name] = number
    return normalized


def _profile_values(profile: Any, plane: Any) -> tuple[list[dict[str, float]], int]:
    if plane not in {"XY", "XZ"}:
        raise ValidationError("Profile plane must be XY or XZ.")
    if not isinstance(profile, list) or len(profile) < 3:
        raise ValidationError("A profile needs at least 3 points.")
    normalized = []
    for point in profile:
        if not isinstance(point, dict) or set(point) != {"x", "y"}:
            raise ValidationError("Every profile point must contain x and y coordinates.")
        values = {}
        for name in ("x", "y"):
            value = point[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationError("Profile coordinates must be finite numbers.")
            try:
                value = float(value)
            except OverflowError as exc:
                raise ValidationError("Profile coordinates must be finite numbers.") from exc
            if not math.isfinite(value):
                raise ValidationError("Profile coordinates must be finite numbers.")
            values[name] = value
        normalized.append(values)
    area = sum(
        point["x"] * normalized[(index + 1) % len(normalized)]["y"]
        - normalized[(index + 1) % len(normalized)]["x"] * point["y"]
        for index, point in enumerate(normalized)
    )
    if not math.isfinite(area) or abs(area) <= 1e-12:
        raise ValidationError("Profile area must be non-zero.")
    plane_code = 0 if plane == "XY" else 1
    return normalized, plane_code


def _spline_control_values(points: Any, plane: Any) -> list[dict[str, float]]:
    if plane not in {"XY", "XZ"} or not isinstance(points, list) or len(points) < 3:
        raise ValidationError("Closed spline profile controls are invalid.")
    normalized = []
    for point in points:
        if not isinstance(point, dict) or set(point) != {"x", "y"}:
            raise ValidationError("Every closed spline control must contain x and y coordinates.")
        values = {}
        for name in ("x", "y"):
            value = point[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationError("Closed spline control coordinates must be finite numbers.")
            try:
                value = float(value)
            except OverflowError as exc:
                raise ValidationError("Closed spline control coordinates must be finite numbers.") from exc
            if not math.isfinite(value):
                raise ValidationError("Closed spline control coordinates must be finite numbers.")
            values[name] = value
        normalized.append(values)
    if any(math.hypot(a["x"] - b["x"], a["y"] - b["y"]) <= 1e-12
           for a, b in zip(normalized, normalized[1:] + normalized[:1])):
        raise ValidationError("Closed spline control vertices must be distinct.")
    return normalized


def _positive_amount(value: Any, name: str, *, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a number.")
    try:
        amount = float(value)
    except OverflowError as exc:
        raise ValidationError(f"{name} must be finite and positive.") from exc
    if not math.isfinite(amount) or amount <= 0 or (maximum is not None and amount > maximum):
        suffix = f" and no greater than {maximum:g}" if maximum is not None else ""
        raise ValidationError(f"{name} must be finite, positive{suffix}.")
    return amount


def _positive_or_signed_coordinate(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("Pipe path coordinates must be finite numbers.")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValidationError("Pipe path coordinates must be finite numbers.") from exc
    if not math.isfinite(number):
        raise ValidationError("Pipe path coordinates must be finite numbers.")
    return number


def _finite_coordinate(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be finite.")
    number = float(value)
    if not math.isfinite(number):
        raise ValidationError(f"{name} must be finite.")
    return number


def _ruled_patch_values(patches: Any) -> list[dict[str, list[list[float]]]]:
    if not isinstance(patches, list) or len(patches) < 4:
        raise ValidationError("Ruled shell needs at least 4 surface patches.")
    normalized = []
    for patch in patches:
        if not isinstance(patch, dict) or set(patch) != {"first", "second"}:
            raise ValidationError("Each ruled patch needs exactly two rail point arrays.")
        rails = {}
        for key in ("first", "second"):
            points = patch[key]
            if not isinstance(points, list) or len(points) < 2:
                raise ValidationError("Each ruled rail needs at least 2 points.")
            rail = []
            for point in points:
                if not isinstance(point, list) or len(point) != 3:
                    raise ValidationError("Ruled rail points must be [x, y, z] coordinate triples.")
                rail.append([_finite_coordinate(value, "Ruled rail coordinate") for value in point])
            if any(math.dist(a, b) <= 1e-7 for a, b in zip(rail, rail[1:])):
                raise ValidationError("Ruled rail points must be distinct and consecutive.")
            rails[key] = rail
        normalized.append(rails)
    return normalized


def _loft_contour_signature(profile: list[dict[str, float]]) -> tuple[float, float, float, float]:
    signed_twice_area = 0.0
    centroid_x = centroid_y = 0.0
    for index, point in enumerate(profile):
        following = profile[(index + 1) % len(profile)]
        cross = point["x"] * following["y"] - following["x"] * point["y"]
        signed_twice_area += cross
        centroid_x += (point["x"] + following["x"]) * cross
        centroid_y += (point["y"] + following["y"]) * cross
    area = abs(signed_twice_area) / 2
    if area <= 1e-12:
        raise ValidationError("Loft section area must be non-zero.")
    return area, centroid_x / (3 * signed_twice_area), centroid_y / (3 * signed_twice_area), signed_twice_area


def _match_loft_contours(
    left: list[tuple[list[dict[str, float]], float, float, float]],
    right: list[tuple[list[dict[str, float]], float, float, float]],
) -> list[tuple[int, int]]:
    """Pair contours by centroid proximity with area as a secondary shape cue."""
    all_points = [point for profile, _, _, _ in (*left, *right) for point in profile]
    span = max(
        max(point["x"] for point in all_points) - min(point["x"] for point in all_points),
        max(point["y"] for point in all_points) - min(point["y"] for point in all_points),
        1e-9,
    )

    def cost(a: tuple[list[dict[str, float]], float, float, float],
             b: tuple[list[dict[str, float]], float, float, float]) -> float:
        _, area_a, cx_a, cy_a = a
        _, area_b, cx_b, cy_b = b
        center_distance = math.hypot(cx_a - cx_b, cy_a - cy_b) / span
        area_change = abs(math.log(area_a / area_b))
        return center_distance + 0.25 * area_change

    left_is_shorter = len(left) <= len(right)
    short, long = (left, right) if left_is_shorter else (right, left)
    # Rectangular Hungarian assignment keeps matching polynomial as contour
    # counts grow, instead of the former bitmask search that scaled exponentially.
    row_count, column_count = len(short), len(long)
    costs = [[cost(source, target) for target in long] for source in short]
    row_potentials = [0.0] * (row_count + 1)
    column_potentials = [0.0] * (column_count + 1)
    matched_row = [0] * (column_count + 1)
    previous_column = [0] * (column_count + 1)
    for row in range(1, row_count + 1):
        matched_row[0] = row
        current_column = 0
        minimum_cost = [math.inf] * (column_count + 1)
        visited = [False] * (column_count + 1)
        while True:
            visited[current_column] = True
            current_row = matched_row[current_column]
            delta = math.inf
            next_column = 0
            for column in range(1, column_count + 1):
                if visited[column]:
                    continue
                reduced_cost = (costs[current_row - 1][column - 1]
                                - row_potentials[current_row] - column_potentials[column])
                if reduced_cost < minimum_cost[column]:
                    minimum_cost[column] = reduced_cost
                    previous_column[column] = current_column
                if minimum_cost[column] < delta:
                    delta = minimum_cost[column]
                    next_column = column
            for column in range(column_count + 1):
                if visited[column]:
                    row_potentials[matched_row[column]] += delta
                    column_potentials[column] -= delta
                else:
                    minimum_cost[column] -= delta
            current_column = next_column
            if matched_row[current_column] == 0:
                break
        while True:
            prior = previous_column[current_column]
            matched_row[current_column] = matched_row[prior]
            current_column = prior
            if current_column == 0:
                break
    assignment = [0] * row_count
    for column in range(1, column_count + 1):
        if matched_row[column]:
            assignment[matched_row[column] - 1] = column - 1
    pairs = ([(i, j) for i, j in enumerate(assignment)] if left_is_shorter
             else [(j, i) for i, j in enumerate(assignment)])
    matched_left = {i for i, _ in pairs}
    matched_right = {j for _, j in pairs}
    for left_index in range(len(left)):
        if left_index not in matched_left:
            right_index = min(range(len(right)), key=lambda j: cost(left[left_index], right[j]))
            pairs.append((left_index, right_index))
    for right_index in range(len(right)):
        if right_index not in matched_right:
            left_index = min(range(len(left)), key=lambda i: cost(left[i], right[right_index]))
            pairs.append((left_index, right_index))
    return pairs


def _resample_loft_profile(profile: list[dict[str, float]], count: int) -> list[dict[str, float]]:
    points = [(point["x"], point["y"]) for point in profile]
    area = sum(points[i][0] * points[(i + 1) % len(points)][1]
               - points[(i + 1) % len(points)][0] * points[i][1]
               for i in range(len(points)))
    if area < 0:
        points.reverse()
    lengths = [math.hypot(points[(i + 1) % len(points)][0] - point[0],
                          points[(i + 1) % len(points)][1] - point[1])
               for i, point in enumerate(points)]
    perimeter = sum(lengths)
    if not math.isfinite(perimeter) or perimeter <= 1e-7:
        raise ValidationError("Loft section perimeter must be finite and positive.")
    samples: list[dict[str, float]] = []
    edge = 0
    start = 0.0
    for index in range(count):
        distance = perimeter * index / count
        while edge < len(lengths) - 1 and start + lengths[edge] < distance:
            start += lengths[edge]
            edge += 1
        ratio = (distance - start) / lengths[edge]
        first, second = points[edge], points[(edge + 1) % len(points)]
        samples.append({"x": first[0] + ratio * (second[0] - first[0]),
                        "y": first[1] + ratio * (second[1] - first[1])})
    anchor = min(range(count), key=lambda i: (samples[i]["x"], samples[i]["y"]))
    return samples[anchor:] + samples[:anchor]


def _canonical_loft_profile(profile: list[dict[str, float]]) -> list[dict[str, float]]:
    points = list(profile)
    area = sum(points[i]["x"] * points[(i + 1) % len(points)]["y"]
               - points[(i + 1) % len(points)]["x"] * points[i]["y"]
               for i in range(len(points)))
    if area < 0:
        points.reverse()
    anchor = min(range(len(points)), key=lambda i: (points[i]["x"], points[i]["y"]))
    return points[anchor:] + points[:anchor]


def _align_loft_profile(
    profile: list[dict[str, float]], reference: list[dict[str, float]],
) -> list[dict[str, float]]:
    """Rotate a resampled section to keep its vertices aligned with the prior section."""
    if len(profile) != len(reference):
        raise ValidationError("Loft sections must have matching sample counts before alignment.")
    count = len(profile)
    stride = max(1, count // 64)
    sample_indices = list(range(0, count, stride))

    def squared_error(shift: int, indices: list[int]) -> float:
        return sum(
            (profile[(index + shift) % count]["x"] - reference[index]["x"]) ** 2
            + (profile[(index + shift) % count]["y"] - reference[index]["y"]) ** 2
            for index in indices
        )

    coarse = min(range(count), key=lambda shift: squared_error(shift, sample_indices))
    candidates = {(coarse + delta) % count for delta in range(-stride, stride + 1)}
    anchor = min(candidates, key=lambda shift: squared_error(shift, list(range(count))))
    return profile[anchor:] + profile[:anchor]
