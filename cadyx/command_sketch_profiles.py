"""Sketch profiles command handlers and implementation.

Core owns lease/revision checks and atomic persistence; these methods operate on
the staged Document supplied by the shared CommandSystem.
"""
from __future__ import annotations

import math
import sys
from typing import Any, Callable
from uuid import uuid4
from .errors import ValidationError
from .engineering import involute_spur_gear_profile
from .models import Document
from .command_values import _finite_number, _number


class SketchProfileCommands:
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

    def _add_sketch_rounded_rectangle(
        self, document: Document, command: dict[str, Any], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch = self._find_sketch(document, command.get("sketch_id"))
        x, y = _finite_number(command.get("x"), "Rectangle x"), _finite_number(command.get("y"), "Rectangle y")
        width, height = _number(command.get("width"), "width"), _number(command.get("height"), "height")
        radius = _number(command.get("corner_radius"), "corner_radius")
        if radius <= 1e-7 or width <= 2 * radius or height <= 2 * radius:
            raise ValidationError("Rounded rectangle needs positive dimensions and a radius below half its smaller side.")
        right, top = x + width, y + height
        if not math.isfinite(right) or not math.isfinite(top):
            raise ValidationError("Rounded rectangle coordinates must remain finite.")
        centers = [(x + radius, y + radius), (right - radius, y + radius),
                   (right - radius, top - radius), (x + radius, top - radius)]
        starts = [math.pi, -math.pi / 2, 0.0, math.pi / 2]
        arc_points = []
        for (cx, cy), angle in zip(centers, starts, strict=True):
            arc_points.append(((cx + radius * math.cos(angle), cy + radius * math.sin(angle)),
                               (cx + radius * math.cos(angle + math.pi / 2),
                                cy + radius * math.sin(angle + math.pi / 2))))
        if progress is not None:
            progress({"phase": "add_sketch_rounded_rectangle"})
        sketch_id = sketch["id"]
        center_ids = [f"point:{uuid4()}" for _ in centers]
        endpoint_ids = [f"point:{uuid4()}" for _ in range(8)]
        circle_ids = [f"circle:{uuid4()}" for _ in centers]
        line_ids = [f"line:{uuid4()}" for _ in centers]
        points = [
            {"id": point_id, "type": "point", "sketch_id": sketch_id, "x": cx, "y": cy,
             "construction": True}
            for point_id, (cx, cy) in zip(center_ids, centers, strict=True)
        ]
        points.extend(
            {"id": endpoint_ids[arc_index * 2 + endpoint_index], "type": "point", "sketch_id": sketch_id,
             "x": xy[0], "y": xy[1]}
            for arc_index, pair in enumerate(arc_points)
            for endpoint_index, xy in enumerate(pair)
        )
        arcs = [
            {"id": circle_ids[index], "type": "circle", "sketch_id": sketch_id,
             "center_id": center_ids[index], "radius": radius,
             "start_id": endpoint_ids[index * 2], "end_id": endpoint_ids[index * 2 + 1],
             "sweep_radians": math.pi / 2}
            for index in range(4)
        ]
        lines = [
            {"id": line_ids[index], "type": "line", "sketch_id": sketch_id,
             "start_id": endpoint_ids[index * 2 + 1], "end_id": endpoint_ids[((index + 1) % 4) * 2]}
            for index in range(4)
        ]
        insert_at = document.objects.index(sketch) + 1
        document.objects[insert_at:insert_at] = points + arcs + lines
        self._rebuild_changed_features(document, {sketch_id}, progress=progress)
        return {"sketch_id": sketch_id, "point_ids": center_ids + endpoint_ids,
                "line_ids": line_ids, "circle_ids": circle_ids, "closed": True}

    def _add_sketch_polygon_tool(
        self,
        document: Document,
        command: dict[str, Any],
        kind: str,
        *,
        operation_name: str | None = None,
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
            if type(sides) is not int or not 3 <= sides <= sys.maxsize:
                raise ValidationError("A regular polygon needs at least 3 sides within the supported integer range.")
            radius = _number(command.get("radius"), "radius")
            radius_mode = command.get("radius_mode", "inscribed")
            if not isinstance(radius_mode, str) or radius_mode not in {"inscribed", "circumscribed"}:
                raise ValidationError("radius_mode must be inscribed or circumscribed.")
            if radius_mode == "circumscribed":
                radius /= math.cos(math.pi / sides)
            rotation = math.radians(_finite_number(command.get("rotation_degrees", 0), "Polygon rotation"))
            points = [(cx + radius*math.cos(rotation + math.tau*index/sides),
                       cy + radius*math.sin(rotation + math.tau*index/sides)) for index in range(sides)]
        if any(not math.isfinite(value) for point in points for value in point):
            raise ValidationError("Sketch coordinates must remain finite.")
        return self._append_sketch_polyline(
            document, sketch, points, closed=True, operation=operation_name or kind, progress=progress,
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


def _handle_add_sketch_involute_gear(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._add_sketch_involute_gear(document, command, progress=progress)


def _handle_add_sketch_rectangle(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._add_sketch_rectangle(document, command, progress=progress)


def _handle_add_sketch_rounded_rectangle(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._add_sketch_rounded_rectangle(document, command, progress=progress)


def _handle_polygon_tool(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    kind = command["type"]
    operation_name = kind
    if kind == "add_sketch_inscribed_polygon":
        command = {**command, "radius_mode": "inscribed"}
        kind = "add_sketch_regular_polygon"
    elif kind == "add_sketch_circumscribed_polygon":
        command = {**command, "radius_mode": "circumscribed"}
        kind = "add_sketch_regular_polygon"
    elif kind == "add_sketch_polygon_from_center":
        center_x = _finite_number(command.get("center_x"), "Polygon center x")
        center_y = _finite_number(command.get("center_y"), "Polygon center y")
        vertex = self._coordinate_pair(command.get("first_vertex"), "First polygon vertex")
        radius = math.hypot(vertex[0] - center_x, vertex[1] - center_y)
        if not math.isfinite(radius) or radius <= 1e-7:
            raise ValidationError("The first polygon vertex must differ from its center.")
        command = {**command, "radius": radius,
                   "rotation_degrees": math.degrees(math.atan2(vertex[1] - center_y,
                                                                vertex[0] - center_x))}
        kind = "add_sketch_regular_polygon"
    return self._add_sketch_polygon_tool(
        document, command, kind, operation_name=operation_name, progress=progress,
    )


def _handle_add_sketch_circle(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._add_sketch_circle(document, command, progress=progress)


def _handle_circle_tool(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    kind = command["type"]
    return self._add_sketch_circle_tool(document, command, kind, progress=progress)


COMMAND_HANDLERS = {
    "add_sketch_involute_gear": _handle_add_sketch_involute_gear,
    "add_sketch_rectangle": _handle_add_sketch_rectangle,
    "add_sketch_rounded_rectangle": _handle_add_sketch_rounded_rectangle,
    "add_sketch_circumscribed_polygon": _handle_polygon_tool,
    "add_sketch_inscribed_polygon": _handle_polygon_tool,
    "add_sketch_parallelogram": _handle_polygon_tool,
    "add_sketch_polygon_from_center": _handle_polygon_tool,
    "add_sketch_rectangle_center": _handle_polygon_tool,
    "add_sketch_rectangle_three_points": _handle_polygon_tool,
    "add_sketch_rectangle_two_corners": _handle_polygon_tool,
    "add_sketch_regular_polygon": _handle_polygon_tool,
    "add_sketch_rotated_rectangle": _handle_polygon_tool,
    "add_sketch_circle": _handle_add_sketch_circle,
    "add_sketch_circle_diameter": _handle_circle_tool,
    "add_sketch_circle_three_points": _handle_circle_tool,
    "add_sketch_circle_two_points": _handle_circle_tool,
}
