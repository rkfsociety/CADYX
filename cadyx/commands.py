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
        if kind == "set_parameter":
            return self._set_parameter(document, command)
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
            return {"object_ids": sorted(seen)}
        if kind == "remove_sketch_object":
            object_id = command.get("object_id")
            item = next((item for item in document.objects if item["id"] == object_id), None)
            if item is None or item["type"] not in {"sketch", "point", "line", "circle", "constraint"}:
                raise NotFoundError("Sketch object was not found.")
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

    def _set_parameter(self, document: Document, command: dict[str, Any]) -> dict[str, Any]:
        feature_id = command.get("feature_id")
        parameter_name = command.get("name")
        if parameter_name not in {"width", "height", "depth"}:
            raise ValidationError("Only box width, height, and depth parameters are supported.")
        feature = next(
            (item for item in document.objects if item.get("id") == feature_id and item.get("type") == "feature"),
            None,
        )
        if feature is None or feature.get("operation") != "create_box":
            raise NotFoundError("Editable box feature was not found.")
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
