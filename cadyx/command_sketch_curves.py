"""Sketch curves command handlers and implementation.

Core owns lease/revision checks and atomic persistence; these methods operate on
the staged Document supplied by the shared CommandSystem.
"""
from __future__ import annotations

import math
from typing import Any, Callable
from uuid import uuid4
from .errors import ValidationError
from .models import Document
from .sketch_slot_tools import build_arc_slot_from_center, build_arc_slot_three_points, build_slot_by_centers, build_slot_by_total_length, build_slot_from_center, build_straight_slot, build_tangent_arc_slot, contour_to_sketch_objects
from .sketch_curve_toolkit import evaluate_archimedean_spiral, sample_parametric_expressions
from .command_values import _finite_number, _number


class SketchCurveCommands:
    def _add_sketch_ellipse(
        self, document: Document, command: dict[str, Any], kind: str, *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch = self._find_sketch(document, command.get("sketch_id"))
        if kind == "add_sketch_ellipse":
            first = self._coordinate_pair(command.get("major_axis_start"), "Major axis start")
            second = self._coordinate_pair(command.get("major_axis_end"), "Major axis end")
            center_x, center_y = first[0] / 2 + second[0] / 2, first[1] / 2 + second[1] / 2
            major_radius = math.hypot(second[0] - first[0], second[1] - first[1]) / 2
            minor_radius = _number(command.get("minor_radius"), "minor_radius")
            rotation = math.atan2(second[1] - first[1], second[0] - first[0])
        elif kind == "add_sketch_ellipse_center":
            center_x, center_y = self._coordinate_pair(command.get("center"), "Ellipse center")
            major_radius = _number(command.get("major_radius"), "major_radius")
            minor_radius = _number(command.get("minor_radius"), "minor_radius")
            rotation = math.radians(_finite_number(command.get("rotation_degrees", 0), "Ellipse rotation"))
        else:
            center_x, center_y = self._coordinate_pair(command.get("center"), "Ellipse center")
            major_x, major_y = self._coordinate_pair(command.get("major_axis_endpoint"), "Major axis endpoint")
            minor_x, minor_y = self._coordinate_pair(command.get("minor_axis_endpoint"), "Minor axis endpoint")
            major_dx, major_dy = major_x - center_x, major_y - center_y
            minor_dx, minor_dy = minor_x - center_x, minor_y - center_y
            major_radius = math.hypot(major_dx, major_dy)
            minor_radius = math.hypot(minor_dx, minor_dy)
            dot = major_dx * minor_dx + major_dy * minor_dy
            scale = major_radius * minor_radius
            if (not math.isfinite(dot) or not math.isfinite(scale)
                    or abs(dot) > 1e-7 * max(scale, 1e-14)):
                raise ValidationError("Ellipse axis endpoints must be perpendicular from the center.")
            rotation = math.atan2(major_dy, major_dx)
        if major_radius < minor_radius or min(major_radius, minor_radius) <= 1e-7:
            raise ValidationError("Ellipse radii must satisfy major_radius >= minor_radius > 1e-7.")
        cosine, sine = math.cos(rotation), math.sin(rotation)
        extent_x = math.hypot(major_radius * cosine, minor_radius * sine)
        extent_y = math.hypot(major_radius * sine, minor_radius * cosine)
        if not all(math.isfinite(value) for value in (center_x - extent_x, center_x + extent_x,
                                                        center_y - extent_y, center_y + extent_y)):
            raise ValidationError("Ellipse coordinates must remain finite.")
        if progress is not None:
            progress({"phase": kind})
        ellipse_id = f"ellipse:{uuid4()}"
        document.objects.insert(document.objects.index(sketch) + 1, {
            "id": ellipse_id, "type": "ellipse", "sketch_id": sketch["id"],
            "center_x": center_x, "center_y": center_y,
            "major_radius": major_radius, "minor_radius": minor_radius,
            "rotation_radians": rotation,
        })
        self._rebuild_changed_features(document, {sketch["id"]}, progress=progress)
        return {"sketch_id": sketch["id"], "ellipse_id": ellipse_id,
                "center": {"x": center_x, "y": center_y},
                "major_radius": major_radius, "minor_radius": minor_radius,
                "rotation_radians": rotation}

    def _add_sketch_elliptical_arc(
        self, document: Document, command: dict[str, Any], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch = self._find_sketch(document, command.get("sketch_id"))
        center_x, center_y = self._coordinate_pair(command.get("center"), "Ellipse arc center")
        major_radius = _number(command.get("major_radius"), "major_radius")
        minor_radius = _number(command.get("minor_radius"), "minor_radius")
        if major_radius < minor_radius or minor_radius <= 1e-7:
            raise ValidationError("Ellipse radii must satisfy major_radius >= minor_radius > 1e-7.")
        rotation = math.radians(_finite_number(command.get("rotation_degrees", 0), "Ellipse rotation"))
        start_angle = math.radians(_finite_number(command.get("start_angle_degrees"), "Ellipse arc start angle"))
        sweep = math.radians(_finite_number(command.get("sweep_degrees"), "Ellipse arc sweep"))
        if abs(sweep) <= 1e-7 or abs(sweep) >= math.tau - 1e-7:
            raise ValidationError("Ellipse arc sweep must be non-zero and less than one revolution.")
        cosine, sine = math.cos(rotation), math.sin(rotation)

        def ellipse_point(angle: float) -> tuple[float, float]:
            local_x, local_y = major_radius * math.cos(angle), minor_radius * math.sin(angle)
            return (center_x + local_x * cosine - local_y * sine,
                    center_y + local_x * sine + local_y * cosine)

        start, end = ellipse_point(start_angle), ellipse_point(start_angle + sweep)
        if not all(math.isfinite(value) for value in (*start, *end)):
            raise ValidationError("Ellipse arc coordinates must remain finite.")
        if math.hypot(start[0] - end[0], start[1] - end[1]) <= 1e-7:
            raise ValidationError("Ellipse arc endpoints must be distinct.")
        if progress is not None:
            progress({"phase": "add_sketch_elliptical_arc"})
        ellipse_id = f"ellipse:{uuid4()}"
        start_id, end_id = f"point:{uuid4()}", f"point:{uuid4()}"
        insert_at = document.objects.index(sketch) + 1
        document.objects[insert_at:insert_at] = [
            {"id": start_id, "type": "point", "sketch_id": sketch["id"], "x": start[0], "y": start[1]},
            {"id": end_id, "type": "point", "sketch_id": sketch["id"], "x": end[0], "y": end[1]},
            {"id": ellipse_id, "type": "ellipse", "sketch_id": sketch["id"],
             "center_x": center_x, "center_y": center_y,
             "major_radius": major_radius, "minor_radius": minor_radius,
             "rotation_radians": rotation, "start_id": start_id, "end_id": end_id,
             "start_angle_radians": start_angle, "sweep_radians": sweep},
        ]
        self._rebuild_changed_features(document, {sketch["id"]}, progress=progress)
        return {"sketch_id": sketch["id"], "ellipse_id": ellipse_id, "arc_id": ellipse_id,
                "ellipse_ids": [ellipse_id], "point_ids": [start_id, end_id],
                "start": {"x": start[0], "y": start[1]},
                "end": {"x": end[0], "y": end[1]}, "sweep_radians": sweep}

    def _add_sketch_spline(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        mode: str,
        curve_data: dict[str, Any] | None = None,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch = self._find_sketch(document, command.get("sketch_id"))
        coordinates = command.get("points")
        if not isinstance(coordinates, list) or len(coordinates) < 3:
            raise ValidationError("An interpolating spline needs at least 3 points.")
        construction = command.get("construction", False)
        if type(construction) is not bool:
            raise ValidationError("Spline construction flag must be a boolean.")
        points = [self._coordinate_pair(point, "Spline point") for point in coordinates]
        if any(math.hypot(right[0] - left[0], right[1] - left[1]) <= 1e-7
               for left, right in zip(points, points[1:])):
            raise ValidationError("Adjacent spline points must be distinct.")

        point_ids = [f"point:{uuid4()}" for _ in points]
        spline_id = f"spline:{uuid4()}"
        additions = [
            {"id": point_id, "type": "point", "sketch_id": sketch["id"],
             "x": point[0], "y": point[1], "construction": construction}
            for point_id, point in zip(point_ids, points)
        ]
        spline = {"id": spline_id, "type": "spline", "sketch_id": sketch["id"],
                  "point_ids": point_ids, "construction": construction}
        if mode != "interpolating":
            spline["mode"] = mode
        if curve_data:
            spline.update(curve_data)
        additions.append(spline)
        insert_at = document.objects.index(sketch) + 1
        document.objects[insert_at:insert_at] = additions
        self._rebuild_changed_features(document, {sketch["id"]}, progress=progress)
        if progress is not None:
            progress({"phase": "add_sketch_spline"})
        return {"sketch_id": sketch["id"], "spline_id": spline_id,
                "point_ids": point_ids, "point_count": len(point_ids), "closed": mode == "closed",
                "mode": mode}

    def _add_sketch_nurbs(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        degree = command.get("degree")
        if isinstance(degree, bool) or not isinstance(degree, int):
            raise ValidationError("NURBS degree must be an integer.")
        knots = command.get("knots")
        weights = command.get("weights")
        if not isinstance(knots, list) or not isinstance(weights, list):
            raise ValidationError("NURBS knots and weights must be lists.")
        return self._add_sketch_spline(
            document, command, mode="nurbs",
            curve_data={"degree": degree, "knots": knots, "weights": weights},
            progress=progress,
        )

    def _add_sketch_sampled_curve(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        kind: str,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch = self._find_sketch(document, command.get("sketch_id"))
        samples = command.get("samples", 48)
        if isinstance(samples, bool) or not isinstance(samples, int) or samples < 3:
            raise ValidationError("Curve samples must be an integer of at least 3.")
        if kind == "add_sketch_spiral":
            center = self._coordinate_pair(command.get("center"), "Spiral center")
            initial_radius = _finite_number(command.get("initial_radius"), "Initial radius")
            pitch = _finite_number(command.get("pitch_per_turn"), "Spiral pitch")
            start = math.radians(_finite_number(command.get("start_angle_degrees", 0), "Start angle"))
            end = math.radians(_finite_number(command.get("end_angle_degrees"), "End angle"))
            if initial_radius < 0 or pitch < 0:
                raise ValidationError("Spiral radius and pitch must be nonnegative.")
            points = [evaluate_archimedean_spiral(center, initial_radius, pitch,
                                                   start + (end - start) * i / (samples - 1))
                      for i in range(samples)]
        elif kind == "add_sketch_parametric_curve":
            try:
                points = sample_parametric_expressions(
                    command.get("x_expression"), command.get("y_expression"),
                    _finite_number(command.get("parameter_start"), "Parameter start"),
                    _finite_number(command.get("parameter_end"), "Parameter end"), samples,
                )
            except (ValueError, TypeError) as exc:
                raise ValidationError(str(exc)) from exc
        else:
            coordinates = command.get("points")
            if not isinstance(coordinates, list):
                raise ValidationError("Coordinate curve points must be a list.")
            points = [self._coordinate_pair(point, "Curve coordinate") for point in coordinates]

        result = self._add_sketch_spline(
            document, {**command, "points": [{"x": x, "y": y} for x, y in points]},
            mode="interpolating", progress=progress,
        )
        result["curve_tool"] = kind
        return result

    def _add_sketch_circle_tangent_to_line(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch = self._find_sketch(document, command.get("sketch_id"))
        line_id = command.get("line_id")
        line = next((item for item in document.objects if item.get("id") == line_id), None)
        if line is None or line.get("type") != "line" or line.get("sketch_id") != sketch["id"]:
            raise ValidationError("A tangent circle needs a line from the same sketch.")
        tangent = self._coordinate_pair(command.get("tangent_point"), "Tangent point")
        radius = _number(command.get("radius"), "radius")
        if radius <= 1e-7:
            raise ValidationError("A tangent circle radius must be greater than 1e-7.")
        side = command.get("side", 1)
        if type(side) is not int or side not in {-1, 1}:
            raise ValidationError("side must be 1 or -1 relative to the line direction.")
        by_id = {item["id"]: item for item in document.objects}
        start, end = by_id[line["start_id"]], by_id[line["end_id"]]
        dx, dy = end["x"] - start["x"], end["y"] - start["y"]
        length = math.hypot(dx, dy)
        offset_x, offset_y = tangent[0] - start["x"], tangent[1] - start["y"]
        cross = dx * offset_y - dy * offset_x
        dot = dx * offset_x + dy * offset_y
        tolerance = 1e-7 * max(1.0, length, math.hypot(offset_x, offset_y))
        if abs(cross) > tolerance * length:
            raise ValidationError("The tangent point must lie on the selected line.")
        if line.get("extent", "segment") != "infinite" and not -tolerance <= dot / length <= length + tolerance:
            raise ValidationError("The tangent point must lie within the selected line segment.")
        center_x = tangent[0] - side * dy / length * radius
        center_y = tangent[1] + side * dx / length * radius
        if not all(math.isfinite(value) for value in (center_x, center_y)):
            raise ValidationError("Tangent circle coordinates must remain finite.")
        result = self._add_sketch_circle(
            document,
            {"sketch_id": sketch["id"], "x": center_x, "y": center_y, "radius": radius},
            progress=progress,
        )
        result["tangent_point"] = {"x": tangent[0], "y": tangent[1]}
        result["line_id"] = line_id
        result["circle_ids"] = [result["circle_id"]]
        result["point_ids"] = [result["point_id"]]
        return result

    def _add_sketch_circle_tangent_to_two_lines(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch = self._find_sketch(document, command.get("sketch_id"))
        line_ids = [command.get("first_line_id"), command.get("second_line_id")]
        if line_ids[0] == line_ids[1]:
            raise ValidationError("A two-tangent circle needs two different lines.")
        by_id = {item["id"]: item for item in document.objects}
        lines = [by_id.get(line_id) for line_id in line_ids]
        if any(line is None or line.get("type") != "line" or line.get("sketch_id") != sketch["id"]
               for line in lines):
            raise ValidationError("Both tangent lines must belong to the same sketch.")
        radius = _number(command.get("radius"), "radius")
        if radius <= 1e-7:
            raise ValidationError("A tangent circle radius must be greater than 1e-7.")
        sides = [command.get("first_side", 1), command.get("second_side", 1)]
        if any(type(side) is not int or side not in {-1, 1} for side in sides):
            raise ValidationError("Tangent sides must be 1 or -1 relative to each line direction.")

        directions: list[tuple[float, float]] = []
        normals: list[tuple[float, float]] = []
        starts: list[tuple[float, float]] = []
        lengths: list[float] = []
        for line in lines:
            start, end = by_id[line["start_id"]], by_id[line["end_id"]]
            ax, ay = start["x"], start["y"]
            dx, dy = end["x"] - ax, end["y"] - ay
            length = math.hypot(dx, dy)
            if not math.isfinite(length) or length <= 1e-7:
                raise ValidationError("Tangent lines must have finite positive lengths.")
            directions.append((dx / length, dy / length))
            normals.append((-dy / length, dx / length))
            starts.append((ax, ay))
            lengths.append(length)

        first_offset = (starts[0][0] + sides[0] * radius * normals[0][0],
                        starts[0][1] + sides[0] * radius * normals[0][1])
        second_offset = (starts[1][0] + sides[1] * radius * normals[1][0],
                         starts[1][1] + sides[1] * radius * normals[1][1])
        first_direction, second_direction = directions
        cross = first_direction[0] * second_direction[1] - first_direction[1] * second_direction[0]
        if abs(cross) <= 1e-12:
            raise ValidationError("Parallel lines do not define one circle center for the selected radius and sides.")
        delta = (second_offset[0] - first_offset[0], second_offset[1] - first_offset[1])
        along_first = (delta[0] * second_direction[1] - delta[1] * second_direction[0]) / cross
        center = (first_offset[0] + along_first * first_direction[0],
                  first_offset[1] + along_first * first_direction[1])
        if not all(math.isfinite(value) for value in center):
            raise ValidationError("Tangent circle coordinates must remain finite.")

        tangent_points = [
            (center[0] - sides[index] * radius * normals[index][0],
             center[1] - sides[index] * radius * normals[index][1])
            for index in range(2)
        ]
        for index, (line, tangent_point) in enumerate(zip(lines, tangent_points, strict=True)):
            if line.get("extent", "segment") == "infinite":
                continue
            projection = ((tangent_point[0] - starts[index][0]) * directions[index][0]
                          + (tangent_point[1] - starts[index][1]) * directions[index][1])
            tolerance = 1e-7 * max(1.0, lengths[index], radius)
            if not -tolerance <= projection <= lengths[index] + tolerance:
                raise ValidationError("Each tangent point must lie within its finite line segment.")

        if progress is not None:
            progress({"phase": "add_sketch_circle_tangent_to_two_lines"})
        result = self._add_sketch_circle(
            document,
            {"sketch_id": sketch["id"], "x": center[0], "y": center[1], "radius": radius},
            progress=progress,
        )
        result["line_ids"] = line_ids
        result["tangent_points"] = [
            {"x": point[0], "y": point[1]} for point in tangent_points
        ]
        result["circle_ids"] = [result["circle_id"]]
        result["point_ids"] = [result["point_id"]]
        return result

    def _add_sketch_circle_tangent_to_three_lines(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch = self._find_sketch(document, command.get("sketch_id"))
        line_ids = [command.get("first_line_id"), command.get("second_line_id"), command.get("third_line_id")]
        if len(set(line_ids)) != 3:
            raise ValidationError("A three-tangent circle needs three different lines.")
        by_id = {item["id"]: item for item in document.objects}
        lines = [by_id.get(line_id) for line_id in line_ids]
        if any(line is None or line.get("type") != "line" or line.get("sketch_id") != sketch["id"]
               for line in lines):
            raise ValidationError("All tangent lines must belong to the same sketch.")
        sides = [command.get("first_side", 1), command.get("second_side", 1),
                 command.get("third_side", 1)]
        if any(type(side) is not int or side not in {-1, 1} for side in sides):
            raise ValidationError("Tangent sides must be 1 or -1 relative to each line direction.")

        matrix: list[list[float]] = []
        right_hand_side: list[float] = []
        directions: list[tuple[float, float]] = []
        normals: list[tuple[float, float]] = []
        starts: list[tuple[float, float]] = []
        lengths: list[float] = []
        for index, line in enumerate(lines):
            start, end = by_id[line["start_id"]], by_id[line["end_id"]]
            ax, ay = start["x"], start["y"]
            dx, dy = end["x"] - ax, end["y"] - ay
            length = math.hypot(dx, dy)
            if not math.isfinite(length) or length <= 1e-7:
                raise ValidationError("Tangent lines must have finite positive lengths.")
            direction = (dx / length, dy / length)
            normal = (-direction[1], direction[0])
            directions.append(direction)
            normals.append(normal)
            starts.append((ax, ay))
            lengths.append(length)
            matrix.append([normal[0], normal[1], -sides[index]])
            right_hand_side.append(normal[0] * ax + normal[1] * ay)

        def determinant(rows: list[list[float]]) -> float:
            return (rows[0][0] * (rows[1][1] * rows[2][2] - rows[1][2] * rows[2][1])
                    - rows[0][1] * (rows[1][0] * rows[2][2] - rows[1][2] * rows[2][0])
                    + rows[0][2] * (rows[1][0] * rows[2][1] - rows[1][1] * rows[2][0]))

        denominator = determinant(matrix)
        if not math.isfinite(denominator) or abs(denominator) <= 1e-12:
            raise ValidationError("The selected tangent sides do not define one finite circle.")
        solution = []
        for column in range(3):
            replaced = [row[:] for row in matrix]
            for row_index in range(3):
                replaced[row_index][column] = right_hand_side[row_index]
            solution.append(determinant(replaced) / denominator)
        center_x, center_y, radius = solution
        if not all(math.isfinite(value) for value in solution) or radius <= 1e-7:
            raise ValidationError("The three tangent lines and selected sides do not define a positive circle.")

        tangent_points = [
            (center_x - sides[index] * radius * normals[index][0],
             center_y - sides[index] * radius * normals[index][1])
            for index in range(3)
        ]
        for index, (line, tangent_point) in enumerate(zip(lines, tangent_points, strict=True)):
            if line.get("extent", "segment") == "infinite":
                continue
            projection = ((tangent_point[0] - starts[index][0]) * directions[index][0]
                          + (tangent_point[1] - starts[index][1]) * directions[index][1])
            tolerance = 1e-7 * max(1.0, lengths[index], radius)
            if not -tolerance <= projection <= lengths[index] + tolerance:
                raise ValidationError("Each tangent point must lie within its finite line segment.")

        if progress is not None:
            progress({"phase": "add_sketch_circle_tangent_to_three_lines"})
        result = self._add_sketch_circle(
            document,
            {"sketch_id": sketch["id"], "x": center_x, "y": center_y, "radius": radius},
            progress=progress,
        )
        result["line_ids"] = line_ids
        result["tangent_points"] = [
            {"x": point[0], "y": point[1]} for point in tangent_points
        ]
        result["circle_ids"] = [result["circle_id"]]
        result["point_ids"] = [result["point_id"]]
        result["radius"] = radius
        return result

    def _add_sketch_arc_center(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch = self._find_sketch(document, command.get("sketch_id"))
        center = self._coordinate_pair(command.get("center"), "Arc center")
        start = self._coordinate_pair(command.get("start"), "Arc start")
        end = self._coordinate_pair(command.get("end"), "Arc end")
        clockwise = command.get("clockwise", False)
        if not isinstance(clockwise, bool):
            raise ValidationError("clockwise must be a boolean.")
        start_radius = math.hypot(start[0] - center[0], start[1] - center[1])
        end_radius = math.hypot(end[0] - center[0], end[1] - center[1])
        if start_radius <= 1e-7 or end_radius <= 1e-7:
            raise ValidationError("Arc endpoints must differ from the center.")
        if math.hypot(start[0] - end[0], start[1] - end[1]) <= 1e-7:
            raise ValidationError("Arc start and end must be distinct.")
        if abs(start_radius - end_radius) > 1e-7:
            raise ValidationError("Arc start and end must have the same radius from the center.")
        radius = (start_radius + end_radius) / 2
        if not all(math.isfinite(value) for value in
                   (center[0] - radius, center[0] + radius, center[1] - radius, center[1] + radius)):
            raise ValidationError("Arc coordinates must remain finite.")
        start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
        end_angle = math.atan2(end[1] - center[1], end[0] - center[0])
        sweep = (end_angle - start_angle) % math.tau
        if clockwise:
            sweep -= math.tau
        if abs(sweep) <= 1e-7 or abs(sweep) >= math.tau - 1e-7:
            raise ValidationError("Arc sweep must be non-zero and less than one revolution.")
        if progress is not None:
            progress({"phase": "add_sketch_arc_center"})
        sketch_id = sketch["id"]
        point_ids = [f"point:{uuid4()}" for _ in range(3)]
        arc_id = f"circle:{uuid4()}"
        sketch_index = document.objects.index(sketch)
        document.objects[sketch_index + 1:sketch_index + 1] = [
            {"id": point_ids[0], "type": "point", "sketch_id": sketch_id,
             "x": center[0], "y": center[1], "construction": True},
            {"id": point_ids[1], "type": "point", "sketch_id": sketch_id,
             "x": start[0], "y": start[1]},
            {"id": point_ids[2], "type": "point", "sketch_id": sketch_id,
             "x": end[0], "y": end[1]},
            {"id": arc_id, "type": "circle", "sketch_id": sketch_id,
             "center_id": point_ids[0], "start_id": point_ids[1], "end_id": point_ids[2],
             "radius": radius, "sweep_radians": sweep},
        ]
        self._rebuild_changed_features(document, {sketch_id}, progress=progress)
        return {"sketch_id": sketch_id, "point_ids": point_ids, "circle_ids": [arc_id],
                "arc_id": arc_id, "radius": radius, "sweep_radians": sweep}

    def _add_sketch_arc_three_points(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch = self._find_sketch(document, command.get("sketch_id"))
        start = self._coordinate_pair(command.get("start"), "Arc start")
        through = self._coordinate_pair(command.get("through"), "Arc through point")
        end = self._coordinate_pair(command.get("end"), "Arc end")
        if (math.hypot(start[0] - through[0], start[1] - through[1]) <= 1e-7
                or math.hypot(start[0] - end[0], start[1] - end[1]) <= 1e-7
                or math.hypot(through[0] - end[0], through[1] - end[1]) <= 1e-7):
            raise ValidationError("Arc start, through and end points must be distinct.")

        ax, ay = start
        bx, by = through
        cx, cy = end
        ux, uy = bx - ax, by - ay
        vx, vy = cx - ax, cy - ay
        determinant = 2 * (ux * vy - uy * vx)
        coordinate_scale = max(1.0, math.hypot(ux, uy), math.hypot(vx, vy))
        if not math.isfinite(determinant) or abs(determinant) <= 1e-12 * coordinate_scale**2:
            raise ValidationError("Arc points must not be collinear.")
        u_squared, v_squared = ux * ux + uy * uy, vx * vx + vy * vy
        center_x = ax + (u_squared * vy - v_squared * uy) / determinant
        center_y = ay + (ux * v_squared - vx * u_squared) / determinant
        radius = math.hypot(ax - center_x, ay - center_y)
        if not all(math.isfinite(value) for value in (center_x, center_y, radius)) or radius <= 1e-7:
            raise ValidationError("Arc points must define a finite positive radius.")
        start_angle = math.atan2(ay - center_y, ax - center_x)
        through_angle = math.atan2(by - center_y, bx - center_x)
        end_angle = math.atan2(cy - center_y, cx - center_x)
        ccw_sweep = (end_angle - start_angle) % math.tau
        ccw_through = (through_angle - start_angle) % math.tau
        sweep = ccw_sweep if ccw_through < ccw_sweep else ccw_sweep - math.tau
        if not math.isfinite(sweep) or abs(sweep) <= 1e-7 or abs(sweep) >= math.tau - 1e-7:
            raise ValidationError("Arc sweep must be non-zero and less than one revolution.")
        if progress is not None:
            progress({"phase": "add_sketch_arc_three_points"})
        sketch_id = sketch["id"]
        point_ids = [f"point:{uuid4()}" for _ in range(3)]
        arc_id = f"circle:{uuid4()}"
        sketch_index = document.objects.index(sketch)
        document.objects[sketch_index + 1:sketch_index + 1] = [
            {"id": point_ids[0], "type": "point", "sketch_id": sketch_id,
             "x": center_x, "y": center_y, "construction": True},
            {"id": point_ids[1], "type": "point", "sketch_id": sketch_id, "x": ax, "y": ay},
            {"id": point_ids[2], "type": "point", "sketch_id": sketch_id, "x": cx, "y": cy},
            {"id": arc_id, "type": "circle", "sketch_id": sketch_id,
             "center_id": point_ids[0], "start_id": point_ids[1], "end_id": point_ids[2],
             "radius": radius, "sweep_radians": sweep},
        ]
        self._rebuild_changed_features(document, {sketch_id}, progress=progress)
        return {"sketch_id": sketch_id, "point_ids": point_ids, "circle_ids": [arc_id],
                "arc_id": arc_id, "center": {"x": center_x, "y": center_y}, "radius": radius,
                "sweep_radians": sweep, "through": {"x": bx, "y": by}}

    def _add_sketch_arc_radius(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch = self._find_sketch(document, command.get("sketch_id"))
        start = self._coordinate_pair(command.get("start"), "Arc start")
        end = self._coordinate_pair(command.get("end"), "Arc end")
        radius = command.get("radius")
        large_arc = command.get("large_arc", False)
        clockwise = command.get("clockwise", False)
        if (isinstance(radius, bool) or not isinstance(radius, (int, float))
                or not math.isfinite(radius) or radius <= 1e-7):
            raise ValidationError("Arc radius must be finite and positive.")
        if not isinstance(large_arc, bool) or not isinstance(clockwise, bool):
            raise ValidationError("large_arc and clockwise must be booleans.")
        dx, dy = end[0] - start[0], end[1] - start[1]
        chord = math.hypot(dx, dy)
        if chord <= 1e-7:
            raise ValidationError("Arc start and end must be distinct.")
        if chord > 2 * radius + 1e-7 * max(1.0, radius):
            raise ValidationError("Arc radius must be at least half the endpoint distance.")
        half_chord = chord / 2
        height = math.sqrt(max(0.0, radius * radius - half_chord * half_chord))
        midpoint_x, midpoint_y = start[0] + dx / 2, start[1] + dy / 2
        normal_x, normal_y = -dy / chord, dx / chord
        centers = ((midpoint_x + normal_x * height, midpoint_y + normal_y * height),
                   (midpoint_x - normal_x * height, midpoint_y - normal_y * height))
        candidates: list[tuple[float, tuple[float, float]]] = []
        for center in centers:
            start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
            end_angle = math.atan2(end[1] - center[1], end[0] - center[0])
            candidates.append(((end_angle - start_angle) % math.tau, center))
        desired_major_ccw = large_arc != clockwise
        _, center = next((item for item in candidates
                          if (item[0] > math.pi + 1e-10) == desired_major_ccw), candidates[0])
        if progress is not None:
            progress({"phase": "add_sketch_arc_radius"})
        callback = (lambda event: progress({**event, "phase": "add_sketch_arc_radius"})) if progress else None
        result = self._add_sketch_arc_center(
            document,
            {"sketch_id": sketch["id"], "center": {"x": center[0], "y": center[1]},
             "start": {"x": start[0], "y": start[1]}, "end": {"x": end[0], "y": end[1]},
             "clockwise": clockwise},
            progress=callback,
        )
        if abs(result["radius"] - radius) > 1e-7 * max(1.0, radius):
            raise ValidationError("The selected radius cannot define this arc.")
        result["center"] = {"x": center[0], "y": center[1]}
        result["large_arc"] = large_arc
        return result

    def _add_sketch_arc_tangent(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        sketch = self._find_sketch(document, command.get("sketch_id"))
        line_id = command.get("line_id")
        by_id = {item["id"]: item for item in document.objects}
        line = by_id.get(line_id)
        if line is None or line.get("type") != "line" or line.get("sketch_id") != sketch["id"]:
            raise ValidationError("The tangent line must belong to the same sketch.")
        start_point = self._coordinate_pair(command.get("tangent_point"), "Arc tangent point")
        radius = command.get("radius")
        sweep_degrees = command.get("sweep_degrees")
        side = command.get("side", 1)
        if (isinstance(radius, bool) or not isinstance(radius, (int, float))
                or not math.isfinite(radius) or radius <= 1e-7):
            raise ValidationError("Arc radius must be finite and positive.")
        if (isinstance(sweep_degrees, bool) or not isinstance(sweep_degrees, (int, float))
                or not math.isfinite(sweep_degrees) or abs(sweep_degrees) <= 1e-7
                or abs(sweep_degrees) >= 360 - 1e-7):
            raise ValidationError("sweep_degrees must be non-zero and less than one revolution.")
        if type(side) is not int or side not in {-1, 1}:
            raise ValidationError("Tangent side must be 1 or -1 relative to the line direction.")
        start_obj, end_obj = by_id[line["start_id"]], by_id[line["end_id"]]
        dx, dy = end_obj["x"] - start_obj["x"], end_obj["y"] - start_obj["y"]
        line_length = math.hypot(dx, dy)
        if not math.isfinite(line_length) or line_length <= 1e-7:
            raise ValidationError("The tangent line must have finite positive length.")
        ux, uy = dx / line_length, dy / line_length
        nx, ny = -uy, ux
        projection = ((start_point[0] - start_obj["x"]) * ux
                      + (start_point[1] - start_obj["y"]) * uy)
        offset = ((start_point[0] - start_obj["x"]) * nx
                  + (start_point[1] - start_obj["y"]) * ny)
        tolerance = 1e-7 * max(1.0, line_length, radius)
        if abs(offset) > tolerance:
            raise ValidationError("The tangent point must lie on the selected line.")
        if line.get("extent", "segment") != "infinite" and not -tolerance <= projection <= line_length + tolerance:
            raise ValidationError("The tangent point must lie within the finite line segment.")
        center = {"x": start_point[0] + side * radius * nx,
                  "y": start_point[1] + side * radius * ny}
        sweep = math.radians(sweep_degrees)
        radial_x, radial_y = start_point[0] - center["x"], start_point[1] - center["y"]
        end = {"x": center["x"] + radial_x * math.cos(sweep) - radial_y * math.sin(sweep),
               "y": center["y"] + radial_x * math.sin(sweep) + radial_y * math.cos(sweep)}
        if progress is not None:
            progress({"phase": "add_sketch_arc_tangent"})
        callback = (lambda event: progress({**event, "phase": "add_sketch_arc_tangent"})) if progress else None
        result = self._add_sketch_arc_center(
            document,
            {"sketch_id": sketch["id"], "center": center,
             "start": {"x": start_point[0], "y": start_point[1]}, "end": end,
             "clockwise": sweep < 0},
            progress=callback,
        )
        result["line_id"] = line_id
        result["tangent_point"] = {"x": start_point[0], "y": start_point[1]}
        result["side"] = side
        result["sweep_degrees"] = sweep_degrees
        return result

    def _add_sketch_slot(
        self,
        document: Document,
        command: dict[str, Any],
        operation: str,
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Create one exact closed slot contour as ordinary sketch point/edge objects."""
        sketch = self._find_sketch(document, command.get("sketch_id"))
        if operation == "add_sketch_slot":
            contour = build_straight_slot(
                self._coordinate_pair(command.get("start"), "Slot start"),
                self._coordinate_pair(command.get("end"), "Slot end"),
                command.get("width"),
            )
        elif operation == "add_sketch_slot_by_centers":
            contour = build_slot_by_centers(
                self._coordinate_pair(command.get("start_center"), "Slot start center"),
                self._coordinate_pair(command.get("end_center"), "Slot end center"),
                command.get("width"),
            )
        elif operation == "add_sketch_slot_total_length":
            contour = build_slot_by_total_length(
                self._coordinate_pair(command.get("center"), "Slot center"),
                command.get("length"), command.get("width"), command.get("angle_degrees", 0),
            )
        elif operation == "add_sketch_slot_centered":
            contour = build_slot_from_center(
                self._coordinate_pair(command.get("center_point"), "Slot center point"),
                command.get("total_length"), command.get("slot_width"),
                command.get("rotation_degrees", 0),
            )
        elif operation == "add_sketch_arc_slot_three_points":
            contour = build_arc_slot_three_points(
                self._coordinate_pair(command.get("start"), "Arc slot start"),
                self._coordinate_pair(command.get("through"), "Arc slot through point"),
                self._coordinate_pair(command.get("end"), "Arc slot end"),
                command.get("width"),
            )
        elif operation == "add_sketch_arc_slot_center":
            contour = build_arc_slot_from_center(
                self._coordinate_pair(command.get("center"), "Arc slot center"),
                command.get("radius"), command.get("start_angle_degrees"),
                command.get("sweep_degrees"), command.get("width"),
            )
        elif operation == "add_sketch_arc_slot_tangent":
            line_id = command.get("line_id")
            line = next((item for item in document.objects if item.get("id") == line_id), None)
            if line is None or line.get("type") != "line" or line.get("sketch_id") != sketch["id"]:
                raise ValidationError("The tangent line must belong to the same sketch.")
            by_id = {item["id"]: item for item in document.objects}
            start_obj, end_obj = by_id.get(line.get("start_id")), by_id.get(line.get("end_id"))
            if (start_obj is None or end_obj is None or start_obj.get("type") != "point"
                    or end_obj.get("type") != "point"):
                raise ValidationError("The tangent line must reference valid sketch points.")
            dx, dy = end_obj["x"] - start_obj["x"], end_obj["y"] - start_obj["y"]
            line_length = math.hypot(dx, dy)
            if not math.isfinite(line_length) or line_length <= 1e-7:
                raise ValidationError("The tangent line must have finite positive length.")
            ux, uy = dx / line_length, dy / line_length
            tangent_point = self._coordinate_pair(command.get("tangent_point"), "Slot tangent point")
            projection = ((tangent_point[0] - start_obj["x"]) * ux
                          + (tangent_point[1] - start_obj["y"]) * uy)
            offset = ((tangent_point[0] - start_obj["x"]) * -uy
                      + (tangent_point[1] - start_obj["y"]) * ux)
            tolerance = 1e-7 * max(1.0, line_length)
            if abs(offset) > tolerance:
                raise ValidationError("The tangent point must lie on the selected line.")
            if line.get("extent", "segment") != "infinite" and not -tolerance <= projection <= line_length + tolerance:
                raise ValidationError("The tangent point must lie within the finite line segment.")
            side = command.get("side", 1)
            contour = build_tangent_arc_slot(
                tangent_point, (ux, uy), command.get("radius"),
                command.get("sweep_degrees"), command.get("width"), side,
            )
        else:
            raise ValidationError(f"Unsupported slot tool: {operation!r}.")

        if progress is not None:
            progress({"phase": operation})
        records = contour_to_sketch_objects(contour, sketch["id"])
        insert_at = document.objects.index(sketch) + 1
        document.objects[insert_at:insert_at] = records
        self._rebuild_changed_features(document, {sketch["id"]}, progress=progress)
        return {
            "sketch_id": sketch["id"],
            "object_ids": [item["id"] for item in records],
            "point_ids": [item["id"] for item in records if item["type"] == "point"],
            "line_ids": [item["id"] for item in records if item["type"] == "line"],
            "circle_ids": [item["id"] for item in records if item["type"] == "circle"],
            "closed": True,
            "centerline": contour["centerline"],
        }


def _handle_sampled_curve(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    kind = command["type"]
    return self._add_sketch_sampled_curve(document, command, kind=kind, progress=progress)


def _handle_add_sketch_ellipse(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    kind = command["type"]
    return self._add_sketch_ellipse(document, command, kind, progress=progress)


def _handle_add_sketch_elliptical_arc(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._add_sketch_elliptical_arc(document, command, progress=progress)


def _handle_add_sketch_spline(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._add_sketch_spline(document, command, mode="interpolating", progress=progress)


def _handle_add_sketch_closed_spline(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._add_sketch_spline(document, command, mode="closed", progress=progress)


def _handle_add_sketch_spline_control_points(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._add_sketch_spline(document, command, mode="control", progress=progress)


def _handle_add_sketch_bspline(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._add_sketch_spline(document, command, mode="bspline", progress=progress)


def _handle_add_sketch_bezier_curve(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._add_sketch_spline(document, command, mode="bezier", progress=progress)


def _handle_add_sketch_nurbs(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._add_sketch_nurbs(document, command, progress=progress)


def _handle_add_sketch_circle_tangent_to_line(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._add_sketch_circle_tangent_to_line(document, command, progress=progress)


def _handle_add_sketch_circle_tangent_to_two_lines(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._add_sketch_circle_tangent_to_two_lines(document, command, progress=progress)


def _handle_add_sketch_circle_tangent_to_three_lines(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._add_sketch_circle_tangent_to_three_lines(document, command, progress=progress)


def _handle_add_sketch_arc_center(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._add_sketch_arc_center(document, command, progress=progress)


def _handle_add_sketch_arc_three_points(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._add_sketch_arc_three_points(document, command, progress=progress)


def _handle_add_sketch_arc_start_center_end(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    kind = command["type"]
    mapped = {"sketch_id": command.get("sketch_id"), "center": command.get("center"),
              "start": command.get("start"), "end": command.get("end"),
              "clockwise": command.get("clockwise", False)}
    callback = (lambda event: progress({**event, "phase": kind})) if progress else None
    return self._add_sketch_arc_center(document, mapped, progress=callback)


def _handle_add_sketch_arc_radius(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._add_sketch_arc_radius(document, command, progress=progress)


def _handle_add_sketch_arc_tangent(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._add_sketch_arc_tangent(document, command, progress=progress)


def _handle_slot(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    kind = command["type"]
    return self._add_sketch_slot(document, command, kind, progress=progress)


COMMAND_HANDLERS = {
    "add_sketch_coordinate_curve": _handle_sampled_curve,
    "add_sketch_parametric_curve": _handle_sampled_curve,
    "add_sketch_spiral": _handle_sampled_curve,
    "add_sketch_ellipse": _handle_add_sketch_ellipse,
    "add_sketch_ellipse_center": _handle_add_sketch_ellipse,
    "add_sketch_ellipse_three_points": _handle_add_sketch_ellipse,
    "add_sketch_elliptical_arc": _handle_add_sketch_elliptical_arc,
    "add_sketch_spline": _handle_add_sketch_spline,
    "add_sketch_closed_spline": _handle_add_sketch_closed_spline,
    "add_sketch_spline_control_points": _handle_add_sketch_spline_control_points,
    "add_sketch_bspline": _handle_add_sketch_bspline,
    "add_sketch_bezier_curve": _handle_add_sketch_bezier_curve,
    "add_sketch_nurbs": _handle_add_sketch_nurbs,
    "add_sketch_circle_tangent_to_line": _handle_add_sketch_circle_tangent_to_line,
    "add_sketch_circle_tangent_to_two_lines": _handle_add_sketch_circle_tangent_to_two_lines,
    "add_sketch_circle_tangent_to_three_lines": _handle_add_sketch_circle_tangent_to_three_lines,
    "add_sketch_arc_center": _handle_add_sketch_arc_center,
    "add_sketch_arc_three_points": _handle_add_sketch_arc_three_points,
    "add_sketch_arc_start_center_end": _handle_add_sketch_arc_start_center_end,
    "add_sketch_arc_radius": _handle_add_sketch_arc_radius,
    "add_sketch_arc_tangent": _handle_add_sketch_arc_tangent,
    "add_sketch_arc_slot_center": _handle_slot,
    "add_sketch_arc_slot_tangent": _handle_slot,
    "add_sketch_arc_slot_three_points": _handle_slot,
    "add_sketch_slot": _handle_slot,
    "add_sketch_slot_by_centers": _handle_slot,
    "add_sketch_slot_centered": _handle_slot,
    "add_sketch_slot_total_length": _handle_slot,
}
