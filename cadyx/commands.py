"""Initial demonstration commands routed through one atomic command system."""

from __future__ import annotations

import math
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .errors import NotFoundError, ValidationError
from .delivery import compare_step_metrics, geometry_sha256, verify_binary_stl
from .engineering import involute_spur_gear_profile
from .kernel import GeometryKernel
from .mesh_geometry import mesh_ply_bytes
from .models import Document
from .scan import DEFAULT_SCAN_PARAMETERS, ScanProcessor, normalize_scan_parameters
from .sketches import closed_profile, open_path
from .capabilities import COMMAND_CAPABILITIES

_SUPPORTED_COMMAND_TYPES = frozenset(item["type"] for item in COMMAND_CAPABILITIES)


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a number.")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValidationError(f"{name} must be a finite number.") from None
    if not math.isfinite(result):
        raise ValidationError(f"{name} must be a finite number.")
    return result


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a number.")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValidationError(f"{name} must be a finite positive number.") from None
    if not math.isfinite(result) or result <= 0:
        raise ValidationError(f"{name} must be a finite positive number.")
    return result


class CommandSystem:
    def __init__(
        self,
        kernel: GeometryKernel,
        *,
        resolve_scan: Callable[[str, str], Path] | None = None,
        resolve_project_file: Callable[[str, str], Path] | None = None,
        scan_processor: ScanProcessor | None = None,
        publish_scan_output: Callable[[str, str, bytes], dict[str, Any]] | None = None,
        publish_cad_delivery: Callable[[str, dict[str, bytes]], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.kernel = kernel
        self.resolve_scan = resolve_scan
        self.resolve_project_file = resolve_project_file
        self.scan_processor = scan_processor if scan_processor is not None else ScanProcessor()
        self.publish_scan_output = publish_scan_output
        self.publish_cad_delivery = publish_cad_delivery

    def execute(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(command, dict):
            raise ValidationError("command must be a JSON object.")
        kind = command.get("type")
        if not isinstance(kind, str) or kind not in _SUPPORTED_COMMAND_TYPES:
            raise ValidationError(f"Unsupported command type: {kind!r}.")
        if kind == "create_box":
            return self._create_box(document, command)
        if kind == "create_metric_thread":
            return self._create_metric_thread(document, command, progress=progress)
        if kind in {"extrude_sketch", "revolve_sketch"}:
            return self._create_profile_feature(document, command, kind, progress=progress)
        if kind == "fuse_bodies":
            return self._fuse_bodies(document, command, progress=progress)
        if kind == "cut_bodies":
            return self._cut_bodies(document, command, progress=progress)
        if kind == "sweep_sketch":
            return self._sweep_sketch(document, command, progress=progress)
        if kind == "fillet_edges":
            return self._fillet_edges(document, command, progress=progress)
        if kind == "set_parameter":
            return self._set_parameter(document, command, progress=progress)
        if kind == "import_scan":
            return self._import_scan(document, command, progress=progress)
        if kind == "import_step":
            return self._import_step(document, command, project_id=project_id, progress=progress)
        if kind == "export_brep":
            return self._export_brep(document, command, project_id=project_id, progress=progress)
        if kind == "resize_imported_thread":
            return self._resize_imported_thread(document, command, progress=progress)
        if kind == "transform_imported_brep":
            return self._transform_imported_brep(document, command, progress=progress)
        if kind == "set_scan_parameter":
            return self._set_scan_parameter(document, command, progress=progress)
        if kind == "create_sketch":
            sketch_id = f"sketch:{uuid4()}"
            document.objects.append({
                "id": sketch_id, "type": "sketch",
                "name": command.get("name", "Sketch"), "plane": command.get("plane", "XY"),
            })
            return {"sketch_id": sketch_id}
        if kind == "add_sketch_element":
            element_kind = command.get("element_type")
            fields = {"point": ("x", "y"), "line": ("start_id", "end_id"), "circle": ("center_id", "radius")}
            if not isinstance(element_kind, str) or element_kind not in fields:
                raise ValidationError("Unsupported sketch element type.")
            object_id = f"{element_kind}:{uuid4()}"
            document.objects.append({
                "id": object_id, "type": element_kind, "sketch_id": command.get("sketch_id"),
                **{key: command.get(key) for key in fields[element_kind]},
            })
            return {"object_id": object_id}
        if kind == "add_sketch_polyline":
            return self._add_sketch_polyline(document, command, progress=progress)
        if kind == "add_sketch_involute_gear":
            return self._add_sketch_involute_gear(document, command, progress=progress)
        if kind == "add_sketch_rectangle":
            return self._add_sketch_rectangle(document, command, progress=progress)
        if kind in {"add_sketch_rectangle_two_corners", "add_sketch_rectangle_center",
                    "add_sketch_rectangle_three_points", "add_sketch_rotated_rectangle",
                    "add_sketch_parallelogram", "add_sketch_regular_polygon"}:
            return self._add_sketch_polygon_tool(document, command, kind, progress=progress)
        if kind == "add_sketch_circle":
            return self._add_sketch_circle(document, command, progress=progress)
        if kind in {"add_sketch_circle_diameter", "add_sketch_circle_two_points", "add_sketch_circle_three_points"}:
            return self._add_sketch_circle_tool(document, command, kind, progress=progress)
        if kind == "mirror_sketch_elements":
            return self._mirror_sketch_elements(document, command, progress=progress)
        if kind == "pattern_sketch_elements":
            return self._pattern_sketch_elements(document, command, progress=progress)
        if kind == "add_sketch_constraint":
            object_id = f"constraint:{uuid4()}"
            document.objects.append({
                "id": object_id, "type": "constraint", "sketch_id": command.get("sketch_id"),
                "kind": command.get("kind"), "references": command.get("references"),
                "value": command.get("value"),
            })
            return {"constraint_id": object_id}
        if kind == "update_sketch_elements":
            updates = command.get("updates")
            if not isinstance(updates, list) or not updates:
                raise ValidationError("updates must be a non-empty list.")
            seen = set()
            for update in updates:
                if not isinstance(update, dict):
                    raise ValidationError("Every update must be an object.")
                object_id = update.get("object_id")
                if not isinstance(object_id, str) or object_id in seen:
                    raise ValidationError("Updates need unique object ids.")
                seen.add(object_id)
                item = next((item for item in document.objects if item["id"] == object_id), None)
                fields = {"point": {"x", "y"}, "circle": {"radius"}}
                if item is None or item["type"] not in fields:
                    raise NotFoundError("Editable sketch element was not found.")
                changes = update.get("changes")
                if not isinstance(changes, dict) or not changes or not changes.keys() <= fields[item["type"]]:
                    raise ValidationError("Unsupported sketch element changes.")
                item.update(changes)
            changed_sketches = {
                item["sketch_id"] for item in document.objects
                if item.get("id") in seen and item.get("type") in {"point", "circle"}
            }
            self._rebuild_changed_features(document, changed_sketches, progress=progress)
            return {"object_ids": sorted(seen)}
        if kind == "replace_sketch_profile":
            return self._replace_sketch_profile(document, command, progress=progress)
        if kind == "replace_sketch_path":
            return self._replace_sketch_path(document, command, progress=progress)
        if kind == "remove_sketch_object":
            object_id = command.get("object_id")
            item = next((item for item in document.objects if item["id"] == object_id), None)
            if item is None or item["type"] not in {"sketch", "point", "line", "circle", "constraint"}:
                raise NotFoundError("Sketch object was not found.")
            if item["type"] == "sketch" and any(
                feature.get("type") == "feature" and feature.get("source_sketch_id") == object_id
                for feature in document.objects
            ):
                raise ValidationError("A sketch used by a solid feature cannot be removed.")
            document.objects.remove(item)
            return {"object_id": object_id}
        raise ValidationError(f"Unsupported command type: {kind!r}.")

    def _add_sketch_polyline(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch_id = command.get("sketch_id")
        coordinates = command.get("points")
        closed = command.get("closed", False)
        if not isinstance(closed, bool):
            raise ValidationError("closed must be a boolean.")
        minimum, maximum = (3, 4096) if closed else (2, 512)
        if not isinstance(coordinates, list) or not minimum <= len(coordinates) <= maximum:
            shape = "closed polygon" if closed else "polyline"
            raise ValidationError(f"A {shape} needs between {minimum} and {maximum} points.")

        points: list[tuple[float, float]] = []
        for point in coordinates:
            if not isinstance(point, dict) or set(point) != {"x", "y"}:
                raise ValidationError("Every polyline point needs x and y coordinates.")
            points.append((
                _finite_number(point["x"], "Polyline x coordinate"),
                _finite_number(point["y"], "Polyline y coordinate"),
            ))

        sketch = self._find_sketch(document, sketch_id)
        return self._append_sketch_polyline(
            document, sketch, points, closed=closed, operation="add_sketch_polyline", progress=progress,
        )

    def _add_sketch_involute_gear(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch = self._find_sketch(document, command.get("sketch_id"))
        if sketch.get("plane") != "XY":
            raise ValidationError("An involute spur gear profile needs an XY sketch.")
        if any(item.get("sketch_id") == sketch["id"] for item in document.objects):
            raise ValidationError("An involute spur gear needs an empty sketch.")
        points, parameters = involute_spur_gear_profile(
            module=command.get("module"),
            tooth_count=command.get("tooth_count"),
            pressure_angle_degrees=command.get("pressure_angle_degrees", 20.0),
            center_x=command.get("center_x", 0.0),
            center_y=command.get("center_y", 0.0),
            rotation_degrees=command.get("rotation_degrees", 0.0),
            backlash=command.get("backlash", 0.0),
        )
        result = self._append_sketch_polyline(
            document, sketch, points, closed=True,
            operation="add_sketch_involute_gear", progress=progress,
        )
        result["gear_parameters"] = parameters
        return result

    def _add_sketch_rectangle(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch = self._find_sketch(document, command.get("sketch_id"))
        x = _finite_number(command.get("x"), "Rectangle x coordinate")
        y = _finite_number(command.get("y"), "Rectangle y coordinate")
        width = _number(command.get("width"), "width")
        height = _number(command.get("height"), "height")
        right, top = x + width, y + height
        if not math.isfinite(right) or not math.isfinite(top):
            raise ValidationError("Rectangle coordinates must be finite numbers.")
        points = [(x, y), (right, y), (right, top), (x, top)]
        return self._append_sketch_polyline(
            document, sketch, points, closed=True, operation="add_sketch_rectangle", progress=progress,
        )

    def _add_sketch_polygon_tool(
        self,
        document: Document,
        command: dict[str, Any],
        kind: str,
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch = self._find_sketch(document, command.get("sketch_id"))
        if kind == "add_sketch_rectangle_two_corners":
            x1, y1, x2, y2 = (
                _finite_number(command.get(key), key) for key in ("x1", "y1", "x2", "y2")
            )
            points = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        elif kind == "add_sketch_rectangle_center":
            cx = _finite_number(command.get("center_x"), "Rectangle center x")
            cy = _finite_number(command.get("center_y"), "Rectangle center y")
            width, height = _number(command.get("width"), "width"), _number(command.get("height"), "height")
            points = [(cx-width/2, cy-height/2), (cx+width/2, cy-height/2),
                      (cx+width/2, cy+height/2), (cx-width/2, cy+height/2)]
        elif kind in {"add_sketch_rectangle_three_points", "add_sketch_parallelogram"}:
            values = command.get("points")
            if not isinstance(values, list) or len(values) != 3:
                raise ValidationError("Three-point geometry requires exactly three coordinate objects.")
            points = [self._coordinate_pair(value, "Sketch point") for value in values]
            first, corner, third = points
            first_vector = (first[0]-corner[0], first[1]-corner[1])
            second_vector = (third[0]-corner[0], third[1]-corner[1])
            cross = first_vector[0]*second_vector[1] - first_vector[1]*second_vector[0]
            if not math.isfinite(cross) or abs(cross) <= 1e-14:
                raise ValidationError("Three-point geometry needs non-collinear points.")
            fourth = (first[0]+third[0]-corner[0], first[1]+third[1]-corner[1])
            if kind == "add_sketch_rectangle_three_points":
                dot = first_vector[0]*second_vector[0] + first_vector[1]*second_vector[1]
                scale = math.hypot(*first_vector) * math.hypot(*second_vector)
                if not math.isfinite(dot) or abs(dot) > 1e-7 * max(1.0, scale):
                    raise ValidationError("Rectangle sides defined by three points must be perpendicular.")
            points = [first, corner, third, fourth]
        elif kind == "add_sketch_rotated_rectangle":
            cx = _finite_number(command.get("center_x"), "Rectangle center x")
            cy = _finite_number(command.get("center_y"), "Rectangle center y")
            width, height = _number(command.get("width"), "width"), _number(command.get("height"), "height")
            angle = math.radians(_finite_number(command.get("angle_degrees"), "Rectangle angle"))
            cosine, sine = math.cos(angle), math.sin(angle)
            points = [(-width/2, -height/2), (width/2, -height/2),
                      (width/2, height/2), (-width/2, height/2)]
            points = [(cx+x*cosine-y*sine, cy+x*sine+y*cosine) for x, y in points]
        else:
            cx = _finite_number(command.get("center_x"), "Polygon center x")
            cy = _finite_number(command.get("center_y"), "Polygon center y")
            sides = command.get("side_count")
            if type(sides) is not int or not 3 <= sides <= 512:
                raise ValidationError("A regular polygon needs 3 to 512 sides.")
            radius = _number(command.get("radius"), "radius")
            radius_mode = command.get("radius_mode", "inscribed")
            if radius_mode not in {"inscribed", "circumscribed"}:
                raise ValidationError("radius_mode must be inscribed or circumscribed.")
            if radius_mode == "circumscribed":
                radius /= math.cos(math.pi / sides)
            rotation = math.radians(_finite_number(command.get("rotation_degrees", 0), "Polygon rotation"))
            points = [(cx + radius*math.cos(rotation + math.tau*index/sides),
                       cy + radius*math.sin(rotation + math.tau*index/sides)) for index in range(sides)]
        if any(not math.isfinite(value) for point in points for value in point):
            raise ValidationError("Sketch coordinates must remain finite.")
        return self._append_sketch_polyline(
            document, sketch, points, closed=True, operation=kind, progress=progress,
        )

    def _add_sketch_circle_tool(
        self,
        document: Document,
        command: dict[str, Any],
        kind: str,
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if kind == "add_sketch_circle_diameter":
            diameter = _number(command.get("diameter"), "diameter")
            return self._add_sketch_circle(
                document, {"sketch_id": command.get("sketch_id"), "x": command.get("x"),
                           "y": command.get("y"), "radius": diameter / 2}, progress=progress,
            )
        if kind == "add_sketch_circle_two_points":
            first = self._coordinate_pair(command.get("first"), "First circle point")
            second = self._coordinate_pair(command.get("second"), "Second circle point")
            cx, cy = first[0] / 2 + second[0] / 2, first[1] / 2 + second[1] / 2
            radius = math.hypot(first[0] - second[0], first[1] - second[1]) / 2
        else:
            first = self._coordinate_pair(command.get("first"), "First circle point")
            second = self._coordinate_pair(command.get("second"), "Second circle point")
            third = self._coordinate_pair(command.get("third"), "Third circle point")
            ax, ay = first
            bx, by = second[0] - ax, second[1] - ay
            cx, cy = third[0] - ax, third[1] - ay
            determinant = 2 * (bx*cy - by*cx)
            scale = max(math.hypot(bx, by), math.hypot(cx, cy), math.hypot(cx-bx, cy-by))
            if not math.isfinite(determinant) or abs(determinant) <= 1e-14 * max(1.0, scale*scale):
                raise ValidationError("Three circle points must be distinct and non-collinear.")
            b2, c2 = bx*bx+by*by, cx*cx+cy*cy
            offset_x = (b2*cy - c2*by) / determinant
            offset_y = (bx*c2 - cx*b2) / determinant
            center_x, center_y = ax + offset_x, ay + offset_y
            cx, cy = center_x, center_y
            radius = math.hypot(offset_x, offset_y)
        if not all(math.isfinite(value) for value in (cx, cy, radius)) or radius <= 0:
            raise ValidationError("Circle points must define a finite circle with positive radius.")
        return self._add_sketch_circle(
            document, {"sketch_id": command.get("sketch_id"), "x": cx, "y": cy, "radius": radius},
            progress=progress,
        )

    def _add_sketch_circle(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch = self._find_sketch(document, command.get("sketch_id"))
        x = _finite_number(command.get("x"), "Circle x coordinate")
        y = _finite_number(command.get("y"), "Circle y coordinate")
        radius = _number(command.get("radius"), "radius")
        if any(not math.isfinite(value) for value in (x - radius, x + radius, y - radius, y + radius)):
            raise ValidationError("Circle coordinates must be finite numbers.")
        if progress is not None:
            progress({"phase": "add_sketch_circle"})
        sketch_id = sketch["id"]
        point_id = f"point:{uuid4()}"
        circle_id = f"circle:{uuid4()}"
        sketch_index = document.objects.index(sketch)
        document.objects[sketch_index + 1:sketch_index + 1] = [
            {"id": point_id, "type": "point", "sketch_id": sketch_id, "x": x, "y": y},
            {"id": circle_id, "type": "circle", "sketch_id": sketch_id,
             "center_id": point_id, "radius": radius},
        ]
        self._rebuild_changed_features(document, {sketch_id}, progress=progress)
        return {"sketch_id": sketch_id, "point_id": point_id, "circle_id": circle_id}

    def _mirror_sketch_elements(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch_id = command.get("sketch_id")
        sketch = self._find_sketch(document, sketch_id)
        start = self._coordinate_pair(command.get("axis_start"), "Mirror axis start")
        end = self._coordinate_pair(command.get("axis_end"), "Mirror axis end")
        dx, dy = end[0] - start[0], end[1] - start[1]
        length_squared = dx * dx + dy * dy
        if not math.isfinite(length_squared) or length_squared <= 1e-14:
            raise ValidationError("Mirror axis endpoints must be distinct.")

        source = self._sketch_geometry_closure(document, sketch_id, command.get("object_ids"))

        def reflect(x: float, y: float) -> tuple[float, float]:
            along = ((x - start[0]) * dx + (y - start[1]) * dy) / length_squared
            projected_x, projected_y = start[0] + along * dx, start[1] + along * dy
            return 2 * projected_x - x, 2 * projected_y - y

        if progress is not None:
            progress({"phase": "mirror_sketch_elements"})
        return self._copy_sketch_geometry(document, sketch, source, [reflect], progress=progress)

    def _pattern_sketch_elements(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch_id = command.get("sketch_id")
        sketch = self._find_sketch(document, sketch_id)
        count = command.get("count")
        if type(count) is not int or not 2 <= count <= 64:
            raise ValidationError("Pattern count must be an integer from 2 to 64, including the source.")
        mode = command.get("mode")
        transforms: list[Callable[[float, float], tuple[float, float]]] = []
        if mode == "linear":
            offset_x = _finite_number(command.get("offset_x"), "Pattern x offset")
            offset_y = _finite_number(command.get("offset_y"), "Pattern y offset")
            if math.hypot(offset_x, offset_y) <= 1e-7:
                raise ValidationError("Linear pattern offset must be non-zero.")
            transforms = [
                (lambda x, y, step=index: (x + offset_x * step, y + offset_y * step))
                for index in range(1, count)
            ]
        elif mode == "circular":
            center_x = _finite_number(command.get("center_x"), "Pattern center x")
            center_y = _finite_number(command.get("center_y"), "Pattern center y")
            step_degrees = _finite_number(command.get("angle_step_degrees"), "Pattern angle step")
            if math.isclose(step_degrees % 360, 0, rel_tol=0, abs_tol=1e-10):
                raise ValidationError("Circular pattern angle step must not repeat the source position.")
            stepped_degrees = [step_degrees * index for index in range(1, count)]
            if any(not math.isfinite(angle) for angle in stepped_degrees):
                raise ValidationError("Circular pattern angles must remain finite.")
            angles = [math.radians(angle) for angle in stepped_degrees]
            normalized = [0.0, *(round(angle % math.tau, 12) for angle in angles)]
            if len(set(normalized)) != len(normalized):
                raise ValidationError("Circular pattern would create overlapping repeated instances.")
            transforms = [
                (lambda x, y, angle=angle: (
                    center_x + (x - center_x) * math.cos(angle) - (y - center_y) * math.sin(angle),
                    center_y + (x - center_x) * math.sin(angle) + (y - center_y) * math.cos(angle),
                ))
                for angle in angles
            ]
        else:
            raise ValidationError("Pattern mode must be linear or circular.")

        source = self._sketch_geometry_closure(document, sketch_id, command.get("object_ids"))
        if len(source) * len(transforms) > 4096:
            raise ValidationError("Pattern would create more than 4096 sketch objects.")
        if progress is not None:
            progress({"phase": "pattern_sketch_elements"})
        return self._copy_sketch_geometry(document, sketch, source, transforms, progress=progress)

    @staticmethod
    def _coordinate_pair(value: Any, name: str) -> tuple[float, float]:
        if not isinstance(value, dict) or set(value) != {"x", "y"}:
            raise ValidationError(f"{name} needs x and y coordinates.")
        return _finite_number(value["x"], f"{name} x"), _finite_number(value["y"], f"{name} y")

    @staticmethod
    def _sketch_geometry_closure(
        document: Document, sketch_id: str, object_ids: Any,
    ) -> list[dict[str, Any]]:
        if not isinstance(object_ids, list) or not object_ids or len(object_ids) > 4096:
            raise ValidationError("object_ids must contain between 1 and 4096 sketch geometry ids.")
        if any(not isinstance(object_id, str) for object_id in object_ids) or len(set(object_ids)) != len(object_ids):
            raise ValidationError("object_ids must contain unique text ids.")
        by_id = {item["id"]: item for item in document.objects}
        included: set[str] = set()
        pending = list(object_ids)
        while pending:
            object_id = pending.pop()
            item = by_id.get(object_id)
            if item is None or item.get("sketch_id") != sketch_id or item.get("type") not in {"point", "line", "circle"}:
                raise ValidationError("Selected objects must be points, lines, or circles from the same sketch.")
            if object_id in included:
                continue
            included.add(object_id)
            if len(included) > 4096:
                raise ValidationError("Selected sketch geometry exceeds 4096 objects including dependencies.")
            if item["type"] == "line":
                pending.extend((item["start_id"], item["end_id"]))
            elif item["type"] == "circle":
                pending.append(item["center_id"])
        return [item for item in document.objects if item.get("id") in included]

    def _copy_sketch_geometry(
        self,
        document: Document,
        sketch: dict[str, Any],
        source: list[dict[str, Any]],
        transforms: list[Callable[[float, float], tuple[float, float]]],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch_id = sketch["id"]
        copies: list[dict[str, Any]] = []
        for transform in transforms:
            id_map = {item["id"]: f"{item['type']}:{uuid4()}" for item in source}
            for item in source:
                clone = dict(item)
                clone["id"] = id_map[item["id"]]
                if item["type"] == "point":
                    clone["x"], clone["y"] = transform(item["x"], item["y"])
                    if not math.isfinite(clone["x"]) or not math.isfinite(clone["y"]):
                        raise ValidationError("Transformed sketch coordinates must be finite.")
                elif item["type"] == "line":
                    clone["start_id"], clone["end_id"] = id_map[item["start_id"]], id_map[item["end_id"]]
                else:
                    clone["center_id"] = id_map[item["center_id"]]
                copies.append(clone)
        insert_at = document.objects.index(sketch) + 1
        document.objects[insert_at:insert_at] = copies
        self._rebuild_changed_features(document, {sketch_id}, progress=progress)
        return {
            "sketch_id": sketch_id,
            "source_object_ids": [item["id"] for item in source],
            "object_ids": [item["id"] for item in copies],
            "point_ids": [item["id"] for item in copies if item["type"] == "point"],
            "line_ids": [item["id"] for item in copies if item["type"] == "line"],
            "circle_ids": [item["id"] for item in copies if item["type"] == "circle"],
            "instance_count": len(transforms) + 1,
        }

    @staticmethod
    def _find_sketch(document: Document, sketch_id: Any) -> dict[str, Any]:
        sketch = next((item for item in document.objects
                       if item.get("id") == sketch_id and item.get("type") == "sketch"), None)
        if sketch is None:
            raise NotFoundError("Sketch was not found.")
        return sketch

    def _append_sketch_polyline(
        self,
        document: Document,
        sketch: dict[str, Any],
        points: list[tuple[float, float]],
        *,
        closed: bool,
        operation: str,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        segments = list(zip(points, points[1:]))
        if closed:
            segments.append((points[-1], points[0]))
        lengths = [math.hypot(start[0] - end[0], start[1] - end[1]) for start, end in segments]
        if any(not math.isfinite(length) or length <= 1e-7 for length in lengths):
            raise ValidationError("Polyline segments must have distinct endpoints.")

        if progress is not None:
            progress({"phase": operation})
        sketch_id = sketch["id"]
        point_ids = [f"point:{uuid4()}" for _ in points]
        line_count = len(points) if closed else len(points) - 1
        line_ids = [f"line:{uuid4()}" for _ in range(line_count)]
        elements = [
            {"id": point_id, "type": "point", "sketch_id": sketch_id, "x": x, "y": y}
            for point_id, (x, y) in zip(point_ids, points, strict=True)
        ]
        elements.extend(
            {"id": line_id, "type": "line", "sketch_id": sketch_id,
             "start_id": point_ids[index], "end_id": point_ids[(index + 1) % len(points)]}
            for index, line_id in enumerate(line_ids)
        )
        sketch_index = document.objects.index(sketch)
        document.objects[sketch_index + 1:sketch_index + 1] = elements
        self._rebuild_changed_features(document, {sketch_id}, progress=progress)
        return {"sketch_id": sketch_id, "point_ids": point_ids, "line_ids": line_ids, "closed": closed}

    def _publish_scan_output(
        self,
        folder: str,
        source_filename: str,
        geometry: dict[str, Any],
        progress: Callable[[dict[str, Any]], None] | None,
    ) -> dict[str, Any]:
        if self.publish_scan_output is None:
            raise ValidationError("Scan output requires a workspace artifact writer.")
        if progress is not None:
            progress({"phase": "write_output"})
        return self.publish_scan_output(folder, source_filename, mesh_ply_bytes(geometry))

    def _import_scan(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if self.resolve_scan is None:
            raise ValidationError("Scan import requires a project workspace file resolver.")
        folder, filename = command.get("folder"), command.get("file")
        if not isinstance(folder, str) or not isinstance(filename, str):
            raise ValidationError("Scan import requires a workspace folder and PLY filename.")
        name = command.get("name", Path(filename).stem)
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValidationError("Scan name must be a non-empty string of at most 120 characters.")
        parameters = normalize_scan_parameters(command.get("parameters", {}))
        source_path = self.resolve_scan(folder, filename)
        geometry, stats, source_hash = self.scan_processor.reconstruct(source_path, parameters, progress=progress)
        if stats["source_kind"] == "triangle_mesh" and any(
            parameters[key] != DEFAULT_SCAN_PARAMETERS[key]
            for key in ("outlier_neighbors", "outlier_std_ratio", "poisson_depth")
        ):
            raise ValidationError("Outlier and Poisson parameters apply only to PLY point clouds.")

        body_id = f"body:{uuid4()}"
        feature_id = f"feature:{uuid4()}"
        artifact = self._publish_scan_output(folder, filename, geometry, progress)
        source = {"folder": folder, "file": filename, "sha256": source_hash}
        document.objects.extend([
            {
                "id": body_id,
                "type": "body",
                "name": name.strip(),
                "feature_ids": [feature_id],
                "geometry": geometry,
            },
            {
                "id": feature_id,
                "type": "feature",
                "operation": "reconstruct_scan",
                "body_id": body_id,
                "source": source,
                "artifact": artifact,
                "source_kind": stats["source_kind"],
                "parameters": parameters,
                "stats": stats,
                "processor": {"name": "Open3D CPU", "version": stats["processor_version"]},
            },
        ])
        return {
            "body_id": body_id,
            "feature_id": feature_id,
            "source_kind": stats["source_kind"],
            "input_vertices": stats["input_vertices"],
            "input_triangles": stats["input_triangles"],
            "output_vertices": stats["output_vertices"],
            "output_triangles": stats["output_triangles"],
            "parameters": parameters,
            "artifact": artifact,
        }

    def _import_step(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        project_id: str | None,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if self.resolve_project_file is None or not isinstance(project_id, str):
            raise ValidationError("STEP import requires a file in the current CADYX project.")
        filename = command.get("file")
        if not isinstance(filename, str) or Path(filename).suffix.casefold() not in {".step", ".stp"}:
            raise ValidationError("STEP import requires a .step or .stp file in the current project.")
        name = command.get("name", Path(filename).stem)
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValidationError("STEP model name must be a non-empty string of at most 120 characters.")
        source_path = self.resolve_project_file(project_id, filename)
        source_size = source_path.stat().st_size
        if source_size > 64 * 1024 * 1024:
            raise ValidationError("STEP source exceeds the 64 MB limit.")
        if progress is not None:
            progress({"phase": "read_step_source"})
        source = source_path.read_bytes()
        if not source or len(source) > 64 * 1024 * 1024:
            raise ValidationError("STEP source is empty or exceeds the 64 MB limit.")
        source_hash = hashlib.sha256(source).hexdigest()
        if progress is not None:
            progress({"phase": "transfer_step_geometry"})
        geometry, metrics = self.kernel.create_imported_step(source)
        body_id, feature_id = f"body:{uuid4()}", f"feature:{uuid4()}"
        source_record = {
            "file": filename,
            "sha256": source_hash,
            "size": len(source),
        }
        document.objects.extend([
            {
                "id": body_id,
                "type": "body",
                "name": name.strip(),
                "feature_ids": [feature_id],
                "geometry": geometry,
            },
            {
                "id": feature_id,
                "type": "feature",
                "operation": "import_step",
                "body_id": body_id,
                "source": source_record,
                "source_metrics": metrics,
            },
        ])
        return {
            "body_id": body_id,
            "feature_id": feature_id,
            "source": source_record,
            "metrics": metrics,
        }

    def _export_brep(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        project_id: str | None,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if self.publish_cad_delivery is None or not isinstance(project_id, str):
            raise ValidationError("CAD delivery export requires a project artifact writer.")
        allowed = {"type", "body_id", "include_stl", "linear_deflection", "angular_deflection"}
        if not command.keys() <= allowed:
            raise ValidationError("CAD delivery export contains unsupported fields.")
        body_id = command.get("body_id")
        body = next((item for item in document.objects
                     if item.get("id") == body_id and item.get("type") == "body"), None)
        if body is None:
            raise NotFoundError("CAD delivery export needs an existing body_id.")
        geometry = body.get("geometry")
        if not isinstance(geometry, dict) or geometry.get("kernel") == "cadyx-mesh-v1":
            raise ValidationError("CAD delivery export requires an OCCT B-rep body, not a triangle mesh.")
        include_stl = command.get("include_stl", False)
        if type(include_stl) is not bool:
            raise ValidationError("include_stl must be a boolean.")
        if include_stl:
            linear_deflection = _number(command.get("linear_deflection", 0.1), "linear_deflection")
            angular_deflection = _number(command.get("angular_deflection", 0.5), "angular_deflection")
            if linear_deflection < 0.001 or not 0.01 <= angular_deflection <= 1.0:
                raise ValidationError("STL deflections must be at least 0.001 linear and from 0.01 to 1.0 radians.")
        else:
            if "linear_deflection" in command or "angular_deflection" in command:
                raise ValidationError("STL deflections can only be supplied when include_stl is true.")
            linear_deflection, angular_deflection = 0.1, 0.5
        exporter = getattr(self.kernel, "export_delivery", None)
        importer = getattr(self.kernel, "create_imported_step", None)
        if not callable(exporter) or not callable(importer):
            raise ValidationError("The configured geometry kernel does not support validated CAD delivery.")

        source_revision = document.revision
        geometry_hash = geometry_sha256(geometry)
        if progress is not None:
            progress({"phase": "validate_source_brep"})
            progress({"phase": "serialize_and_export"})
        exported = exporter(
            geometry, include_stl=include_stl, linear_deflection=linear_deflection,
            angular_deflection=angular_deflection,
        )
        step_data = exported.get("step") if isinstance(exported, dict) else None
        source_metrics = exported.get("metrics") if isinstance(exported, dict) else None
        if not isinstance(step_data, bytes) or not step_data or len(step_data) > 64 * 1024 * 1024:
            raise ValidationError("STEP output is empty or exceeds the existing 64 MB round-trip limit.")
        if not isinstance(source_metrics, dict):
            raise ValidationError("Geometry kernel did not return source B-rep metrics.")
        if progress is not None:
            progress({"phase": "verify_step_round_trip"})
        _, imported_metrics = importer(step_data)
        step_check = compare_step_metrics(source_metrics, imported_metrics)

        stl_data = exported.get("stl")
        stl_check = None
        if include_stl:
            if progress is not None:
                progress({"phase": "verify_stl_mesh"})
            native_stl_metrics = exported.get("stl_metrics")
            if not isinstance(stl_data, bytes) or not isinstance(native_stl_metrics, dict):
                raise ValidationError("Geometry kernel did not return the requested STL and verification metrics.")
            stl_check = verify_binary_stl(
                stl_data, source_volume=source_metrics.get("volume"), native_metrics=native_stl_metrics,
            )

        safe_name = re.sub(r"[^\w.-]+", "-", body.get("name", "body"), flags=re.UNICODE).strip(" .-_")
        safe_name = re.sub(r"-+", "-", safe_name)[:56].rstrip(" .-_") or "body"
        prefix = f"{safe_name}-r{source_revision:06d}-{geometry_hash[:12]}"
        step_hash = hashlib.sha256(step_data).hexdigest()
        step_record = {"file": f"{prefix}-{step_hash[:16]}.step", "sha256": step_hash, "size": len(step_data)}
        files: dict[str, bytes] = {step_record["file"]: step_data}
        stl_record = None
        if include_stl and stl_data is not None:
            stl_hash = hashlib.sha256(stl_data).hexdigest()
            stl_record = {"file": f"{prefix}-{stl_hash[:16]}.stl", "sha256": stl_hash, "size": len(stl_data)}
            files[stl_record["file"]] = stl_data
        manifest = {
            "format": "cadyx-cad-delivery",
            "version": 1,
            "source": {
                "document_revision": source_revision,
                "body_id": body_id,
                "body_name": body["name"],
                "geometry_sha256": geometry_hash,
                "kernel": geometry.get("kernel"),
            },
            "kernel": {"name": "Open CASCADE Technology", "version": exported.get("kernel_version")},
            "outputs": {"step": step_record, "stl": stl_record},
            "verification": {"step_round_trip": step_check, "stl": stl_check},
        }
        manifest_data = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
        manifest_hash = hashlib.sha256(manifest_data).hexdigest()
        manifest_name = f"{prefix}-{manifest_hash[:16]}.cad-delivery.json"
        files[manifest_name] = manifest_data
        if progress is not None:
            progress({"phase": "publish_cad_delivery"})
        published = self.publish_cad_delivery(project_id, files)
        published_by_name = {item["file"]: item for item in published}
        return {
            "source_revision": source_revision,
            "body_id": body_id,
            "geometry_sha256": geometry_hash,
            "manifest": published_by_name[manifest_name],
            "step": published_by_name[step_record["file"]],
            "stl": published_by_name[stl_record["file"]] if stl_record else None,
            "verification": manifest["verification"],
        }

    def _set_scan_parameter(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if self.resolve_scan is None:
            raise ValidationError("Scan parameter editing requires a project workspace file resolver.")
        feature_id = command.get("feature_id")
        feature = next(
            (item for item in document.objects
             if item.get("id") == feature_id and item.get("type") == "feature" and
             item.get("operation") == "reconstruct_scan"),
            None,
        )
        if feature is None:
            raise NotFoundError("Editable scan feature was not found.")
        parameter_name = command.get("name")
        if not isinstance(parameter_name, str) or parameter_name not in DEFAULT_SCAN_PARAMETERS:
            raise ValidationError(f"Unsupported scan parameter: {parameter_name!r}.")
        if (feature.get("source_kind") == "triangle_mesh" and
                parameter_name in {"outlier_neighbors", "outlier_std_ratio", "poisson_depth"}):
            raise ValidationError("Outlier and Poisson parameters apply only to PLY point clouds.")
        feature_parameters = feature.get("parameters")
        if not isinstance(feature_parameters, dict):
            raise ValidationError("Scan feature parameters are invalid.")
        parameters = normalize_scan_parameters({
            **feature_parameters, parameter_name: command.get("value"),
        })
        source = feature.get("source")
        if not isinstance(source, dict) or not all(isinstance(source.get(key), str) for key in ("folder", "file", "sha256")):
            raise ValidationError("Scan feature source metadata is invalid.")
        body = next(
            (item for item in document.objects
             if item.get("id") == feature.get("body_id") and item.get("type") == "body"),
            None,
        )
        if body is None:
            raise ValidationError("Scan feature refers to a missing body.")
        source_path = self.resolve_scan(source["folder"], source["file"])
        geometry, stats, source_hash = self.scan_processor.reconstruct(
            source_path, parameters, expected_sha256=source["sha256"], progress=progress,
        )
        artifact = self._publish_scan_output(source["folder"], source["file"], geometry, progress)
        body["geometry"] = geometry
        feature["parameters"] = parameters
        feature["stats"] = stats
        feature["source"] = {**source, "sha256": source_hash}
        feature["artifact"] = artifact
        feature["processor"] = {"name": "Open3D CPU", "version": stats["processor_version"]}
        return {
            "feature_id": feature_id,
            "parameter": parameter_name,
            "value": parameters[parameter_name],
            "output_vertices": stats["output_vertices"],
            "output_triangles": stats["output_triangles"],
            "artifact": artifact,
        }

    def _create_box(self, document: Document, command: dict[str, Any]) -> dict[str, Any]:
        name = command.get("name", "Box")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValidationError("Box name must be a non-empty string of at most 120 characters.")
        parameters = {
            "width": _number(command.get("width"), "width"),
            "height": _number(command.get("height"), "height"),
            "depth": _number(command.get("depth"), "depth"),
        }
        geometry = self.kernel.create_box(**parameters)
        body_id = f"body:{uuid4()}"
        feature_id = f"feature:{uuid4()}"
        document.objects.extend([
            {
                "id": body_id,
                "type": "body",
                "name": name.strip(),
                "feature_ids": [feature_id],
                "geometry": geometry,
            },
            {
                "id": feature_id,
                "type": "feature",
                "operation": "create_box",
                "body_id": body_id,
                "parameters": parameters,
                "geometry": geometry,
            },
        ])
        return {"body_id": body_id, "feature_id": feature_id, "geometry": geometry}

    def _set_parameter(
        self, document: Document, command: dict[str, Any], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        feature_id = command.get("feature_id")
        parameter_name = command.get("name")
        feature = next(
            (item for item in document.objects if item.get("id") == feature_id and item.get("type") == "feature"),
            None,
        )
        if feature is None or feature.get("operation") != "create_box":
            if feature is None:
                raise NotFoundError("Editable feature was not found.")
            if feature.get("operation") == "metric_thread":
                if parameter_name == "handedness":
                    value = command.get("value")
                    if not isinstance(value, str) or value not in {"right", "left"}:
                        raise ValidationError("handedness must be 'right' or 'left'.")
                elif parameter_name in {"major_diameter", "pitch", "length"}:
                    value = _number(command.get("value"), parameter_name)
                else:
                    raise ValidationError("Only major_diameter, pitch, length, and handedness are editable for a thread.")
                parameters = dict(feature["parameters"])
                parameters[parameter_name] = value
                if progress is not None:
                    progress({"phase": "kernel_metric_thread"})
                geometry = self.kernel.create_metric_thread(**parameters)
                body = next((item for item in document.objects
                             if item.get("id") == feature.get("body_id") and item.get("type") == "body"), None)
                if body is None:
                    raise ValidationError("Metric thread feature refers to a missing body.")
                feature["parameters"], feature["geometry"], body["geometry"] = parameters, geometry, geometry
                return {"feature_id": feature_id, "parameter": parameter_name,
                        "value": value, "geometry": geometry}
            if feature.get("operation") in {"extrude_sketch", "revolve_sketch"}:
                expected = "depth" if feature["operation"] == "extrude_sketch" else "angle_degrees"
                if parameter_name == "centered" and feature["operation"] == "extrude_sketch":
                    value = command.get("value")
                    if type(value) is not bool:
                        raise ValidationError("centered must be a boolean.")
                elif parameter_name == expected:
                    value = _number(command.get("value"), parameter_name)
                else:
                    raise ValidationError(f"Only the {expected} and centered parameters are supported for this feature.")
                parameters = dict(feature["parameters"])
                parameters[parameter_name] = value
                if progress is not None:
                    progress({"phase": "prepare_profile"})
                geometry = self._profile_geometry(document, feature, parameters, progress=progress)
                body = next((item for item in document.objects
                             if item.get("id") == feature.get("body_id") and item.get("type") == "body"), None)
                if body is None:
                    raise ValidationError("Profile feature refers to a missing body.")
                feature["parameters"] = parameters
                feature["geometry"] = geometry
                body["geometry"] = geometry
                self._rebuild_derived_features(document, {body["id"]}, progress=progress)
                return {"feature_id": feature_id, "parameter": parameter_name,
                        "value": value, "geometry": geometry}
            if feature.get("operation") == "sweep_sketch" and parameter_name == "radius":
                value = _number(command.get("value"), "radius")
                parameters = dict(feature["parameters"])
                parameters["radius"] = value
                plane, path = open_path(document.objects, feature.get("source_sketch_id"))
                geometry = self.kernel.create_swept_pipe(path, plane=plane, radius=value)
                body = next((item for item in document.objects
                             if item.get("id") == feature.get("body_id") and item.get("type") == "body"), None)
                if body is None:
                    raise ValidationError("Sweep feature refers to a missing body.")
                feature["parameters"], feature["geometry"], body["geometry"] = parameters, geometry, geometry
                self._rebuild_derived_features(document, {body["id"]}, progress=progress)
                return {"feature_id": feature_id, "parameter": parameter_name, "value": value, "geometry": geometry}
            if feature.get("operation") == "fillet_edges" and parameter_name == "radius":
                value = _number(command.get("value"), "radius")
                parameters = dict(feature["parameters"])
                parameters["radius"] = value
                source = next((item for item in document.objects
                               if item.get("id") == feature.get("source_body_id") and item.get("type") == "body"), None)
                body = next((item for item in document.objects
                             if item.get("id") == feature.get("body_id") and item.get("type") == "body"), None)
                if source is None or body is None:
                    raise ValidationError("Fillet feature refers to a missing body.")
                geometry = self.kernel.create_fillet(source["geometry"],
                    edge_indices=feature["edge_indices"], radius=value)
                feature["parameters"], feature["geometry"], body["geometry"] = parameters, geometry, geometry
                self._rebuild_derived_features(document, {body["id"]}, progress=progress)
                return {"feature_id": feature_id, "parameter": parameter_name, "value": value, "geometry": geometry}
            raise NotFoundError("Editable feature was not found.")
        if parameter_name not in {"width", "height", "depth"}:
            raise ValidationError("Only box width, height, and depth parameters are supported.")
        value = _number(command.get("value"), parameter_name)
        parameters = dict(feature["parameters"])
        parameters[parameter_name] = value
        geometry = self.kernel.create_box(**parameters)
        feature["parameters"] = parameters
        feature["geometry"] = geometry
        body = next(
            (item for item in document.objects if item.get("id") == feature.get("body_id") and item.get("type") == "body"),
            None,
        )
        if body is None:
            raise ValidationError("Box feature refers to a missing body.")
        body["geometry"] = geometry
        self._rebuild_derived_features(document, {body["id"]}, progress=progress)
        return {"feature_id": feature_id, "parameter": parameter_name, "value": value, "geometry": geometry}

    def _create_metric_thread(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        major_diameter = _number(command.get("major_diameter"), "major_diameter")
        pitch = _number(command.get("pitch"), "pitch")
        length = _number(command.get("length"), "length")
        handedness = command.get("handedness", "right")
        if not isinstance(handedness, str) or handedness not in {"right", "left"}:
            raise ValidationError("handedness must be 'right' or 'left'.")
        default_name = f"M{major_diameter:g}x{pitch:g} {handedness}-hand thread"
        name = command.get("name", default_name)
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValidationError("Feature name must be a non-empty string of at most 120 characters.")
        parameters = {
            "major_diameter": major_diameter,
            "pitch": pitch,
            "length": length,
            "handedness": handedness,
        }
        if progress is not None:
            progress({"phase": "prepare_metric_thread"})
        if progress is not None:
            progress({"phase": "kernel_metric_thread"})
        geometry = self.kernel.create_metric_thread(**parameters)
        body_id, feature_id = f"body:{uuid4()}", f"feature:{uuid4()}"
        document.objects.extend([
            {"id": body_id, "type": "body", "name": name.strip(),
             "feature_ids": [feature_id], "geometry": geometry},
            {"id": feature_id, "type": "feature", "operation": "metric_thread",
             "body_id": body_id, "parameters": parameters, "geometry": geometry},
        ])
        return {"body_id": body_id, "feature_id": feature_id, "geometry": geometry}

    def _create_profile_feature(
        self, document: Document, command: dict[str, Any], operation: str, *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch_id = command.get("sketch_id")
        name = command.get("name", "Extrusion" if operation == "extrude_sketch" else "Revolution")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValidationError("Feature name must be a non-empty string of at most 120 characters.")
        if progress is not None:
            progress({"phase": "prepare_profile"})
        plane, profile = closed_profile(document.objects, sketch_id)
        parameters: dict[str, Any] = {"source_sketch_id": sketch_id}
        if operation == "extrude_sketch":
            parameters["depth"] = _number(command.get("depth"), "depth")
            centered = command.get("centered", False)
            if type(centered) is not bool:
                raise ValidationError("centered must be a boolean.")
            if centered:
                parameters["centered"] = True
        else:
            if plane != "XZ":
                raise ValidationError("Revolution requires a sketch on the XZ plane.")
            angle = _number(command.get("angle_degrees", 360), "angle_degrees")
            if angle > 360:
                raise ValidationError("angle_degrees must be no greater than 360.")
            parameters["angle_degrees"] = angle
        if progress is not None:
            progress({"phase": "kernel_extrusion" if operation == "extrude_sketch" else "kernel_revolution"})
        geometry = self._profile_geometry(document, {
            "operation": operation, "source_sketch_id": sketch_id,
        }, parameters, profile=profile, plane=plane)
        if progress is not None:
            progress({"phase": "validate_result"})
        body_id = f"body:{uuid4()}"
        feature_id = f"feature:{uuid4()}"
        document.objects.extend([
            {"id": body_id, "type": "body", "name": name.strip(),
             "feature_ids": [feature_id], "geometry": geometry},
            {"id": feature_id, "type": "feature", "operation": operation,
             "body_id": body_id, "source_sketch_id": sketch_id,
             "parameters": {key: value for key, value in parameters.items() if key != "source_sketch_id"},
             "geometry": geometry},
        ])
        return {"body_id": body_id, "feature_id": feature_id, "source_sketch_id": sketch_id,
                "geometry": geometry}

    def _profile_geometry(
        self, document: Document, feature: dict[str, Any], parameters: dict[str, Any], *,
        profile: list[dict[str, float]] | None = None, plane: str | None = None,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        source_sketch_id = feature.get("source_sketch_id") or parameters.get("source_sketch_id")
        if profile is None or plane is None:
            plane, profile = closed_profile(document.objects, source_sketch_id)
        operation = feature.get("operation")
        if operation == "extrude_sketch":
            if progress is not None:
                progress({"phase": "kernel_extrusion"})
            return self.kernel.create_extruded_profile(
                profile, plane=plane, depth=parameters["depth"],
                centered=parameters.get("centered", False),
            )
        if operation == "revolve_sketch":
            if progress is not None:
                progress({"phase": "kernel_revolution"})
            if plane != "XZ":
                raise ValidationError("Revolution requires a sketch on the XZ plane.")
            return self.kernel.create_revolved_profile(profile, angle_degrees=parameters["angle_degrees"])
        raise ValidationError("Unsupported sketch feature operation.")

    def _replace_sketch_profile(
        self, document: Document, command: dict[str, Any], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch_id = command.get("sketch_id")
        sketch = next((item for item in document.objects
                       if item.get("id") == sketch_id and item.get("type") == "sketch"), None)
        if sketch is None:
            raise NotFoundError("Sketch was not found.")
        coordinates = command.get("points")
        if not isinstance(coordinates, list) or not 3 <= len(coordinates) <= 4096:
            raise ValidationError("A replacement profile needs between 3 and 4096 points.")
        points: list[tuple[float, float]] = []
        for point in coordinates:
            if not isinstance(point, dict) or set(point) != {"x", "y"}:
                raise ValidationError("Every replacement profile point needs x and y coordinates.")
            values = []
            for axis in ("x", "y"):
                value = point[axis]
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValidationError("Replacement profile coordinates must be finite numbers.")
                value = float(value)
                if not math.isfinite(value):
                    raise ValidationError("Replacement profile coordinates must be finite numbers.")
                values.append(value)
            points.append((values[0], values[1]))
        owned = [item for item in document.objects if item.get("sketch_id") == sketch_id]
        if any(item.get("type") not in {"point", "line"} for item in owned):
            raise ValidationError("Only a line-loop sketch without circles or constraints can be replaced.")
        if progress is not None:
            progress({"phase": "replace_sketch_profile"})
        document.objects = [item for item in document.objects if item.get("sketch_id") != sketch_id]
        point_ids = [f"point:{uuid4()}" for _ in points]
        line_ids = [f"line:{uuid4()}" for _ in points]
        sketch_index = document.objects.index(sketch)
        elements = [
            {"id": point_id, "type": "point", "sketch_id": sketch_id, "x": x, "y": y}
            for point_id, (x, y) in zip(point_ids, points, strict=True)
        ]
        elements.extend(
            {"id": line_id, "type": "line", "sketch_id": sketch_id,
             "start_id": point_ids[index], "end_id": point_ids[(index + 1) % len(points)]}
            for index, line_id in enumerate(line_ids)
        )
        document.objects[sketch_index + 1:sketch_index + 1] = elements
        self._rebuild_changed_features(document, {sketch_id}, progress=progress)
        return {"sketch_id": sketch_id, "point_ids": point_ids, "line_ids": line_ids}

    def _replace_sketch_path(
        self, document: Document, command: dict[str, Any], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch_id = command.get("sketch_id")
        sketch = next((item for item in document.objects
                       if item.get("id") == sketch_id and item.get("type") == "sketch"), None)
        if sketch is None:
            raise NotFoundError("Sketch was not found.")
        coordinates = command.get("points")
        if not isinstance(coordinates, list) or not 3 <= len(coordinates) <= 512:
            raise ValidationError("A replacement sweep path needs 3 to 512 points.")
        points: list[tuple[float, float]] = []
        for point in coordinates:
            if not isinstance(point, dict) or set(point) != {"x", "y"}:
                raise ValidationError("Every sweep path point needs x and y coordinates.")
            values = []
            for axis in ("x", "y"):
                value = point[axis]
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValidationError("Sweep path coordinates must be finite numbers.")
                value = float(value)
                if not math.isfinite(value):
                    raise ValidationError("Sweep path coordinates must be finite numbers.")
                values.append(value)
            if points and math.hypot(values[0] - points[-1][0], values[1] - points[-1][1]) <= 1e-7:
                raise ValidationError("Sweep path points must have distinct consecutive endpoints.")
            points.append((values[0], values[1]))
        if any(item.get("type") not in {"point", "line"}
               for item in document.objects if item.get("sketch_id") == sketch_id):
            raise ValidationError("Only a point-and-line sketch can be replaced by a sweep path.")
        if progress is not None:
            progress({"phase": "replace_sweep_path"})
        document.objects = [item for item in document.objects if item.get("sketch_id") != sketch_id]
        point_ids = [f"point:{uuid4()}" for _ in points]
        line_ids = [f"line:{uuid4()}" for _ in range(len(points) - 1)]
        sketch_index = document.objects.index(sketch)
        elements = [
            {"id": point_id, "type": "point", "sketch_id": sketch_id, "x": x, "y": y}
            for point_id, (x, y) in zip(point_ids, points, strict=True)
        ]
        elements.extend(
            {"id": line_id, "type": "line", "sketch_id": sketch_id,
             "start_id": point_ids[index], "end_id": point_ids[index + 1]}
            for index, line_id in enumerate(line_ids)
        )
        document.objects[sketch_index + 1:sketch_index + 1] = elements
        self._rebuild_changed_features(document, {sketch_id}, progress=progress)
        return {"sketch_id": sketch_id, "point_ids": point_ids, "line_ids": line_ids}

    def _rebuild_changed_features(
        self, document: Document, changed_sketches: set[str], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        by_id = {item["id"]: item for item in document.objects}
        changed_bodies: set[str] = set()
        features = [item for item in document.objects if item.get("type") == "feature"]
        for feature in features:
            if (feature.get("operation") not in {"extrude_sketch", "revolve_sketch", "sweep_sketch"}
                    or feature.get("source_sketch_id") not in changed_sketches):
                continue
            if progress is not None:
                progress({"phase": "rebuild_features"})
            body = by_id.get(feature.get("body_id"))
            if body is None or body.get("type") != "body":
                raise ValidationError("Profile feature refers to a missing body.")
            if feature.get("operation") == "sweep_sketch":
                plane, path = open_path(document.objects, feature.get("source_sketch_id"))
                geometry = self.kernel.create_swept_pipe(
                    path, plane=plane, radius=feature["parameters"]["radius"],
                )
            else:
                geometry = self._profile_geometry(document, feature, feature["parameters"], progress=progress)
            body["geometry"] = geometry
            feature["geometry"] = geometry
            changed_bodies.add(body["id"])
        self._rebuild_derived_features(document, changed_bodies, progress=progress)

    def _rebuild_derived_features(
        self, document: Document, changed_bodies: set[str], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if not changed_bodies:
            return
        by_id = {item["id"]: item for item in document.objects}
        rebuilt_bodies = set(changed_bodies)
        features = [item for item in document.objects if item.get("type") == "feature"]
        for feature in features:
            operation = feature.get("operation")
            if operation == "fillet_edges":
                sources = {feature.get("source_body_id")}
            elif operation in {"fuse_bodies", "cut_bodies"}:
                sources = {feature.get("base_body_id"), feature.get("tool_body_id")}
            else:
                continue
            if not sources & rebuilt_bodies:
                continue
            body = by_id.get(feature.get("body_id"))
            if body is None or body.get("type") != "body":
                raise ValidationError("Derived feature refers to a missing result body.")
            if progress is not None:
                progress({"phase": "kernel_boolean_fuse" if operation == "fuse_bodies" else
                          "kernel_boolean_cut" if operation == "cut_bodies" else "kernel_fillet"})
            if operation == "fillet_edges":
                source = by_id.get(feature.get("source_body_id"))
                if source is None or source.get("type") != "body":
                    raise ValidationError("Fillet feature refers to a missing source body.")
                geometry = self.kernel.create_fillet(source["geometry"],
                    edge_indices=feature["edge_indices"], radius=feature["parameters"]["radius"])
            elif operation == "fuse_bodies":
                base, tool = by_id.get(feature.get("base_body_id")), by_id.get(feature.get("tool_body_id"))
                if any(item is None or item.get("type") != "body" for item in (base, tool)):
                    raise ValidationError("Boolean fuse feature refers to a missing source body.")
                geometry = self.kernel.fuse_profiles(base["geometry"], tool["geometry"])
            else:
                base, tool = by_id.get(feature.get("base_body_id")), by_id.get(feature.get("tool_body_id"))
                if any(item is None or item.get("type") != "body" for item in (base, tool)):
                    raise ValidationError("Boolean cut feature refers to a missing source body.")
                geometry = self.kernel.cut_solids(base["geometry"], tool["geometry"])
            body["geometry"] = geometry
            feature["geometry"] = geometry
            rebuilt_bodies.add(body["id"])

    def _fuse_bodies(
        self, document: Document, command: dict[str, Any], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        base_id, tool_id = command.get("base_body_id"), command.get("tool_body_id")
        if not isinstance(base_id, str) or not isinstance(tool_id, str) or base_id == tool_id:
            raise ValidationError("fuse_bodies needs two different body IDs.")
        by_id = {item["id"]: item for item in document.objects}
        base, tool = by_id.get(base_id), by_id.get(tool_id)
        if any(item is None or item.get("type") != "body" for item in (base, tool)):
            raise NotFoundError("One of the bodies to fuse was not found.")
        consumed = self._consumed_body_ids(document)
        if base_id in consumed or tool_id in consumed:
            raise ValidationError("A body already consumed by a fuse feature cannot be fused again.")
        name = command.get("name", "Объединённая модель")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValidationError("Fused body name must be a non-empty string of at most 120 characters.")
        if progress is not None:
            progress({"phase": "kernel_boolean_fuse"})
        geometry = self.kernel.fuse_profiles(base.get("geometry"), tool.get("geometry"))
        body_id, feature_id = f"body:{uuid4()}", f"feature:{uuid4()}"
        document.objects.extend([
            {"id": body_id, "type": "body", "name": name.strip(),
             "feature_ids": [feature_id], "geometry": geometry},
            {"id": feature_id, "type": "feature", "operation": "fuse_bodies",
             "body_id": body_id, "base_body_id": base_id, "tool_body_id": tool_id,
             "parameters": {}, "geometry": geometry},
        ])
        return {"body_id": body_id, "feature_id": feature_id,
                "base_body_id": base_id, "tool_body_id": tool_id, "geometry": geometry}

    def _cut_bodies(
        self, document: Document, command: dict[str, Any], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        base_id, tool_id = command.get("base_body_id"), command.get("tool_body_id")
        if not isinstance(base_id, str) or not isinstance(tool_id, str) or base_id == tool_id:
            raise ValidationError("cut_bodies needs two different body IDs.")
        by_id = {item["id"]: item for item in document.objects}
        base, tool = by_id.get(base_id), by_id.get(tool_id)
        if any(item is None or item.get("type") != "body" for item in (base, tool)):
            raise NotFoundError("One of the bodies to cut was not found.")
        if base_id in self._consumed_body_ids(document) or tool_id in self._consumed_body_ids(document):
            raise ValidationError("A body already consumed by a Boolean feature cannot be used again.")
        name = command.get("name", "Модель с вырезом")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValidationError("Cut body name must be a non-empty string of at most 120 characters.")
        if progress is not None:
            progress({"phase": "kernel_boolean_cut"})
        geometry = self.kernel.cut_solids(base.get("geometry"), tool.get("geometry"))
        body_id, feature_id = f"body:{uuid4()}", f"feature:{uuid4()}"
        document.objects.extend([
            {"id": body_id, "type": "body", "name": name.strip(),
             "feature_ids": [feature_id], "geometry": geometry},
            {"id": feature_id, "type": "feature", "operation": "cut_bodies",
             "body_id": body_id, "base_body_id": base_id, "tool_body_id": tool_id,
             "parameters": {}, "geometry": geometry},
        ])
        return {"body_id": body_id, "feature_id": feature_id,
                "base_body_id": base_id, "tool_body_id": tool_id, "geometry": geometry}

    @staticmethod
    def _consumed_body_ids(document: Document) -> set[str]:
        consumed: set[str] = set()
        for feature in document.objects:
            if feature.get("type") != "feature":
                continue
            if feature.get("operation") in {"fuse_bodies", "cut_bodies"}:
                consumed.update((feature.get("base_body_id"), feature.get("tool_body_id")))
            elif feature.get("operation") in {
                "fillet_edges", "resize_imported_thread", "transform_imported_brep",
            }:
                consumed.add(feature.get("source_body_id"))
        return consumed - {None}

    def _resize_imported_thread(
        self, document: Document, command: dict[str, Any], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        source_id = command.get("body_id")
        source = next((item for item in document.objects
                       if item.get("id") == source_id and item.get("type") == "body"), None)
        if source is None:
            raise NotFoundError("Imported thread source body was not found.")
        if source_id in self._consumed_body_ids(document):
            raise ValidationError("A consumed solid body cannot be modified again.")
        if not isinstance(source.get("geometry"), dict) or source["geometry"].get("kind") != "imported_brep":
            raise ValidationError("Thread resizing requires an imported OCCT B-rep body.")
        x_start = _finite_number(command.get("x_start"), "x_start")
        x_end = _finite_number(command.get("x_end"), "x_end")
        if x_end <= x_start:
            raise ValidationError("Thread resize x_end must be greater than x_start.")
        axis_y = _finite_number(command.get("axis_origin_y", 0.0), "axis_origin_y")
        axis_z = _finite_number(command.get("axis_origin_z", 0.0), "axis_origin_z")
        major_diameter = _number(command.get("major_diameter"), "major_diameter")
        diameter_delta = _number(command.get("diameter_delta"), "diameter_delta")
        parameters = {
            "x_start": x_start,
            "x_end": x_end,
            "axis_origin_y": axis_y,
            "axis_origin_z": axis_z,
            "major_diameter": major_diameter,
            "diameter_delta": diameter_delta,
        }
        name = command.get("name", f"Резьба +{diameter_delta:g} мм · {source.get('name', 'Тело')}")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValidationError("Thread feature name must be a non-empty string of at most 120 characters.")
        if progress is not None:
            progress({"phase": "kernel_thread_resize"})
        geometry = self.kernel.modify_imported_brep(
            source["geometry"], operation="resize_thread_x",
            parameters=[x_start, x_end, axis_y, axis_z, major_diameter / 2.0, diameter_delta],
        )
        body_id, feature_id = f"body:{uuid4()}", f"feature:{uuid4()}"
        document.objects.extend([
            {"id": body_id, "type": "body", "name": name.strip(),
             "feature_ids": [feature_id], "geometry": geometry},
            {"id": feature_id, "type": "feature", "operation": "resize_imported_thread",
             "body_id": body_id, "source_body_id": source_id,
             "parameters": parameters, "geometry": geometry},
        ])
        return {"body_id": body_id, "feature_id": feature_id, "source_body_id": source_id,
                "parameters": parameters, "geometry": geometry}

    def _transform_imported_brep(
        self, document: Document, command: dict[str, Any], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        source_id = command.get("body_id")
        source = next((item for item in document.objects
                       if item.get("id") == source_id and item.get("type") == "body"), None)
        if source is None:
            raise NotFoundError("Imported B-rep source body was not found.")
        if source_id in self._consumed_body_ids(document):
            raise ValidationError("A consumed solid body cannot be transformed again.")
        if not isinstance(source.get("geometry"), dict) or source["geometry"].get("kind") != "imported_brep":
            raise ValidationError("Rigid transformation requires an imported OCCT B-rep body.")
        rotation_axis = command.get("rotation_axis")
        translation = command.get("translation")
        if (not isinstance(rotation_axis, list) or len(rotation_axis) != 3
                or not isinstance(translation, list) or len(translation) != 3):
            raise ValidationError("Rigid transformation needs three rotation-axis and translation coordinates.")
        axis = [_finite_number(value, "rotation_axis coordinate") for value in rotation_axis]
        vector = [_finite_number(value, "translation coordinate") for value in translation]
        if math.hypot(*axis[:2], axis[2]) <= 1e-12:
            raise ValidationError("Rigid transformation rotation_axis cannot be zero.")
        angle_degrees = _finite_number(command.get("angle_degrees"), "angle_degrees")
        if abs(angle_degrees) > 360:
            raise ValidationError("Rigid transformation angle_degrees must be between -360 and 360.")
        name = command.get("name", f"Выравнивание · {source.get('name', 'Тело')}")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValidationError("Transform feature name must be a non-empty string of at most 120 characters.")
        parameters = {
            "rotation_axis": axis,
            "angle_degrees": angle_degrees,
            "translation": vector,
        }
        if progress is not None:
            progress({"phase": "kernel_transform_brep"})
        geometry = self.kernel.modify_imported_brep(
            source["geometry"], operation="rigid_transform",
            parameters=[*axis, math.radians(angle_degrees), *vector],
        )
        body_id, feature_id = f"body:{uuid4()}", f"feature:{uuid4()}"
        document.objects.extend([
            {"id": body_id, "type": "body", "name": name.strip(),
             "feature_ids": [feature_id], "geometry": geometry},
            {"id": feature_id, "type": "feature", "operation": "transform_imported_brep",
             "body_id": body_id, "source_body_id": source_id,
             "parameters": parameters, "geometry": geometry},
        ])
        return {"body_id": body_id, "feature_id": feature_id, "source_body_id": source_id,
                "parameters": parameters, "geometry": geometry}

    def _sweep_sketch(
        self, document: Document, command: dict[str, Any], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch_id = command.get("sketch_id")
        name = command.get("name", "Трубчатое выдавливание")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValidationError("Feature name must be a non-empty string of at most 120 characters.")
        if progress is not None:
            progress({"phase": "prepare_sweep_path"})
        plane, path = open_path(document.objects, sketch_id)
        radius = _number(command.get("radius"), "radius")
        if progress is not None:
            progress({"phase": "kernel_sweep"})
        geometry = self.kernel.create_swept_pipe(path, plane=plane, radius=radius)
        body_id, feature_id = f"body:{uuid4()}", f"feature:{uuid4()}"
        document.objects.extend([
            {"id": body_id, "type": "body", "name": name.strip(),
             "feature_ids": [feature_id], "geometry": geometry},
            {"id": feature_id, "type": "feature", "operation": "sweep_sketch",
             "body_id": body_id, "source_sketch_id": sketch_id,
             "parameters": {"radius": radius}, "geometry": geometry},
        ])
        return {"body_id": body_id, "feature_id": feature_id, "source_sketch_id": sketch_id,
                "geometry": geometry}

    def _fillet_edges(
        self, document: Document, command: dict[str, Any], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        source_id = command.get("body_id")
        source = next((item for item in document.objects
                       if item.get("id") == source_id and item.get("type") == "body"), None)
        if source is None:
            raise NotFoundError("Fillet source body was not found.")
        if source_id in self._consumed_body_ids(document):
            raise ValidationError("A consumed solid body cannot be filleted again.")
        edges = command.get("edge_indices")
        if (not isinstance(edges, list) or not 1 <= len(edges) <= 128
                or any(type(index) is not int or index < 0 for index in edges)
                or len(set(edges)) != len(edges)):
            raise ValidationError("edge_indices must contain 1 to 128 distinct non-negative edge indices.")
        radius = _number(command.get("radius"), "radius")
        name = command.get("name", f"Скругление · {source.get('name', 'Тело')}")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValidationError("Feature name must be a non-empty string of at most 120 characters.")
        if progress is not None:
            progress({"phase": "kernel_fillet"})
        geometry = self.kernel.create_fillet(source["geometry"], edge_indices=edges, radius=radius)
        body_id, feature_id = f"body:{uuid4()}", f"feature:{uuid4()}"
        document.objects.extend([
            {"id": body_id, "type": "body", "name": name.strip(),
             "feature_ids": [feature_id], "geometry": geometry},
            {"id": feature_id, "type": "feature", "operation": "fillet_edges",
             "body_id": body_id, "source_body_id": source_id, "edge_indices": list(edges),
             "parameters": {"radius": radius}, "geometry": geometry},
        ])
        return {"body_id": body_id, "feature_id": feature_id, "source_body_id": source_id,
                "edge_indices": list(edges), "geometry": geometry}
