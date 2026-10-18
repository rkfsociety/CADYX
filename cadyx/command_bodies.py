"""Bodies command handlers and implementation.

Core owns lease/revision checks and atomic persistence; these methods operate on
the staged Document supplied by the shared CommandSystem.
"""
from __future__ import annotations

import math
from typing import Any, Callable
from uuid import uuid4
from .errors import NotFoundError, ValidationError
from .models import Document
from .sketches import closed_profile, closed_profiles, closed_spline_profile, generated_gear_curve_profiles, open_path
from .command_values import _finite_number, _number


class BodyCommands:
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
        curve_profiles = None
        if spline_profile is None:
            if operation == "extrude_sketch":
                plane, profiles = closed_profiles(document.objects, sketch_id)
                profile = profiles[0]
                curve_profiles = generated_gear_curve_profiles(document.objects, sketch_id, profiles)
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
        }, parameters, profile=profile, plane=plane, curve_points=curve_points,
            profiles=profiles, curve_profiles=curve_profiles)
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


def _handle_create_box(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._create_box(document, command)


def _handle_create_metric_thread(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._create_metric_thread(document, command, progress=progress)


def _handle_profile_feature(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    kind = command["type"]
    return self._create_profile_feature(document, command, kind, progress=progress)


def _handle_loft_sketches(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._loft_sketches(document, command, progress=progress)


def _handle_create_ruled_shell(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._create_ruled_shell(document, command, progress=progress)


def _handle_update_loft_sections(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._update_loft_sections(document, command, progress=progress)


def _handle_fuse_bodies(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._fuse_bodies(document, command, progress=progress)


def _handle_cut_bodies(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._cut_bodies(document, command, progress=progress)


def _handle_sweep_sketch(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._sweep_sketch(document, command, progress=progress)


def _handle_fillet_edges(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._fillet_edges(document, command, progress=progress)


def _handle_resize_imported_thread(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._resize_imported_thread(document, command, progress=progress)


def _handle_transform_imported_brep(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._transform_imported_brep(document, command, progress=progress)


COMMAND_HANDLERS = {
    "create_box": _handle_create_box,
    "create_metric_thread": _handle_create_metric_thread,
    "extrude_sketch": _handle_profile_feature,
    "revolve_sketch": _handle_profile_feature,
    "loft_sketches": _handle_loft_sketches,
    "create_ruled_shell": _handle_create_ruled_shell,
    "update_loft_sections": _handle_update_loft_sections,
    "fuse_bodies": _handle_fuse_bodies,
    "cut_bodies": _handle_cut_bodies,
    "sweep_sketch": _handle_sweep_sketch,
    "fillet_edges": _handle_fillet_edges,
    "resize_imported_thread": _handle_resize_imported_thread,
    "transform_imported_brep": _handle_transform_imported_brep,
}
