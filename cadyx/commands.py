"""Initial demonstration commands routed through one atomic command system."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .errors import NotFoundError, ValidationError
from .kernel import GeometryKernel
from .mesh_geometry import mesh_ply_bytes
from .models import Document
from .scan import DEFAULT_SCAN_PARAMETERS, ScanProcessor, normalize_scan_parameters
from .sketches import closed_profile, open_path


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a number.")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValidationError(f"{name} must be a finite positive number.")
    return result


class CommandSystem:
    def __init__(
        self,
        kernel: GeometryKernel,
        *,
        resolve_scan: Callable[[str, str], Path] | None = None,
        scan_processor: ScanProcessor | None = None,
        publish_scan_output: Callable[[str, str, bytes], dict[str, Any]] | None = None,
    ) -> None:
        self.kernel = kernel
        self.resolve_scan = resolve_scan
        self.scan_processor = scan_processor if scan_processor is not None else ScanProcessor()
        self.publish_scan_output = publish_scan_output

    def execute(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(command, dict):
            raise ValidationError("command must be a JSON object.")
        kind = command.get("type")
        if kind == "create_box":
            return self._create_box(document, command)
        if kind in {"extrude_sketch", "revolve_sketch"}:
            return self._create_profile_feature(document, command, kind, progress=progress)
        if kind == "fuse_bodies":
            return self._fuse_bodies(document, command, progress=progress)
        if kind == "sweep_sketch":
            return self._sweep_sketch(document, command, progress=progress)
        if kind == "fillet_edges":
            return self._fillet_edges(document, command, progress=progress)
        if kind == "set_parameter":
            return self._set_parameter(document, command, progress=progress)
        if kind == "import_scan":
            return self._import_scan(document, command, progress=progress)
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
        return {"feature_id": feature_id, "parameter": parameter_name, "value": value, "geometry": geometry}

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
            elif operation == "fuse_bodies":
                sources = {feature.get("base_body_id"), feature.get("tool_body_id")}
            else:
                continue
            if not sources & rebuilt_bodies:
                continue
            body = by_id.get(feature.get("body_id"))
            if body is None or body.get("type") != "body":
                raise ValidationError("Derived feature refers to a missing result body.")
            if progress is not None:
                progress({"phase": "kernel_boolean_fuse" if operation == "fuse_bodies" else "kernel_fillet"})
            if operation == "fillet_edges":
                source = by_id.get(feature.get("source_body_id"))
                if source is None or source.get("type") != "body":
                    raise ValidationError("Fillet feature refers to a missing source body.")
                geometry = self.kernel.create_fillet(source["geometry"],
                    edge_indices=feature["edge_indices"], radius=feature["parameters"]["radius"])
            else:
                base, tool = by_id.get(feature.get("base_body_id")), by_id.get(feature.get("tool_body_id"))
                if any(item is None or item.get("type") != "body" for item in (base, tool)):
                    raise ValidationError("Boolean fuse feature refers to a missing source body.")
                geometry = self.kernel.fuse_profiles(base["geometry"], tool["geometry"])
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

    @staticmethod
    def _consumed_body_ids(document: Document) -> set[str]:
        consumed: set[str] = set()
        for feature in document.objects:
            if feature.get("type") != "feature":
                continue
            if feature.get("operation") == "fuse_bodies":
                consumed.update((feature.get("base_body_id"), feature.get("tool_body_id")))
            elif feature.get("operation") == "fillet_edges":
                consumed.add(feature.get("source_body_id"))
        return consumed - {None}

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
