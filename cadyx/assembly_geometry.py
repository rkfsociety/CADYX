"""Resolve persistent assembly face references and derive local joint frames."""
from __future__ import annotations

import math
from copy import deepcopy
from collections import OrderedDict
import threading
from typing import Any

from .delivery import geometry_sha256
from .errors import ValidationError

_AXIAL_SURFACES = {"cylinder", "cone"}
_TOPOLOGY_CACHE_MAX_ENTRIES = 64
_TOPOLOGY_CACHE_MAX_BYTES = 16 * 1024 * 1024
_TOPOLOGY_CACHE: OrderedDict[tuple[Any, str], tuple[list[dict[str, Any]], int]] = OrderedDict()
_TOPOLOGY_CACHE_BYTES = 0
_TOPOLOGY_CACHE_LOCK = threading.RLock()


def _unit(value: Any, label: str) -> list[float]:
    if (not isinstance(value, list) or len(value) != 3
            or any(type(component) not in (int, float) or not math.isfinite(component)
                   for component in value)):
        raise ValidationError(f"Assembly face {label} must be a finite XYZ vector.")
    norm = math.sqrt(sum(float(component) ** 2 for component in value))
    if norm <= 1e-12:
        raise ValidationError(f"Assembly face {label} must be nonzero.")
    return [float(component) / norm for component in value]


def _cross(a: list[float], b: list[float]) -> list[float]:
    return [a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def face_frame(face: dict[str, Any]) -> dict[str, list[float]]:
    """Build a deterministic frame from the analytic face data returned by OCCT."""
    surface = face.get("surface")
    if surface == "plane":
        origin, direction = face.get("center"), face.get("normal")
    elif surface in _AXIAL_SURFACES:
        origin, direction = face.get("axis_origin"), face.get("axis_direction")
    elif surface == "sphere":
        origin, direction = face.get("center"), face.get("axis_direction")
    else:
        raise ValidationError(
            f"Assembly mates cannot derive a stable local frame from {surface!r} faces.",
            code="ASSEMBLY_REFERENCE_UNSUPPORTED",
        )
    if (not isinstance(origin, list) or len(origin) != 3
            or any(type(component) not in (int, float) or not math.isfinite(component)
                   for component in origin)):
        raise ValidationError("Assembly face has no finite local frame origin.")
    z_axis = _unit(direction, "axis")
    helper = [1.0, 0.0, 0.0] if abs(z_axis[0]) < 0.8 else [0.0, 1.0, 0.0]
    x_axis = _unit(_cross(helper, z_axis), "tangent axis")
    y_axis = _unit(_cross(z_axis, x_axis), "second tangent axis")
    m00, m01, m02 = x_axis[0], y_axis[0], z_axis[0]
    m10, m11, m12 = x_axis[1], y_axis[1], z_axis[1]
    m20, m21, m22 = x_axis[2], y_axis[2], z_axis[2]
    trace = m00 + m11 + m22
    if trace > 0:
        scale = math.sqrt(trace + 1.0) * 2
        quaternion = [0.25 * scale, (m21 - m12) / scale,
                      (m02 - m20) / scale, (m10 - m01) / scale]
    elif m00 > m11 and m00 > m22:
        scale = math.sqrt(1.0 + m00 - m11 - m22) * 2
        quaternion = [(m21 - m12) / scale, 0.25 * scale,
                      (m01 + m10) / scale, (m02 + m20) / scale]
    elif m11 > m22:
        scale = math.sqrt(1.0 + m11 - m00 - m22) * 2
        quaternion = [(m02 - m20) / scale, (m01 + m10) / scale,
                      0.25 * scale, (m12 + m21) / scale]
    else:
        scale = math.sqrt(1.0 + m22 - m00 - m11) * 2
        quaternion = [(m10 - m01) / scale, (m02 + m20) / scale,
                      (m12 + m21) / scale, 0.25 * scale]
    norm = math.sqrt(sum(component * component for component in quaternion))
    quaternion = [component / norm for component in quaternion]
    return {"translation": [float(component) for component in origin],
            "rotation": quaternion}


def make_face_reference(body_id: str, face: dict[str, Any]) -> dict[str, Any]:
    frame = face_frame(face)
    direction = frame["rotation"]
    # The frame's local Z direction is the persisted analytic axis/normal.
    w, x, y, z = direction
    axis = [2 * (x * z + w * y), 2 * (y * z - w * x),
            1 - 2 * (x * x + y * y)]
    return {"body_id": body_id, "face_key": face["semantic_key"],
            "face_index": face["index"], "surface": face["surface"],
            "origin": frame["translation"], "direction": axis}


def _reference_candidate(reference: dict[str, Any], faces: list[dict[str, Any]], *,
                         allow_rebind: bool) -> dict[str, Any]:
    exact = [face for face in faces if face.get("semantic_key") == reference["face_key"]]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise ValidationError("Assembly face semantic key resolves to multiple faces.",
                              code="ASSEMBLY_REFERENCE_AMBIGUOUS")
    if not allow_rebind:
        raise ValidationError("Assembly face reference is stale or missing.",
                              code="ASSEMBLY_REFERENCE_INVALID",
                              diagnostics={"entity_ids": [reference["face_key"]],
                                           "failed_precondition": "the saved semantic face key must resolve exactly"})

    direction = _unit(reference["direction"], "reference direction")
    origin = reference["origin"]
    candidates: list[tuple[float, dict[str, Any]]] = []
    for face in faces:
        if face.get("surface") != reference["surface"]:
            continue
        try:
            frame = face_frame(face)
            current = make_face_reference(reference["body_id"], face)
        except ValidationError:
            continue
        alignment = sum(a * b for a, b in zip(direction, current["direction"], strict=True))
        if reference["surface"] in _AXIAL_SURFACES:
            aligned = abs(alignment) >= 0.999
        elif reference["surface"] == "sphere":
            aligned = True
        else:
            aligned = alignment >= 0.999
        if not aligned:
            continue
        distance = math.dist(origin, frame["translation"])
        candidates.append((distance, face))
    candidates.sort(key=lambda item: (item[0], item[1].get("index", -1)))
    if not candidates:
        raise ValidationError("Assembly face reference cannot be resolved after a geometry change.",
                              code="ASSEMBLY_REFERENCE_INVALID",
                              diagnostics={"entity_ids": [reference["face_key"]],
                                           "failed_precondition": "a matching analytic face must remain"})
    if len(candidates) > 1 and abs(candidates[1][0] - candidates[0][0]) <= 1e-7:
        raise ValidationError("Assembly face reference became ambiguous after a geometry change.",
                              code="ASSEMBLY_REFERENCE_AMBIGUOUS",
                              diagnostics={"entity_ids": [reference["face_key"]],
                                           "failed_precondition": "one closest analytic face must remain"})
    return candidates[0][1]


def _faces_for_body(body: dict[str, Any], kernel: Any) -> list[dict[str, Any]]:
    global _TOPOLOGY_CACHE_BYTES
    # Topology is a kernel result, not just a property of serialized geometry:
    # adapters may implement different topology contracts for the same payload.
    key = (kernel, geometry_sha256(body["geometry"]))
    with _TOPOLOGY_CACHE_LOCK:
        cached = _TOPOLOGY_CACHE.get(key)
        if cached is not None:
            _TOPOLOGY_CACHE.move_to_end(key)
            return cached[0]
    topology = kernel.topology(body["geometry"])
    faces = topology.get("faces") if isinstance(topology, dict) else None
    if not isinstance(faces, list):
        raise ValidationError("The geometry kernel did not return assembly face metadata.",
                              code="ASSEMBLY_REFERENCE_INVALID")
    size = len(str(faces).encode("utf-8"))
    if size <= _TOPOLOGY_CACHE_MAX_BYTES:
        with _TOPOLOGY_CACHE_LOCK:
            while (_TOPOLOGY_CACHE and
                   (_TOPOLOGY_CACHE_BYTES + size > _TOPOLOGY_CACHE_MAX_BYTES
                    or len(_TOPOLOGY_CACHE) >= _TOPOLOGY_CACHE_MAX_ENTRIES)):
                _, (_, removed_size) = _TOPOLOGY_CACHE.popitem(last=False)
                _TOPOLOGY_CACHE_BYTES -= removed_size
            _TOPOLOGY_CACHE[key] = (faces, size)
            _TOPOLOGY_CACHE_BYTES += size
    return faces


def refresh_assembly_geometry_references(
    assembly: dict[str, Any], objects: list[dict[str, Any]], kernel: Any, *,
    allow_rebind: bool = True,
) -> dict[str, Any] | None:
    """Refresh geometry-backed frames, failing closed when a face cannot be resolved."""
    candidate = deepcopy(assembly)
    referenced = [mate for mate in candidate.get("mates", [])
                  if mate.get("reference_a") is not None or mate.get("reference_b") is not None]
    if not referenced:
        return None
    bodies = {item["id"]: item for item in objects if item.get("type") == "body"}
    parts = {item["id"]: item for item in candidate.get("parts", [])}
    instances = {item["id"]: item for item in candidate.get("instances", [])}
    topologies: dict[str, list[dict[str, Any]]] = {}
    changed = False
    for mate in referenced:
        kind = mate.get("kind")
        for side in ("a", "b"):
            surface = mate[f"reference_{side}"].get("surface")
            if kind == "spherical" and surface != "sphere":
                raise ValidationError("Spherical mates require spherical face references.",
                                      code="ASSEMBLY_REFERENCE_INVALID")
            if kind in {"revolute", "slider", "coaxial", "cylindrical"} \
                    and surface not in {"plane", "cylinder", "cone"}:
                raise ValidationError(f"{kind} mates require a planar or axial face reference.",
                                      code="ASSEMBLY_REFERENCE_INVALID")
        for side in ("a", "b"):
            key = f"reference_{side}"
            reference = mate[key]
            instance_id = mate[f"{side}_instance_id"]
            instance = instances.get(instance_id)
            part = parts.get(instance.get("target_id")) if instance else None
            if part is None or part.get("body_id") != reference["body_id"]:
                raise ValidationError(
                    f"Assembly {side.upper()} face reference does not belong to its instance part.",
                    code="ASSEMBLY_REFERENCE_INVALID",
                    diagnostics={"entity_ids": [instance_id, reference["body_id"]]},
                )
            body = bodies.get(reference["body_id"])
            if body is None or body.get("geometry") is None:
                raise ValidationError("Assembly face reference body is missing or has no B-rep geometry.",
                                      code="ASSEMBLY_REFERENCE_INVALID",
                                      diagnostics={"entity_ids": [reference["body_id"]]})
            faces = topologies.get(reference["body_id"])
            if faces is None:
                faces = _faces_for_body(body, kernel)
                topologies[reference["body_id"]] = faces
            face = _reference_candidate(reference, faces, allow_rebind=allow_rebind)
            frame = face_frame(face)
            updated = make_face_reference(reference["body_id"], face)
            if reference != updated or mate[f"frame_{side}"] != frame:
                if not allow_rebind:
                    raise ValidationError("Saved assembly face frame does not match the current B-rep.",
                                          code="ASSEMBLY_REFERENCE_INVALID",
                                          diagnostics={"entity_ids": [reference["face_key"]]})
                mate[key] = updated
                mate[f"frame_{side}"] = frame
                changed = True
    if changed:
        from .assembly_mates import solve_assembly_mates
        diagnostics = solve_assembly_mates(candidate)
        assembly.clear()
        assembly.update(candidate)
        return diagnostics
    return None
