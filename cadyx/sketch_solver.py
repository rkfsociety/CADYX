"""Bounded deterministic 2D sketch constraint solver."""
from __future__ import annotations

import math
from typing import Any

from .errors import ValidationError

_TOLERANCE = 1e-7
_MAX_VARIABLES = 128
_MAX_CONSTRAINTS = 256
_MAX_ITERATIONS = 40


def solve_sketch(
    objects: list[dict[str, Any]], sketch_id: str,
    parameter_bindings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Solve one sketch in place only after a bounded least-squares solve succeeds."""
    owned = [item for item in objects if item.get("sketch_id") == sketch_id]
    points = [item for item in owned if item.get("type") == "point"]
    circles = [item for item in owned if item.get("type") == "circle"]
    constraints = [item for item in owned if item.get("type") == "constraint"]
    locked = {(binding.get("object_id"), binding.get("path"))
              for binding in (parameter_bindings or [])}
    if not constraints:
        degrees = (sum((item["id"], field) not in locked for item in points for field in ("x", "y"))
                   + sum((item["id"], "radius") not in locked for item in circles))
        return {"status": "under_constrained" if degrees else "fully_constrained",
                "degrees_of_freedom": degrees,
                "iterations": 0, "max_residual": 0.0, "conflicts": [],
                "under_constrained": degrees > 0, "over_constrained": False}
    if len(constraints) > _MAX_CONSTRAINTS:
        raise ValidationError(f"Sketch solver limit exceeded: at most {_MAX_CONSTRAINTS} constraints.",
                              code="RESOURCE_LIMIT", diagnostics={"category": "resource_limit",
                              "entity_ids": [item.get("id") for item in constraints if isinstance(item.get("id"), str)],
                              "failed_precondition": "the sketch constraint count must stay within the solver budget",
                              "measurements": {"constraint_count": len(constraints), "maximum": _MAX_CONSTRAINTS},
                              "correction_hint": "Reduce the number of constraints in this solve to the documented limit."})
    variables: list[tuple[dict[str, Any], str]] = []
    for point in points:
        variables.extend(((point, "x"), (point, "y")))
    for circle in circles:
        variables.append((circle, "radius"))
    if len(variables) > _MAX_VARIABLES:
        raise ValidationError(f"Sketch solver limit exceeded: at most {_MAX_VARIABLES} scalar variables.",
                              code="RESOURCE_LIMIT", diagnostics={"category": "resource_limit",
                              "failed_precondition": "the sketch scalar-variable count must stay within the solver budget",
                              "measurements": {"variable_count": len(variables), "maximum": _MAX_VARIABLES},
                              "correction_hint": "Reduce the number of free sketch variables to the documented limit."})
    by_id = {item["id"]: item for item in owned}
    initial = [float(item[field]) for item, field in variables]
    free_indices = [index for index, (item, field) in enumerate(variables)
                    if (item["id"], field) not in locked]
    for constraint in constraints:
        kind, refs = constraint.get("kind"), constraint.get("references")
        expected = {"horizontal": ("line",), "vertical": ("line",),
                    "coincident": ("point", "point"), "distance": ("point", "point"),
                    "radius": ("circle",), "parallel": ("line", "line"),
                    "perpendicular": ("line", "line"), "equal": ("same", "same"),
                    "angular": ("line", "line"), "tangent": ("tangent", "tangent"),
                    "symmetry": ("point", "point", "line"), "fixed": ("point",)}
        if (not isinstance(kind, str) or kind not in expected or not isinstance(refs, list)
                or len(refs) != len(expected[kind]) or any(not isinstance(ref, str) for ref in refs)):
            raise ValidationError("Sketch constraint kind or reference count is invalid.")
        targets = [by_id.get(ref) for ref in refs]
        if any(target is None for target in targets):
            raise ValidationError("Sketch constraint reference is missing.")
        for target, required in zip(targets, expected[kind], strict=True):
            if target.get("sketch_id") != sketch_id:
                raise ValidationError("Sketch constraint reference must belong to its sketch.")
            if required == "same":
                if target.get("type") not in {"line", "circle"}:
                    raise ValidationError("Equal constraints need lines or circles.")
            elif required == "tangent":
                if target.get("type") not in {"line", "circle"}:
                    raise ValidationError("Tangent constraints need lines or circles.")
            elif target.get("type") != required:
                raise ValidationError("Sketch constraint reference has the wrong entity type.")
        if kind == "equal" and targets[0].get("type") != targets[1].get("type"):
            raise ValidationError("Equal constraints require two lines or two circles.")
        if kind == "tangent" and {target.get("type") for target in targets} not in ({"line", "circle"}, {"circle"}):
            raise ValidationError("Tangent constraints require a line and circle or two circles.")
        if kind == "tangent" and {target.get("type") for target in targets} == {"line", "circle"}:
            if constraint.get("tangent_side", 1) not in {-1, 1}:
                raise ValidationError("Tangent side must be -1 or 1.")
        if kind == "fixed":
            position = constraint.get("fixed_position")
            if (not isinstance(position, list) or len(position) != 2
                    or any(isinstance(value, bool) or not isinstance(value, (int, float))
                           or not math.isfinite(value) for value in position)):
                raise ValidationError("Fixed point constraint needs a finite saved position.")
        if kind in {"distance", "radius", "angular"}:
            value = constraint.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValidationError("Sketch constraint value must be finite.")
            if (kind == "distance" and value < 0) or (kind == "radius" and value <= 0):
                raise ValidationError("Sketch constraint value is outside its allowed range.")

    def set_values(values: list[float]) -> None:
        for (item, field), value in zip(variables, values, strict=True):
            item[field] = value

    def residuals(values: list[float]) -> list[float]:
        set_values(values)
        result: list[float] = []
        for constraint in constraints:
            kind, refs = constraint.get("kind"), constraint.get("references")
            targets = [by_id.get(ref) for ref in refs] if isinstance(refs, list) else []
            raw_value = constraint.get("value", 0.0)
            value = float(raw_value) if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool) else 0.0
            if kind in {"horizontal", "vertical"}:
                line = targets[0]
                a, b = by_id[line["start_id"]], by_id[line["end_id"]]
                result.append((a["y"] - b["y"]) if kind == "horizontal" else (a["x"] - b["x"]))
            elif kind in {"coincident", "distance"}:
                a, b = targets
                dx, dy = a["x"] - b["x"], a["y"] - b["y"]
                distance = math.hypot(dx, dy)
                if kind == "coincident":
                    result.extend((dx, dy))
                else:
                    result.append(distance - value)
            elif kind == "radius":
                result.append(targets[0]["radius"] - value)
            elif kind == "fixed":
                result.extend((targets[0]["x"] - constraint["fixed_position"][0],
                               targets[0]["y"] - constraint["fixed_position"][1]))
            elif kind in {"parallel", "perpendicular", "equal", "angular"}:
                a, b = targets
                if a["type"] == b["type"] == "circle":
                    result.append(a["radius"] - b["radius"])
                else:
                    a0, a1 = by_id[a["start_id"]], by_id[a["end_id"]]
                    b0, b1 = by_id[b["start_id"]], by_id[b["end_id"]]
                    ax, ay = a1["x"] - a0["x"], a1["y"] - a0["y"]
                    bx, by = b1["x"] - b0["x"], b1["y"] - b0["y"]
                    al, bl = max(math.hypot(ax, ay), 1e-14), max(math.hypot(bx, by), 1e-14)
                    cross = (ax * by - ay * bx) / (al * bl)
                    dot = (ax * bx + ay * by) / (al * bl)
                    if kind == "parallel":
                        result.append(cross)
                    elif kind == "perpendicular":
                        result.append(dot)
                    elif kind == "equal":
                        result.append(al - bl)
                    else:
                        angle = math.atan2(ax * by - ay * bx, ax * bx + ay * by)
                        result.append(math.atan2(math.sin(angle - math.radians(value)),
                                                 math.cos(angle - math.radians(value))))
            elif kind == "tangent":
                a, b = targets
                line, circle = (a, b) if a["type"] == "line" else (b, a)
                if line["type"] == "line" and circle["type"] == "circle":
                    start, end, center = by_id[line["start_id"]], by_id[line["end_id"]], by_id[circle["center_id"]]
                    dx, dy = end["x"] - start["x"], end["y"] - start["y"]
                    length = max(math.hypot(dx, dy), 1e-14)
                    result.append(((center["x"] - start["x"]) * dy -
                                   (center["y"] - start["y"]) * dx) / length
                                  - constraint.get("tangent_side", 1) * circle["radius"])
                else:
                    ca, cb = by_id[a["center_id"]], by_id[b["center_id"]]
                    result.append(math.hypot(ca["x"] - cb["x"], ca["y"] - cb["y"])
                                  - a["radius"] - b["radius"])
            elif kind == "symmetry":
                p, q, axis = targets
                start, end = by_id[axis["start_id"]], by_id[axis["end_id"]]
                dx, dy = end["x"] - start["x"], end["y"] - start["y"]
                length = max(math.hypot(dx, dy), 1e-14)
                mx, my = (p["x"] + q["x"]) / 2 - start["x"], (p["y"] + q["y"]) / 2 - start["y"]
                result.extend(((mx * dy - my * dx) / length,
                               ((q["x"] - p["x"]) * dx + (q["y"] - p["y"]) * dy) / length))
        if not all(math.isfinite(value) for value in result):
            raise ValidationError("Sketch constraints produced a non-finite residual.")
        return result

    def jacobian(values: list[float], base: list[float]) -> list[list[float]]:
        rows = [[0.0] * len(values) for _ in base]
        for column, current in enumerate(values):
            step = 1e-6 * max(1.0, abs(current))
            shifted = list(values)
            shifted[column] += step
            trial = residuals(shifted)
            for row, (left, right) in enumerate(zip(base, trial, strict=True)):
                rows[row][column] = (right - left) / step
        set_values(values)
        return rows

    def solve_linear(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
        size = len(vector)
        augmented = [row[:] + [rhs] for row, rhs in zip(matrix, vector, strict=True)]
        for column in range(size):
            pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
            if abs(augmented[pivot][column]) < 1e-18:
                return None
            augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
            scale = augmented[column][column]
            for entry in range(column, size + 1):
                augmented[column][entry] /= scale
            for row in range(size):
                if row == column:
                    continue
                factor = augmented[row][column]
                if factor:
                    for entry in range(column, size + 1):
                        augmented[row][entry] -= factor * augmented[column][entry]
        return [augmented[row][size] for row in range(size)]

    values = list(initial)
    damping = 1e-3
    iterations = 0
    for iterations in range(1, _MAX_ITERATIONS + 1):
        current = residuals(values)
        if not current or max(map(abs, current)) <= _TOLERANCE:
            break
        jac = jacobian(values, current)
        size = len(free_indices)
        normal = [[sum(row[i] * row[j] for row in jac) for j in free_indices] for i in free_indices]
        gradient = [-sum(row[i] * residual for row, residual in zip(jac, current, strict=True))
                    for i in free_indices]
        for index, i in enumerate(free_indices):
            normal[index][index] += damping + 1e-10
            gradient[index] -= 1e-10 * (values[i] - initial[i])
        delta = solve_linear(normal, gradient)
        if delta is None:
            break
        candidate = list(values)
        for index, variable_index in enumerate(free_indices):
            candidate[variable_index] += delta[index]
        if any(not math.isfinite(value) for value in candidate):
            set_values(values)
            damping = min(1e12, damping * 10.0)
            continue
        trial = residuals(candidate)
        if sum(value * value for value in trial) < sum(value * value for value in current):
            values, damping = candidate, max(1e-9, damping * 0.3)
        else:
            set_values(values)
            damping = min(1e12, damping * 10.0)

    final = residuals(values)
    conflicts = [{"constraint_id": constraint["id"], "residual": max(map(abs, final[offset:offset + count]))}
                 for constraint, offset, count in _residual_ranges(constraints, by_id)
                 if max(map(abs, final[offset:offset + count])) > _TOLERANCE]
    if conflicts:
        set_values(initial)
        raise ValidationError("Sketch constraints are inconsistent or did not converge: " +
                              ", ".join(f"{item['constraint_id']} ({item['residual']:.6g})" for item in conflicts),
                              code="SKETCH_CONSTRAINT_INCONSISTENT", diagnostics={
                                  "category": "constraint_solver",
                                  "entity_ids": [item["constraint_id"] for item in conflicts],
                                  "failed_precondition": "all sketch constraint residuals must be within solver tolerance",
                                  "measurements": {"conflicts": conflicts, "tolerance": _TOLERANCE,
                                                   "iterations": iterations, "maximum_iterations": _MAX_ITERATIONS},
                                  "correction_hint": "Review the listed constraint IDs and residuals; change the intended constraints or their references."})
    jac = jacobian(values, final)
    rank = _matrix_rank([[row[index] for index in free_indices] for row in jac])
    full_rank = _matrix_rank(jac)
    set_values(values)
    equation_count = len(final)
    under_constrained = rank < len(free_indices)
    over_constrained = equation_count > full_rank
    status = ("over_constrained" if over_constrained else
              "under_constrained" if under_constrained else "fully_constrained")
    return {"status": status,
            "degrees_of_freedom": max(0, len(free_indices) - rank), "iterations": iterations,
            "max_residual": max(map(abs, final), default=0.0), "conflicts": [],
            "under_constrained": under_constrained, "over_constrained": over_constrained}


def _residual_ranges(constraints: list[dict[str, Any]], by_id: dict[str, dict[str, Any]]) -> list[tuple[dict[str, Any], int, int]]:
    del by_id
    offset = 0
    ranges = []
    for item in constraints:
        count = 2 if item["kind"] in {"coincident", "symmetry", "fixed"} else 1
        ranges.append((item, offset, count))
        offset += count
    return ranges


def _matrix_rank(rows: list[list[float]]) -> int:
    if not rows:
        return 0
    matrix = [row[:] for row in rows]
    row_count, columns = len(matrix), len(matrix[0])
    rank = 0
    for column in range(columns):
        pivot = max(range(rank, row_count), key=lambda row: abs(matrix[row][column]), default=rank)
        if rank >= row_count or abs(matrix[pivot][column]) < 1e-8:
            continue
        matrix[rank], matrix[pivot] = matrix[pivot], matrix[rank]
        scale = matrix[rank][column]
        for index in range(column, columns):
            matrix[rank][index] /= scale
        for row in range(row_count):
            if row == rank:
                continue
            factor = matrix[row][column]
            for index in range(column, columns):
                matrix[row][index] -= factor * matrix[rank][index]
        rank += 1
        if rank == row_count:
            break
    return rank
