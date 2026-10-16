"""Higher-level manufacturing/body features composed from the stable OCCT kernel primitives."""
from __future__ import annotations

import math
from typing import Any
from uuid import uuid4

from .advanced_features import _raw_brep
from .errors import NotFoundError, ValidationError
from .native_kernel import NativeKernel
from .sketches import closed_profile

_INSTALLED = False

CAPABILITIES = (
    {"type": "create_sketch_from_body_section", "description": "Extract closed planar contours from any editable B-rep body into editable sketches.", "required": ["body_id", "normal_axis", "offset"], "optional": ["max_points", "name"], "constraints": ["normal_axis is X, Y or Z; max_points is at least 16 total samples (default 2048); open/branched sections are rejected by the native sectioner."]},
    {"type": "pad_sketch_to_body", "description": "Extrude a closed sketch and fuse it into an existing body.", "required": ["body_id", "sketch_id", "depth"], "optional": ["centered", "name"], "constraints": ["Pad must intersect the source body and final result must remain one solid."]},
    {"type": "rib_from_sketch", "description": "Create an additive rib from a thin closed sketch profile and fuse it into a body.", "required": ["body_id", "sketch_id", "depth"], "optional": ["centered", "name"], "constraints": ["Use a thin closed rib profile; result must fuse into one solid."]},
    {"type": "pocket_sketch", "description": "Extrude a closed sketch as a cutting tool and subtract it from an existing body.", "required": ["body_id", "sketch_id", "depth"], "optional": ["centered", "name"], "constraints": ["Pocket must remove material and final result must remain one solid."]},
)


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a number.")
    value = float(value)
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValidationError(f"{name} must be finite" + (" and positive." if positive else "."))
    return value


def _body(document, value: Any) -> dict[str, Any]:
    if not isinstance(value, str):
        raise ValidationError("body_id must be a string.")
    body = next((item for item in document.objects if item.get("id") == value and item.get("type") == "body"), None)
    if body is None or not isinstance(body.get("geometry"), dict):
        raise NotFoundError("Editable source body was not found.")
    return body


def _feature(document, source, operation, geometry, parameters, name):
    body_id, feature_id = f"body:{uuid4()}", f"feature:{uuid4()}"
    label = name.strip() if isinstance(name, str) and name.strip() else operation.replace("_", " ").title()
    document.objects.extend([
        {"id": body_id, "type": "body", "name": label, "feature_ids": [feature_id], "geometry": geometry},
        {"id": feature_id, "type": "feature", "body_id": body_id, "operation": operation,
         "source_body_id": source["id"], "parameters": parameters, "geometry": geometry},
    ])
    return {"body_id": body_id, "feature_id": feature_id, "source_body_id": source["id"], "geometry": geometry}


def _execute(self, document, command, *, progress=None):
    kind = command.get("type")
    source = _body(document, command.get("body_id"))

    if kind == "create_sketch_from_body_section":
        axis = command.get("normal_axis")
        if axis not in {"X", "Y", "Z"}:
            raise ValidationError("normal_axis must be X, Y or Z.")
        offset = _number(command.get("offset"), "offset")
        max_points = command.get("max_points", 2048)
        if type(max_points) is not int or max_points < 16:
            raise ValidationError("max_points must be an integer of at least 16.")
        native = NativeKernel()
        brep = _raw_brep(source["geometry"])
        result = native.section_profile(brep, normal_axis=axis, offset=offset, max_points=max_points)
        profiles = result.get("profiles") if isinstance(result, dict) else None
        if not isinstance(profiles, list) or not profiles:
            raise ValidationError("Body section produced no closed contours.")
        base_name = command.get("name", "Body Section")
        if not isinstance(base_name, str) or not base_name.strip() or len(base_name.strip()) > 120:
            raise ValidationError("name must contain 1 to 120 characters.")
        plane = "XZ" if axis == "Y" else "XY"
        sketch_ids = []
        for index, points in enumerate(profiles, start=1):
            sketch_id = f"sketch:{uuid4()}"
            sketch_name = base_name.strip() if len(profiles) == 1 else f"{base_name.strip()} · contour {index}"
            document.objects.append({"id": sketch_id, "type": "sketch", "name": sketch_name,
                                     "plane": plane, "section_normal_axis": axis,
                                     "section_offset": offset, "section_profile_index": index - 1,
                                     "section_source_body_id": source["id"]})
            self._add_sketch_polyline(document, {"sketch_id": sketch_id, "points": points, "closed": True},
                                      operation="draw_body_section", progress=progress)
            sketch_ids.append(sketch_id)
        return {"sketch_ids": sketch_ids, "body_id": source["id"], "normal_axis": axis, "offset": offset}

    sketch_id = command.get("sketch_id")
    sketch = next((item for item in document.objects if item.get("id") == sketch_id and item.get("type") == "sketch"), None)
    if sketch is None:
        raise NotFoundError("Source sketch was not found.")
    _saved_sketch, profile = closed_profile(document.objects, sketch_id)
    depth = _number(command.get("depth"), "depth", positive=True)
    centered = command.get("centered", False)
    if type(centered) is not bool:
        raise ValidationError("centered must be a boolean.")
    if progress:
        progress({"phase": "build_tool_extrusion"})
    tool = self.kernel.create_extruded_profile(profile, plane=sketch["plane"], depth=depth, centered=centered)
    if kind in {"pad_sketch_to_body", "rib_from_sketch"}:
        geometry = self.kernel.fuse_profiles(source["geometry"], tool)
    elif kind == "pocket_sketch":
        geometry = self.kernel.cut_solids(source["geometry"], tool)
    else:
        raise ValidationError(f"Unsupported manufacturing feature: {kind!r}.")
    parameters = {"sketch_id": sketch_id, "depth": depth, "centered": centered}
    return _feature(document, source, kind, geometry, parameters, command.get("name"))


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from . import capabilities, commands
    names = {item["type"] for item in CAPABILITIES}
    original = commands.CommandSystem.execute

    def execute(self, document, command, *, progress=None, project_id=None):
        if isinstance(command, dict) and command.get("type") in names:
            return _execute(self, document, command, progress=progress)
        return original(self, document, command, progress=progress, project_id=project_id)

    commands.CommandSystem.execute = execute
    capabilities.COMMAND_CAPABILITIES = capabilities.COMMAND_CAPABILITIES + CAPABILITIES
    commands.COMMAND_CAPABILITIES = capabilities.COMMAND_CAPABILITIES
    _INSTALLED = True
