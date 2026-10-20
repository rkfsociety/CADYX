"""Scan command handlers and implementation.

Core owns lease/revision checks and atomic persistence; these methods operate on
the staged Document supplied by the shared CommandSystem.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
from .errors import NotFoundError, ValidationError
from .mesh_geometry import mesh_ply_bytes
from .models import Document
from .scan import DEFAULT_SCAN_PARAMETERS, normalize_scan_parameters, read_point_cloud_ply


class ScanCommands:
    def _process_point_cloud(
        self, document: Document, command: dict[str, Any], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        from .point_cloud import normalize_cloud_parameters, process_points
        from .scan import _sha256_file
        body = next((o for o in document.objects if o["id"] == command.get("body_id") and o["type"] == "body"), None)
        if body is None or body.get("geometry", {}).get("kind") != "point_cloud":
            raise ValidationError("Processing requires a saved point-cloud body.")
        previous = next((o for o in reversed(document.objects) if o["id"] in body["feature_ids"]
                         and o.get("operation") in {"import_point_cloud", "process_point_cloud"}), None)
        if previous is None or project_id is None or self.publish_project_scan_output is None:
            raise ValidationError("Processing requires a source-linked project cloud and artifact writer.")
        method = command.get("method")
        parameters = normalize_cloud_parameters(method, command.get("parameters", {}))
        source = previous.get("artifact") or previous["source"]
        filename = source.get("project_file", source.get("file"))
        path = self._scan_source_path({"project_file": filename}, project_id)
        if progress:
            progress({"phase": "read_point_cloud"})
        points, digest, _ = read_point_cloud_ply(path, full_resolution=True)
        if digest != source.get("sha256"):
            raise ValidationError("Point-cloud source changed; no processing was saved.")
        if progress:
            progress({"phase": "filter_point_cloud"})
        result = process_points(points, method, parameters)
        # The artifact contains every output point; only the document/view uses a sample.
        full_geometry = self.kernel.create_point_cloud(result)
        sample = result[::max(1, (len(result) + 124999) // 125000)]
        if len(sample) < len(result) and len(sample) >= 6:
            for axis in range(3):
                sample[axis * 2] = min(result, key=lambda point: point[axis])
                sample[axis * 2 + 1] = max(result, key=lambda point: point[axis])
        geometry = self.kernel.create_point_cloud(sample)
        stats = {"input_points": len(points), "output_points": len(result),
                 "removed_points": len(points) - len(result), "display_sample_count": len(sample),
                 "bounds": {"min": [min(v[a] for v in result) for a in range(3)],
                            "max": [max(v[a] for v in result) for a in range(3)]},
                 "coordinate_units": "source_unverified", "scale_status": "unverified"}
        original = next(o for o in document.objects if o["id"] in body["feature_ids"]
                        and o.get("operation") == "import_point_cloud")
        output_name = original["source"]["project_file"]
        artifact = self._publish_scan_source_output({"project_file": filename}, output_name, full_geometry, progress, project_id)
        artifact_path = self._scan_source_path({"project_file": artifact["file"]}, project_id)
        artifact = {**artifact, "sha256": _sha256_file(artifact_path)}
        feature_id = f"feature:{uuid4()}"
        feature = {"id": feature_id, "type": "feature", "operation": "process_point_cloud",
                   "body_id": body["id"], "previous_feature_id": previous["id"],
                   "method": method, "parameters": parameters,
                   "source": {"project_file": filename, "sha256": digest}, "artifact": artifact,
                   "stats": stats, "processor": {"name": "CADYX point-cloud tools", "version": 1}}
        body["geometry"] = geometry
        body["feature_ids"].append(feature_id)
        document.objects.append(feature)
        return {"body_id": body["id"], "feature_id": feature_id, "method": method, "stats": stats, "artifact": artifact}

    def _scan_source_path(self, source: dict[str, Any], project_id: str | None) -> Path:
        if all(isinstance(source.get(key), str) for key in ("folder", "file")):
            if self.resolve_scan is None:
                raise ValidationError("Scan import requires a workspace file resolver.")
            return self.resolve_scan(source["folder"], source["file"])
        filename = source.get("project_file")
        if isinstance(filename, str) and project_id is not None and self.resolve_project_file is not None:
            return self.resolve_project_file(project_id, filename)
        raise ValidationError("Scan source metadata is invalid or its project file is unavailable.")

    def _publish_scan_source_output(
        self,
        source: dict[str, Any],
        source_filename: str,
        geometry: dict[str, Any],
        progress: Callable[[dict[str, Any]], None] | None,
        project_id: str | None,
    ) -> dict[str, Any]:
        if "project_file" in source:
            if self.publish_project_scan_output is None or project_id is None:
                raise ValidationError("Project scan output requires a project artifact writer.")
            if progress is not None:
                progress({"phase": "write_output"})
            return self.publish_project_scan_output(project_id, source_filename, mesh_ply_bytes(geometry))
        return self._publish_scan_output(source["folder"], source_filename, geometry, progress)

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
        folder, filename = command.get("folder"), command.get("file")
        if self.resolve_scan is None or not isinstance(folder, str) or not isinstance(filename, str):
            raise ValidationError("Scan import requires a workspace folder and PLY filename.")
        return self._import_scan_source(
            document, command, {"folder": folder, "file": filename},
            self.resolve_scan(folder, filename), progress=progress, project_id=None,
        )

    def _import_project_scan(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        filename = command.get("file")
        if (self.resolve_project_file is None or project_id is None or not isinstance(filename, str)
                or Path(filename).suffix.casefold() != ".ply"):
            raise ValidationError("Project scan import requires a staged PLY filename and project context.")
        source = {"project_file": filename}
        return self._import_scan_source(
            document, command, source, self.resolve_project_file(project_id, filename),
            progress=progress, project_id=project_id,
        )

    def _import_project_point_cloud(
        self,
        document: Document,
        command: dict[str, Any],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        filename = command.get("file")
        if (self.resolve_project_file is None or project_id is None or not isinstance(filename, str)
                or Path(filename).suffix.casefold() != ".ply"):
            raise ValidationError("Point-cloud import requires a staged PLY filename and project context.")
        name = command.get("name", Path(filename).stem)
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValidationError("Point-cloud name must be a non-empty string of at most 120 characters.")
        if progress is not None:
            progress({"phase": "read_point_cloud"})
        points, source_hash, stats = read_point_cloud_ply(
            self.resolve_project_file(project_id, filename),
        )
        stats.update({"coordinate_units": "source_unverified", "scale_status": "unverified"})
        geometry = self.kernel.create_point_cloud(points)
        body_id = f"body:{uuid4()}"
        feature_id = f"feature:{uuid4()}"
        source = {"project_file": filename, "sha256": source_hash}
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
                "operation": "import_point_cloud",
                "body_id": body_id,
                "source": source,
                "source_kind": "point_cloud",
                "stats": stats,
                "processor": {"name": "CADYX point-cloud codec", "version": 1},
            },
        ])
        return {
            "body_id": body_id,
            "feature_id": feature_id,
            "source": source,
            "source_kind": "point_cloud",
            "point_count": stats["point_count"],
            "bounds": stats["bounds"],
        }

    def _import_scan_source(
        self,
        document: Document,
        command: dict[str, Any],
        source: dict[str, Any],
        source_path: Path,
        *,
        progress: Callable[[dict[str, Any]], None] | None,
        project_id: str | None,
    ) -> dict[str, Any]:
        filename = source.get("file", source.get("project_file"))
        name = command.get("name", Path(filename).stem)
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValidationError("Scan name must be a non-empty string of at most 120 characters.")
        parameters = normalize_scan_parameters(command.get("parameters", {}))
        geometry, stats, source_hash = self.scan_processor.reconstruct(source_path, parameters, progress=progress)
        if stats["source_kind"] == "triangle_mesh" and any(
            parameters[key] != DEFAULT_SCAN_PARAMETERS[key]
            for key in ("reconstruction_method", "outlier_neighbors", "outlier_std_ratio",
                        "poisson_depth", "poisson_density_quantile")
        ):
            raise ValidationError("Outlier and Poisson parameters apply only to PLY point clouds.")

        body_id = f"body:{uuid4()}"
        feature_id = f"feature:{uuid4()}"
        artifact = self._publish_scan_source_output(source, filename, geometry, progress, project_id)
        source = {**source, "sha256": source_hash}
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
        project_id: str | None = None,
    ) -> dict[str, Any]:
        if self.resolve_scan is None and self.resolve_project_file is None:
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
                parameter_name in {"reconstruction_method", "outlier_neighbors", "outlier_std_ratio",
                                   "poisson_depth", "poisson_density_quantile"}):
            raise ValidationError("Outlier and Poisson parameters apply only to PLY point clouds.")
        feature_parameters = feature.get("parameters")
        if not isinstance(feature_parameters, dict):
            raise ValidationError("Scan feature parameters are invalid.")
        if (feature_parameters.get("reconstruction_method", "poisson") == "ball_pivoting" and
                parameter_name in {"poisson_depth", "poisson_density_quantile"}):
            raise ValidationError("Poisson parameters apply only to the Poisson reconstruction method.")
        parameters = normalize_scan_parameters({
            **feature_parameters, parameter_name: command.get("value"),
        })
        source = feature.get("source")
        if not isinstance(source, dict) or not isinstance(source.get("sha256"), str):
            raise ValidationError("Scan feature source metadata is invalid.")
        body = next(
            (item for item in document.objects
             if item.get("id") == feature.get("body_id") and item.get("type") == "body"),
            None,
        )
        if body is None:
            raise ValidationError("Scan feature refers to a missing body.")
        source_path = self._scan_source_path(source, project_id)
        geometry, stats, source_hash = self.scan_processor.reconstruct(
            source_path, parameters, expected_sha256=source["sha256"], progress=progress,
        )
        source_filename = source.get("file", source.get("project_file"))
        artifact = self._publish_scan_source_output(source, source_filename, geometry, progress,
                                                     project_id)
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


def _handle_import_scan(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._import_scan(document, command, progress=progress)


def _handle_import_project_scan(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._import_project_scan(document, command, progress=progress, project_id=project_id)


def _handle_import_project_point_cloud(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._import_project_point_cloud(document, command, progress=progress, project_id=project_id)


def _handle_set_scan_parameter(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._set_scan_parameter(document, command, progress=progress, project_id=project_id)


def _handle_process_point_cloud(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    return self._process_point_cloud(document, command, progress=progress, project_id=project_id)


COMMAND_HANDLERS = {
    "process_point_cloud": _handle_process_point_cloud,
    "import_scan": _handle_import_scan,
    "import_project_scan": _handle_import_project_scan,
    "import_project_point_cloud": _handle_import_project_point_cloud,
    "set_scan_parameter": _handle_set_scan_parameter,
}
