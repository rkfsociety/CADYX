"""Advanced OCCT modeling commands layered onto the stable v7 command/document contract.

The results are stored as ordinary imported_brep geometry, so rendering, topology inspection,
booleans and verified STEP/STL delivery keep using the existing kernel path.
"""
from __future__ import annotations

import base64
import ctypes
import math
import zlib
from typing import Any
from uuid import uuid4

from .errors import NotFoundError, ValidationError
from .native_kernel import NativeKernel
from .sketches import closed_profile

_INSTALLED = False

ADVANCED_CAPABILITIES = (
    {"type": "create_section_sketch", "description": "Create an editable XZ section sketch at a Y offset for direct loft authoring.", "required": ["offset"], "optional": ["name"], "constraints": ["offset is finite; resulting sketch is compatible with loft_sketches and loft_profiles."]},
    {"type": "loft_profiles", "description": "Create an editable solid loft from ordered XZ section sketches without requiring STEP-derived section metadata.", "required": ["sketch_ids", "section_offsets"], "optional": ["name"], "constraints": ["2-64 closed XZ profiles; offsets strictly increase."]},
    {"type": "shell_body", "description": "Hollow a solid by removing selected faces and offsetting the remaining skin.", "required": ["body_id", "face_indices", "thickness"], "optional": ["tolerance", "name"], "constraints": ["Refresh topology first; thickness is signed and non-zero."]},
    {"type": "offset_body", "description": "Offset a complete solid skin by a signed distance.", "required": ["body_id", "offset"], "optional": ["tolerance", "name"], "constraints": ["Offset must be non-zero and result must remain one valid solid."]},
    {"type": "chamfer_edges", "description": "Chamfer topology-selected edges by a constant distance.", "required": ["body_id", "edge_indices", "distance"], "optional": ["name"], "constraints": ["Refresh topology first; distance must be positive."]},
    {"type": "variable_fillet_edges", "description": "Create a linear two-radius fillet on topology-selected edges.", "required": ["body_id", "edge_indices", "start_radius", "end_radius"], "optional": ["name"], "constraints": ["Refresh topology first; both radii must be positive."]},
    {"type": "draft_faces", "description": "Apply mold draft to selected faces around a neutral plane.", "required": ["body_id", "face_indices", "angle_degrees", "pull_direction", "neutral_origin", "neutral_normal"], "optional": ["name"], "constraints": ["Angle magnitude is below 89 degrees; vectors are XYZ arrays."]},
    {"type": "mirror_body", "description": "Mirror a solid across an arbitrary plane.", "required": ["body_id", "plane_origin", "plane_normal"], "optional": ["name"], "constraints": ["Plane normal must be non-zero."]},
    {"type": "linear_pattern_body", "description": "Copy a body along a vector and fuse all instances into one solid.", "required": ["body_id", "count", "offset"], "optional": ["name"], "constraints": ["2-64 instances; copies must fuse to one solid."]},
    {"type": "circular_pattern_body", "description": "Copy a body around an axis and fuse all instances into one solid.", "required": ["body_id", "count", "axis_origin", "axis_direction", "angle_step_degrees"], "optional": ["name"], "constraints": ["2-64 instances; copies must fuse to one solid."]},
    {"type": "hole", "description": "Cut a cylindrical hole from a body along an arbitrary axis.", "required": ["body_id", "origin", "axis", "diameter", "depth"], "optional": ["name"], "constraints": ["Diameter/depth positive; result remains one solid."]},
    {"type": "boss", "description": "Fuse a cylindrical boss to a body along an arbitrary axis.", "required": ["body_id", "origin", "axis", "diameter", "depth"], "optional": ["name"], "constraints": ["Diameter/depth positive; cylinder must intersect the body."]},
)


def _number(value: Any, name: str, *, positive: bool = False, nonzero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a number.")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0) or (nonzero and abs(result) <= 1e-9):
        raise ValidationError(f"{name} is outside the supported finite range.")
    return result


def _vector(value: Any, name: str, *, nonzero: bool = False) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValidationError(f"{name} must be an XYZ array.")
    result = [_number(item, name) for item in value]
    if nonzero and math.sqrt(sum(item * item for item in result)) <= 1e-12:
        raise ValidationError(f"{name} must be non-zero.")
    return result


def _indices(value: Any, name: str) -> list[int]:
    if (not isinstance(value, list) or not value or len(value) > 256
            or any(type(item) is not int or item < 0 for item in value)
            or len(set(value)) != len(value)):
        raise ValidationError(f"{name} must contain distinct non-negative topology indices.")
    return list(value)


def _body(document, body_id: Any) -> dict[str, Any]:
    if not isinstance(body_id, str):
        raise ValidationError("body_id must be a string.")
    item = next((obj for obj in document.objects if obj.get("id") == body_id and obj.get("type") == "body"), None)
    if item is None or not isinstance(item.get("geometry"), dict):
        raise NotFoundError("Editable source body was not found.")
    return item


def _raw_brep(geometry: dict[str, Any]) -> bytes:
    brep, _metrics = NativeKernel().export_geometry(geometry)
    return brep


def _wrap_brep(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > 64 * 1024 * 1024:
        raise ValidationError("Advanced OCCT operation returned an invalid B-rep payload.")
    geometry = {
        "kernel": "occt-v1",
        "kind": "imported_brep",
        "data": base64.b64encode(zlib.compress(raw, level=9)).decode("ascii"),
        "volume": 1.0,
        "brep_valid": True,
    }
    graph = NativeKernel().geometry_graph(geometry)
    geometry["volume"] = graph["volume"]
    return geometry


def _advanced_native(geometry: dict[str, Any], operation: int, values: list[float], indices: list[int] | None = None) -> dict[str, Any]:
    native = NativeKernel()
    library = native.library
    if not hasattr(library, "cadyx_advanced_brep"):
        raise RuntimeError("CADYX native runtime is stale; run scripts/setup-runtime.ps1 to rebuild advanced modeling support.")
    function = library.cadyx_advanced_brep
    function.argtypes = [
        ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t, ctypes.c_int,
        ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_int), ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t), ctypes.c_char_p, ctypes.c_size_t,
    ]
    function.restype = ctypes.c_void_p
    brep = _raw_brep(geometry)
    data = (ctypes.c_ubyte * len(brep)).from_buffer_copy(brep)
    numbers = (ctypes.c_double * len(values))(*values) if values else None
    selected = indices or []
    native_indices = (ctypes.c_int * len(selected))(*selected) if selected else None
    size = ctypes.c_size_t()
    error = ctypes.create_string_buffer(2048)
    result = function(data, len(brep), operation, numbers, len(values), native_indices, len(selected),
                      ctypes.byref(size), error, len(error))
    if not result:
        raise ValidationError(error.value.decode("utf-8", errors="replace"))
    try:
        return _wrap_brep(bytes(ctypes.string_at(result, size.value)))
    finally:
        library.cadyx_free(result)


def _append_feature(document, source: dict[str, Any], operation: str, geometry: dict[str, Any],
                    parameters: dict[str, Any], name: Any) -> dict[str, Any]:
    body_id, feature_id = f"body:{uuid4()}", f"feature:{uuid4()}"
    label = name.strip() if isinstance(name, str) and name.strip() else operation.replace("_", " ").title()
    document.objects.extend([
        {"id": body_id, "type": "body", "name": label, "feature_ids": [feature_id], "geometry": geometry},
        {"id": feature_id, "type": "feature", "body_id": body_id, "operation": operation,
         "source_body_id": source["id"], "parameters": parameters, "geometry": geometry},
    ])
    return {"body_id": body_id, "feature_id": feature_id, "source_body_id": source["id"], "geometry": geometry}


def _execute(self, document, command: dict[str, Any], *, progress=None, project_id=None):
    kind = command.get("type")
    if kind == "create_section_sketch":
        offset = _number(command.get("offset"), "offset")
        sketch_id = f"sketch:{uuid4()}"
        name = command.get("name")
        document.objects.append({"id": sketch_id, "type": "sketch",
                                 "name": name.strip() if isinstance(name, str) and name.strip() else "Section Sketch",
                                 "plane": "XZ", "section_normal_axis": "Y", "section_offset": offset})
        return {"sketch_id": sketch_id, "section_offset": offset}

    if kind == "loft_profiles":
        sketch_ids, offsets = command.get("sketch_ids"), command.get("section_offsets")
        if (not isinstance(sketch_ids, list) or not 2 <= len(sketch_ids) <= 64
                or len(set(sketch_ids)) != len(sketch_ids)
                or not isinstance(offsets, list) or len(offsets) != len(sketch_ids)):
            raise ValidationError("loft_profiles needs 2 to 64 unique sketch ids and matching offsets.")
        normalized_offsets = [_number(value, "section offset") for value in offsets]
        if any(right <= left for left, right in zip(normalized_offsets, normalized_offsets[1:])):
            raise ValidationError("Loft section offsets must be strictly increasing.")
        sections = []
        for sketch_id in sketch_ids:
            sketch = next((obj for obj in document.objects if obj.get("id") == sketch_id and obj.get("type") == "sketch"), None)
            if sketch is None or sketch.get("plane") != "XZ":
                raise ValidationError("loft_profiles currently requires closed XZ section sketches.")
            _sketch, profile = closed_profile(document.objects, sketch_id)
            sections.append(profile)
        if progress: progress({"phase": "kernel_loft"})
        geometry = self.kernel.create_lofted_profiles(sections, section_offsets=normalized_offsets)
        body_id, feature_id = f"body:{uuid4()}", f"feature:{uuid4()}"
        document.objects.extend([
            {"id": body_id, "type": "body", "name": command.get("name", "Loft"), "feature_ids": [feature_id], "geometry": geometry},
            {"id": feature_id, "type": "feature", "body_id": body_id, "operation": "loft_profiles",
             "source_sketch_ids": list(sketch_ids), "parameters": {"section_offsets": normalized_offsets}, "geometry": geometry},
        ])
        return {"body_id": body_id, "feature_id": feature_id, "geometry": geometry}

    source = _body(document, command.get("body_id"))
    operation = None
    values: list[float] = []
    indices = None
    parameters: dict[str, Any] = {}
    if kind == "shell_body":
        indices = _indices(command.get("face_indices"), "face_indices")
        thickness = _number(command.get("thickness"), "thickness", nonzero=True)
        tolerance = _number(command.get("tolerance", 1e-4), "tolerance", positive=True)
        operation, values = 1, [thickness, tolerance]
        parameters = {"face_indices": indices, "thickness": thickness, "tolerance": tolerance}
    elif kind == "offset_body":
        offset = _number(command.get("offset"), "offset", nonzero=True)
        tolerance = _number(command.get("tolerance", 1e-4), "tolerance", positive=True)
        operation, values = 8, [offset, tolerance]
        parameters = {"offset": offset, "tolerance": tolerance}
    elif kind == "chamfer_edges":
        indices = _indices(command.get("edge_indices"), "edge_indices")
        distance = _number(command.get("distance"), "distance", positive=True)
        operation, values = 2, [distance]
        parameters = {"edge_indices": indices, "distance": distance}
    elif kind == "variable_fillet_edges":
        indices = _indices(command.get("edge_indices"), "edge_indices")
        first = _number(command.get("start_radius"), "start_radius", positive=True)
        second = _number(command.get("end_radius"), "end_radius", positive=True)
        operation, values = 4, [first, second]
        parameters = {"edge_indices": indices, "start_radius": first, "end_radius": second}
    elif kind == "draft_faces":
        indices = _indices(command.get("face_indices"), "face_indices")
        angle = _number(command.get("angle_degrees"), "angle_degrees", nonzero=True)
        pull = _vector(command.get("pull_direction"), "pull_direction", nonzero=True)
        origin = _vector(command.get("neutral_origin"), "neutral_origin")
        normal = _vector(command.get("neutral_normal"), "neutral_normal", nonzero=True)
        operation, values = 3, [angle, *pull, *origin, *normal]
        parameters = {"face_indices": indices, "angle_degrees": angle, "pull_direction": pull,
                      "neutral_origin": origin, "neutral_normal": normal}
    elif kind == "mirror_body":
        origin = _vector(command.get("plane_origin"), "plane_origin")
        normal = _vector(command.get("plane_normal"), "plane_normal", nonzero=True)
        operation, values = 5, [*origin, *normal]
        parameters = {"plane_origin": origin, "plane_normal": normal}
    elif kind == "linear_pattern_body":
        count = command.get("count")
        if type(count) is not int or not 2 <= count <= 64: raise ValidationError("count must be an integer from 2 to 64.")
        offset = _vector(command.get("offset"), "offset", nonzero=True)
        operation, values = 6, [float(count), *offset]
        parameters = {"count": count, "offset": offset}
    elif kind == "circular_pattern_body":
        count = command.get("count")
        if type(count) is not int or not 2 <= count <= 64: raise ValidationError("count must be an integer from 2 to 64.")
        origin = _vector(command.get("axis_origin"), "axis_origin")
        axis = _vector(command.get("axis_direction"), "axis_direction", nonzero=True)
        step = _number(command.get("angle_step_degrees"), "angle_step_degrees", nonzero=True)
        operation, values = 7, [float(count), *origin, *axis, step]
        parameters = {"count": count, "axis_origin": origin, "axis_direction": axis, "angle_step_degrees": step}
    elif kind in {"hole", "boss"}:
        origin = _vector(command.get("origin"), "origin")
        axis = _vector(command.get("axis"), "axis", nonzero=True)
        diameter = _number(command.get("diameter"), "diameter", positive=True)
        depth = _number(command.get("depth"), "depth", positive=True)
        operation, values = (9 if kind == "hole" else 10), [*origin, *axis, diameter, depth]
        parameters = {"origin": origin, "axis": axis, "diameter": diameter, "depth": depth}
    else:
        raise ValidationError(f"Unsupported advanced command: {kind!r}.")

    if progress: progress({"phase": kind})
    geometry = _advanced_native(source["geometry"], operation, values, indices)
    return _append_feature(document, source, kind, geometry, parameters, command.get("name"))


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from . import capabilities, commands
    command_types = {item["type"] for item in ADVANCED_CAPABILITIES}
    original = commands.CommandSystem.execute

    def execute(self, document, command, *, progress=None, project_id=None):
        if isinstance(command, dict) and command.get("type") in command_types:
            return _execute(self, document, command, progress=progress, project_id=project_id)
        return original(self, document, command, progress=progress, project_id=project_id)

    commands.CommandSystem.execute = execute
    capabilities.COMMAND_CAPABILITIES = capabilities.COMMAND_CAPABILITIES + ADVANCED_CAPABILITIES
    commands.COMMAND_CAPABILITIES = capabilities.COMMAND_CAPABILITIES
    _INSTALLED = True
