"""Revision-bound, non-mutating interference screens for placed assembly instances.

Every result covers only the saved pose and the explicitly requested pose samples.
Transforms are applied to temporary OCCT B-reps, never to the document.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Callable

from .assemblies import flatten_assembly
from .assembly_mates import solve_assembly_mates
from .errors import ValidationError


def _nonnegative(value: Any, name: str) -> float:
    if type(value) not in (int, float):
        raise ValidationError(f"{name} must be a finite non-negative number.")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValidationError(f"{name} must be a finite non-negative number.") from None
    if not math.isfinite(result) or result < 0:
        raise ValidationError(f"{name} must be a finite non-negative number.")
    return result


def _samples(value: Any, graph: dict[str, Any]) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 16:
        raise ValidationError("pose_samples must be a list of up to 16 discrete joint configurations.")
    mates = {mate["id"]: mate for mate in graph["mates"]}
    normalized = []
    for sample in value:
        if not isinstance(sample, dict) or set(sample) != {"joint_values"}:
            raise ValidationError("Each pose sample requires exactly joint_values.")
        values = sample["joint_values"]
        if not isinstance(values, list) or not 1 <= len(values) <= 16:
            raise ValidationError("Each pose sample needs 1–16 joint overrides.")
        seen: set[str] = set()
        overrides = []
        for entry in values:
            if (not isinstance(entry, dict) or not
                    {"mate_id"} < set(entry) <= {"mate_id", "angle_deg", "offset_mm"}):
                raise ValidationError("Joint overrides require mate_id and at least one joint coordinate.")
            ident = entry["mate_id"]
            if not isinstance(ident, str) or ident not in mates or ident in seen:
                raise ValidationError("Joint override must identify one existing distinct mate.")
            seen.add(ident)
            for key in ("angle_deg", "offset_mm"):
                if key in entry:
                    number = entry[key]
                    if type(number) not in (int, float) or not math.isfinite(number):
                        raise ValidationError(f"{key} must be finite.")
            overrides.append(dict(entry))
        normalized.append({"joint_values": overrides})
    return normalized


def _pairs(value: Any, leaves: list[dict[str, Any]]) -> list[list[str]]:
    available = {entry["id"] for entry in leaves}
    if value is None:
        if len(leaves) > 8:
            raise ValidationError("Specify instance_pairs when an assembly has more than 8 leaf instances.")
        value = [[a["id"], b["id"]] for index, a in enumerate(leaves)
                 for b in leaves[index + 1:]]
    if not isinstance(value, list) or not 1 <= len(value) <= 32:
        raise ValidationError("instance_pairs must select 1–32 distinct leaf pairs.")
    seen: set[tuple[str, str]] = set()
    pairs = []
    for pair in value:
        if (not isinstance(pair, list) or len(pair) != 2
                or any(not isinstance(item, str) or item not in available for item in pair)
                or pair[0] == pair[1]):
            raise ValidationError("Each instance pair must name two distinct flattened leaf instance paths.")
        key = tuple(sorted(pair))
        if key in seen:
            raise ValidationError("Duplicate instance pair.")
        seen.add(key)
        pairs.append(list(pair))
    return pairs


def _classify(measured: dict[str, Any], required: float, tolerance: float) -> dict[str, Any]:
    distance = measured.get("minimum_distance")
    deflection = measured.get("deflection")
    volume = measured.get("intersection_volume")
    if (type(distance) not in (float, int) or not math.isfinite(distance) or distance < 0
            or type(deflection) not in (float, int) or not math.isfinite(deflection) or deflection <= 0
            or type(volume) not in (float, int) or not math.isfinite(volume) or volume < 0
            or type(measured.get("inner_solution")) is not bool
            or type(measured.get("solution_count")) is not int or measured["solution_count"] < 1):
        raise ValidationError("OCCT returned invalid assembly-fit metrics.")
    witnesses = []
    for field in ("point_on_first", "point_on_second"):
        point = measured.get(field)
        if (not isinstance(point, list) or len(point) != 3
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in point)):
            raise ValidationError("OCCT returned invalid assembly-fit witness points.")
        witnesses.append([float(v) for v in point])
    lower, upper = max(0.0, distance - deflection), distance + deflection
    threshold = max(0.0, required - tolerance)
    if measured["inner_solution"] or volume > deflection ** 3:
        interference = "interfering"
    elif volume > 0 or distance <= deflection:
        interference = "touching_or_indeterminate"
    else:
        interference = "separate"
    status = ("fail" if interference == "interfering" or upper < threshold
              else "pass" if interference == "separate" and lower >= threshold
              else "untested")
    return {
        "status": status, "interference_status": interference,
        "measured_distance": float(distance), "distance_interval": [lower, upper],
        "intersection_volume": float(volume), "deflection": float(deflection),
        "inner_solution": measured["inner_solution"],
        "point_on_first": witnesses[0], "point_on_second": witnesses[1],
    }


def evaluate_assembly_fit(
    document: Any, kernel: Any, instance_pairs: Any, pose_samples: Any,
    minimum_clearance: Any, tolerance: Any,
    *, progress: Callable[[str, float], None] | None = None,
) -> dict[str, Any]:
    """Check at most 128 exact B-rep pair measurements over finite sampled poses."""
    minimum = _nonnegative(minimum_clearance, "minimum_clearance")
    allowed = _nonnegative(tolerance, "tolerance")
    graph = document.assembly
    if not graph["parts"] or len(graph["instances"]) < 2:
        raise ValidationError("Assembly fit requires at least two part instances.")
    leaves = flatten_assembly(graph, limit=256)
    pairs = _pairs(instance_pairs, leaves)
    poses = _samples(pose_samples, graph)
    if len(pairs) * (1 + len(poses)) > 128:
        raise ValidationError("Assembly fit permits at most 128 pair/pose measurements.")
    bodies = {body["id"]: body for body in document.objects if body.get("type") == "body"}
    expected = {entry["id"] for entry in leaves}
    reports = []
    completed = 0
    total = len(pairs) * (len(poses) + 1)
    configurations = [{"joint_values": []}, *poses]
    for sample_index, configuration in enumerate(configurations):
        snapshot = deepcopy(graph)
        mates = {mate["id"]: mate for mate in snapshot["mates"]}
        for override in configuration["joint_values"]:
            mate = mates[override["mate_id"]]
            for field in ("angle_deg", "offset_mm"):
                if field in override:
                    mate[field] = float(override[field])
        try:
            solve_assembly_mates(snapshot)
        except ValidationError as exc:
            # A closed kinematic loop may not have a solution at the requested pose.
            reports.append({
                "sample_index": sample_index, "joint_values": configuration["joint_values"],
                "status": "untested", "reason": str(exc), "pairs": [],
            })
            completed += len(pairs)
            if progress:
                progress("assembly_pose_unsupported", completed / total)
            continue
        posed = {entry["id"]: entry for entry in flatten_assembly(snapshot, limit=256)}
        if set(posed) != expected:
            raise ValidationError("Assembly leaf paths changed while evaluating a pose.")
        pair_results = []
        for first_id, second_id in pairs:
            first, second = posed[first_id], posed[second_id]
            left = bodies[first["body_id"]]["geometry"]
            right = bodies[second["body_id"]]["geometry"]
            item = {
                "instance_ids": [first_id, second_id],
                "body_ids": [first["body_id"], second["body_id"]],
            }
            if any(g.get("kernel") == "cadyx-mesh-v1" for g in (left, right)):
                item.update({"status": "untested", "reason": "Triangle meshes are not exact OCCT B-reps."})
            else:
                def report(phase: str, fraction: float) -> None:
                    if progress:
                        progress(phase, (completed + max(0.0, min(1.0, fraction))) / total)
                measured = kernel.minimum_distance_placed(
                    left, right,
                    first_translation=first["translation"], first_rotation=first["rotation"],
                    second_translation=second["translation"], second_rotation=second["rotation"],
                    progress=report if progress else None,
                )
                item.update(_classify(measured, minimum, allowed))
            pair_results.append(item)
            completed += 1
            if progress:
                progress("assembly_fit", completed / total)
        statuses = {pair["status"] for pair in pair_results}
        reports.append({
            "sample_index": sample_index, "joint_values": configuration["joint_values"],
            "status": "fail" if "fail" in statuses else "untested" if "untested" in statuses else "pass",
            "instance_poses": [
                {"id": key, "translation": posed[key]["translation"], "rotation": posed[key]["rotation"]}
                for key in sorted(posed) if key in {item for pair in pairs for item in pair}
            ],
            "pairs": pair_results,
        })
    statuses = {sample["status"] for sample in reports}
    return {
        "status": "fail" if "fail" in statuses else "untested" if "untested" in statuses else "pass",
        "revision": document.revision, "method": "OCCT_BRepExtrema_DistShapeShape",
        "interference_method": "OCCT_BRepAlgoAPI_Common",
        "coverage": "discrete_poses_only", "continuous_motion_certified": False,
        "units": "mm", "volume_units": "mm^3",
        "minimum_clearance": minimum, "tolerance": allowed,
        "instance_pairs": pairs, "sample_count": len(reports),
        "pair_measurement_budget": 128, "samples": reports,
    }
