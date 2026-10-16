"""Pure similarity transforms for CADYX sketch geometry records.

The helpers operate on document-shaped geometry dictionaries, but do not mutate
documents or constraints. Pass the sketch's geometry records and selected IDs;
references to defining points are included automatically in the returned
dependency closure.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Callable, Sequence
from uuid import uuid4

from .errors import ValidationError

Point = tuple[float, float]
GeometryTransform = Callable[[float, float], Point]
_SUPPORTED_TYPES = {"point", "line", "circle", "ellipse", "spline"}


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{label} must be a finite number.")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValidationError(f"{label} is out of range.") from exc
    if not math.isfinite(result):
        raise ValidationError(f"{label} must be a finite number.")
    return result


def _point(x: object, y: object, label: str) -> Point:
    return _number(x, f"{label} x"), _number(y, f"{label} y")


def _unique_geometry(objects: Sequence[dict]) -> dict[str, dict]:
    if not isinstance(objects, (list, tuple)):
        raise ValidationError("Sketch objects must be a list or tuple.")
    by_id: dict[str, dict] = {}
    sketch_ids: set[str] = set()
    for item in objects:
        if not isinstance(item, dict):
            raise ValidationError("Every sketch object must be a mapping.")
        object_id, kind = item.get("id"), item.get("type")
        if not isinstance(object_id, str) or not object_id or object_id in by_id:
            raise ValidationError("Sketch geometry IDs must be unique non-empty strings.")
        if not isinstance(kind, str) or kind not in _SUPPORTED_TYPES:
            raise ValidationError("Only point, line, circle, ellipse, and spline geometry can be transformed.")
        sketch_id = item.get("sketch_id")
        if not isinstance(sketch_id, str) or not sketch_id:
            raise ValidationError("Every sketch geometry record must reference a sketch.")
        sketch_ids.add(sketch_id)
        by_id[object_id] = item
    if len(sketch_ids) > 1:
        raise ValidationError("All selected geometry must belong to one sketch.")
    return by_id


def geometry_dependency_closure(objects: Sequence[dict], object_ids: Sequence[str]) -> list[dict]:
    """Return selected objects plus their defining-point dependencies.

    Output preserves input document order. Constraints are never included or
    copied; unsupported selected kinds and broken references fail explicitly.
    """
    by_id = _unique_geometry(objects)
    if not isinstance(object_ids, (list, tuple)) or not object_ids:
        raise ValidationError("object_ids must contain at least one sketch geometry ID.")
    if any(not isinstance(value, str) for value in object_ids) or len(set(object_ids)) != len(object_ids):
        raise ValidationError("object_ids must contain unique text IDs.")

    included: set[str] = set()
    pending = list(object_ids)

    def add_point_reference(object_id: object) -> None:
        if not isinstance(object_id, str) or object_id not in by_id or by_id[object_id]["type"] != "point":
            raise ValidationError("Sketch geometry references must identify existing points.")
        pending.append(object_id)

    while pending:
        object_id = pending.pop()
        item = by_id.get(object_id)
        if item is None:
            raise ValidationError("Selected geometry or a point dependency does not exist.")
        if object_id in included:
            continue
        included.add(object_id)
        kind = item["type"]
        if kind == "point":
            _point(item.get("x"), item.get("y"), "Point")
        elif kind == "line":
            start_id, end_id = item.get("start_id"), item.get("end_id")
            if start_id == end_id:
                raise ValidationError("Line endpoints must reference two distinct points.")
            add_point_reference(start_id)
            add_point_reference(end_id)
        elif kind == "circle":
            add_point_reference(item.get("center_id"))
            if _number(item.get("radius"), "Circle radius") <= 1e-7:
                raise ValidationError("Circle radius must be greater than 1e-7.")
            if "sweep_radians" in item:
                add_point_reference(item.get("start_id"))
                add_point_reference(item.get("end_id"))
                _number(item.get("start_angle_radians"), "Arc start angle")
                sweep = _number(item.get("sweep_radians"), "Arc sweep")
                if abs(sweep) <= 1e-7 or abs(sweep) >= math.tau - 1e-7:
                    raise ValidationError("Arc sweep must be non-zero and less than one revolution.")
        elif kind == "ellipse":
            if "sweep_radians" in item:
                add_point_reference(item.get("start_id"))
                add_point_reference(item.get("end_id"))
                _number(item.get("start_angle_radians"), "Ellipse arc start angle")
                sweep = _number(item.get("sweep_radians"), "Ellipse arc sweep")
                if abs(sweep) <= 1e-7 or abs(sweep) >= math.tau - 1e-7:
                    raise ValidationError("Ellipse arc sweep must be non-zero and less than one revolution.")
            major = _number(item.get("major_radius"), "Ellipse major radius")
            minor = _number(item.get("minor_radius"), "Ellipse minor radius")
            _number(item.get("center_x"), "Ellipse center x")
            _number(item.get("center_y"), "Ellipse center y")
            _number(item.get("rotation_radians"), "Ellipse rotation")
            if minor <= 1e-7 or major < minor:
                raise ValidationError("Ellipse radii must satisfy major_radius >= minor_radius > 1e-7.")
        elif kind == "spline":
            point_ids = item.get("point_ids")
            if not isinstance(point_ids, list) or len(point_ids) < 3:
                raise ValidationError("A spline must reference at least 3 control points.")
            if any(not isinstance(point_id, str) for point_id in point_ids):
                raise ValidationError("Spline control point references must be text IDs.")
            if len(set(point_ids)) != len(point_ids):
                raise ValidationError("Spline control point references must be unique.")
            for point_id in point_ids:
                add_point_reference(point_id)

    return [deepcopy(item) for item in objects if item["id"] in included]


def _transform_records(records: Sequence[dict], transform: GeometryTransform,
                       *, duplicate: bool) -> list[dict]:
    by_id = _unique_geometry(records)
    # A full closure is required so remapped copies never point back to source geometry.
    closed = geometry_dependency_closure(records, list(by_id))
    ids = {item["id"] for item in closed}
    remap = ({object_id: f"{by_id[object_id]['type']}:{uuid4()}" for object_id in ids}
             if duplicate else {object_id: object_id for object_id in ids})
    output: list[dict] = []

    def move(x: object, y: object, label: str) -> Point:
        point = _point(x, y, label)
        result = transform(*point)
        if (not isinstance(result, (tuple, list)) or len(result) != 2
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in result)):
            raise ValidationError("A geometry transform must return two finite coordinates.")
        moved = float(result[0]), float(result[1])
        if not all(math.isfinite(v) for v in moved):
            raise ValidationError("Transformed sketch coordinates must remain finite.")
        return moved

    origin = transform(0.0, 0.0)
    x_axis = transform(1.0, 0.0)
    y_axis = transform(0.0, 1.0)
    if any(not math.isfinite(float(v)) for point in (origin, x_axis, y_axis) for v in point):
        raise ValidationError("Transform basis coordinates must remain finite.")
    xx, xy = x_axis[0] - origin[0], x_axis[1] - origin[1]
    yx, yy = y_axis[0] - origin[0], y_axis[1] - origin[1]
    scale_x, scale_y = math.hypot(xx, xy), math.hypot(yx, yy)
    determinant = xx * yy - xy * yx
    if (not math.isfinite(determinant) or scale_x <= 1e-12 or scale_y <= 1e-12
            or not math.isclose(scale_x, scale_y, rel_tol=1e-9, abs_tol=1e-9)
            or abs(xx * yx + xy * yy) > 1e-9 * scale_x * scale_y):
        raise ValidationError("Sketch transforms must be nonsingular similarities without shear.")
    scale = (scale_x + scale_y) / 2
    rotation = math.atan2(xy, xx)

    for source in closed:
        item = deepcopy(source)
        old_id = source["id"]
        item["id"] = remap[old_id]
        kind = source["type"]
        if kind == "point":
            item["x"], item["y"] = move(source.get("x"), source.get("y"), "Point")
        elif kind == "line":
            item["start_id"] = remap[source["start_id"]]
            item["end_id"] = remap[source["end_id"]]
        elif kind == "circle":
            item["center_id"] = remap[source["center_id"]]
            radius = _number(source.get("radius"), "Circle radius")
            item["radius"] = radius * scale
            if not math.isfinite(item["radius"]):
                raise ValidationError("Transformed circle radius must remain finite.")
            if "sweep_radians" in source:
                item["start_id"] = remap[source["start_id"]]
                item["end_id"] = remap[source["end_id"]]
                # Similarities can rotate or uniformly scale, but cannot reverse orientation.
                item["start_angle_radians"] = _number(source.get("start_angle_radians"), "Arc start angle") + rotation
        elif kind == "ellipse":
            center = move(source.get("center_x"), source.get("center_y"), "Ellipse center")
            item["center_x"], item["center_y"] = center
            item["major_radius"] = _number(source.get("major_radius"), "Ellipse major radius") * scale
            item["minor_radius"] = _number(source.get("minor_radius"), "Ellipse minor radius") * scale
            item["rotation_radians"] = _number(source.get("rotation_radians"), "Ellipse rotation") + rotation
            if not all(math.isfinite(item[key]) for key in
                       ("center_x", "center_y", "major_radius", "minor_radius", "rotation_radians")):
                raise ValidationError("Transformed ellipse values must remain finite.")
            if "sweep_radians" in source:
                item["start_id"] = remap[source["start_id"]]
                item["end_id"] = remap[source["end_id"]]
        elif kind == "spline":
            point_ids = source.get("point_ids")
            if not isinstance(point_ids, list):
                raise ValidationError("A spline must reference its control points.")
            item["point_ids"] = [remap[point_id] for point_id in point_ids]
        output.append(item)

    return output


def translate_geometry(objects: Sequence[dict], object_ids: Sequence[str], dx: float, dy: float) -> list[dict]:
    """Translate a selected dependency closure, preserving its IDs."""
    offset_x, offset_y = _number(dx, "Translation x"), _number(dy, "Translation y")
    selected = geometry_dependency_closure(objects, object_ids)
    return _transform_records(selected, lambda x, y: (x + offset_x, y + offset_y), duplicate=False)


def duplicate_geometry(objects: Sequence[dict], object_ids: Sequence[str], *,
                       dx: float = 0.0, dy: float = 0.0) -> list[dict]:
    """Copy a selected dependency closure with new IDs and optional translation."""
    offset_x, offset_y = _number(dx, "Copy x offset"), _number(dy, "Copy y offset")
    selected = geometry_dependency_closure(objects, object_ids)
    return _transform_records(selected, lambda x, y: (x + offset_x, y + offset_y), duplicate=True)


def rotate_geometry(objects: Sequence[dict], object_ids: Sequence[str], *,
                    center_x: float, center_y: float, angle_radians: float) -> list[dict]:
    """Rotate a selected dependency closure around a 2D center, preserving IDs."""
    cx, cy = _number(center_x, "Rotation center x"), _number(center_y, "Rotation center y")
    angle = _number(angle_radians, "Rotation angle")
    cosine, sine = math.cos(angle), math.sin(angle)
    selected = geometry_dependency_closure(objects, object_ids)
    return _transform_records(selected,
                              lambda x, y: (cx + (x - cx) * cosine - (y - cy) * sine,
                                            cy + (x - cx) * sine + (y - cy) * cosine),
                              duplicate=False)


def scale_geometry(objects: Sequence[dict], object_ids: Sequence[str], *,
                   center_x: float, center_y: float, factor: float) -> list[dict]:
    """Apply positive uniform scale around a center, preserving IDs."""
    cx, cy = _number(center_x, "Scale center x"), _number(center_y, "Scale center y")
    scale = _number(factor, "Scale factor")
    if scale <= 1e-12:
        raise ValidationError("Scale factor must be greater than 1e-12.")
    selected = geometry_dependency_closure(objects, object_ids)
    return _transform_records(selected,
                              lambda x, y: (cx + (x - cx) * scale, cy + (y - cy) * scale),
                              duplicate=False)
