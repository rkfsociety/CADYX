"""Constrained human-in-the-loop traced-curve version saves.

The browser may keep an unsaved draft locally, but the committed geometry is
always recomputed and validated here from source-image coordinates.  Every save
creates a new sketch; earlier trace versions are immutable document history.
"""
from __future__ import annotations

import math
import re
from copy import deepcopy
from typing import Any
from uuid import uuid4

from .errors import ConflictError, ValidationError

_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_TOLERANCE = 1e-7

CAPABILITIES = (
    {
        "type": "save_traced_curve_version",
        "description": "Save an immutable traced contour/arc version after authoritative server-side coordinate recomputation and validation.",
        "required": ["name", "source_image_sha256", "coordinate_system", "segments"],
        "optional": ["trace_id", "base_version", "plane", "closed"],
        "constraints": [
            "coordinate_system contains finite source origin {x,y}, positive units_per_pixel, unit, and y_axis up/down.",
            "segments contain connected line or circular-arc source-image geometry; server recomputes model coordinates before persistence.",
            "base_version must equal the latest saved trace version; every save creates a new immutable sketch version.",
        ],
    },
)

ACTIVITY_ACTIONS = {"save_traced_curve_version": "Сохраняет версию трассировки"}


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a finite number.")
    number = float(value)
    if not math.isfinite(number):
        raise ValidationError(f"{name} must be a finite number.")
    return number


def _point(value: Any, name: str) -> tuple[float, float]:
    if not isinstance(value, dict) or set(value) != {"x", "y"}:
        raise ValidationError(f"{name} must be an object with x and y.")
    return _finite(value["x"], f"{name}.x"), _finite(value["y"], f"{name}.y")


def _coordinate_system(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError("coordinate_system must be an object.")
    if set(value) != {"origin", "units_per_pixel", "unit", "y_axis"}:
        raise ValidationError(
            "coordinate_system must contain origin, units_per_pixel, unit, and y_axis."
        )
    origin = _point(value["origin"], "coordinate_system.origin")
    scale = _finite(value["units_per_pixel"], "coordinate_system.units_per_pixel")
    unit = value["unit"]
    y_axis = value["y_axis"]
    if scale <= 0:
        raise ValidationError("coordinate_system.units_per_pixel must be positive.")
    if not isinstance(unit, str) or not unit.strip() or len(unit.strip()) > 32:
        raise ValidationError("coordinate_system.unit must contain 1 to 32 characters.")
    if y_axis not in {"up", "down"}:
        raise ValidationError("coordinate_system.y_axis must be up or down.")
    return {
        "origin": {"x": origin[0], "y": origin[1]},
        "units_per_pixel": scale,
        "unit": unit.strip(),
        "y_axis": y_axis,
    }


def _transform(point: tuple[float, float], coordinates: dict[str, Any]) -> tuple[float, float]:
    origin = coordinates["origin"]
    scale = coordinates["units_per_pixel"]
    x = (point[0] - origin["x"]) * scale
    source_y = (point[1] - origin["y"]) * scale
    y = -source_y if coordinates["y_axis"] == "up" else source_y
    if not math.isfinite(x) or not math.isfinite(y):
        raise ValidationError("Recomputed trace coordinates must remain finite.")
    return x, y


def _distance(left: tuple[float, float], right: tuple[float, float]) -> float:
    return math.hypot(right[0] - left[0], right[1] - left[1])


def _normalize_segments(value: Any, coordinates: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValidationError("segments must be a non-empty list.")
    if len(value) > 4096:
        raise ValidationError("segments may contain at most 4096 entries.")
    result: list[dict[str, Any]] = []
    y_sign = -1.0 if coordinates["y_axis"] == "up" else 1.0
    for index, segment in enumerate(value):
        if not isinstance(segment, dict):
            raise ValidationError(f"segments[{index}] must be an object.")
        kind = segment.get("kind")
        if kind == "line":
            if set(segment) != {"kind", "start", "end"}:
                raise ValidationError("Line segments contain only kind, start, and end.")
            start = _transform(_point(segment["start"], "line.start"), coordinates)
            end = _transform(_point(segment["end"], "line.end"), coordinates)
            if _distance(start, end) <= _TOLERANCE:
                raise ValidationError("Trace line endpoints must be distinct.")
            result.append({"kind": "line", "start": start, "end": end})
        elif kind == "arc":
            allowed = {"kind", "center", "radius_pixels", "start_angle_degrees", "sweep_degrees"}
            if set(segment) != allowed:
                raise ValidationError(
                    "Arc segments contain kind, center, radius_pixels, start_angle_degrees, and sweep_degrees."
                )
            center = _transform(_point(segment["center"], "arc.center"), coordinates)
            radius = _finite(segment["radius_pixels"], "arc.radius_pixels") * coordinates["units_per_pixel"]
            start_angle = math.radians(_finite(segment["start_angle_degrees"], "arc.start_angle_degrees"))
            sweep = math.radians(_finite(segment["sweep_degrees"], "arc.sweep_degrees")) * y_sign
            start_angle *= y_sign
            if radius <= _TOLERANCE:
                raise ValidationError("Trace arc radius must be positive.")
            if abs(sweep) <= _TOLERANCE or abs(sweep) >= math.tau - _TOLERANCE:
                raise ValidationError("Trace arc sweep must be non-zero and less than one revolution.")
            start = (
                center[0] + radius * math.cos(start_angle),
                center[1] + radius * math.sin(start_angle),
            )
            end_angle = start_angle + sweep
            end = (
                center[0] + radius * math.cos(end_angle),
                center[1] + radius * math.sin(end_angle),
            )
            result.append(
                {
                    "kind": "arc",
                    "center": center,
                    "radius": radius,
                    "start": start,
                    "end": end,
                    "sweep_radians": sweep,
                }
            )
        else:
            raise ValidationError("Trace segment kind must be line or arc.")
    for left, right in zip(result, result[1:]):
        if _distance(left["end"], right["start"]) > _TOLERANCE:
            raise ValidationError("Trace segments must form one connected chain.")
    return result


def _latest_version(document: Any, trace_id: str) -> int:
    latest = 0
    for item in document.objects:
        if item.get("type") != "sketch" or item.get("trace_id") != trace_id:
            continue
        version = item.get("trace_version")
        if type(version) is not int or version <= 0:
            raise ValidationError("Existing trace version metadata is invalid.")
        if version > latest:
            latest = version
    return latest


def _execute(self: Any, document: Any, command: dict[str, Any], *, progress=None, project_id=None) -> dict[str, Any]:
    del project_id
    if command.get("type") != "save_traced_curve_version":
        raise ValidationError(f"Unsupported curve editing command: {command.get('type')!r}.")
    name = command.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        raise ValidationError("name must contain 1 to 120 characters.")
    source_hash = command.get("source_image_sha256")
    if not isinstance(source_hash, str) or _SHA256.fullmatch(source_hash) is None:
        raise ValidationError("source_image_sha256 must be a 64-character hexadecimal SHA-256.")
    coordinates = _coordinate_system(command.get("coordinate_system"))
    segments = _normalize_segments(command.get("segments"), coordinates)
    closed = command.get("closed", False)
    if type(closed) is not bool:
        raise ValidationError("closed must be a boolean.")
    if closed and _distance(segments[-1]["end"], segments[0]["start"]) > _TOLERANCE:
        raise ValidationError("A closed trace must end at its first point.")
    plane = command.get("plane", "XY")
    if plane not in {"XY", "XZ"}:
        raise ValidationError("plane must be XY or XZ.")
    trace_id = command.get("trace_id")
    if trace_id is None:
        trace_id = str(uuid4())
    if not isinstance(trace_id, str) or not trace_id.strip() or len(trace_id) > 120:
        raise ValidationError("trace_id must contain 1 to 120 characters.")
    latest = _latest_version(document, trace_id)
    base_version = command.get("base_version", latest)
    if type(base_version) is not int or base_version < 0:
        raise ValidationError("base_version must be a non-negative integer.")
    if base_version != latest:
        raise ConflictError(
            f"Trace {trace_id} has version {latest}; base_version {base_version} is stale.",
            code="REVISION_CONFLICT",
        )
    version = latest + 1
    if progress is not None:
        progress({"phase": "validate_traced_curve"})

    sketch_id = f"sketch:{uuid4()}"
    sketch = {
        "id": sketch_id,
        "type": "sketch",
        "name": f"{name.strip()} · v{version}",
        "plane": plane,
        "trace_id": trace_id,
        "trace_version": version,
        "trace_source_image_sha256": source_hash.lower(),
        "trace_coordinate_system": deepcopy(coordinates),
        "trace_source_segments": deepcopy(command["segments"]),
        "trace_closed": closed,
    }
    additions: list[dict[str, Any]] = [sketch]
    point_ids: list[str] = []
    element_ids: list[str] = []

    first_id = f"point:{uuid4()}"
    point_ids.append(first_id)
    additions.append(
        {
            "id": first_id,
            "type": "point",
            "sketch_id": sketch_id,
            "x": segments[0]["start"][0],
            "y": segments[0]["start"][1],
        }
    )
    current_id = first_id
    for index, segment in enumerate(segments):
        is_last = index == len(segments) - 1
        if segment["kind"] == "arc":
            center_id = f"point:{uuid4()}"
            point_ids.append(center_id)
            additions.append(
                {
                    "id": center_id,
                    "type": "point",
                    "sketch_id": sketch_id,
                    "x": segment["center"][0],
                    "y": segment["center"][1],
                    "construction": True,
                }
            )
        if closed and is_last:
            end_id = first_id
        else:
            end_id = f"point:{uuid4()}"
            point_ids.append(end_id)
            additions.append(
                {
                    "id": end_id,
                    "type": "point",
                    "sketch_id": sketch_id,
                    "x": segment["end"][0],
                    "y": segment["end"][1],
                }
            )
        if segment["kind"] == "line":
            element_id = f"line:{uuid4()}"
            additions.append(
                {
                    "id": element_id,
                    "type": "line",
                    "sketch_id": sketch_id,
                    "start_id": current_id,
                    "end_id": end_id,
                }
            )
        else:
            element_id = f"circle:{uuid4()}"
            additions.append(
                {
                    "id": element_id,
                    "type": "circle",
                    "sketch_id": sketch_id,
                    "center_id": center_id,
                    "radius": segment["radius"],
                    "start_id": current_id,
                    "end_id": end_id,
                    "sweep_radians": segment["sweep_radians"],
                }
            )
        element_ids.append(element_id)
        current_id = end_id

    document.objects.extend(additions)
    if progress is not None:
        progress({"phase": "save_traced_curve_version"})
    return {
        "trace_id": trace_id,
        "trace_version": version,
        "sketch_id": sketch_id,
        "point_ids": point_ids,
        "element_ids": element_ids,
        "source_image_sha256": source_hash.lower(),
        "closed": closed,
    }
