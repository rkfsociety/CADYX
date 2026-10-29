"""Deterministic, caller-driven rigid assembly mate solver.

Mates act on sibling instance frames (not B-rep geometry). They never fuse parts.
The first instance in each connected component, or the requested anchor, is
grounded for a solve. Loop closures are checked instead of silently overridden.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
import json
import math
from typing import Any

from .assemblies import _multiply, _rotate, transform_point, validate_transform
from .errors import ValidationError

MATE_DOF = {"fixed": 0, "coincident": 3, "coaxial": 2,
            "revolute": 1, "slider": 1, "cylindrical": 2, "spherical": 3,
            "parallel": 4, "perpendicular": 5, "distance": 5, "angle": 5}
AXIS_RELATIONS = {"parallel", "perpendicular", "distance", "angle"}
POSITION_TOLERANCE_MM = 1e-6
ROTATION_TOLERANCE_RAD = 1e-6
MAX_ASSEMBLY_INSTANCES = 16384
MAX_ASSEMBLY_MATES = 65536
IDENTITY_FRAME = {"translation": [0.0, 0.0, 0.0],
                  "rotation": [1.0, 0.0, 0.0, 0.0]}
DEFAULT_LIMITS = {"angle_min_deg": None, "angle_max_deg": None,
                  "offset_min_mm": None, "offset_max_mm": None}
FACE_SURFACES = {"plane", "cylinder", "cone", "sphere"}


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
        "body_id", "face_key", "face_index", "surface", "origin", "direction"
    }:
        raise ValidationError(f"{label} requires body_id, semantic face key, index, surface and local axis data.")
    if (not isinstance(value["body_id"], str) or not value["body_id"].startswith("body:")
            or not isinstance(value["face_key"], str) or not value["face_key"].startswith("face:")
            or type(value["face_index"]) is not int or value["face_index"] < 0
            or value["surface"] not in FACE_SURFACES):
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


def _mate_signature(mate: dict[str, Any]) -> str:
    reverse = mate["a_instance_id"] > mate["b_instance_id"]
    kind = mate["kind"]
    if kind in {"coincident", "spherical"} or (kind == "distance" and mate["offset_mm"] == 0):
        kind = "origin_coincidence"
    elif kind == "angle" and mate["angle_deg"] in {0, 180}:
        kind = "parallel"
    elif kind == "angle" and mate["angle_deg"] == 90:
        kind = "perpendicular"
    first = mate["frame_b" if reverse else "frame_a"]
    second = mate["frame_a" if reverse else "frame_b"]
    pair = sorted((mate["a_instance_id"], mate["b_instance_id"]))
    if kind == "origin_coincidence":
        fields = [pair, kind, first["translation"], second["translation"]]
    else:
        fields = [pair, kind, first, second, float(mate["angle_deg"]), float(mate["offset_mm"]),
                  mate["orientation"],
                  mate["reference_b" if reverse else "reference_a"],
                  mate["reference_a" if reverse else "reference_b"]]
    return json.dumps(fields, sort_keys=True, separators=(",", ":"))


def _matches(a: dict[str, Any], b: dict[str, Any]) -> bool:
    distance = math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in
                             zip(a["translation"], b["translation"])))
    dot = abs(sum(float(x) * float(y) for x, y in zip(a["rotation"], b["rotation"])))
    angle = 2 * math.acos(min(1.0, max(-1.0, dot)))
    return distance <= POSITION_TOLERANCE_MM and angle <= ROTATION_TOLERANCE_RAD


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
                            raise ValidationError(
                                f"Conflicting assembly mate loop at {mate['id']} between "
                                f"{current} and {other}; check frames and joint coordinates.")
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
                            raise ValidationError(
                                f"Conflicting assembly mate loop at {additional['id']} between "
                                f"{current} and {other}; check frames and joint coordinates.")

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
                        if instances[other]["grounded"]:
                            raise ValidationError(
                                f"Conflicting grounded instance {other} at mate {mate['id']}; "
                                "its saved pose does not satisfy the assembly connection.")
                        raise ValidationError(
                            f"Conflicting assembly mate loop at {mate['id']} between "
                            f"{current} and {other}; check frames and joint coordinates.")
                solutions[other] = grounded_pose if instances[other]["grounded"] else candidate
                queue.append(other)
                component_for[other] = root

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
    mates_by_component: dict[str, list[dict[str, Any]]] = {root: [] for root in anchors}
    for mate in graph["mates"]:
        mates_by_component[component_for[mate["a_instance_id"]]].append(mate)
    for root in anchors:
        members = {ident for ident, component in component_for.items() if component == root}
        budget = 6 * max(0, len(members) - 1)
        used_constraints = 0
        signatures: set[str] = set()
        component_mates = sorted(mates_by_component[root],
                                 key=lambda mate: (-(6 - MATE_DOF[mate["kind"]]), mate["id"]))
        for mate in component_mates:
            signature = _mate_signature(mate)
            contribution = 6 - MATE_DOF[mate["kind"]]
            if signature in signatures or used_constraints + contribution > budget:
                redundant.add(mate["id"])
                continue
            signatures.add(signature)
            used_constraints += contribution
        internal_dof += max(0, budget - used_constraints)

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
