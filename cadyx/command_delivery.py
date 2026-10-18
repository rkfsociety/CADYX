"""Delivery command handlers and implementation.

Core owns lease/revision checks and atomic persistence; these methods operate on
the staged Document supplied by the shared CommandSystem.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
from .errors import NotFoundError, ValidationError
from .delivery import compare_step_metrics, geometry_sha256, verify_binary_stl
from .models import Document
from .command_values import _finite_number, _number


class DeliveryCommands:
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
            raise ValidationError("STEP source exceeds the 64 MB limit.", code="RESOURCE_LIMIT")
        if progress is not None:
            progress({"phase": "read_step_source"})
        source = source_path.read_bytes()
        if not source or len(source) > 64 * 1024 * 1024:
            raise ValidationError("STEP source is empty or exceeds the 64 MB limit.",
                                  code="RESOURCE_LIMIT" if len(source) > 64 * 1024 * 1024 else "KERNEL_OPERATION_FAILED")
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
            raise ValidationError("STEP output is empty or exceeds the existing 64 MB round-trip limit.",
                                  code="RESOURCE_LIMIT" if isinstance(step_data, bytes) and len(step_data) > 64 * 1024 * 1024 else "EXPORT_VERIFICATION_FAILED")
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


def _handle_import_step(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._import_step(document, command, project_id=project_id, progress=progress)


def _handle_export_brep(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._export_brep(document, command, project_id=project_id, progress=progress)


def _handle_create_sketch_from_section(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._create_sketch_from_section(document, command, progress=progress)


COMMAND_HANDLERS = {
    "import_step": _handle_import_step,
    "export_brep": _handle_export_brep,
    "create_sketch_from_section": _handle_create_sketch_from_section,
}
