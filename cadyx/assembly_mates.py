"""Deterministic, caller-driven rigid assembly mate solver.

Mates act on sibling instance frames (not B-rep geometry). They never fuse parts.
The first instance in each connected component, or the requested anchor, is
grounded for a solve. Loop closures are checked instead of silently overridden.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
import math
from typing import Any

from .assemblies import _multiply, _rotate, transform_point, validate_transform
from .errors import ValidationError
from .numerical import SparseRankAccumulator, solve_sparse_positive_definite

MATE_DOF = {"fixed": 0, "coincident": 3, "coaxial": 2,
            "revolute": 1, "slider": 1, "cylindrical": 2, "spherical": 3,
            "parallel": 4, "perpendicular": 5, "distance": 5, "angle": 5}
AXIS_RELATIONS = {"parallel", "perpendicular", "distance", "angle"}
POSITION_TOLERANCE_MM = 1e-6
ROTATION_TOLERANCE_RAD = 1e-6
CONSTRAINT_RANK_TOLERANCE = 1e-8
MAX_LOOP_SOLVER_ITERATIONS = 40
MAX_ASSEMBLY_INSTANCES = 16384
MAX_ASSEMBLY_MATES = 65536
IDENTITY_FRAME = {"translation": [0.0, 0.0, 0.0],
                  "rotation": [1.0, 0.0, 0.0, 0.0]}
DEFAULT_LIMITS = {"angle_min_deg": None, "angle_max_deg": None,
                  "offset_min_mm": None, "offset_max_mm": None}
FACE_SURFACES = {"plane", "cylinder", "cone", "sphere"}
REFERENCE_KINDS = {"face", "vertex"}


def _number(value: Any, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValidationError(f"{label} must be a finite number.")
    return float(value)


def _frame(value: Any, label: str) -> None:
    if not isinstance(value, dict) or set(value) != {"translation", "rotation"}:
        raise ValidationError(f"{label} requires translation and rotation.")
    validate_transform(value["translation"], value["rotation"])


def _vector(value: Any, label: str) -> None:
    if (not isinstance(value, list) or len(value) != 3
            or any(type(component) not in (int, float) or not math.isfinite(component)
                   for component in value)):
        raise ValidationError(f"{label} must be a finite XYZ vector.")


def _geometry_reference(value: Any, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != {
        "body_id", "entity_kind", "entity_key", "entity_index", "surface", "origin", "direction"
    }:
        raise ValidationError(f"{label} requires a body, typed B-rep entity key, index and local frame.")
    entity_kind = value["entity_kind"]
    entity_key = value["entity_key"]
    surface = value["surface"]
    surface_valid = ((isinstance(surface, str) and surface in FACE_SURFACES)
                     if entity_kind == "face" else surface == "point")
    if (not isinstance(value["body_id"], str) or not value["body_id"].startswith("body:")
            or not isinstance(entity_kind, str) or entity_kind not in REFERENCE_KINDS
            or not isinstance(entity_key, str)
            or not entity_key.startswith("face:" if entity_kind == "face" else "vertex:")
            or type(value["entity_index"]) is not int or value["entity_index"] < 0
            or not surface_valid):
        raise ValidationError(f"{label} has invalid body or face identity.")
    _vector(value["origin"], f"{label} origin")
    _vector(value["direction"], f"{label} direction")
    norm = math.sqrt(sum(float(component) ** 2 for component in value["direction"]))
    if abs(norm - 1.0) > 1e-6:
        raise ValidationError(f"{label} direction must be unit length.")


def _limits(value: Any, kind: str, angle: float, offset: float) -> None:
    if not isinstance(value, dict) or set(value) != set(DEFAULT_LIMITS):
        raise ValidationError("Mate limits require angle and offset minimum/maximum fields.")
    parsed: dict[str, float | None] = {}
    for key in DEFAULT_LIMITS:
        number = value[key]
        if number is None:
            parsed[key] = None
        else:
            parsed[key] = _number(number, f"Mate limit {key}")
    pairs = (("angle_min_deg", "angle_max_deg"),
             ("offset_min_mm", "offset_max_mm"))
    for minimum, maximum in pairs:
        low, high = parsed[minimum], parsed[maximum]
        if low is not None and high is not None and low > high:
            raise ValidationError(f"Mate limit {minimum} must not exceed {maximum}.")
    angle_allowed = kind in {"revolute", "coaxial", "cylindrical", "angle"}
    offset_allowed = kind in {"slider", "coaxial", "cylindrical", "distance"}
    if not angle_allowed and any(parsed[key] is not None
                                 for key in ("angle_min_deg", "angle_max_deg")):
        raise ValidationError(f"{kind} mates do not accept angular limits.")
    if not offset_allowed and any(parsed[key] is not None
                                  for key in ("offset_min_mm", "offset_max_mm")):
        raise ValidationError(f"{kind} mates do not accept axial limits.")
    if parsed["angle_min_deg"] is not None and angle < parsed["angle_min_deg"]:
        raise ValidationError("Mate angle is below its configured minimum.")
    if parsed["angle_max_deg"] is not None and angle > parsed["angle_max_deg"]:
        raise ValidationError("Mate angle is above its configured maximum.")
    if parsed["offset_min_mm"] is not None and offset < parsed["offset_min_mm"]:
        raise ValidationError("Mate offset is below its configured minimum.")
    if parsed["offset_max_mm"] is not None and offset > parsed["offset_max_mm"]:
        raise ValidationError("Mate offset is above its configured maximum.")


def validate_mate_records(graph: dict[str, Any]) -> None:
    """Validate all mate references and parameters without solving positions."""
    mates = graph.get("mates")
    if not isinstance(mates, list):
        raise ValidationError("Assembly mates must be a list.")
    instances = {entry["id"]: entry for entry in graph["instances"]}
    seen: set[str] = set()
    for mate in mates:
        if not isinstance(mate, dict) or set(mate) != {
            "id", "name", "kind", "a_instance_id", "b_instance_id",
            "frame_a", "frame_b", "angle_deg", "offset_mm", "orientation", "limits",
            "reference_a", "reference_b",
        }:
            raise ValidationError("Assembly mate record has invalid fields.")
        ident = mate["id"]
        if (not isinstance(ident, str) or not ident.startswith("mate:")
                or not ident[5:] or ident in seen):
            raise ValidationError("Assembly mate ID must be distinct and typed.")
        seen.add(ident)
        if (not isinstance(mate["name"], str) or not mate["name"].strip()
                or len(mate["name"].strip()) > 120):
            raise ValidationError("Assembly mate name must contain 1–120 characters.")
        kind = mate["kind"]
        if not isinstance(kind, str) or kind not in MATE_DOF:
            raise ValidationError("Unsupported assembly mate kind.")
        a, b = mate["a_instance_id"], mate["b_instance_id"]
        if not isinstance(a, str) or not isinstance(b, str) or a not in instances or b not in instances or a == b:
            raise ValidationError("Mate must reference two distinct existing instances.")
        if instances[a]["parent_id"] != instances[b]["parent_id"]:
            raise ValidationError("Mate instances must share the same parent assembly.")
        _frame(mate["frame_a"], "Mate frame A")
        _frame(mate["frame_b"], "Mate frame B")
        angle = _number(mate["angle_deg"], "Mate angle")
        offset = _number(mate["offset_mm"], "Mate offset")
        orientation = mate["orientation"]
        if not isinstance(orientation, list) or len(orientation) != 4:
            raise ValidationError("Mate orientation must be a unit quaternion.")
        validate_transform([0.0, 0.0, 0.0], orientation)
        if (kind in {"fixed", "coincident", "spherical", "parallel", "perpendicular"}
                and (angle != 0 or offset != 0)):
            raise ValidationError(f"{kind} mates do not accept angle or distance coordinates.")
        if kind in {"revolute", "angle"} and offset != 0:
            raise ValidationError(f"{kind} mates do not accept distance coordinates.")
        if kind in {"slider", "distance"} and angle != 0:
            raise ValidationError(f"{kind} mates do not accept angle coordinates.")
        if kind not in {"coincident", "spherical"} and orientation != [1.0, 0.0, 0.0, 0.0]:
            raise ValidationError("Only coincident and spherical mates accept free relative orientation.")
        _limits(mate["limits"], kind, angle, offset)
        if kind == "angle" and not 0.0 <= angle <= 180.0:
            raise ValidationError("Assembly angle constraints must be between 0 and 180 degrees.")
        if kind == "distance" and offset < 0:
            raise ValidationError("Assembly point distance cannot be negative.")
        _geometry_reference(mate["reference_a"], "Mate reference A")
        _geometry_reference(mate["reference_b"], "Mate reference B")
        if (mate["reference_a"] is None) != (mate["reference_b"] is None):
            raise ValidationError("Geometry-referenced mates need matching references A and B.")


def _pose(translation: list[float], rotation: list[float]) -> dict[str, list[float]]:
    return {"translation": translation, "rotation": rotation}


def _compose(a: dict[str, Any], b: dict[str, Any]) -> dict[str, list[float]]:
    return _pose(transform_point(b["translation"], a["translation"], a["rotation"]),
                 _multiply(a["rotation"], b["rotation"]))


def _inverse(p: dict[str, Any]) -> dict[str, list[float]]:
    w, x, y, z = p["rotation"]
    inverse_rotation = [w, -x, -y, -z]
    return _pose(_rotate(inverse_rotation, [-float(v) for v in p["translation"]]),
                 inverse_rotation)


def _motion(mate: dict[str, Any]) -> dict[str, list[float]]:
    kind = mate["kind"]
    if kind in {"coincident", "spherical"}:
        rotation = mate["orientation"].copy()
    elif kind in ("coaxial", "cylindrical", "revolute"):
        half = math.radians(mate["angle_deg"]) / 2
        rotation = [math.cos(half), 0.0, 0.0, math.sin(half)]
    else:
        rotation = [1.0, 0.0, 0.0, 0.0]
    z = mate["offset_mm"] if kind in ("coaxial", "cylindrical", "slider") else 0.0
    return _pose([0.0, 0.0, z], rotation)


def _next_pose(source: dict[str, Any], mate: dict[str, Any],
               forward: bool, initial: dict[str, dict[str, list[float]]]) -> dict[str, list[float]]:
    kind = mate["kind"]
    if kind in AXIS_RELATIONS:
        source_id = mate["a_instance_id"] if forward else mate["b_instance_id"]
        target_id = mate["b_instance_id"] if forward else mate["a_instance_id"]
        source_frame = mate["frame_a"] if forward else mate["frame_b"]
        target_frame = mate["frame_b"] if forward else mate["frame_a"]
        group_delta = _compose(source, _inverse(initial[source_id]))
        candidate = _compose(group_delta, initial[target_id])
        source_world = _compose(source, source_frame)
        target_world = _compose(candidate, target_frame)
        axis_a = _rotate(source_world["rotation"], [0.0, 0.0, 1.0])
        axis_b = _rotate(target_world["rotation"], [0.0, 0.0, 1.0])
        if kind in {"parallel", "perpendicular", "angle"}:
            dot = sum(a * b for a, b in zip(axis_a, axis_b, strict=True))
            tangent = [axis_b[i] - dot * axis_a[i] for i in range(3)]
            tangent_length = math.sqrt(sum(value * value for value in tangent))
            if tangent_length <= 1e-12:
                helper = [1.0, 0.0, 0.0] if abs(axis_a[0]) < 0.8 else [0.0, 1.0, 0.0]
                tangent = _cross(axis_a, helper)
                tangent_length = math.sqrt(sum(value * value for value in tangent))
            tangent = [value / tangent_length for value in tangent]
            if kind == "parallel":
                desired_axis = axis_a if dot >= 0 else [-value for value in axis_a]
            elif kind == "perpendicular":
                desired_axis = tangent
            else:
                angle = math.radians(mate["angle_deg"])
                desired_axis = [math.cos(angle) * axis_a[i] + math.sin(angle) * tangent[i]
                                for i in range(3)]
            correction = _rotation_between(axis_b, desired_axis)
            held_origin = target_world["translation"]
            candidate["rotation"] = _multiply(correction, candidate["rotation"])
            rotated_frame_origin = _rotate(candidate["rotation"], target_frame["translation"])
            candidate["translation"] = [held_origin[i] - rotated_frame_origin[i] for i in range(3)]
        if kind == "distance":
            delta = [target_world["translation"][i] - source_world["translation"][i]
                     for i in range(3)]
            distance = math.sqrt(sum(value * value for value in delta))
            direction = ([value / distance for value in delta] if distance > 1e-12 else axis_a)
            desired_origin = [source_world["translation"][i] + mate["offset_mm"] * direction[i]
                              for i in range(3)]
            candidate["translation"] = [candidate["translation"][i]
                                         + desired_origin[i] - target_world["translation"][i]
                                         for i in range(3)]
        return candidate
    left = mate["frame_a"] if forward else mate["frame_b"]
    right = mate["frame_b"] if forward else mate["frame_a"]
    motion = _motion(mate)
    return _compose(_compose(_compose(source, left),
                             motion if forward else _inverse(motion)),
                    _inverse(right))


def _cross(a: list[float], b: list[float]) -> list[float]:
    return [a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def _rotation_between(source: list[float], target: list[float]) -> list[float]:
    dot = min(1.0, max(-1.0, sum(a * b for a, b in zip(source, target, strict=True))))
    if dot < -1.0 + 1e-12:
        helper = [1.0, 0.0, 0.0] if abs(source[0]) < 0.8 else [0.0, 1.0, 0.0]
        axis = _cross(source, helper)
        length = math.sqrt(sum(value * value for value in axis))
        return [0.0, *(value / length for value in axis)]
    cross = _cross(source, target)
    quaternion = [1.0 + dot, *cross]
    length = math.sqrt(sum(value * value for value in quaternion))
    return [value / length for value in quaternion]


def _relation_matches(a: dict[str, Any], b: dict[str, Any], mate: dict[str, Any],
                      forward: bool) -> bool:
    source_frame = mate["frame_a"] if forward else mate["frame_b"]
    target_frame = mate["frame_b"] if forward else mate["frame_a"]
    source_world, target_world = _compose(a, source_frame), _compose(b, target_frame)
    axis_a = _rotate(source_world["rotation"], [0.0, 0.0, 1.0])
    axis_b = _rotate(target_world["rotation"], [0.0, 0.0, 1.0])
    dot = min(1.0, max(-1.0, sum(x * y for x, y in zip(axis_a, axis_b, strict=True))))
    kind = mate["kind"]
    if kind == "parallel":
        return math.acos(min(1.0, abs(dot))) <= ROTATION_TOLERANCE_RAD
    if kind == "perpendicular":
        return abs(dot) <= ROTATION_TOLERANCE_RAD
    if kind == "angle":
        return abs(math.degrees(math.acos(dot)) - mate["angle_deg"]) <= math.degrees(ROTATION_TOLERANCE_RAD)
    distance = math.dist(source_world["translation"], target_world["translation"])
    return abs(distance - mate["offset_mm"]) <= POSITION_TOLERANCE_MM


def _matches(a: dict[str, Any], b: dict[str, Any]) -> bool:
    distance = math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in
                             zip(a["translation"], b["translation"])))
    dot = abs(sum(float(x) * float(y) for x, y in zip(a["rotation"], b["rotation"])))
    angle = 2 * math.acos(min(1.0, max(-1.0, dot)))
    return distance <= POSITION_TOLERANCE_MM and angle <= ROTATION_TOLERANCE_RAD


def _unit_vector(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 1e-15:
        raise ValidationError("Assembly constraint frame contains a zero direction.")
    return [value / norm for value in vector]


def _tangent_basis(axis: list[float]) -> tuple[list[float], list[float]]:
    helper = [1.0, 0.0, 0.0] if abs(axis[0]) < 0.8 else [0.0, 1.0, 0.0]
    first = _unit_vector(_cross(axis, helper))
    return first, _cross(axis, first)


def _rotation_vector(quaternion: list[float]) -> list[float]:
    w, x, y, z = quaternion
    if w < 0:
        w, x, y, z = -w, -x, -y, -z
    vector = [x, y, z]
    length = math.sqrt(sum(value * value for value in vector))
    if length <= 1e-15:
        return [0.0, 0.0, 0.0]
    angle = 2.0 * math.atan2(length, max(0.0, w))
    return [angle * value / length for value in vector]


def _constraint_residuals(mate: dict[str, Any],
                          poses: dict[str, dict[str, list[float]]], *,
                          coordinates: bool = True) -> list[float]:
    world_a = _compose(poses[mate["a_instance_id"]], mate["frame_a"])
    world_b = _compose(poses[mate["b_instance_id"]], mate["frame_b"])
    delta = [world_b["translation"][i] - world_a["translation"][i] for i in range(3)]
    axis_a = _unit_vector(_rotate(world_a["rotation"], [0.0, 0.0, 1.0]))
    axis_b = _unit_vector(_rotate(world_b["rotation"], [0.0, 0.0, 1.0]))
    cross = _cross(axis_a, axis_b)
    tangent_a, tangent_b = _tangent_basis(axis_a)
    axis_residuals = [sum(cross[i] * tangent[i] for i in range(3))
                      for tangent in (tangent_a, tangent_b)]
    point_residuals = [sum(delta[i] * tangent[i] for i in range(3))
                       for tangent in (tangent_a, tangent_b)]
    kind = mate["kind"]
    if kind == "fixed":
        relative = _compose(_inverse(world_a), world_b)
        error = _compose(_inverse(_motion(mate)), relative)
        return [*error["translation"], *_rotation_vector(error["rotation"])]
    if kind in {"coincident", "spherical"}:
        return delta
    if kind in {"coaxial", "cylindrical"}:
        residuals = [*axis_residuals, *point_residuals]
        if coordinates:
            relative = _compose(_inverse(world_a), world_b)
            error = _compose(_inverse(_motion(mate)), relative)
            residuals.extend((sum(delta[i] * axis_a[i] for i in range(3)) - mate["offset_mm"],
                              _rotation_vector(error["rotation"])[2]))
        return residuals
    if kind == "revolute":
        residuals = [*axis_residuals, *point_residuals,
                     sum(delta[i] * axis_a[i] for i in range(3))]
        if coordinates:
            relative = _compose(_inverse(world_a), world_b)
            error = _compose(_inverse(_motion(mate)), relative)
            residuals.append(_rotation_vector(error["rotation"])[2])
        return residuals
    if kind == "slider":
        relative_rotation = _multiply(
            [world_a["rotation"][0], *[-value for value in world_a["rotation"][1:]]],
            world_b["rotation"],
        )
        residuals = [*_rotation_vector(relative_rotation), *point_residuals]
        if coordinates:
            residuals.append(sum(delta[i] * axis_a[i] for i in range(3)) - mate["offset_mm"])
        return residuals
    if kind == "parallel":
        return axis_residuals
    if kind == "perpendicular":
        return [sum(axis_a[i] * axis_b[i] for i in range(3))]
    if kind == "distance":
        if mate["offset_mm"] <= POSITION_TOLERANCE_MM:
            return delta
        return [math.dist(world_a["translation"], world_b["translation"]) - mate["offset_mm"]]
    if kind == "angle":
        target = math.radians(mate["angle_deg"])
        if mate["angle_deg"] <= 1e-8 or mate["angle_deg"] >= 180.0 - 1e-8:
            sign = 1.0 if mate["angle_deg"] <= 1e-8 else -1.0
            angular_cross = _cross(axis_a, [sign * value for value in axis_b])
            return [sum(angular_cross[i] * tangent[i] for i in range(3))
                    for tangent in (tangent_a, tangent_b)]
        return [sum(axis_a[i] * axis_b[i] for i in range(3)) - math.cos(target)]
    raise ValidationError("Assembly solver has no residual definition for this mate kind.")


def _constraint_rank_count(mate: dict[str, Any]) -> int:
    kind = mate["kind"]
    if kind == "fixed":
        return 6
    if kind in {"coincident", "spherical"}:
        return 3
    if kind in {"coaxial", "cylindrical"}:
        return 4
    if kind in {"revolute", "slider"}:
        return 5
    if kind == "parallel":
        return 2
    if kind == "perpendicular":
        return 1
    if kind == "distance":
        return 3 if mate["offset_mm"] <= POSITION_TOLERANCE_MM else 1
    if kind == "angle":
        return 2 if mate["angle_deg"] <= 1e-8 or mate["angle_deg"] >= 180.0 - 1e-8 else 1
    raise ValidationError("Assembly solver has no rank definition for this mate kind.")


def _pose_with_perturbation(pose: dict[str, list[float]], axis: int,
                            amount: float) -> dict[str, list[float]]:
    changed = _pose(pose["translation"].copy(), pose["rotation"].copy())
    if axis < 3:
        changed["translation"][axis] += amount
        return changed
    component = axis - 3
    half = amount / 2.0
    delta = [math.cos(half), 0.0, 0.0, 0.0]
    delta[component + 1] = math.sin(half)
    changed["rotation"] = _multiply(delta, changed["rotation"])
    return changed


def _mate_jacobian_rows(mate: dict[str, Any], poses: dict[str, dict[str, list[float]]],
                        columns: dict[tuple[str, int], int], *,
                        coordinates: bool = True) -> list[dict[int, float]]:
    base = _constraint_residuals(mate, poses, coordinates=coordinates)
    rows = [dict() for _ in base]
    for instance_id in (mate["a_instance_id"], mate["b_instance_id"]):
        for axis in range(6):
            column = columns.get((instance_id, axis))
            if column is None:
                continue
            current = (poses[instance_id]["translation"][axis] if axis < 3 else 0.0)
            step = 1e-6 * max(1.0, abs(current)) if axis < 3 else 1e-6
            plus, minus = dict(poses), dict(poses)
            plus[instance_id] = _pose_with_perturbation(poses[instance_id], axis, step)
            minus[instance_id] = _pose_with_perturbation(poses[instance_id], axis, -step)
            left = _constraint_residuals(mate, plus, coordinates=coordinates)
            right = _constraint_residuals(mate, minus, coordinates=coordinates)
            for index, (low, high) in enumerate(zip(left, right, strict=True)):
                derivative = (low - high) / (2.0 * step)
                if abs(derivative) >= 1e-12:
                    rows[index][column] = derivative
    return rows


def _pose_residuals(mates: list[dict[str, Any]],
                    poses: dict[str, dict[str, list[float]]]) -> list[float]:
    return [value for mate in mates for value in _constraint_residuals(mate, poses)]


def _optimize_loop_component(mates: list[dict[str, Any]],
                             poses: dict[str, dict[str, list[float]]],
                             free_instances: list[str]) -> bool:
    """Bounded damped least-squares closure solve, preserving grounded poses."""
    columns = {(ident, axis): index * 6 + axis
               for index, ident in enumerate(free_instances) for axis in range(6)}
    size = len(columns)
    if not size:
        return max(map(abs, _pose_residuals(mates, poses)), default=0.0) <= POSITION_TOLERANCE_MM
    damping = 1e-3
    values = _pose_residuals(mates, poses)
    score = sum(value * value for value in values)
    for _ in range(MAX_LOOP_SOLVER_ITERATIONS):
        if max(map(abs, values), default=0.0) <= POSITION_TOLERANCE_MM:
            return True
        sparse_jacobian = [row for mate in mates for row in _mate_jacobian_rows(mate, poses, columns)]
        normal: dict[int, dict[int, float]] = {}
        gradient = [0.0] * size
        for row, residual in zip(sparse_jacobian, values, strict=True):
            for i, first in row.items():
                gradient[i] -= first * residual
                entries = normal.setdefault(i, {})
                for j, second in row.items():
                    entries[j] = entries.get(j, 0.0) + first * second
        for index in range(size):
            row = normal.setdefault(index, {})
            row[index] = row.get(index, 0.0) + damping * max(1.0, row.get(index, 0.0))
        delta = solve_sparse_positive_definite(normal, gradient)
        if delta is None:
            return False
        trial_poses = {ident: _pose(pose["translation"].copy(), pose["rotation"].copy())
                       for ident, pose in poses.items()}
        for ident in free_instances:
            pose = trial_poses[ident]
            index = columns[(ident, 0)]
            for axis in range(3):
                pose["translation"][axis] += delta[index + axis]
            rotation_delta = [0.0, 0.0, 0.0, 0.0]
            magnitude = math.sqrt(sum(delta[index + axis] ** 2 for axis in range(3, 6)))
            if magnitude:
                half = magnitude / 2.0
                rotation_delta = [math.cos(half), *(math.sin(half) * delta[index + axis] / magnitude
                                                     for axis in range(3, 6))]
                pose["rotation"] = _multiply(rotation_delta, pose["rotation"])
        trial_values = _pose_residuals(mates, trial_poses)
        trial_score = sum(value * value for value in trial_values)
        if math.isfinite(trial_score) and trial_score < score:
            poses.update(trial_poses)
            values, score = trial_values, trial_score
            damping = max(1e-12, damping * 0.3)
        else:
            damping = min(1e12, damping * 10.0)
    return max(map(abs, values), default=0.0) <= POSITION_TOLERANCE_MM


def _raise_mate_conflict(mate: dict[str, Any], first: str, second: str,
                         poses: dict[str, dict[str, list[float]]], *,
                         grounded: bool = False) -> None:
    residuals = _constraint_residuals(mate, poses)
    maximum = max(map(abs, residuals), default=0.0)
    message = (f"Conflicting grounded instance {second} at mate {mate['id']}; its saved pose "
               "does not satisfy the assembly connection." if grounded else
               f"Conflicting assembly mate loop at {mate['id']} between {first} and {second}; "
               "check frames and joint coordinates.")
    raise ValidationError(message, code="ASSEMBLY_CONSTRAINT_CONFLICT",
                          diagnostics={"category": "constraint_solver",
                                       "entity_ids": [mate["id"], first, second],
                                       "failed_precondition": "all assembly mate residuals must be satisfied",
                                       "measurements": {"residuals": residuals,
                                                        "maximum_absolute_residual": maximum,
                                                        "translation_tolerance_mm": POSITION_TOLERANCE_MM,
                                                        "rotation_tolerance_rad": ROTATION_TOLERANCE_RAD},
                                       "correction_hint": "Inspect the mate frames, joint coordinates and grounded poses."})


def solve_assembly_mates(graph: dict[str, Any], *,
                         anchor_instance_id: str | None = None,
                         require_unfixed_anchor: bool = False) -> dict[str, Any]:
    """Solve placements in parent-local frames; mutate only after closure checks."""
    if len(graph.get("instances", [])) > MAX_ASSEMBLY_INSTANCES:
        raise ValidationError("Assembly solver instance limit exceeded.", code="RESOURCE_LIMIT",
                              diagnostics={"category": "resource_limit",
                                           "measurements": {"instance_count": len(graph["instances"]),
                                                            "maximum": MAX_ASSEMBLY_INSTANCES}})
    if len(graph.get("mates", [])) > MAX_ASSEMBLY_MATES:
        raise ValidationError("Assembly solver mate limit exceeded.", code="RESOURCE_LIMIT",
                              diagnostics={"category": "resource_limit",
                                           "measurements": {"mate_count": len(graph["mates"]),
                                                            "maximum": MAX_ASSEMBLY_MATES}})
    validate_mate_records(graph)
    instances = {item["id"]: item for item in graph["instances"]}
    initial = {ident: _pose(item["translation"].copy(), item["rotation"].copy())
               for ident, item in instances.items()}
    if anchor_instance_id is not None and anchor_instance_id not in instances:
        raise ValidationError("Mate solve anchor instance does not exist.")
    adjacency: dict[str, list[tuple[dict[str, Any], bool]]] = {
        ident: [] for ident in instances
    }
    for mate in graph["mates"]:
        adjacency[mate["a_instance_id"]].append((mate, True))
        adjacency[mate["b_instance_id"]].append((mate, False))
    for neighbors in adjacency.values():
        neighbors.sort(key=lambda entry: entry[0]["id"])

    solutions: dict[str, dict[str, list[float]]] = {}
    anchors: list[str] = []
    redundant: set[str] = set()
    loop_components: set[str] = set()
    component_for: dict[str, str] = {}
    grounded = sorted(ident for ident, item in instances.items() if item["grounded"])
    preferred = [anchor_instance_id] if anchor_instance_id is not None else []
    order = grounded + [ident for ident in preferred if ident not in grounded] + [
        ident for ident in sorted(instances)
        if ident not in grounded and ident not in preferred
    ]
    for root in order:
        if root in solutions:
            continue
        anchors.append(root)
        component_for[root] = root
        solutions[root] = _pose(instances[root]["translation"].copy(),
                                instances[root]["rotation"].copy())
        queue = deque([root])
        while queue:
            current = queue.popleft()
            by_neighbor: dict[str, list[tuple[dict[str, Any], bool]]] = {}
            for mate, forward in adjacency[current]:
                other = mate["b_instance_id"] if forward else mate["a_instance_id"]
                by_neighbor.setdefault(other, []).append((mate, forward))
            for other, relations in by_neighbor.items():
                relations.sort(key=lambda entry: (
                    entry[0]["kind"] in AXIS_RELATIONS,
                    -(6 - MATE_DOF[entry[0]["kind"]]), entry[0]["id"],
                ))
                if other in solutions:
                    for mate, forward in relations:
                        satisfied = (_relation_matches(solutions[current], solutions[other], mate, forward)
                                     if mate["kind"] in AXIS_RELATIONS
                                     else _matches(solutions[other], _next_pose(
                                         solutions[current], mate, forward, initial)))
                        if not satisfied:
                            loop_components.add(root)
                    continue

                mate, forward = relations[0]
                candidate = _next_pose(solutions[current], mate, forward, initial)
                for additional, additional_forward in relations[1:]:
                    if additional["kind"] in AXIS_RELATIONS:
                        source_id = (additional["a_instance_id"] if additional_forward
                                     else additional["b_instance_id"])
                        target_id = (additional["b_instance_id"] if additional_forward
                                     else additional["a_instance_id"])
                        local_initial = {source_id: solutions[current], target_id: candidate}
                        candidate = _next_pose(solutions[current], additional,
                                               additional_forward, local_initial)
                    else:
                        exact = _next_pose(solutions[current], additional,
                                           additional_forward, initial)
                        if not _matches(candidate, exact):
                            loop_components.add(root)

                grounded_pose = initial[other]
                for mate, forward in relations:
                    if mate["kind"] in AXIS_RELATIONS:
                        satisfied = _relation_matches(solutions[current], candidate, mate, forward)
                    else:
                        exact = _next_pose(solutions[current], mate, forward, initial)
                        satisfied = _matches(candidate, exact)
                    if instances[other]["grounded"]:
                        satisfied = (_relation_matches(solutions[current], grounded_pose, mate, forward)
                                     if mate["kind"] in AXIS_RELATIONS
                                     else _matches(grounded_pose, _next_pose(
                                         solutions[current], mate, forward, initial)))
                    if not satisfied:
                        loop_components.add(root)
                solutions[other] = grounded_pose if instances[other]["grounded"] else candidate
                queue.append(other)
                component_for[other] = root

        if root in loop_components:
            members = {ident for ident, component in component_for.items() if component == root}
            anchored = {ident for ident in members if instances[ident]["grounded"]}
            free_instances = sorted(members - anchored) if anchored else sorted(members - {root})
            component_mates = [mate for mate in graph["mates"]
                               if component_for.get(mate["a_instance_id"]) == root]
            component_poses = {ident: solutions[ident] for ident in members}
            if not _optimize_loop_component(component_mates, component_poses, free_instances):
                failing = max(component_mates, key=lambda mate: max(map(
                    abs, _constraint_residuals(mate, component_poses)), default=0.0))
                first, second = failing["a_instance_id"], failing["b_instance_id"]
                _raise_mate_conflict(failing, first, second, component_poses,
                                     grounded=instances[first]["grounded"] or instances[second]["grounded"])
            solutions.update(component_poses)

        if require_unfixed_anchor and root != anchor_instance_id:
            if anchor_instance_id in solutions and any(
                item["grounded"] and item["id"] in solutions
                for item in instances.values()
                if item["id"] != anchor_instance_id
            ):
                raise ValidationError(
                    f"Cannot move {anchor_instance_id}: its assembly group contains a grounded instance; "
                    "change a joint coordinate instead."
                )

    internal_dof = 0
    constraint_rank = 0
    constraint_equation_count = 0
    rank_methods: set[str] = set()
    mates_by_component: dict[str, list[dict[str, Any]]] = {root: [] for root in anchors}
    for mate in graph["mates"]:
        mates_by_component[component_for[mate["a_instance_id"]]].append(mate)
    for root in anchors:
        members = {ident for ident, component in component_for.items() if component == root}
        anchored = {ident for ident in members if instances[ident]["grounded"]}
        free_instances = sorted(members - anchored) if anchored else sorted(members - {root})
        columns = {(ident, axis): index * 6 + axis
                   for index, ident in enumerate(free_instances) for axis in range(6)}
        component_poses = {ident: solutions[ident] for ident in members}
        component_mates = sorted(mates_by_component[root], key=lambda mate: mate["id"])
        if (len(component_mates) == len(members) - 1
                and len(free_instances) == len(members) - 1):
            # A tree adds one previously unconstrained body per edge, so the
            # local rank of each mate sums exactly without building a global Jacobian.
            rank = sum(_constraint_rank_count(mate) for mate in component_mates)
            rank_methods.add("tree_constraint_rank")
            equations = sum(len(_constraint_residuals(mate, component_poses, coordinates=False))
                            for mate in component_mates)
            constraint_rank += rank
            constraint_equation_count += equations
            internal_dof += max(0, len(columns) - rank)
            continue

        rank_methods.add("finite_difference_pose_jacobian")
        rank_tracker = SparseRankAccumulator(tolerance=CONSTRAINT_RANK_TOLERANCE)
        for mate in component_mates:
            rows = _mate_jacobian_rows(mate, component_poses, columns, coordinates=False)
            independent_rows = sum(rank_tracker.add_row(row) for row in rows)
            constraint_equation_count += len(rows)
            if independent_rows < len(rows):
                redundant.add(mate["id"])
        constraint_rank += rank_tracker.rank
        internal_dof += max(0, len(columns) - rank_tracker.rank)

    # Only commit placements when every closure is verified.
    for ident, pose in solutions.items():
        instances[ident]["translation"] = pose["translation"]
        instances[ident]["rotation"] = pose["rotation"]
    return {
        "status": "overconstrained" if redundant else (
            "underconstrained" if internal_dof or len(anchors) > 1 else "fully_mated"),
        "anchor_instance_ids": anchors,
        "component_count": len(anchors),
        "global_dof": sum(0 if instances[root]["grounded"] else 6 for root in anchors),
        "internal_dof": internal_dof,
        "constraint_rank": constraint_rank,
        "constraint_equation_count": constraint_equation_count,
        "rank_method": "+".join(sorted(rank_methods)) or "no_constraints",
        "rank_tolerance": CONSTRAINT_RANK_TOLERANCE,
        "grounded_instance_ids": sorted(
            ident for ident, item in instances.items() if item["grounded"]
        ),
        "redundant_mate_ids": sorted(redundant),
        "instance_poses": [
            {"instance_id": ident, "translation": solutions[ident]["translation"].copy(),
             "rotation": solutions[ident]["rotation"].copy()}
            for ident in sorted(solutions)
        ],
        "tolerances": {"translation_mm": POSITION_TOLERANCE_MM,
                       "rotation_rad": ROTATION_TOLERANCE_RAD},
    }


def inspect_assembly_mates(graph: dict[str, Any]) -> dict[str, Any]:
    """Read-only, deterministic joint diagnostics at the saved assembly pose."""
    snapshot = deepcopy(graph)
    report = solve_assembly_mates(snapshot)
    original = {entry["id"]: entry for entry in graph["instances"]}
    for pose in report["instance_poses"]:
        entry = original[pose["instance_id"]]
        if not _matches(entry, pose):
            raise ValidationError(
                f"Saved assembly placement of {pose['instance_id']} violates its mates.")
    return report
