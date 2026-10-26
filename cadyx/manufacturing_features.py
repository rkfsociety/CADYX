"""Higher-level manufacturing/body features composed from the stable OCCT kernel primitives."""
from __future__ import annotations

import math
from typing import Any
from uuid import uuid4

from .advanced_features import _raw_brep
from .errors import NotFoundError, ValidationError
from .native_kernel import NativeKernel
from .profile_segments import sketch_segment_profiles, sketch_segments
from .sketches import closed_profile, closed_profiles
from .sketch_workplanes import kernel_plane, place_geometry


CAPABILITIES = (
    {"type": "create_sketch_from_body_section", "description": "Extract closed planar contours from any editable B-rep body into editable sketches.", "required": ["body_id", "normal_axis", "offset"], "optional": ["max_points", "name"], "constraints": ["normal_axis is X, Y or Z; max_points is at least 16 total samples (default 2048); open/branched sections are rejected by the native sectioner."]},
    {"type": "pad_sketch_to_body", "description": "Extrude one or more explicitly grouped sketch profiles and fuse them into an existing body.", "required": ["body_id", "sketch_id", "depth"], "optional": ["centered", "name", "profile_groups"], "constraints": ["By default, inner closed contours of one outer contour are holes. profile_groups can explicitly map several outer contour indices to their hole indices, enabling multiple disjoint additive regions in one feature. XY, XZ, YZ, CUSTOM, and FACE work planes are placed in their saved frames. The final result must remain one solid."]},
    {"type": "rib_from_sketch", "description": "Create an additive rib from a thin closed sketch profile and fuse it into a body.", "required": ["body_id", "sketch_id", "depth"], "optional": ["centered", "name"], "constraints": ["Use a thin closed rib profile; supported sketch work planes are placed in their saved frames; result must fuse into one solid."]},
    {"type": "pocket_sketch", "description": "Extrude one or more explicitly grouped sketch profiles as cutting tools and subtract them from an existing body.", "required": ["body_id", "sketch_id", "depth"], "optional": ["centered", "name", "profile_groups"], "constraints": ["By default, one outer contour with optional inner holes is used. profile_groups can explicitly map multiple disjoint pocket contours in one feature. Supported sketch work planes are placed in their saved frames; the pocket must remove material and final result must remain one solid."]},
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


def _feature(document, source, operation, geometry, parameters, name, *, source_sketch_id=None):
    body_id, feature_id = f"body:{uuid4()}", f"feature:{uuid4()}"
    label = name.strip() if isinstance(name, str) and name.strip() else operation.replace("_", " ").title()
    document.objects.extend([
        {"id": body_id, "type": "body", "name": label, "feature_ids": [feature_id], "geometry": geometry},
        {"id": feature_id, "type": "feature", "body_id": body_id, "operation": operation,
         "source_body_id": source["id"], "source_sketch_id": source_sketch_id,
         "parameters": parameters, "geometry": geometry},
    ])
    return {"body_id": body_id, "feature_id": feature_id, "source_body_id": source["id"], "geometry": geometry}


def normalize_profile_groups(value: Any, profile_count: int) -> list[dict[str, Any]]:
    """Validate explicit outer/hole contour groups against closed-profile order."""
    if not isinstance(value, list) or not value:
        raise ValidationError("profile_groups must be a non-empty array.")
    groups, seen = [], set()
    for group in value:
        if not isinstance(group, dict) or set(group) != {"outer_index", "inner_indices"}:
            raise ValidationError("Each profile group needs outer_index and inner_indices.")
        outer = group["outer_index"]
        inner = group["inner_indices"]
        if type(outer) is not int or not 0 <= outer < profile_count:
            raise ValidationError("profile group outer_index is outside the sketch profile range.")
        if (not isinstance(inner, list)
                or any(type(index) is not int or not 0 <= index < profile_count for index in inner)
                or len(set(inner)) != len(inner) or outer in inner):
            raise ValidationError("profile group inner_indices must be unique valid contour indices excluding outer_index.")
        indices = [outer, *inner]
        if any(index in seen for index in indices):
            raise ValidationError("Every sketch profile must belong to exactly one profile group.")
        seen.update(indices)
        groups.append({"outer_index": outer, "inner_indices": list(inner)})
    if seen != set(range(profile_count)):
        raise ValidationError("profile_groups must assign every closed sketch contour exactly once.")
    return groups


def grouped_profile_feature_geometry(kernel, source_geometry, sketch, sketch_objects,
                                     profiles, profile_groups, *, plane, depth, centered, operation):
    """Build explicit outer/hole regions and apply all of them as one feature."""
    groups = normalize_profile_groups(profile_groups, len(profiles))
    sketch_id = sketch["id"]
    segment_profiles = sketch_segment_profiles(sketch_objects, sketch_id)
    native_plane = kernel_plane(sketch, plane)
    geometry = source_geometry
    for group in groups:
        indices = [group["outer_index"], *group["inner_indices"]]
        group_profiles = [profiles[index] for index in indices]
        if len(indices) == 1:
            segments = segment_profiles[indices[0]] if segment_profiles is not None else None
            tool = kernel.create_extruded_profile(
                group_profiles[0], plane=native_plane, depth=depth, centered=centered,
                **({"segments": segments} if segments is not None else {}),
            )
        else:
            group_segments = ([segment_profiles[index] for index in indices]
                              if segment_profiles is not None else None)
            tool = kernel.create_extruded_profile_with_holes(
                group_profiles, plane=native_plane, depth=depth, centered=centered,
                **({"segment_profiles": group_segments} if group_segments is not None else {}),
            )
        tool = place_geometry(sketch, tool, kernel)
        if operation == "pocket_sketch":
            geometry = kernel.cut_solids(geometry, tool)
        else:
            geometry = kernel.fuse_profiles(geometry, tool)
    return geometry


def _execute(self, document, command, *, progress=None, project_id=None):
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
    profile_groups = command.get("profile_groups")
    if kind == "pad_sketch_to_body" or profile_groups is not None:
        plane, profiles = closed_profiles(document.objects, sketch_id)
    else:
        plane, profile = closed_profile(document.objects, sketch_id)
        profiles = [profile]
    depth = _number(command.get("depth"), "depth", positive=True)
    centered = command.get("centered", False)
    if type(centered) is not bool:
        raise ValidationError("centered must be a boolean.")
    if progress:
        progress({"phase": "build_tool_extrusion"})
    if profile_groups is not None:
        groups = normalize_profile_groups(profile_groups, len(profiles))
        geometry = grouped_profile_feature_geometry(
            self.kernel, source["geometry"], sketch, document.objects, profiles, groups,
            plane=plane, depth=depth, centered=centered, operation=kind,
        )
        parameters = {"sketch_id": sketch_id, "depth": depth, "centered": centered,
                      "profile_groups": groups}
        return _feature(document, source, kind, geometry, parameters, command.get("name"),
                        source_sketch_id=sketch_id)
    segment_profiles = sketch_segment_profiles(document.objects, sketch_id) if len(profiles) > 1 else None
    native_plane = kernel_plane(sketch, plane)
    tool = (self.kernel.create_extruded_profile(profiles[0], plane=native_plane, depth=depth, centered=centered,
                                                segments=sketch_segments(document.objects, sketch_id))
            if len(profiles) == 1 else
            self.kernel.create_extruded_profile_with_holes(
                profiles, plane=native_plane, depth=depth, centered=centered,
                segment_profiles=segment_profiles,
            ))
    tool = place_geometry(sketch, tool, self.kernel)
    if kind in {"pad_sketch_to_body", "rib_from_sketch"}:
        geometry = self.kernel.fuse_profiles(source["geometry"], tool)
    elif kind == "pocket_sketch":
        geometry = self.kernel.cut_solids(source["geometry"], tool)
    else:
        raise ValidationError(f"Unsupported manufacturing feature: {kind!r}.")
    parameters = {"sketch_id": sketch_id, "depth": depth, "centered": centered}
    return _feature(document, source, kind, geometry, parameters, command.get("name"),
                    source_sketch_id=sketch_id)
