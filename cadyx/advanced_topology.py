"""Face/edge topology metadata and geometric selector fingerprints for agent authoring."""
from __future__ import annotations

import ctypes
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


def _call_json(
    native: NativeKernel,
    brep: bytes,
    function_name: str,
    *,
    missing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    library = native.library
    if not hasattr(library, function_name):
        return {} if missing is None else missing
    data = (ctypes.c_ubyte * len(brep)).from_buffer_copy(brep)
    function = getattr(library, function_name)
    function.argtypes = [
        ctypes.POINTER(ctypes.c_ubyte),
        ctypes.c_size_t,
        ctypes.c_char_p,
        ctypes.c_size_t,
    ]
    function.restype = ctypes.c_void_p
    error = ctypes.create_string_buffer(2048)
    result = function(data, len(brep), error, len(error))
    if not result:
        raise ValidationError(error.value.decode("utf-8", errors="replace"))
    try:
        payload = json.loads(ctypes.string_at(result))
    finally:
        library.cadyx_free(result)
    if not isinstance(payload, dict):
        raise ValidationError("OCCT returned invalid topology metadata.")
    return payload


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

    details = _call_json(
        native,
        brep,
        "cadyx_agent_topology",
        missing={"edges": [], "faces": []},
    )
    extra_edges = details.get("edges")
    faces = details.get("faces")
    if not isinstance(extra_edges, list) or not isinstance(faces, list):
        raise ValidationError("OCCT returned invalid analytic topology metadata.")

    # New runtimes return complete face metadata together with analytic
    # topology, avoiding a second B-rep parse and face traversal. Keep the
    # old bridge as a compatibility fallback for already-built runtimes.
    if any(not isinstance(face, dict) or "surface" not in face for face in faces):
        face_payload = _call_json(
            native, brep, "cadyx_face_topology", missing={"faces": []}
        )
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
