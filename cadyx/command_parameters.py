"""Parameters command handlers and implementation.

Core owns lease/revision checks and atomic persistence; these methods operate on
the staged Document supplied by the shared CommandSystem.
"""
from __future__ import annotations

import math
import re
from copy import deepcopy
from typing import Any, Callable
from uuid import uuid4
from .errors import NotFoundError, ValidationError
from .model_parameters import assign_property, evaluate_expression, property_location
from .models import Document
from .sketches import closed_profile, closed_spline_profile, open_path
from .command_values import _finite_number, _number


class ParameterCommands:
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
                        original = {point["id"]: (point["x"], point["y"]) for point in sketch_points}
                        for point in sketch_points:
                            point["x"] *= scale
                        def ellipse_point(cx, cy, major, minor, rotation, angle):
                            cosine, sine = math.cos(rotation), math.sin(rotation)
                            return (cx + major * math.cos(angle) * cosine - minor * math.sin(angle) * sine,
                                    cy + major * math.cos(angle) * sine + minor * math.sin(angle) * cosine)
                        def ellipse_parameter(x, y, cx, cy, major, minor, rotation):
                            cosine, sine = math.cos(rotation), math.sin(rotation)
                            dx, dy = x - cx, y - cy
                            return math.atan2((-sine * dx + cosine * dy) / minor,
                                              (cosine * dx + sine * dy) / major)
                        for arc in document.objects:
                            if (arc.get("sketch_id") != sketch_id
                                    or arc.get("type") not in {"circle", "ellipse"}
                                    or "sweep_radians" not in arc):
                                continue
                            if arc["type"] == "circle":
                                cx, cy = original[arc["center_id"]]
                                major = minor = arc["radius"]
                                rotation = 0.0
                                sx, sy = original[arc["start_id"]]
                                start = math.atan2(sy - cy, sx - cx)
                            else:
                                cx, cy = arc["center_x"], arc["center_y"]
                                major, minor = arc["major_radius"], arc["minor_radius"]
                                rotation = arc["rotation_radians"]
                                start = arc["start_angle_radians"]
                            sweep = arc["sweep_radians"]
                            cosine, sine = math.cos(rotation), math.sin(rotation)
                            qxx = (major * major * cosine * cosine + minor * minor * sine * sine) * scale * scale
                            qxy = (major * major - minor * minor) * cosine * sine * scale
                            qyy = major * major * sine * sine + minor * minor * cosine * cosine
                            trace = qxx + qyy
                            discriminant = math.hypot(qxx - qyy, 2 * qxy)
                            major_squared = (trace + discriminant) / 2
                            minor_squared = (trace - discriminant) / 2
                            if minor_squared <= 0 or not math.isfinite(major_squared):
                                raise ValidationError("Diameter scaling produced a degenerate profile arc.")
                            new_major, new_minor = math.sqrt(major_squared), math.sqrt(minor_squared)
                            new_rotation = (0.0 if discriminant <= 1e-14 * max(1.0, trace)
                                            else 0.5 * math.atan2(2 * qxy, qxx - qyy))
                            new_cx, new_cy = cx * scale, cy
                            old_end = start + sweep
                            start_point = ellipse_point(cx, cy, major, minor, rotation, start)
                            end_point = ellipse_point(cx, cy, major, minor, rotation, old_end)
                            new_start = ellipse_parameter(start_point[0] * scale, start_point[1],
                                                          new_cx, new_cy, new_major, new_minor, new_rotation)
                            new_end = ellipse_parameter(end_point[0] * scale, end_point[1],
                                                        new_cx, new_cy, new_major, new_minor, new_rotation)
                            if abs(abs(sweep) - math.tau) <= 1e-9:
                                new_sweep = math.copysign(math.tau, sweep)
                            elif sweep > 0:
                                new_sweep = (new_end - new_start) % math.tau
                            else:
                                new_sweep = -((new_start - new_end) % math.tau)
                            if abs(new_sweep) <= 1e-10:
                                raise ValidationError("Diameter scaling collapsed a profile arc sweep.")
                            if arc["type"] == "circle":
                                arc["id"] = "ellipse:" + arc["id"].split(":", 1)[1]
                            arc.pop("radius", None)
                            arc.pop("center_id", None)
                            arc.update(type="ellipse", center_x=new_cx, center_y=new_cy,
                                       major_radius=new_major, minor_radius=new_minor,
                                       rotation_radians=new_rotation, start_angle_radians=new_start,
                                       sweep_radians=new_sweep)
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

    @staticmethod
    def _model_parameter_values(document: Document) -> dict[str, float]:
        return {name: float(record["value"]) for name, record in document.model_parameters.items()}

    def _create_model_parameter(self, document: Document, command: dict[str, Any]) -> dict[str, Any]:
        name = command.get("name")
        if not isinstance(name, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None:
            raise ValidationError("Model parameter name must be a valid identifier.")
        if name in document.model_parameters:
            raise ValidationError(f"Model parameter {name} already exists.")
        value = _finite_number(command.get("value"), "Model parameter value")
        unit = command.get("unit", "mm")
        if unit not in {"mm", "deg", "unitless"}:
            raise ValidationError("Model parameter unit must be mm, deg, or unitless.")
        document.model_parameters[name] = {"value": value, "unit": unit}
        return {"name": name, "value": value, "unit": unit}

    def _create_model_parameters(self, document: Document, command: dict[str, Any]) -> dict[str, Any]:
        records = command.get("parameters")
        if not isinstance(records, dict) or not records:
            raise ValidationError("parameters must be a non-empty map of model parameter records.")
        staged = {}
        for name, record in records.items():
            if not isinstance(name, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None:
                raise ValidationError("Model parameter names must be valid identifiers.")
            if name in document.model_parameters or name in staged:
                raise ValidationError(f"Model parameter {name} already exists.")
            if not isinstance(record, dict) or set(record) - {"value", "unit"} or "value" not in record:
                raise ValidationError(f"Model parameter {name} needs a value and optional unit.")
            value = _finite_number(record["value"], "Model parameter value")
            unit = record.get("unit", "mm")
            if unit not in {"mm", "deg", "unitless"}:
                raise ValidationError("Model parameter unit must be mm, deg, or unitless.")
            staged[name] = {"value": value, "unit": unit}
        document.model_parameters.update(staged)
        return {"parameters": staged}

    @staticmethod
    def _commit_staged_document(document: Document, staged: Document) -> None:
        document.objects = staged.objects
        document.model_parameters = staged.model_parameters
        document.parameter_bindings = staged.parameter_bindings

    def _set_model_parameter(
        self, document: Document, command: dict[str, Any], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        name = command.get("name")
        if not isinstance(name, str) or name not in document.model_parameters:
            raise NotFoundError("Model parameter was not found.")
        value = _finite_number(command.get("value"), "Model parameter value")
        staged = deepcopy(document)
        self._prepare_edge_references(staged)
        staged.model_parameters[name]["value"] = value
        changed_features: set[str] = set()
        changed_sketches: set[str] = set()
        by_id = {item["id"]: item for item in staged.objects}
        values = self._model_parameter_values(staged)
        changed_targets: set[str] = set()
        resolved: list[tuple[dict[str, Any], str, float]] = []
        for binding in staged.parameter_bindings:
            result = evaluate_expression(binding["expression"], values)
            target = by_id[binding["object_id"]]
            parent, final = property_location(target, binding["path"])
            if not math.isclose(float(parent[final]), result, rel_tol=0.0, abs_tol=1e-12):
                resolved.append((target, binding["path"], result))
        for target, path, result in resolved:
            assign_property(target, path, result)
            changed_targets.add(target["id"])
            if target["type"] == "feature":
                changed_features.add(target["id"])
            elif target["type"] == "sketch":
                changed_sketches.add(target["id"])
            else:
                sketch_id = target.get("sketch_id")
                if isinstance(sketch_id, str):
                    changed_sketches.add(sketch_id)
        if progress is not None:
            progress({"phase": "rebuild_model_parameters", "parameter": name,
                      "changed_targets": len(changed_targets)})
        self._rebuild_parameter_targets(staged, changed_features, changed_sketches, progress=progress)
        staged.validate()
        self._commit_staged_document(document, staged)
        return {"name": name, "value": value, "unit": staged.model_parameters[name]["unit"],
                "changed_object_ids": sorted(changed_targets)}

    def _bind_model_parameter(
        self, document: Document, command: dict[str, Any], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        object_id, path, expression = command.get("object_id"), command.get("path"), command.get("expression")
        if not isinstance(object_id, str) or not isinstance(path, str) or not isinstance(expression, str):
            raise ValidationError("Parameter binding needs object_id, path, and expression strings.")
        staged = deepcopy(document)
        self._prepare_edge_references(staged)
        target = next((item for item in staged.objects if item.get("id") == object_id), None)
        if target is None:
            raise NotFoundError("Parameter binding target was not found.")
        result = evaluate_expression(expression, self._model_parameter_values(staged))
        parent, final = property_location(target, path)
        parent[final] = result
        staged.parameter_bindings = [binding for binding in staged.parameter_bindings
                                     if (binding["object_id"], binding["path"]) != (object_id, path)]
        staged.parameter_bindings.append({"object_id": object_id, "path": path,
                                          "expression": expression})
        if target["type"] == "feature":
            source_id = target.get("source_body_id")
            if isinstance(source_id, str):
                self._rebuild_derived_features(staged, {source_id}, progress=progress)
            else:
                self._rebuild_bound_feature(staged, object_id, progress=progress)
        else:
            sketch_id = object_id if target["type"] == "sketch" else target.get("sketch_id")
            if isinstance(sketch_id, str):
                self._rebuild_parameter_targets(staged, set(), {sketch_id}, progress=progress)
        staged.validate()
        self._commit_staged_document(document, staged)
        return {"object_id": object_id, "path": path, "expression": expression, "value": result}

    def _bind_model_parameters(
        self, document: Document, command: dict[str, Any], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        bindings = command.get("bindings")
        if not isinstance(bindings, list) or not bindings:
            raise ValidationError("bindings must be a non-empty list.")
        staged = deepcopy(document)
        self._prepare_edge_references(staged)
        by_id = {item["id"]: item for item in staged.objects}
        values = self._model_parameter_values(staged)
        replacements: list[tuple[dict[str, Any], str, float]] = []
        next_bindings = list(staged.parameter_bindings)
        seen: set[tuple[str, str]] = set()
        for binding in bindings:
            if not isinstance(binding, dict) or set(binding) != {"object_id", "path", "expression"}:
                raise ValidationError("Each binding needs object_id, path, and expression.")
            object_id, path, expression = binding["object_id"], binding["path"], binding["expression"]
            if not all(isinstance(value, str) for value in (object_id, path, expression)):
                raise ValidationError("Binding object_id, path, and expression must be strings.")
            key = (object_id, path)
            if key in seen:
                raise ValidationError("A parameter binding target may occur only once per command.")
            seen.add(key)
            target = by_id.get(object_id)
            if target is None:
                raise NotFoundError("Parameter binding target was not found.")
            result = evaluate_expression(expression, values)
            parent, final = property_location(target, path)
            replacements.append((target, path, result))
            next_bindings = [item for item in next_bindings
                             if (item["object_id"], item["path"]) != key]
            next_bindings.append({"object_id": object_id, "path": path, "expression": expression})
        for target, path, result in replacements:
            assign_property(target, path, result)
        staged.parameter_bindings = next_bindings
        changed_features = {target["id"] for target, _, _ in replacements if target["type"] == "feature"}
        changed_sketches = {target["id"] if target["type"] == "sketch" else target.get("sketch_id")
                            for target, _, _ in replacements if target["type"] in {"sketch", "point", "circle", "ellipse"}}
        changed_sketches.discard(None)
        self._rebuild_parameter_targets(staged, changed_features, changed_sketches, progress=progress)
        staged.validate()
        self._commit_staged_document(document, staged)
        return {"bindings": [{"object_id": target["id"], "path": path, "value": value}
                              for target, path, value in replacements]}

    def _rebuild_parameter_targets(
        self, document: Document, feature_ids: set[str], sketch_ids: set[str], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        by_id = {item["id"]: item for item in document.objects}
        features = [by_id[feature_id] for feature_id in feature_ids]
        feature_order = {item["id"]: index for index, item in enumerate(document.objects)}
        produced_bodies = {feature.get("body_id") for feature in features}
        roots = [feature for feature in features if feature.get("source_body_id") not in produced_bodies]
        if sketch_ids:
            self._regenerate_generated_gear_profiles(document, sketch_ids, progress=progress)
            from .sketch_solver import solve_sketch
            for sketch_id in sorted(sketch_ids):
                solve_sketch(document.objects, sketch_id, document.parameter_bindings)
            self._rebuild_changed_features(document, sketch_ids, progress=progress)
        changed_bodies: set[str] = set()
        for feature in sorted(roots, key=lambda item: feature_order[item["id"]]):
            source_id = feature.get("source_body_id")
            if isinstance(source_id, str):
                changed_bodies.add(source_id)
                continue
            self._rebuild_bound_feature(document, feature["id"], progress=progress,
                                        rebuild_derived=False)
            body_id = feature.get("body_id")
            if isinstance(body_id, str):
                changed_bodies.add(body_id)
        self._rebuild_derived_features(document, changed_bodies, progress=progress)

    def _regenerate_generated_gear_profiles(
        self,
        document: Document,
        sketch_ids: set[str],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        from .engineering import involute_internal_gear_profile, involute_spur_gear_profile

        by_id = {item["id"]: item for item in document.objects}
        for sketch_id in sketch_ids:
            sketch = by_id.get(sketch_id)
            if not isinstance(sketch, dict) or sketch.get("type") != "sketch":
                continue
            generated = sketch.get("generated_gear_profile")
            if not isinstance(generated, dict):
                continue
            operation = generated.get("operation")
            parameters = generated.get("parameters")
            old_points = generated.get("point_ids")
            old_lines = generated.get("line_ids")
            spline_id = generated.get("spline_id")
            if (operation not in {"add_sketch_involute_gear", "add_sketch_internal_involute_gear"}
                    or not isinstance(parameters, dict) or not isinstance(old_points, list)
                    or (not isinstance(old_lines, list) and not isinstance(spline_id, str))):
                raise ValidationError("Generated gear sketch metadata is invalid.")
            input_keys = {
                "module", "tooth_count", "pressure_angle_degrees", "center_x", "center_y",
                "rotation_degrees", "backlash",
            }
            gear_parameters = {key: parameters[key] for key in input_keys if key in parameters}
            tooth_count = gear_parameters.get("tooth_count")
            if (isinstance(tooth_count, bool) or not isinstance(tooth_count, (int, float))
                    or not math.isfinite(tooth_count) or tooth_count < 3
                    or not float(tooth_count).is_integer()):
                raise ValidationError("Generated gear tooth_count must remain an integer.")
            gear_parameters["tooth_count"] = int(tooth_count)
            generator = (involute_spur_gear_profile if operation == "add_sketch_involute_gear"
                         else involute_internal_gear_profile)
            points, rebuilt_parameters = generator(**gear_parameters)
            segments = len(points)
            point_ids = list(old_points[:segments])
            point_ids.extend(f"point:{uuid4()}" for _ in range(segments - len(point_ids)))
            if not isinstance(spline_id, str):
                spline_id = f"spline:{uuid4()}"

            if progress is not None:
                progress({"phase": "rebuild_generated_gear_profile"})
            retained_ids = set(point_ids) | {spline_id}
            removed_ids = set(old_points) | set(old_lines or [])
            document.objects[:] = [
                item for item in document.objects
                if item.get("id") not in removed_ids or item.get("id") in retained_ids
            ]
            by_id = {item["id"]: item for item in document.objects}
            elements = []
            for point_id, (x, y) in zip(point_ids, points, strict=True):
                item = by_id.get(point_id)
                if item is None:
                    item = {"id": point_id, "type": "point", "sketch_id": sketch_id}
                    elements.append(item)
                item.update({"x": x, "y": y})
            spline = by_id.get(spline_id)
            if spline is None:
                spline = {"id": spline_id, "type": "spline", "sketch_id": sketch_id,
                          "point_ids": point_ids, "mode": "closed"}
                elements.append(spline)
            elif spline.get("type") != "spline":
                raise ValidationError("Generated gear spline identifier has the wrong object type.")
            else:
                spline["point_ids"] = point_ids
            if elements:
                sketch_index = document.objects.index(sketch)
                document.objects[sketch_index + 1:sketch_index + 1] = elements
            sketch["generated_gear_profile"] = {
                "operation": operation,
                "parameters": rebuilt_parameters,
                "point_ids": point_ids,
                "spline_id": spline_id,
            }

    def _rebuild_bound_feature(
        self, document: Document, feature_id: str, *,
        progress: Callable[[dict[str, Any]], None] | None = None,
        rebuild_derived: bool = True,
    ) -> None:
        feature = next((item for item in document.objects
                        if item.get("id") == feature_id and item.get("type") == "feature"), None)
        if feature is None:
            raise NotFoundError("Bound feature was not found.")
        operation = feature.get("operation")
        parameters = feature.get("parameters")
        body = next((item for item in document.objects
                     if item.get("id") == feature.get("body_id") and item.get("type") == "body"), None)
        if body is None or not isinstance(parameters, dict):
            raise ValidationError("Bound feature is missing its result body or parameters.")
        if operation == "create_box":
            geometry = self.kernel.create_box(**parameters)
        elif operation in {"extrude_sketch", "revolve_sketch", "sweep_sketch", "pad_sketch_to_body"}:
            geometry = (self.kernel.create_swept_pipe(
                open_path(document.objects, feature.get("source_sketch_id"))[1],
                plane=open_path(document.objects, feature.get("source_sketch_id"))[0],
                radius=parameters["radius"],
            ) if operation == "sweep_sketch" else
                self._profile_geometry(document, feature, parameters, progress=progress))
        elif operation == "extrude_profile_on_plane":
            from .agent_authoring import _agent_native, _vector, _workplane_rotation
            profile = parameters.get("profile")
            if not isinstance(profile, list):
                raise ValidationError("Arbitrary-plane extrusion is missing its editable profile.")
            depth = _number(parameters.get("depth"), "depth")
            origin = _vector(parameters.get("origin"), "origin")
            normal = _vector(parameters.get("normal"), "normal", nonzero=True)
            x_axis = _vector(parameters.get("x_axis"), "x_axis", nonzero=True)
            centered = parameters.get("centered", False)
            if type(centered) is not bool:
                raise ValidationError("centered must be a boolean.")
            temporary = self.kernel.create_extruded_profile(
                profile, plane="XY", depth=depth, centered=centered,
            )
            rotation_axis, angle, _, _ = _workplane_rotation(normal, x_axis)
            geometry = _agent_native(temporary, 2, [*rotation_axis, angle, *origin])
        elif operation in {"boss", "hole", "linear_pattern_body", "circular_pattern_body"}:
            source = next((item for item in document.objects
                           if item.get("id") == feature.get("source_body_id") and item.get("type") == "body"), None)
            if source is None:
                raise ValidationError("Bound feature source body was not found.")
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
            from .advanced_features import _advanced_native
            geometry = _advanced_native(source["geometry"], operation_id, values)
        else:
            raise ValidationError(f"Model parameter binding is not yet supported for {operation!r} features.")
        body["geometry"] = geometry
        feature["geometry"] = geometry
        if rebuild_derived:
            self._rebuild_derived_features(document, {body["id"]}, progress=progress)


def _handle_create_model_parameter(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._create_model_parameter(document, command)


def _handle_create_model_parameters(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._create_model_parameters(document, command)


def _handle_set_model_parameter(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._set_model_parameter(document, command, progress=progress)


def _handle_bind_model_parameter(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._bind_model_parameter(document, command, progress=progress)


def _handle_bind_model_parameters(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._bind_model_parameters(document, command, progress=progress)


def _handle_set_parameter(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._set_parameter(document, command, progress=progress)


COMMAND_HANDLERS = {
    "create_model_parameter": _handle_create_model_parameter,
    "create_model_parameters": _handle_create_model_parameters,
    "set_model_parameter": _handle_set_model_parameter,
    "bind_model_parameter": _handle_bind_model_parameter,
    "bind_model_parameters": _handle_bind_model_parameters,
    "set_parameter": _handle_set_parameter,
}
