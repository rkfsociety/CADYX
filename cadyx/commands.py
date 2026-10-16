"""Initial demonstration commands routed through one atomic command system."""

from __future__ import annotations

import math
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import uuid4

from .errors import NotFoundError, ValidationError
from .delivery import compare_step_metrics, geometry_sha256, verify_binary_stl
from .engineering import involute_spur_gear_profile
from .kernel import GeometryKernel
from .mesh_geometry import mesh_ply_bytes
from .models import Document
from .scan import DEFAULT_SCAN_PARAMETERS, ScanProcessor, normalize_scan_parameters
from .sketches import closed_profile, closed_profiles, closed_spline_profile, open_path
from .sketch_slot_tools import (
    build_arc_slot_from_center,
    build_arc_slot_three_points,
    build_slot_by_centers,
    build_slot_by_total_length,
    build_slot_from_center,
    build_straight_slot,
    build_tangent_arc_slot,
    contour_to_sketch_objects,
)
from .sketch_curve_toolkit import (
    evaluate_archimedean_spiral,
    sample_parametric_expressions,
)
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
        if kind == "loft_sketches":
            return self._loft_sketches(document, command, progress=progress)
        if kind == "create_ruled_shell":
            return self._create_ruled_shell(document, command, progress=progress)
        if kind == "update_loft_sections":
            return self._update_loft_sections(document, command, progress=progress)
        if kind == "fuse_bodies":
            return self._fuse_bodies(document, command, progress=progress)
        if kind == "cut_bodies":
            return self._cut_bodies(document, command, progress=progress)
        if kind == "remove_body":
            return self._remove_body(document, command)
        if kind == "remove_objects":
            return self._remove_objects(document, command)
        if kind == "clear_model":
            if command.keys() != {"type"}:
                raise ValidationError("clear_model does not accept additional fields.")
            removed_object_count = len(document.objects)
            removed_body_count = sum(item.get("type") == "body" for item in document.objects)
            removed_sketch_count = sum(item.get("type") == "sketch" for item in document.objects)
            document.objects.clear()
            return {
                "removed_object_count": removed_object_count,
                "removed_body_count": removed_body_count,
                "removed_sketch_count": removed_sketch_count,
                "project_files_preserved": True,
            }
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
        if kind == "create_sketch_from_section":
            return self._create_sketch_from_section(document, command, progress=progress)
        if kind == "set_scan_parameter":
            return self._set_scan_parameter(document, command, progress=progress)
        if kind in {"add_sketch_spiral", "add_sketch_parametric_curve", "add_sketch_coordinate_curve"}:
            return self._add_sketch_sampled_curve(document, command, kind=kind, progress=progress)
        if kind == "create_sketch":
            sketch_id = f"sketch:{uuid4()}"
            plane = command.get("plane", "XY")
            if plane not in {"XY", "XZ"}:
                raise ValidationError("Sketch plane must be XY or XZ.")
            sketch = {
                "id": sketch_id, "type": "sketch",
                "name": command.get("name", "Sketch"), "plane": plane,
            }
            if "section_normal_axis" in command or "section_offset" in command:
                axis = command.get("section_normal_axis")
                expected_axis = "Z" if plane == "XY" else "Y"
                if axis != expected_axis or "section_offset" not in command:
                    raise ValidationError(f"An offset {plane} sketch needs section_normal_axis={expected_axis} and section_offset.")
                sketch["section_normal_axis"] = axis
                sketch["section_offset"] = _finite_number(command.get("section_offset"), "Sketch plane offset")
            document.objects.append(sketch)
            return {"sketch_id": sketch_id}
        if kind in {"add_sketch_point", "add_sketch_construction_point"}:
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
        if kind in {"add_sketch_segment", "add_sketch_line", "add_sketch_infinite_line",
                    "add_sketch_centerline", "add_sketch_construction_line"}:
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
        if kind == "add_sketch_element":
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
        if kind == "add_sketch_polyline":
            return self._add_sketch_polyline(document, command, progress=progress)
        if kind == "add_sketch_line_chain":
            if command.get("closed", False) is not False:
                raise ValidationError("A line chain is always open; use add_sketch_polyline for a closed path.")
            return self._add_sketch_polyline(document, command, operation=kind, progress=progress)
        if kind == "add_sketch_involute_gear":
            return self._add_sketch_involute_gear(document, command, progress=progress)
        if kind == "add_sketch_rectangle":
            return self._add_sketch_rectangle(document, command, progress=progress)
        if kind == "add_sketch_rounded_rectangle":
            return self._add_sketch_rounded_rectangle(document, command, progress=progress)
        if kind in {"add_sketch_rectangle_two_corners", "add_sketch_rectangle_center",
                    "add_sketch_rectangle_three_points", "add_sketch_rotated_rectangle",
                    "add_sketch_parallelogram", "add_sketch_regular_polygon",
                    "add_sketch_inscribed_polygon", "add_sketch_circumscribed_polygon",
                    "add_sketch_polygon_from_center"}:
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
        if kind == "add_sketch_circle":
            return self._add_sketch_circle(document, command, progress=progress)
        if kind in {"add_sketch_ellipse", "add_sketch_ellipse_center",
                    "add_sketch_ellipse_three_points"}:
            return self._add_sketch_ellipse(document, command, kind, progress=progress)
        if kind == "add_sketch_elliptical_arc":
            return self._add_sketch_elliptical_arc(document, command, progress=progress)
        if kind == "add_sketch_spline":
            return self._add_sketch_spline(document, command, mode="interpolating", progress=progress)
        if kind == "add_sketch_closed_spline":
            return self._add_sketch_spline(document, command, mode="closed", progress=progress)
        if kind == "add_sketch_spline_control_points":
            return self._add_sketch_spline(document, command, mode="control", progress=progress)
        if kind == "add_sketch_bspline":
            return self._add_sketch_spline(document, command, mode="bspline", progress=progress)
        if kind == "add_sketch_bezier_curve":
            return self._add_sketch_spline(document, command, mode="bezier", progress=progress)
        if kind == "add_sketch_nurbs":
            return self._add_sketch_nurbs(document, command, progress=progress)
        if kind in {"add_sketch_circle_diameter", "add_sketch_circle_two_points", "add_sketch_circle_three_points"}:
            return self._add_sketch_circle_tool(document, command, kind, progress=progress)
        if kind == "add_sketch_circle_tangent_to_line":
            return self._add_sketch_circle_tangent_to_line(document, command, progress=progress)
        if kind == "add_sketch_circle_tangent_to_two_lines":
            return self._add_sketch_circle_tangent_to_two_lines(document, command, progress=progress)
        if kind == "add_sketch_circle_tangent_to_three_lines":
            return self._add_sketch_circle_tangent_to_three_lines(document, command, progress=progress)
        if kind == "add_sketch_arc_center":
            return self._add_sketch_arc_center(document, command, progress=progress)
        if kind == "add_sketch_arc_three_points":
            return self._add_sketch_arc_three_points(document, command, progress=progress)
        if kind == "add_sketch_arc_start_center_end":
            mapped = {"sketch_id": command.get("sketch_id"), "center": command.get("center"),
                      "start": command.get("start"), "end": command.get("end"),
                      "clockwise": command.get("clockwise", False)}
            callback = (lambda event: progress({**event, "phase": kind})) if progress else None
            return self._add_sketch_arc_center(document, mapped, progress=callback)
        if kind == "add_sketch_arc_radius":
            return self._add_sketch_arc_radius(document, command, progress=progress)
        if kind == "add_sketch_arc_tangent":
            return self._add_sketch_arc_tangent(document, command, progress=progress)
        if kind in {"add_sketch_slot", "add_sketch_slot_by_centers",
                    "add_sketch_slot_total_length", "add_sketch_slot_centered",
                    "add_sketch_arc_slot_three_points",
                    "add_sketch_arc_slot_center", "add_sketch_arc_slot_tangent"}:
            return self._add_sketch_slot(document, command, kind, progress=progress)
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
        raise ValidationError(f"Unsupported command type: {kind!r}.")

    @staticmethod
    def _remove_body(document: Document, command: dict[str, Any]) -> dict[str, Any]:
        if not command.keys() <= {"type", "body_id", "remove_source_file"}:
            raise ValidationError("Body removal contains unsupported fields.")
        remove_source_file = command.get("remove_source_file", False)
        if type(remove_source_file) is not bool:
            raise ValidationError("remove_source_file must be a boolean.")
        body_id = command.get("body_id")
        if not isinstance(body_id, str):
            raise NotFoundError("Body to remove was not found.")
        body = next((item for item in document.objects
                     if item.get("id") == body_id and item.get("type") == "body"), None)
        if body is None:
            raise NotFoundError("Body to remove was not found.")

        # Do not leave dependent features or sections pointing at a missing body.
        # A feature's body_id identifies its own output and is safe to cascade.
        def contains_reference(value: Any) -> bool:
            if isinstance(value, dict):
                return any(contains_reference(child) for child in value.values())
            if isinstance(value, list):
                return any(contains_reference(child) for child in value)
            return value == body_id

        dependents = []
        for item in document.objects:
            if item is body:
                continue
            if item.get("type") == "feature" and item.get("body_id") == body_id:
                continue
            if contains_reference(item):
                dependents.append(item.get("id"))
        if dependents:
            raise ValidationError("Body is referenced by other document objects and cannot be removed.")

        feature_ids = set(body.get("feature_ids", []))
        source_file = None
        if remove_source_file:
            import_features = [
                item for item in document.objects
                if item.get("id") in feature_ids and item.get("operation") == "import_step"
            ]
            if len(import_features) != 1:
                raise ValidationError("Removing a staged source file requires one imported STEP feature owned by this body.")
            source_file = import_features[0].get("source", {}).get("file")
            if not isinstance(source_file, str):
                raise ValidationError("The imported STEP feature has no valid source filename.")
            shared_source = any(
                item.get("type") == "feature"
                and item.get("operation") == "import_step"
                and item.get("id") not in feature_ids
                and item.get("source", {}).get("file") == source_file
                for item in document.objects
            )
            if shared_source:
                raise ValidationError("The STEP source file is still used by another imported body and cannot be removed.")
        document.objects[:] = [
            item for item in document.objects
            if item is not body and item.get("id") not in feature_ids
        ]
        return {
            "body_id": body_id,
            "removed_feature_ids": sorted(feature_ids),
            **({"source_file_to_remove": source_file} if source_file else {}),
        }

    @staticmethod
    def _remove_objects(document: Document, command: dict[str, Any]) -> dict[str, Any]:
        if command.keys() != {"type", "object_ids"}:
            raise ValidationError("remove_objects requires only object_ids.")
        object_ids = command.get("object_ids")
        if (not isinstance(object_ids, list) or not object_ids
                or any(not isinstance(value, str) or not value for value in object_ids)
                or len(set(object_ids)) != len(object_ids)):
            raise ValidationError("object_ids must be a non-empty list of unique object IDs.")
        by_id = {item["id"]: item for item in document.objects}
        missing = [value for value in object_ids if value not in by_id]
        if missing:
            raise NotFoundError(f"Objects to remove were not found: {', '.join(missing)}")
        allowed_types = {"body", "feature", "sketch", "point", "line", "circle", "ellipse", "spline", "constraint"}
        unsupported = [value for value in object_ids if by_id[value].get("type") not in allowed_types]
        if unsupported:
            raise ValidationError(f"Objects cannot be removed with remove_objects: {', '.join(unsupported)}")
        removed = set(object_ids)

        def references_removed(value: Any) -> bool:
            if isinstance(value, dict):
                return any(references_removed(child) for key, child in value.items() if key != "id")
            if isinstance(value, list):
                return any(references_removed(child) for child in value)
            return isinstance(value, str) and value in removed

        external = [item["id"] for item in document.objects
                    if item["id"] not in removed and references_removed(item)]
        if external:
            raise ValidationError(f"Objects are still referenced by retained objects: {', '.join(external)}")
        document.objects[:] = [item for item in document.objects if item["id"] not in removed]
        return {"removed_object_ids": object_ids, "removed_count": len(object_ids)}

    def _create_sketch_from_section(
        self, document: Document, command: dict[str, Any], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        body_id = command.get("body_id")
        body = next((item for item in document.objects
                     if item.get("id") == body_id and item.get("type") == "body"), None)
        if body is None:
            raise NotFoundError("Section source body was not found.")
        if not isinstance(body.get("geometry"), dict) or body["geometry"].get("kind") != "imported_brep":
            raise ValidationError("Section extraction requires an imported STEP B-rep body.")
        axis = command.get("normal_axis")
        if axis not in {"X", "Y", "Z"}:
            raise ValidationError("normal_axis must be X, Y or Z.")
        offset = _finite_number(command.get("offset"), "Section plane offset")
        max_points = command.get("max_points", 2048)
        if type(max_points) is not int or max_points < 16:
            raise ValidationError("max_points must be an integer of at least 16.")
        name = command.get("name", f"Сечение {axis}={offset:g}")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValidationError("Sketch name must be a non-empty string of at most 120 characters.")
        if progress is not None:
            progress({"phase": "extract_imported_section"})
        profiles = self.kernel.extract_section_profile(
            body["geometry"], normal_axis=axis, offset=offset, max_points=max_points,
        )
        sketches = []
        sketch_plane = "XZ" if axis == "Y" else "XY"
        for index, points in enumerate(profiles, start=1):
            sketch_id = f"sketch:{uuid4()}"
            sketch_name = name.strip() if len(profiles) == 1 else f"{name.strip()} · контур {index}"
            sketch = {"id": sketch_id, "type": "sketch", "name": sketch_name,
                      "plane": sketch_plane, "section_normal_axis": axis,
                      "section_offset": offset, "section_profile_index": index - 1}
            document.objects.append(sketch)
            result = self._add_sketch_polyline(
                document, {"sketch_id": sketch_id, "points": points, "closed": True},
                operation="draw_extracted_section", progress=progress,
            )
            sketches.append({"sketch_id": sketch_id, "sample_count": len(points),
                             "point_ids": result["point_ids"], "line_ids": result["line_ids"]})
        return {"sketches": sketches, "body_id": body_id, "normal_axis": axis, "offset": offset}

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
        show_in_preview = command.get("show_in_preview", True)
        if type(show_in_preview) is not bool:
            raise ValidationError("show_in_preview must be a boolean.")
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
                "visible": show_in_preview,
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
            "visible": show_in_preview,
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
            if feature.get("operation") in {"extrude_sketch", "revolve_sketch", "pad_sketch_to_body"}:
                expected = "angle_degrees" if feature["operation"] == "revolve_sketch" else "depth"
                if parameter_name == "centered" and feature["operation"] in {"extrude_sketch", "pad_sketch_to_body"}:
                    value = command.get("value")
                    if type(value) is not bool:
                        raise ValidationError("centered must be a boolean.")
                elif (feature["operation"] == "revolve_sketch"
                      and parameter_name in {"outer_diameter", "height"}):
                    value = _number(command.get("value"), parameter_name)
                elif parameter_name == expected:
                    value = _number(command.get("value"), parameter_name)
                else:
                    supported = ("angle_degrees, outer_diameter, and height" if feature["operation"] == "revolve_sketch"
                                 else "depth and centered")
                    raise ValidationError(f"Only {supported} parameters are supported for this feature.")
                parameters = dict(feature["parameters"])
                if feature["operation"] == "revolve_sketch" and parameter_name in {"outer_diameter", "height"}:
                    sketch_id = feature.get("source_sketch_id")
                    sketch_points = [item for item in document.objects
                                     if item.get("type") == "point" and item.get("sketch_id") == sketch_id]
                    if len(sketch_points) < 3:
                        raise ValidationError("Revolved profile has too few editable points.")
                    old_value = parameters.get(parameter_name)
                    if not isinstance(old_value, (int, float)) or isinstance(old_value, bool) or old_value <= 0:
                        spline_profile = closed_spline_profile(document.objects, sketch_id)
                        old_profile = (spline_profile[1] if spline_profile is not None
                                       else closed_profile(document.objects, sketch_id)[1])
                        old_value = (2.0 * max(abs(point["x"]) for point in old_profile)
                                     if parameter_name == "outer_diameter" else
                                     max(point["y"] for point in old_profile) - min(point["y"] for point in old_profile))
                    if old_value <= 0:
                        raise ValidationError(f"Revolved profile has no positive {parameter_name}.")
                    scale = value / old_value
                    if parameter_name == "outer_diameter":
                        for point in sketch_points:
                            point["x"] *= scale
                    else:
                        bottom = min(point["y"] for point in sketch_points)
                        for point in sketch_points:
                            point["y"] = bottom + (point["y"] - bottom) * scale
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
            if feature.get("operation") == "chamfer_edges" and parameter_name == "distance":
                value = _number(command.get("value"), "distance", positive=True)
                parameters = dict(feature["parameters"])
                parameters["distance"] = value
                source = next((item for item in document.objects
                               if item.get("id") == feature.get("source_body_id") and item.get("type") == "body"), None)
                body = next((item for item in document.objects
                             if item.get("id") == feature.get("body_id") and item.get("type") == "body"), None)
                if source is None or body is None:
                    raise ValidationError("Chamfer feature refers to a missing source or result body.")
                from .advanced_features import _advanced_native
                geometry = _advanced_native(source["geometry"], 2, [value], parameters["edge_indices"])
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
            if feature.get("operation") in {"linear_pattern_body", "circular_pattern_body", "hole", "boss"}:
                operation = feature["operation"]
                parameters = dict(feature.get("parameters", {}))
                if operation in {"linear_pattern_body", "circular_pattern_body"} and parameter_name == "count":
                    from .advanced_features import _pattern_count
                    value = _pattern_count(command.get("value"))
                    parameters["count"] = value
                    if operation == "circular_pattern_body" and parameters.get("full_circle") is True:
                        parameters["angle_step_degrees"] = 360.0 / value
                elif operation == "linear_pattern_body" and parameter_name == "offset":
                    from .advanced_features import _vector
                    value = _vector(command.get("value"), "offset", nonzero=True)
                    parameters["offset"] = value
                elif operation == "circular_pattern_body" and parameter_name == "full_circle":
                    value = command.get("value")
                    if type(value) is not bool:
                        raise ValidationError("full_circle must be a boolean.")
                    parameters["full_circle"] = value
                    if value:
                        parameters["angle_step_degrees"] = 360.0 / parameters["count"]
                elif operation == "circular_pattern_body" and parameter_name == "angle_step_degrees":
                    value = _number(command.get("value"), parameter_name, nonzero=True)
                    parameters[parameter_name] = value
                    parameters["full_circle"] = False
                elif operation in {"hole", "boss"} and parameter_name in {"diameter", "depth"}:
                    value = _number(command.get("value"), parameter_name, positive=True)
                    parameters[parameter_name] = value
                else:
                    supported = ("count and offset" if operation == "linear_pattern_body" else
                                 "count, angle_step_degrees, and full_circle" if operation == "circular_pattern_body"
                                 else "diameter and depth")
                    raise ValidationError(f"Only {supported} parameters are supported for this feature.")
                source = next((item for item in document.objects
                               if item.get("id") == feature.get("source_body_id") and item.get("type") == "body"), None)
                body = next((item for item in document.objects
                             if item.get("id") == feature.get("body_id") and item.get("type") == "body"), None)
                if source is None or body is None:
                    raise ValidationError("Advanced feature refers to a missing source or result body.")
                operation_id = {"linear_pattern_body": 6, "circular_pattern_body": 7,
                                "hole": 9, "boss": 10}[operation]
                if operation == "linear_pattern_body":
                    values = [float(parameters["count"]), *parameters["offset"]]
                elif operation == "circular_pattern_body":
                    values = [float(parameters["count"]), *parameters["axis_origin"],
                              *parameters["axis_direction"], parameters["angle_step_degrees"]]
                else:
                    values = [*parameters["origin"], *parameters["axis"],
                              parameters["diameter"], parameters["depth"]]
                if progress is not None:
                    progress({"phase": operation})
                from .advanced_features import _advanced_native
                geometry = _advanced_native(source["geometry"], operation_id, values)
                feature["parameters"], feature["geometry"], body["geometry"] = parameters, geometry, geometry
                self._rebuild_derived_features(document, {body["id"]}, progress=progress)
                return {"feature_id": feature_id, "parameter": parameter_name,
                        "value": value, "geometry": geometry}
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
        spline_profile = closed_spline_profile(document.objects, sketch_id)
        profiles = None
        if spline_profile is None:
            if operation == "extrude_sketch":
                plane, profiles = closed_profiles(document.objects, sketch_id)
                profile = profiles[0]
            else:
                plane, profile = closed_profile(document.objects, sketch_id)
            curve_points = None
        else:
            plane, profile, curve_points = spline_profile
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
            parameters["outer_diameter"] = max(abs(point["x"]) for point in profile) * 2.0
            axial_values = [point["y"] for point in profile]
            parameters["height"] = max(axial_values) - min(axial_values)
        if progress is not None:
            progress({"phase": "kernel_extrusion" if operation == "extrude_sketch" else "kernel_revolution"})
        geometry = self._profile_geometry(document, {
            "operation": operation, "source_sketch_id": sketch_id,
        }, parameters, profile=profile, plane=plane, curve_points=curve_points, profiles=profiles)
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

    def _loft_sketches(
        self, document: Document, command: dict[str, Any], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        section_groups = command.get("section_groups")
        if section_groups is not None:
            if (not isinstance(section_groups, list) or len(section_groups) < 2
                    or any(not isinstance(group, list) or not group
                           or any(not isinstance(value, str) for value in group) for group in section_groups)):
                raise ValidationError("Loft section_groups needs at least two stations with one or more sketch IDs each.")
            sketch_ids = [sketch_id for group in section_groups for sketch_id in group]
            if len(set(sketch_ids)) != len(sketch_ids):
                raise ValidationError("Loft section_groups must contain unique sketch IDs.")
        else:
            sketch_ids = command.get("sketch_ids")
            section_groups = [[sketch_id] for sketch_id in sketch_ids] if isinstance(sketch_ids, list) else None
        if (not isinstance(sketch_ids, list) or len(sketch_ids) < 2
                or any(not isinstance(value, str) for value in sketch_ids)
                or len(set(sketch_ids)) != len(sketch_ids)):
            raise ValidationError("Loft needs at least two unique section sketch IDs.")
        name = command.get("name", "Loft")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValidationError("Feature name must be a non-empty string of at most 120 characters.")
        sections, offsets = [], []
        for group in section_groups:
            group_profiles, group_offsets = [], []
            for sketch_id in group:
                sketch = self._find_sketch(document, sketch_id)
                if sketch.get("plane") != "XZ" or sketch.get("section_normal_axis") != "Y":
                    raise ValidationError("Loft sections must be XZ sketches extracted from Y-normal planes.")
                if not isinstance(sketch.get("section_offset"), (int, float)):
                    raise ValidationError("Each loft sketch must retain its source section offset.")
                _, profile = closed_profile(document.objects, sketch_id)
                group_profiles.append(profile)
                group_offsets.append(float(sketch["section_offset"]))
            if any(offset != group_offsets[0] for offset in group_offsets):
                raise ValidationError("All sketches in one loft station must share a section_offset.")
            sections.append(group_profiles)
            offsets.append(group_offsets[0])
        if any(right <= left for left, right in zip(offsets, offsets[1:])):
            raise ValidationError("Loft station groups must be ordered by strictly increasing section_offset.")
        if progress is not None:
            progress({"phase": "prepare_loft_sections"})
            progress({"phase": "kernel_loft"})
        geometry = (self.kernel.create_lofted_profiles(
            [group[0] for group in sections], section_offsets=offsets,
        ) if all(len(group) == 1 for group in sections) else
            self.kernel.create_multicontour_loft(sections, section_offsets=offsets))
        body_id, feature_id = f"body:{uuid4()}", f"feature:{uuid4()}"
        parameters = {"section_sketch_ids": sketch_ids, "section_offsets": offsets}
        if any(len(group) > 1 for group in section_groups):
            parameters["section_sketch_groups"] = section_groups
        document.objects.extend([
            {"id": body_id, "type": "body", "name": name.strip(),
             "feature_ids": [feature_id], "geometry": geometry},
            {"id": feature_id, "type": "feature", "operation": "loft_sketches",
             "body_id": body_id, "source_sketch_ids": sketch_ids,
             "parameters": parameters, "geometry": geometry},
        ])
        return {"body_id": body_id, "feature_id": feature_id,
                "source_sketch_ids": sketch_ids, "geometry": geometry}

    def _create_ruled_shell(
        self, document: Document, command: dict[str, Any], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        name = command.get("name", "Ruled shell")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValidationError("Feature name must be a non-empty string of at most 120 characters.")
        tolerance = _finite_number(command.get("sewing_tolerance", 1e-3), "sewing_tolerance")
        if progress is not None:
            progress({"phase": "prepare_ruled_patches"})
        geometry = self.kernel.create_ruled_shell(
            command.get("patches"), sewing_tolerance=tolerance,
        )
        body_id, feature_id = f"body:{uuid4()}", f"feature:{uuid4()}"
        parameters = {"patches": geometry["patches"], "sewing_tolerance": geometry["sewing_tolerance"]}
        document.objects.extend([
            {"id": body_id, "type": "body", "name": name.strip(),
             "feature_ids": [feature_id], "geometry": geometry},
            {"id": feature_id, "type": "feature", "operation": "create_ruled_shell",
             "body_id": body_id, "parameters": parameters, "geometry": geometry},
        ])
        return {"body_id": body_id, "feature_id": feature_id, "geometry": geometry}

    def _update_loft_sections(
        self, document: Document, command: dict[str, Any], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        feature_id = command.get("feature_id")
        feature = next((item for item in document.objects
                        if item.get("id") == feature_id and item.get("type") == "feature"
                        and item.get("operation") == "loft_sketches"), None)
        if feature is None:
            raise NotFoundError("Loft feature was not found.")
        groups = command.get("section_groups")
        if (not isinstance(groups, list) or len(groups) < 2
                or any(not isinstance(group, list) or not group
                       or any(not isinstance(sketch_id, str) for sketch_id in group) for group in groups)):
            raise ValidationError("section_groups needs at least two stations with one or more sketch IDs each.")
        flattened = [sketch_id for group in groups for sketch_id in group]
        if len(set(flattened)) != len(flattened):
            raise ValidationError("Loft section_groups must contain unique sketch IDs.")
        offsets, profiles = [], []
        for group in groups:
            group_offsets, group_profiles = [], []
            for sketch_id in group:
                sketch = self._find_sketch(document, sketch_id)
                if sketch.get("plane") != "XZ" or sketch.get("section_normal_axis") != "Y":
                    raise ValidationError("Loft sections must be XZ sketches extracted from Y-normal planes.")
                group_offsets.append(_finite_number(sketch.get("section_offset"), "section_offset"))
                _, profile = closed_profile(document.objects, sketch_id)
                group_profiles.append(profile)
            if any(offset != group_offsets[0] for offset in group_offsets):
                raise ValidationError("All sketches in one loft station must share a section_offset.")
            offsets.append(group_offsets[0])
            profiles.append(group_profiles)
        if any(right <= left for left, right in zip(offsets, offsets[1:])):
            raise ValidationError("Loft station groups must be ordered by strictly increasing section_offset.")
        geometry = (self.kernel.create_lofted_profiles(
            [group[0] for group in profiles], section_offsets=offsets,
        ) if all(len(group) == 1 for group in profiles) else
            self.kernel.create_multicontour_loft(profiles, section_offsets=offsets))
        body = next((item for item in document.objects if item.get("id") == feature.get("body_id")), None)
        if body is None or body.get("type") != "body":
            raise ValidationError("Loft feature refers to a missing body.")
        section_groups = {"section_sketch_ids": flattened, "section_offsets": offsets}
        if any(len(group) > 1 for group in groups):
            section_groups["section_sketch_groups"] = groups
        feature["source_sketch_ids"] = flattened
        feature["parameters"] = section_groups
        feature["geometry"] = geometry
        body["geometry"] = geometry
        if progress is not None:
            progress({"phase": "update_loft_sections"})
        return {"feature_id": feature_id, "body_id": body["id"], "geometry": geometry}

    def _profile_geometry(
        self, document: Document, feature: dict[str, Any], parameters: dict[str, Any], *,
        profile: list[dict[str, float]] | None = None, plane: str | None = None,
        curve_points: list[dict[str, float]] | None = None,
        profiles: list[list[dict[str, float]]] | None = None,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        source_sketch_id = feature.get("source_sketch_id") or parameters.get("source_sketch_id")
        operation = feature.get("operation")
        if operation == "pad_sketch_to_body":
            sketch = next((item for item in document.objects if item.get("id") == source_sketch_id
                           and item.get("type") == "sketch"), None)
            if sketch is None:
                raise ValidationError("Pad feature source sketch is missing.")
            plane, profiles = closed_profiles(document.objects, source_sketch_id)
            depth = parameters["depth"]
            centered = parameters.get("centered", False)
            tool = (self.kernel.create_extruded_profile(profiles[0], plane=plane, depth=depth, centered=centered)
                    if len(profiles) == 1 else
                    self.kernel.create_extruded_profile_with_holes(profiles, plane=plane, depth=depth,
                                                                   centered=centered))
            axis = sketch.get("section_normal_axis")
            offset = sketch.get("section_offset", 0.0)
            if offset:
                shift = [0.0, 0.0, 0.0]
                shift[2 if plane == "XY" and axis == "Z" else 1] = offset
                tool = self.kernel.translate_solid(tool, shift)
            source_body = next((item for item in document.objects if item.get("id") == feature.get("source_body_id")
                                and item.get("type") == "body"), None)
            if source_body is None:
                raise ValidationError("Pad feature source body is missing.")
            return self.kernel.fuse_profiles(source_body["geometry"], tool)
        if operation != "loft_sketches" and (profile is None or plane is None):
            spline_profile = closed_spline_profile(document.objects, source_sketch_id)
            if spline_profile is None:
                if operation == "extrude_sketch":
                    plane, profiles = closed_profiles(document.objects, source_sketch_id)
                    profile = profiles[0]
                else:
                    plane, profile = closed_profile(document.objects, source_sketch_id)
                curve_points = None
            else:
                plane, profile, curve_points = spline_profile
        if operation == "extrude_sketch":
            if progress is not None:
                progress({"phase": "kernel_extrusion"})
            options = {"plane": plane, "depth": parameters["depth"],
                       "centered": parameters.get("centered", False)}
            if curve_points is not None:
                options["curve_points"] = curve_points
            elif profiles is not None and len(profiles) > 1:
                return self.kernel.create_extruded_profile_with_holes(
                    profiles, plane=plane, depth=parameters["depth"],
                    centered=parameters.get("centered", False),
                )
            return self.kernel.create_extruded_profile(profile, **options)
        if operation == "revolve_sketch":
            if progress is not None:
                progress({"phase": "kernel_revolution"})
            if plane != "XZ":
                raise ValidationError("Revolution requires a sketch on the XZ plane.")
            options = {"angle_degrees": parameters["angle_degrees"]}
            if curve_points is not None:
                options["curve_points"] = curve_points
            return self.kernel.create_revolved_profile(profile, **options)
        if operation == "loft_sketches":
            sketch_ids = parameters.get("section_sketch_ids")
            if (not isinstance(sketch_ids, list) or len(sketch_ids) < 2
                    or not isinstance(parameters.get("section_offsets"), list)):
                raise ValidationError("Loft feature section references are invalid.")
            groups = parameters.get("section_sketch_groups", [[value] for value in sketch_ids])
            sections, offsets = [], []
            for group in groups:
                group_profiles, group_offsets = [], []
                for sketch_id in group:
                    sketch = next((item for item in document.objects if item.get("id") == sketch_id), None)
                    if sketch is None:
                        raise ValidationError("Loft section sketch is missing.")
                    _, section = closed_profile(document.objects, sketch_id)
                    group_profiles.append(section)
                    group_offsets.append(sketch.get("section_offset"))
                if not group_offsets or any(value != group_offsets[0] for value in group_offsets):
                    raise ValidationError("Loft section group has inconsistent section offsets.")
                sections.append(group_profiles)
                offsets.append(group_offsets[0])
            if any(len(group) != 1 for group in sections):
                return self.kernel.create_multicontour_loft(sections, section_offsets=offsets)
            return self.kernel.create_lofted_profiles(
                [group[0] for group in sections], section_offsets=offsets,
            )
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

    def _rebuild_changed_features(
        self, document: Document, changed_sketches: set[str], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        by_id = {item["id"]: item for item in document.objects}
        changed_bodies: set[str] = set()
        features = [item for item in document.objects if item.get("type") == "feature"]
        for feature in features:
            source_sketch_ids = feature.get("source_sketch_ids", [])
            if feature.get("source_sketch_id"):
                source_sketch_ids = [feature["source_sketch_id"]]
            if (feature.get("operation") not in {"extrude_sketch", "revolve_sketch", "sweep_sketch", "loft_sketches", "pad_sketch_to_body"}
                    or not changed_sketches.intersection(source_sketch_ids)):
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
            elif feature.get("operation") == "loft_sketches":
                geometry = self._profile_geometry(document, feature, feature["parameters"], progress=progress)
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
            if operation in {"fillet_edges", "chamfer_edges"}:
                sources = {feature.get("source_body_id")}
            elif operation in {"linear_pattern_body", "circular_pattern_body", "hole", "boss", "pad_sketch_to_body"}:
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
            elif operation == "chamfer_edges":
                source = by_id.get(feature.get("source_body_id"))
                if source is None or source.get("type") != "body":
                    raise ValidationError("Chamfer feature refers to a missing source body.")
                from .advanced_features import _advanced_native
                geometry = _advanced_native(source["geometry"], 2,
                    [feature["parameters"]["distance"]], feature["parameters"]["edge_indices"])
            elif operation == "pad_sketch_to_body":
                geometry = self._profile_geometry(document, feature, feature["parameters"], progress=progress)
            elif operation in {"linear_pattern_body", "circular_pattern_body", "hole", "boss"}:
                source = by_id.get(feature.get("source_body_id"))
                if source is None or source.get("type") != "body":
                    raise ValidationError("Advanced feature refers to a missing source body.")
                parameters = feature["parameters"]
                if operation == "linear_pattern_body":
                    operation_id = 6
                    values = [float(parameters["count"]), *parameters["offset"]]
                elif operation == "circular_pattern_body":
                    operation_id = 7
                    values = [float(parameters["count"]), *parameters["axis_origin"],
                              *parameters["axis_direction"], parameters["angle_step_degrees"]]
                else:
                    operation_id = 9 if operation == "hole" else 10
                    values = [*parameters["origin"], *parameters["axis"],
                              parameters["diameter"], parameters["depth"]]
                from .advanced_features import _advanced_native
                geometry = _advanced_native(source["geometry"], operation_id, values)
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
        if (not isinstance(edges, list) or not edges
                or any(type(index) is not int or index < 0 for index in edges)
                or len(set(edges)) != len(edges)):
            raise ValidationError("edge_indices must contain distinct non-negative edge indices.")
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
