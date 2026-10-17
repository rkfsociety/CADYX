"""Face/edge topology metadata and geometric selector fingerprints for agent authoring."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .advanced_features import _raw_brep
from .errors import ValidationError
from .native_kernel import NativeKernel



def _rounded(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 6)
    if isinstance(value, list):
        return [_rounded(item) for item in value]
    return value


def _signature(kind: str, item: dict[str, Any]) -> str:
    payload: dict[str, Any] = {
        "kind": kind,
        "type": item.get("surface") or item.get("curve"),
        "center": _rounded(item.get("center", [])),
        "measure": _rounded(item.get("area", item.get("length", 0.0))),
    }
    analytic = {}
    for key in (
        "radius",
        "major_radius",
        "minor_radius",
        "reference_radius",
        "semi_angle",
        "origin",
        "normal",
        "direction",
        "axis_origin",
        "axis_direction",
        "start",
        "end",
    ):
        if key in item:
            analytic[key] = _rounded(item[key])
    if analytic:
        payload["analytic"] = analytic
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:20]
    return f"{kind}:{digest}"


def _merge_by_index(
    base: list[dict[str, Any]], extra: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    details = {
        item.get("index"): item
        for item in extra
        if isinstance(item, dict) and type(item.get("index")) is int
    }
    for item in base:
        if not isinstance(item, dict):
            continue
        supplement = details.get(item.get("index"))
        if supplement:
            for key, value in supplement.items():
                if key != "index":
                    item[key] = value
    return base


def enrich_topology(geometry: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    native = NativeKernel()
    brep = _raw_brep(geometry)

    details = native.agent_topology(brep)
    if details is None:
        details = {"edges": [], "faces": []}
    extra_edges = details.get("edges")
    faces = details.get("faces")
    if not isinstance(extra_edges, list) or not isinstance(faces, list):
        raise ValidationError("OCCT returned invalid analytic topology metadata.")

    # New runtimes return complete face metadata together with analytic
    # topology, avoiding a second B-rep parse and face traversal. Keep the
    # old bridge as a compatibility fallback for already-built runtimes.
    if any(not isinstance(face, dict) or "surface" not in face for face in faces):
        face_payload = native.face_topology(brep)
        if face_payload is None:
            face_payload = {"faces": []}
        base_faces = face_payload.get("faces")
        if not isinstance(base_faces, list):
            raise ValidationError("OCCT returned invalid face topology metadata.")
        faces = _merge_by_index(base_faces, faces)

    edges = result.get("edges")
    if isinstance(edges, list):
        _merge_by_index(edges, extra_edges)
        for edge in edges:
            if isinstance(edge, dict):
                edge["semantic_key"] = _signature("edge", edge)

    for face in faces:
        if isinstance(face, dict):
            face["semantic_key"] = _signature("face", face)
    result["faces"] = faces
    return result
