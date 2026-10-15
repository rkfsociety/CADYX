"""Agent-oriented high-level features for photo/reference-driven CAD authoring."""
from __future__ import annotations

import ctypes
import math
from typing import Any
from uuid import uuid4

from .advanced_features import _advanced_native, _raw_brep, _wrap_brep
from .errors import NotFoundError, ValidationError
from .models import Document
from .native_kernel import NativeKernel

_INSTALLED = False
_ORIGINAL_RENDER = None

CAPABILITIES = (
    {
        "type": "through_hole",
        "description": "Cut an arbitrary-axis through hole; CADYX derives a cutter long enough to cross the current body bounds.",
        "required": ["body_id", "origin", "axis", "diameter"],
        "optional": ["name"],
        "constraints": ["origin and axis are XYZ arrays; diameter is positive; the cutter must remove material and leave one solid."],
    },
    {
        "type": "counterbore_hole",
        "description": "Cut a cylindrical hole with a larger coaxial counterbore.",
        "required": ["body_id", "origin", "axis", "hole_diameter", "counterbore_diameter", "counterbore_depth"],
        "optional": ["through", "hole_depth", "name"],
        "constraints": ["counterbore_diameter must exceed hole_diameter; through defaults true; hole_depth is required when through is false."],
    },
    {
        "type": "countersink_hole",
        "description": "Cut a cylindrical hole with an analytic conical countersink.",
        "required": ["body_id", "origin", "axis", "hole_diameter", "countersink_diameter"],
        "optional": ["angle_degrees", "through", "hole_depth", "name"],
        "constraints": ["countersink_diameter must exceed hole_diameter; included angle defaults to 90 degrees and must be between 30 and 150 degrees."],
    },
    {
        "type": "threaded_hole",
        "description": "Cut an internal single-start ISO metric thread by subtracting CADYX's exact external metric-thread tool on an arbitrary axis.",
        "required": ["body_id", "origin", "axis", "major_diameter", "pitch", "length"],
        "optional": ["handedness", "name"],
        "constraints": ["length is measured from origin along axis; handedness is right or left; result must remain one valid solid."],
    },
    {
        "type": "extrude_profile_on_plane",
        "description": "Extrude a closed local 2D profile on an arbitrary 3D work plane defined by origin, normal and local X axis.",
        "required": ["profile", "origin", "normal", "x_axis", "depth"],
        "optional": ["centered", "name"],
        "constraints": ["profile is a closed ordered list of x/y points; x_axis must not be parallel to normal; depth is positive."],
    },
)

_SOURCE_RESULT_OPERATIONS = {
    "shell_body",
    "offset_body",
    "chamfer_edges",
    "variable_fillet_edges",
    "draft_faces",
    "mirror_body",
    "linear_pattern_body",
    "circular_pattern_body",
    "hole",
    "boss",
    "pad_sketch_to_body",
    "rib_from_sketch",
    "pocket_sketch",
    "through_hole",
    "counterbore_hole",
    "countersink_hole",
    "threaded_hole",
}


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a number.")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValidationError(f"{name} must be finite" + (" and positive." if positive else "."))
    return result


def _vector(value: Any, name: str, *, nonzero: bool = False) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValidationError(f"{name} must be an XYZ array.")
    result = [_number(item, name) for item in value]
    if nonzero and math.sqrt(sum(item * item for item in result)) <= 1e-12:
        raise ValidationError(f"{name} must be non-zero.")
    return result


def _unit(value: list[float], name: str) -> list[float]:
    length = math.sqrt(sum(item * item for item in value))
    if not math.isfinite(length) or length <= 1e-12:
        raise ValidationError(f"{name} must be non-zero.")
    return [item / length for item in value]


def _dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def _cross(left: list[float], right: list[float]) -> list[float]:
    return [
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    ]


def _body(document: Document, body_id: Any) -> dict[str, Any]:
    if not isinstance(body_id, str):
        raise ValidationError("body_id must be a string.")
    body = next(
        (item for item in document.objects if item.get("id") == body_id and item.get("type") == "body"),
        None,
    )
    if body is None or not isinstance(body.get("geometry"), dict):
        raise NotFoundError("Editable source body was not found.")
    return body


def _append_source_feature(
    document: Document,
    source: dict[str, Any],
    operation: str,
    geometry: dict[str, Any],
    parameters: dict[str, Any],
    name: Any,
) -> dict[str, Any]:
    body_id, feature_id = f"body:{uuid4()}", f"feature:{uuid4()}"
    label = name.strip() if isinstance(name, str) and name.strip() else operation.replace("_", " ").title()
    document.objects.extend(
        [
            {"id": body_id, "type": "body", "name": label, "feature_ids": [feature_id], "geometry": geometry},
            {
                "id": feature_id,
                "type": "feature",
                "body_id": body_id,
                "operation": operation,
                "source_body_id": source["id"],
                "parameters": parameters,
                "geometry": geometry,
            },
        ]
    )
    return {
        "body_id": body_id,
        "feature_id": feature_id,
        "source_body_id": source["id"],
        "geometry": geometry,
    }


def _append_standalone_feature(
    document: Document,
    operation: str,
    geometry: dict[str, Any],
    parameters: dict[str, Any],
    name: Any,
) -> dict[str, Any]:
    body_id, feature_id = f"body:{uuid4()}", f"feature:{uuid4()}"
    label = name.strip() if isinstance(name, str) and name.strip() else operation.replace("_", " ").title()
    document.objects.extend(
        [
            {"id": body_id, "type": "body", "name": label, "feature_ids": [feature_id], "geometry": geometry},
            {
                "id": feature_id,
                "type": "feature",
                "body_id": body_id,
                "operation": operation,
                "parameters": parameters,
                "geometry": geometry,
            },
        ]
    )
    return {"body_id": body_id, "feature_id": feature_id, "geometry": geometry}


def _agent_native(geometry: dict[str, Any], operation: int, values: list[float]) -> dict[str, Any]:
    native = NativeKernel()
    library = native.library
    if not hasattr(library, "cadyx_agent_feature_brep"):
        raise RuntimeError(
            "CADYX native runtime is stale; run scripts/setup-runtime.ps1 to rebuild agent authoring support."
        )
    function = library.cadyx_agent_feature_brep
    function.argtypes = [
        ctypes.POINTER(ctypes.c_ubyte),
        ctypes.c_size_t,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_double),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_char_p,
        ctypes.c_size_t,
    ]
    function.restype = ctypes.c_void_p
    brep = _raw_brep(geometry)
    data = (ctypes.c_ubyte * len(brep)).from_buffer_copy(brep)
    numbers = (ctypes.c_double * len(values))(*values)
    size = ctypes.c_size_t()
    error = ctypes.create_string_buffer(2048)
    result = function(
        data,
        len(brep),
        operation,
        numbers,
        len(values),
        ctypes.byref(size),
        error,
        len(error),
    )
    if not result:
        raise ValidationError(error.value.decode("utf-8", errors="replace"))
    try:
        return _wrap_brep(bytes(ctypes.string_at(result, size.value)))
    finally:
        library.cadyx_free(result)


def _must_remove(before: dict[str, Any], after: dict[str, Any], label: str) -> None:
    before_volume = before.get("volume")
    after_volume = after.get("volume")
    if (
        isinstance(before_volume, (int, float))
        and not isinstance(before_volume, bool)
        and isinstance(after_volume, (int, float))
        and not isinstance(after_volume, bool)
        and after_volume >= before_volume - max(1e-9, abs(before_volume) * 1e-12)
    ):
        raise ValidationError(f"{label} did not remove material from the source body.")


def _through_span(kernel: Any, geometry: dict[str, Any], diameter: float) -> float:
    metrics = kernel.metrics(geometry)
    bounds = metrics.get("bounds") if isinstance(metrics, dict) else None
    if (
        not isinstance(bounds, dict)
        or not isinstance(bounds.get("min"), list)
        or not isinstance(bounds.get("max"), list)
        or len(bounds["min"]) != 3
        or len(bounds["max"]) != 3
    ):
        raise ValidationError("Body bounds are unavailable for a through-hole operation.")
    low = [_number(value, "body bound") for value in bounds["min"]]
    high = [_number(value, "body bound") for value in bounds["max"]]
    diagonal = math.dist(low, high)
    if not math.isfinite(diagonal) or diagonal <= 1e-9:
        raise ValidationError("Body bounds are degenerate.")
    return max(diagonal * 2.0 + diameter * 2.0, 1.0)


def _cylinder_cut(
    kernel: Any,
    geometry: dict[str, Any],
    *,
    origin: list[float],
    axis: list[float],
    diameter: float,
    through: bool,
    depth: float | None,
    label: str,
) -> tuple[dict[str, Any], list[float], float]:
    if through:
        span = _through_span(kernel, geometry, diameter)
        cut_origin = [origin[index] - axis[index] * span for index in range(3)]
        cut_depth = span * 2.0
    else:
        if depth is None:
            raise ValidationError("hole_depth is required when through is false.")
        cut_origin = list(origin)
        cut_depth = _number(depth, "hole_depth", positive=True)
    result = _advanced_native(
        geometry,
        9,
        [*cut_origin, *axis, diameter, cut_depth],
    )
    _must_remove(geometry, result, label)
    return result, cut_origin, cut_depth


def _matrix_to_axis_angle(matrix: list[list[float]]) -> tuple[list[float], float]:
    r00, r01, r02 = matrix[0]
    r10, r11, r12 = matrix[1]
    r20, r21, r22 = matrix[2]
    trace = r00 + r11 + r22
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        qw = 0.25 * scale
        qx = (r21 - r12) / scale
        qy = (r02 - r20) / scale
        qz = (r10 - r01) / scale
    elif r00 > r11 and r00 > r22:
        scale = math.sqrt(max(0.0, 1.0 + r00 - r11 - r22)) * 2.0
        qx = 0.25 * scale
        qw = (r21 - r12) / scale
        qy = (r01 + r10) / scale
        qz = (r02 + r20) / scale
    elif r11 > r22:
        scale = math.sqrt(max(0.0, 1.0 + r11 - r00 - r22)) * 2.0
        qy = 0.25 * scale
        qw = (r02 - r20) / scale
        qx = (r01 + r10) / scale
        qz = (r12 + r21) / scale
    else:
        scale = math.sqrt(max(0.0, 1.0 + r22 - r00 - r11)) * 2.0
        qz = 0.25 * scale
        qw = (r10 - r01) / scale
        qx = (r02 + r20) / scale
        qy = (r12 + r21) / scale
    norm = math.sqrt(qw * qw + qx * qx + qy * qy + qz * qz)
    if not math.isfinite(norm) or norm <= 1e-12:
        raise ValidationError("Work-plane orientation is numerically unstable.")
    qw, qx, qy, qz = (value / norm for value in (qw, qx, qy, qz))
    if qw < 0:
        qw, qx, qy, qz = (-qw, -qx, -qy, -qz)
    vector_norm = math.sqrt(qx * qx + qy * qy + qz * qz)
    if vector_norm <= 1e-12:
        return [0.0, 0.0, 1.0], 0.0
    axis = [qx / vector_norm, qy / vector_norm, qz / vector_norm]
    angle = 2.0 * math.atan2(vector_norm, max(0.0, qw))
    return axis, angle


def _workplane_rotation(normal: list[float], x_axis: list[float]) -> tuple[list[float], float, list[float], list[float]]:
    normal_unit = _unit(normal, "normal")
    projection = _dot(x_axis, normal_unit)
    local_x = [x_axis[index] - projection * normal_unit[index] for index in range(3)]
    local_x = _unit(local_x, "x_axis")
    local_y = _unit(_cross(normal_unit, local_x), "work-plane Y axis")
    matrix = [
        [local_x[0], local_y[0], normal_unit[0]],
        [local_x[1], local_y[1], normal_unit[1]],
        [local_x[2], local_y[2], normal_unit[2]],
    ]
    rotation_axis, angle = _matrix_to_axis_angle(matrix)
    return rotation_axis, angle, normal_unit, local_x


def _rotation_from_z(axis: list[float]) -> tuple[list[float], float, list[float]]:
    target = _unit(axis, "axis")
    cosine = max(-1.0, min(1.0, target[2]))
    if cosine >= 1.0 - 1e-12:
        return [0.0, 0.0, 1.0], 0.0, target
    if cosine <= -1.0 + 1e-12:
        return [1.0, 0.0, 0.0], math.pi, target
    rotation_axis = _unit([-target[1], target[0], 0.0], "rotation axis")
    return rotation_axis, math.acos(cosine), target


def _boolean(value: Any, name: str, default: bool) -> bool:
    if value is None:
        return default
    if type(value) is not bool:
        raise ValidationError(f"{name} must be a boolean.")
    return value


def _execute(self, document: Document, command: dict[str, Any], *, progress=None) -> dict[str, Any]:
    kind = command.get("type")

    if kind == "extrude_profile_on_plane":
        profile = command.get("profile")
        if not isinstance(profile, list):
            raise ValidationError("profile must be a list of local x/y coordinates.")
        origin = _vector(command.get("origin"), "origin")
        normal = _vector(command.get("normal"), "normal", nonzero=True)
        x_axis = _vector(command.get("x_axis"), "x_axis", nonzero=True)
        depth = _number(command.get("depth"), "depth", positive=True)
        centered = _boolean(command.get("centered"), "centered", False)
        temporary = self.kernel.create_extruded_profile(profile, plane="XY", depth=depth, centered=centered)
        rotation_axis, angle, normal_unit, local_x = _workplane_rotation(normal, x_axis)
        geometry = _agent_native(temporary, 2, [*rotation_axis, angle, *origin])
        parameters = {
            "profile": temporary.get("profile", profile),
            "origin": origin,
            "normal": normal_unit,
            "x_axis": local_x,
            "depth": depth,
            "centered": centered,
        }
        if progress:
            progress({"phase": "extrude_profile_on_plane"})
        return _append_standalone_feature(
            document, kind, geometry, parameters, command.get("name")
        )

    source = _body(document, command.get("body_id"))
    origin = _vector(command.get("origin"), "origin")
    axis = _unit(_vector(command.get("axis"), "axis", nonzero=True), "axis")

    if kind == "through_hole":
        diameter = _number(command.get("diameter"), "diameter", positive=True)
        geometry, cut_origin, cut_depth = _cylinder_cut(
            self.kernel,
            source["geometry"],
            origin=origin,
            axis=axis,
            diameter=diameter,
            through=True,
            depth=None,
            label="Through hole",
        )
        parameters = {
            "origin": origin,
            "axis": axis,
            "diameter": diameter,
            "through": True,
            "resolved_cutter_origin": cut_origin,
            "resolved_cutter_depth": cut_depth,
        }

    elif kind == "counterbore_hole":
        hole_diameter = _number(command.get("hole_diameter"), "hole_diameter", positive=True)
        counterbore_diameter = _number(
            command.get("counterbore_diameter"), "counterbore_diameter", positive=True
        )
        if counterbore_diameter <= hole_diameter:
            raise ValidationError("counterbore_diameter must exceed hole_diameter.")
        counterbore_depth = _number(
            command.get("counterbore_depth"), "counterbore_depth", positive=True
        )
        through = _boolean(command.get("through"), "through", True)
        hole_depth = command.get("hole_depth")
        if through and hole_depth is not None:
            raise ValidationError("hole_depth must be omitted when through is true.")
        geometry, cut_origin, cut_depth = _cylinder_cut(
            self.kernel,
            source["geometry"],
            origin=origin,
            axis=axis,
            diameter=hole_diameter,
            through=through,
            depth=hole_depth,
            label="Counterbore main hole",
        )
        counterbored = _advanced_native(
            geometry,
            9,
            [*origin, *axis, counterbore_diameter, counterbore_depth],
        )
        _must_remove(geometry, counterbored, "Counterbore")
        geometry = counterbored
        parameters = {
            "origin": origin,
            "axis": axis,
            "hole_diameter": hole_diameter,
            "counterbore_diameter": counterbore_diameter,
            "counterbore_depth": counterbore_depth,
            "through": through,
            "hole_depth": None if through else cut_depth,
            "resolved_cutter_origin": cut_origin,
            "resolved_cutter_depth": cut_depth,
        }

    elif kind == "countersink_hole":
        hole_diameter = _number(command.get("hole_diameter"), "hole_diameter", positive=True)
        countersink_diameter = _number(
            command.get("countersink_diameter"), "countersink_diameter", positive=True
        )
        if countersink_diameter <= hole_diameter:
            raise ValidationError("countersink_diameter must exceed hole_diameter.")
        angle_degrees = _number(command.get("angle_degrees", 90.0), "angle_degrees", positive=True)
        if not 30.0 <= angle_degrees <= 150.0:
            raise ValidationError("angle_degrees must be between 30 and 150 degrees.")
        through = _boolean(command.get("through"), "through", True)
        hole_depth = command.get("hole_depth")
        if through and hole_depth is not None:
            raise ValidationError("hole_depth must be omitted when through is true.")
        geometry, cut_origin, cut_depth = _cylinder_cut(
            self.kernel,
            source["geometry"],
            origin=origin,
            axis=axis,
            diameter=hole_diameter,
            through=through,
            depth=hole_depth,
            label="Countersink main hole",
        )
        radial_difference = (countersink_diameter - hole_diameter) / 2.0
        countersink_depth = radial_difference / math.tan(math.radians(angle_degrees) / 2.0)
        if not math.isfinite(countersink_depth) or countersink_depth <= 1e-9:
            raise ValidationError("Countersink dimensions produce an invalid depth.")
        countersunk = _agent_native(
            geometry,
            1,
            [*origin, *axis, countersink_diameter, hole_diameter, countersink_depth],
        )
        _must_remove(geometry, countersunk, "Countersink")
        geometry = countersunk
        parameters = {
            "origin": origin,
            "axis": axis,
            "hole_diameter": hole_diameter,
            "countersink_diameter": countersink_diameter,
            "angle_degrees": angle_degrees,
            "countersink_depth": countersink_depth,
            "through": through,
            "hole_depth": None if through else cut_depth,
            "resolved_cutter_origin": cut_origin,
            "resolved_cutter_depth": cut_depth,
        }

    elif kind == "threaded_hole":
        major_diameter = _number(command.get("major_diameter"), "major_diameter", positive=True)
        pitch = _number(command.get("pitch"), "pitch", positive=True)
        length = _number(command.get("length"), "length", positive=True)
        handedness = command.get("handedness", "right")
        if handedness not in {"right", "left"}:
            raise ValidationError("handedness must be 'right' or 'left'.")
        thread = self.kernel.create_metric_thread(
            major_diameter=major_diameter,
            pitch=pitch,
            length=length,
            handedness=handedness,
        )
        rotation_axis, angle, normalized_axis = _rotation_from_z(axis)
        tool = _agent_native(thread, 2, [*rotation_axis, angle, *origin])
        geometry = self.kernel.cut_solids(source["geometry"], tool)
        parameters = {
            "origin": origin,
            "axis": normalized_axis,
            "major_diameter": major_diameter,
            "pitch": pitch,
            "length": length,
            "handedness": handedness,
        }

    else:
        raise ValidationError(f"Unsupported agent authoring command: {kind!r}.")

    if progress:
        progress({"phase": kind})
    return _append_source_feature(
        document, source, kind, geometry, parameters, command.get("name")
    )


def _render_scene_without_consumed_sources(document: Document, kernel: Any):
    hidden = {
        item.get("source_body_id")
        for item in document.objects
        if item.get("type") == "feature" and item.get("operation") in _SOURCE_RESULT_OPERATIONS
    }
    hidden.discard(None)
    if not hidden:
        return _ORIGINAL_RENDER(document, kernel)
    filtered = Document(
        id=document.id,
        version=document.version,
        revision=document.revision,
        objects=[
            item
            for item in document.objects
            if not (item.get("type") == "body" and item.get("id") in hidden)
        ],
    )
    return _ORIGINAL_RENDER(filtered, kernel)


def install() -> None:
    global _INSTALLED, _ORIGINAL_RENDER
    if _INSTALLED:
        return
    from . import capabilities, commands, core, rendering

    names = {item["type"] for item in CAPABILITIES}
    original_execute = commands.CommandSystem.execute

    def execute(self, document, command, *, progress=None, project_id=None):
        if isinstance(command, dict) and command.get("type") in names:
            return _execute(self, document, command, progress=progress)
        return original_execute(
            self, document, command, progress=progress, project_id=project_id
        )

    commands.CommandSystem.execute = execute
    capabilities.COMMAND_CAPABILITIES = capabilities.COMMAND_CAPABILITIES + CAPABILITIES
    commands.COMMAND_CAPABILITIES = capabilities.COMMAND_CAPABILITIES

    _ORIGINAL_RENDER = core.render_scene
    core.render_scene = _render_scene_without_consumed_sources
    rendering.render_scene = _render_scene_without_consumed_sources
    _INSTALLED = True
