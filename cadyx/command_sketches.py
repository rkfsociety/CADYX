"""Sketches command handlers and implementation.

Core owns lease/revision checks and atomic persistence; these methods operate on
the staged Document supplied by the shared CommandSystem.
"""
from __future__ import annotations

import math
import sys
from typing import Any, Callable, Iterable
from uuid import uuid4
from .errors import NotFoundError, ValidationError
from .models import Document
from .command_values import _finite_number
from .sketch_workplanes import placement_from_command


class SketchCommands:
    def _add_sketch_polyline(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        operation: str = "add_sketch_polyline",
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch_id = command.get("sketch_id")
        coordinates = command.get("points")
        closed = command.get("closed", False)
        if not isinstance(closed, bool):
            raise ValidationError("closed must be a boolean.")
        minimum = 3 if closed else 2
        if not isinstance(coordinates, list) or len(coordinates) < minimum:
            shape = "closed polygon" if closed else "polyline"
            raise ValidationError(f"A {shape} needs at least {minimum} points.")

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
            document, sketch, points, closed=closed, operation=operation, progress=progress,
        )

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
        if type(count) is not int or count < 2 or count > sys.maxsize:
            raise ValidationError("Pattern count must be an integer of at least 2, including the source.")
        mode = command.get("mode")
        if mode == "linear":
            offset_x = _finite_number(command.get("offset_x"), "Pattern x offset")
            offset_y = _finite_number(command.get("offset_y"), "Pattern y offset")
            if math.hypot(offset_x, offset_y) <= 1e-7:
                raise ValidationError("Linear pattern offset must be non-zero.")
            transforms = (
                (lambda x, y, step=index: (x + offset_x * step, y + offset_y * step))
                for index in range(1, count)
            )
        elif mode == "circular":
            center_x = _finite_number(command.get("center_x"), "Pattern center x")
            center_y = _finite_number(command.get("center_y"), "Pattern center y")
            step_degrees = _finite_number(command.get("angle_step_degrees"), "Pattern angle step")
            if math.isclose(step_degrees % 360, 0, rel_tol=0, abs_tol=1e-10):
                raise ValidationError("Circular pattern angle step must not repeat the source position.")
            normalized = {0.0}
            for index in range(1, count):
                degrees = step_degrees * index
                if not math.isfinite(degrees):
                    raise ValidationError("Circular pattern angles must remain finite.")
                normalized_angle = round(math.radians(degrees) % math.tau, 12)
                if normalized_angle in normalized:
                    raise ValidationError("Circular pattern would create overlapping repeated instances.")
                normalized.add(normalized_angle)
            transforms = (
                (lambda x, y, angle=angle: (
                    center_x + (x - center_x) * math.cos(angle) - (y - center_y) * math.sin(angle),
                    center_y + (x - center_x) * math.sin(angle) + (y - center_y) * math.cos(angle),
                ))
                for angle in (math.radians(step_degrees * index) for index in range(1, count))
            )
        else:
            raise ValidationError("Pattern mode must be linear or circular.")

        source = self._sketch_geometry_closure(document, sketch_id, command.get("object_ids"))
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
        if not isinstance(object_ids, list) or not object_ids:
            raise ValidationError("object_ids must contain at least one sketch geometry id.")
        if any(not isinstance(object_id, str) for object_id in object_ids) or len(set(object_ids)) != len(object_ids):
            raise ValidationError("object_ids must contain unique text ids.")
        by_id = {item["id"]: item for item in document.objects}
        included: set[str] = set()
        pending = list(object_ids)
        while pending:
            object_id = pending.pop()
            item = by_id.get(object_id)
            if item is None or item.get("sketch_id") != sketch_id or item.get("type") not in {"point", "line", "circle", "ellipse", "spline"}:
                raise ValidationError("Selected objects must be point, line, circle, ellipse, or spline geometry from the same sketch.")
            if object_id in included:
                continue
            included.add(object_id)
            if item["type"] == "line":
                pending.extend((item["start_id"], item["end_id"]))
            elif item["type"] == "circle":
                pending.append(item["center_id"])
                if "sweep_radians" in item:
                    pending.extend((item["start_id"], item["end_id"]))
            elif item["type"] == "ellipse" and "sweep_radians" in item:
                pending.extend((item["start_id"], item["end_id"]))
            elif item["type"] == "spline":
                pending.extend(item["point_ids"])
        return [item for item in document.objects if item.get("id") in included]

    def _copy_sketch_geometry(
        self,
        document: Document,
        sketch: dict[str, Any],
        source: list[dict[str, Any]],
        transforms: Iterable[Callable[[float, float], tuple[float, float]]],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch_id = sketch["id"]
        copies: list[dict[str, Any]] = []
        instance_count = 1
        for instance_count, transform in enumerate(transforms, start=2):
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
                elif item["type"] == "spline":
                    clone["point_ids"] = [id_map[point_id] for point_id in item["point_ids"]]
                elif item["type"] == "circle":
                    clone["center_id"] = id_map[item["center_id"]]
                    if "sweep_radians" in item:
                        clone["start_id"], clone["end_id"] = id_map[item["start_id"]], id_map[item["end_id"]]
                        origin, x_axis, y_axis = transform(0.0, 0.0), transform(1.0, 0.0), transform(0.0, 1.0)
                        determinant = ((x_axis[0] - origin[0]) * (y_axis[1] - origin[1])
                                       - (x_axis[1] - origin[1]) * (y_axis[0] - origin[0]))
                        if determinant < 0:
                            clone["sweep_radians"] = -clone["sweep_radians"]
                else:
                    origin = transform(0.0, 0.0)
                    center = transform(item["center_x"], item["center_y"])
                    major_direction = (math.cos(item["rotation_radians"]), math.sin(item["rotation_radians"]))
                    major_endpoint = transform(item["center_x"] + major_direction[0],
                                               item["center_y"] + major_direction[1])
                    clone["center_x"], clone["center_y"] = center
                    clone["rotation_radians"] = math.atan2(major_endpoint[1] - center[1],
                                                            major_endpoint[0] - center[0])
                    if not all(math.isfinite(value) for value in (*origin, *center, *major_endpoint)):
                        raise ValidationError("Transformed ellipse coordinates must remain finite.")
                    if "sweep_radians" in item:
                        clone["start_id"], clone["end_id"] = id_map[item["start_id"]], id_map[item["end_id"]]
                        x_axis, y_axis = transform(1.0, 0.0), transform(0.0, 1.0)
                        determinant = ((x_axis[0] - origin[0]) * (y_axis[1] - origin[1])
                                       - (x_axis[1] - origin[1]) * (y_axis[0] - origin[0]))
                        if determinant < 0:
                            clone["start_angle_radians"] = -clone["start_angle_radians"]
                            clone["sweep_radians"] = -clone["sweep_radians"]
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
            "ellipse_ids": [item["id"] for item in copies if item["type"] == "ellipse"],
            "spline_ids": [item["id"] for item in copies if item["type"] == "spline"],
            "instance_count": instance_count,
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
        construction: bool = False,
        line_extent: str = "segment",
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
            {"id": point_id, "type": "point", "sketch_id": sketch_id, "x": x, "y": y,
             **({"construction": True} if construction else {})}
            for point_id, (x, y) in zip(point_ids, points, strict=True)
        ]
        lines = [
            {"id": line_id, "type": "line", "sketch_id": sketch_id,
             "start_id": point_ids[index], "end_id": point_ids[(index + 1) % len(points)],
             **({"construction": True} if construction else {}),
             **({"extent": line_extent} if line_extent != "segment" else {})}
            for index, line_id in enumerate(line_ids)
        ]
        elements.extend(lines)
        sketch_index = document.objects.index(sketch)
        document.objects[sketch_index + 1:sketch_index + 1] = elements
        self._rebuild_changed_features(document, {sketch_id}, progress=progress)
        return {"sketch_id": sketch_id, "point_ids": point_ids, "line_ids": line_ids, "closed": closed}

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
        if not isinstance(coordinates, list) or len(coordinates) < 3:
            raise ValidationError("A replacement profile needs at least 3 points.")
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
        if not isinstance(coordinates, list) or len(coordinates) < 3:
            raise ValidationError("A replacement sweep path needs at least 3 points.")
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


def _handle_create_sketch(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    sketch_id = f"sketch:{uuid4()}"
    sketch = {
        "id": sketch_id, "type": "sketch", "name": command.get("name", "Sketch"),
        **placement_from_command(document, command, self.kernel),
    }
    document.objects.append(sketch)
    return {"sketch_id": sketch_id}


def _handle_sketch_point(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    kind = command["type"]
    sketch = self._find_sketch(document, command.get("sketch_id"))
    point_id = f"point:{uuid4()}"
    point = {
        "id": point_id, "type": "point", "sketch_id": sketch["id"],
        "x": _finite_number(command.get("x"), "Sketch point x"),
        "y": _finite_number(command.get("y"), "Sketch point y"),
    }
    if kind == "add_sketch_construction_point":
        point["construction"] = True
    document.objects.append(point)
    return {"point_id": point_id}


def _handle_sketch_line(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    kind = command["type"]
    sketch = self._find_sketch(document, command.get("sketch_id"))
    start = self._coordinate_pair(command.get("start"), "Line start")
    end = self._coordinate_pair(command.get("end"), "Line end")
    extent = "infinite" if kind == "add_sketch_infinite_line" else (
        "centerline" if kind == "add_sketch_centerline" else "segment"
    )
    construction = kind == "add_sketch_construction_line" or extent != "segment"
    result = self._append_sketch_polyline(
        document, sketch, [start, end], closed=False, operation=kind,
        construction=construction, line_extent=extent,
        progress=progress,
    )
    return {"sketch_id": sketch["id"], "point_ids": result["point_ids"],
            "line_ids": result["line_ids"], "line_id": result["line_ids"][0],
            "closed": False}


def _handle_add_sketch_element(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    element_kind = command.get("element_type")
    fields = {"point": ("x", "y"), "line": ("start_id", "end_id"),
              "circle": ("center_id", "radius"),
              "ellipse": ("center_x", "center_y", "major_radius", "minor_radius", "rotation_radians")}
    if not isinstance(element_kind, str) or element_kind not in fields:
        raise ValidationError("Unsupported sketch element type.")
    object_id = f"{element_kind}:{uuid4()}"
    document.objects.append({
        "id": object_id, "type": element_kind, "sketch_id": command.get("sketch_id"),
        **{key: command.get(key) for key in fields[element_kind]},
    })
    return {"object_id": object_id}


def _handle_add_sketch_polyline(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._add_sketch_polyline(document, command, progress=progress)


def _handle_add_sketch_line_chain(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    kind = command["type"]
    if command.get("closed", False) is not False:
        raise ValidationError("A line chain is always open; use add_sketch_polyline for a closed path.")
    return self._add_sketch_polyline(document, command, operation=kind, progress=progress)


def _handle_mirror_sketch_elements(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._mirror_sketch_elements(document, command, progress=progress)


def _handle_pattern_sketch_elements(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._pattern_sketch_elements(document, command, progress=progress)


def _handle_add_sketch_constraint(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    sketch_id = command.get("sketch_id")
    self._find_sketch(document, sketch_id)
    object_id = f"constraint:{uuid4()}"
    constraint = {
        "id": object_id, "type": "constraint", "sketch_id": sketch_id,
        "kind": command.get("kind"), "references": command.get("references"),
        "value": command.get("value"),
    }
    if constraint["kind"] == "fixed" and isinstance(constraint["references"], list):
        point = next((item for item in document.objects
                      if item.get("id") == constraint["references"][0]), None)
        if point is not None and point.get("type") == "point":
            constraint["fixed_position"] = [point["x"], point["y"]]
    if (constraint["kind"] == "tangent" and isinstance(constraint["references"], list)
            and len(constraint["references"]) == 2
            and all(isinstance(ref, str) for ref in constraint["references"])):
        targets = {item.get("id"): item for item in document.objects}
        first, second = (targets.get(ref) for ref in constraint["references"])
        line, circle = (first, second) if first and first.get("type") == "line" else (second, first)
        if line and circle and line.get("type") == "line" and circle.get("type") == "circle":
            by_id = {item["id"]: item for item in document.objects}
            start, end = by_id[line["start_id"]], by_id[line["end_id"]]
            center = by_id[circle["center_id"]]
            signed_distance = ((center["x"] - start["x"]) * (end["y"] - start["y"])
                               - (center["y"] - start["y"]) * (end["x"] - start["x"]))
            constraint["tangent_side"] = 1 if signed_distance >= 0 else -1
    document.objects.append(constraint)
    from .sketch_solver import solve_sketch
    diagnostics = solve_sketch(document.objects, sketch_id, document.parameter_bindings)
    self._rebuild_changed_features(document, {sketch_id}, progress=progress)
    return {"constraint_id": object_id, "solver": diagnostics}


def _handle_update_sketch_elements(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
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
        item.update({key: _finite_number(value, f"Sketch {key}") for key, value in changes.items()})
    changed_sketches = {
        item["sketch_id"] for item in document.objects
        if item.get("id") in seen and item.get("type") in {"point", "circle"}
    }
    from .sketch_solver import solve_sketch
    diagnostics = {sketch_id: solve_sketch(document.objects, sketch_id, document.parameter_bindings)
                   for sketch_id in sorted(changed_sketches)}
    self._rebuild_changed_features(document, changed_sketches, progress=progress)
    return {"object_ids": sorted(seen), "solver": diagnostics}


def _handle_replace_sketch_profile(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._replace_sketch_profile(document, command, progress=progress)


def _handle_replace_sketch_path(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._replace_sketch_path(document, command, progress=progress)


def _handle_remove_sketch_object(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    object_id = command.get("object_id")
    item = next((item for item in document.objects if item["id"] == object_id), None)
    if item is None or item["type"] not in {"sketch", "point", "line", "circle", "spline", "constraint"}:
        raise NotFoundError("Sketch object was not found.")
    if item["type"] == "sketch" and any(
        feature.get("type") == "feature"
        and (feature.get("source_sketch_id") == object_id
             or object_id in feature.get("source_sketch_ids", []))
        for feature in document.objects
    ):
        raise ValidationError("A sketch used by a solid feature cannot be removed.")
    document.objects.remove(item)
    return {"object_id": object_id}


def _handle_set_sketch_workplane(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    sketch = self._find_sketch(document, command.get("sketch_id"))
    fields = placement_from_command(document, command, self.kernel)
    for field in ("plane", "frame", "attachment", "section_normal_axis", "section_offset"):
        sketch.pop(field, None)
    sketch.update(fields)
    self._rebuild_changed_features(document, {sketch["id"]}, progress=progress)
    return {"sketch_id": sketch["id"], "plane": sketch["plane"], "frame": sketch.get("frame")}


COMMAND_HANDLERS = {
    "set_sketch_workplane": _handle_set_sketch_workplane,
    "create_sketch": _handle_create_sketch,
    "add_sketch_construction_point": _handle_sketch_point,
    "add_sketch_point": _handle_sketch_point,
    "add_sketch_centerline": _handle_sketch_line,
    "add_sketch_construction_line": _handle_sketch_line,
    "add_sketch_infinite_line": _handle_sketch_line,
    "add_sketch_line": _handle_sketch_line,
    "add_sketch_segment": _handle_sketch_line,
    "add_sketch_element": _handle_add_sketch_element,
    "add_sketch_polyline": _handle_add_sketch_polyline,
    "add_sketch_line_chain": _handle_add_sketch_line_chain,
    "mirror_sketch_elements": _handle_mirror_sketch_elements,
    "pattern_sketch_elements": _handle_pattern_sketch_elements,
    "add_sketch_constraint": _handle_add_sketch_constraint,
    "update_sketch_elements": _handle_update_sketch_elements,
    "replace_sketch_profile": _handle_replace_sketch_profile,
    "replace_sketch_path": _handle_replace_sketch_path,
    "remove_sketch_object": _handle_remove_sketch_object,
}
