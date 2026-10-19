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

MATE_DOF = {"fixed": 0, "coincident": 3, "coaxial": 2,
            "revolute": 1, "slider": 1, "cylindrical": 2}
POSITION_TOLERANCE_MM = 1e-6
ROTATION_TOLERANCE_RAD = 1e-6
IDENTITY_FRAME = {"translation": [0.0, 0.0, 0.0],
                  "rotation": [1.0, 0.0, 0.0, 0.0]}


def _number(value: Any, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValidationError(f"{label} must be a finite number.")
    return float(value)


def _frame(value: Any, label: str) -> None:
    if not isinstance(value, dict) or set(value) != {"translation", "rotation"}:
        raise ValidationError(f"{label} requires translation and rotation.")
    validate_transform(value["translation"], value["rotation"])


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
            "frame_a", "frame_b", "angle_deg", "offset_mm", "orientation"
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
        if kind in ("fixed", "coincident") and (angle != 0 or offset != 0):
            raise ValidationError("Fixed/coincident mates do not allow joint angle or offset.")
        if kind == "revolute" and offset != 0:
            raise ValidationError("Revolute mates do not allow axial offset.")
        if kind == "slider" and angle != 0:
            raise ValidationError("Slider mates do not allow axial rotation.")
        if kind != "coincident" and orientation != [1.0, 0.0, 0.0, 0.0]:
            raise ValidationError("Only coincident mates accept free relative orientation.")


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
    if kind == "coincident":
        rotation = mate["orientation"].copy()
    elif kind in ("coaxial", "cylindrical", "revolute"):
        half = math.radians(mate["angle_deg"]) / 2
        rotation = [math.cos(half), 0.0, 0.0, math.sin(half)]
    else:
        rotation = [1.0, 0.0, 0.0, 0.0]
    z = mate["offset_mm"] if kind in ("coaxial", "cylindrical", "slider") else 0.0
    return _pose([0.0, 0.0, z], rotation)


def _next_pose(source: dict[str, Any], mate: dict[str, Any],
               forward: bool) -> dict[str, list[float]]:
    left = mate["frame_a"] if forward else mate["frame_b"]
    right = mate["frame_b"] if forward else mate["frame_a"]
    motion = _motion(mate)
    return _compose(_compose(_compose(source, left),
                             motion if forward else _inverse(motion)),
                    _inverse(right))


def _matches(a: dict[str, Any], b: dict[str, Any]) -> bool:
    distance = math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in
                             zip(a["translation"], b["translation"])))
    dot = abs(sum(float(x) * float(y) for x, y in zip(a["rotation"], b["rotation"])))
    angle = 2 * math.acos(min(1.0, max(-1.0, dot)))
    return distance <= POSITION_TOLERANCE_MM and angle <= ROTATION_TOLERANCE_RAD


def solve_assembly_mates(graph: dict[str, Any], *,
                         anchor_instance_id: str | None = None) -> dict[str, Any]:
    """Solve placements in parent-local frames; mutate only after closure checks."""
    validate_mate_records(graph)
    instances = {item["id"]: item for item in graph["instances"]}
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
    internal_dof = 0
    redundant: set[str] = set()
    used: set[str] = set()
    order = sorted(instances)
    if anchor_instance_id is not None:
        order.remove(anchor_instance_id)
        order.insert(0, anchor_instance_id)
    for root in order:
        if root in solutions:
            continue
        anchors.append(root)
        solutions[root] = _pose(instances[root]["translation"].copy(),
                                instances[root]["rotation"].copy())
        queue = deque([root])
        while queue:
            current = queue.popleft()
            for mate, forward in adjacency[current]:
                other = mate["b_instance_id"] if forward else mate["a_instance_id"]
                candidate = _next_pose(solutions[current], mate, forward)
                if other in solutions:
                    if not _matches(solutions[other], candidate):
                        raise ValidationError(
                            f"Conflicting assembly mate loop at {mate['id']} between "
                            f"{current} and {other}; check frames and joint coordinates.")
                    if mate["id"] not in used:
                        redundant.add(mate["id"])
                else:
                    solutions[other] = candidate
                    queue.append(other)
                    internal_dof += MATE_DOF[mate["kind"]]
                used.add(mate["id"])

    # Only commit placements when every closure is verified.
    for ident, pose in solutions.items():
        instances[ident]["translation"] = pose["translation"]
        instances[ident]["rotation"] = pose["rotation"]
    return {
        "status": "overconstrained" if redundant else (
            "underconstrained" if internal_dof or len(anchors) > 1 else "fully_mated"),
        "anchor_instance_ids": anchors,
        "component_count": len(anchors),
        "global_dof": 6 * len(anchors),
        "internal_dof": internal_dof,
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
