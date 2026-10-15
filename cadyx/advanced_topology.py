"""Face topology and geometric selector fingerprints for advanced feature authoring."""
from __future__ import annotations

import ctypes
import hashlib
import json
from typing import Any

from .errors import ValidationError
from .native_kernel import NativeKernel

_INSTALLED = False


def _signature(kind: str, item: dict[str, Any]) -> str:
    center = item.get("center", [])
    payload = {
        "kind": kind,
        "type": item.get("surface") or item.get("curve"),
        "center": [round(float(value), 6) for value in center] if isinstance(center, list) else [],
        "measure": round(float(item.get("area", item.get("length", 0.0))), 6),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20]
    return f"{kind}:{digest}"


def _faces(geometry: dict[str, Any]) -> list[dict[str, Any]]:
    native = NativeKernel()
    library = native.library
    if not hasattr(library, "cadyx_face_topology"):
        return []
    brep, _metrics = native.export_geometry(geometry)
    data = (ctypes.c_ubyte * len(brep)).from_buffer_copy(brep)
    function = library.cadyx_face_topology
    function.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t, ctypes.c_char_p, ctypes.c_size_t]
    function.restype = ctypes.c_void_p
    error = ctypes.create_string_buffer(2048)
    result = function(data, len(brep), error, len(error))
    if not result:
        raise ValidationError(error.value.decode("utf-8", errors="replace"))
    try:
        payload = json.loads(ctypes.string_at(result))
    finally:
        library.cadyx_free(result)
    faces = payload.get("faces")
    if not isinstance(faces, list):
        raise ValidationError("OCCT returned invalid face topology metadata.")
    for face in faces:
        face["semantic_key"] = _signature("face", face)
    return faces


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from .kernel import OCCTKernelAdapter
    original = OCCTKernelAdapter.topology

    def topology(self, geometry):
        result = original(self, geometry)
        edges = result.get("edges")
        if isinstance(edges, list):
            for edge in edges:
                if isinstance(edge, dict):
                    edge["semantic_key"] = _signature("edge", edge)
        result["faces"] = _faces(geometry)
        return result

    OCCTKernelAdapter.topology = topology
    _INSTALLED = True
