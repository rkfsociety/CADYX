"""Scan reverse-engineering commands layered onto the stable CADYX command contract."""
from __future__ import annotations

import math
from typing import Any
from uuid import uuid4

from .errors import NotFoundError, ValidationError
from .mesh_geometry import tessellate_mesh_geometry
from .scan_reverse_alignment import _alignment_from_command, _apply_alignment
from .scan_reverse_common import _bounds, _finite, _positive, _span
from .scan_reverse_sections import (
    _axis_index, _contour_metrics, _project, _resample_closed, _section_options,
    _simplify_closed, _slice_mesh,
)
from .scan_reverse_compare import _deviation


SCAN_REVERSE_CAPABILITIES = (
    {
        "type": "align_scan",
        "description": "Align and scale an imported scan in place using PCA or an explicit orthonormal datum basis.",
        "required": ["body_id"],
        "optional": ["mode", "long_axis", "basis", "origin", "center", "axis_signs", "scale", "units", "translation", "ground_z", "name"],
        "constraints": [
            "body_id must be an imported PLY or STL cadyx-mesh-v1 reference body.",
            "PCA mode maps the dominant principal axis to long_axis (Y by default); explicit mode needs a right-handed orthonormal basis.",
            "scale is positive; units is unitless, mm, cm, m, or in. Existing alignment is recomputed from the unchanged source scan.",
        ],
    },
    {
        "type": "create_sketch_from_scan_section",
        "description": "Intersect a scan triangle mesh with one Y- or Z-normal plane and create editable closed polyline sketches from its contours.",
        "required": ["body_id", "offset"],
        "optional": ["normal_axis", "join_tolerance", "simplify_tolerance", "max_points", "max_contours", "min_area", "min_perimeter", "name"],
        "constraints": [
            "Y-normal sections create XZ sketches compatible with loft_sketches; Z-normal sections create XY sketches.",
            "Only closed non-branched contours are materialized; rejected/open component counts are returned for inspection.",
        ],
    },
    {
        "type": "create_scan_sections",
        "description": "Create editable scan-section sketch groups at multiple offsets in one atomic command.",
        "required": ["body_id", "offsets"],
        "optional": ["normal_axis", "join_tolerance", "simplify_tolerance", "max_points", "max_contours", "min_area", "min_perimeter", "name"],
        "constraints": [
            "offsets contains strictly increasing finite stations.",
            "Y-normal sections are directly compatible with loft_sketches, including multi-contour section_groups.",
        ],
    },
    {
        "type": "compare_body_to_scan",
        "description": "Measure sampled point-to-triangle deviation between an editable B-rep body and a scan mesh using a BVH accelerator.",
        "required": ["body_id", "scan_body_id"],
        "optional": ["max_samples", "tolerance", "bidirectional"],
        "constraints": [
            "body_id must be a non-mesh body and scan_body_id a cadyx-mesh-v1 body.",
            "The result is sampled against viewport tessellation, not an exhaustive exact B-rep distance proof.",
        ],
    },
)

def _mesh_body(document, body_id: Any) -> dict[str, Any]:
    if not isinstance(body_id, str):
        raise ValidationError("body_id must be a string.")
    body = next((item for item in document.objects
                 if item.get("id") == body_id and item.get("type") == "body"), None)
    if body is None:
        raise NotFoundError("Scan body was not found.")
    geometry = body.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("kernel") != "cadyx-mesh-v1":
        raise ValidationError("This operation requires a cadyx-mesh-v1 scan body.")
    return body

def _scan_feature(document, body_id: str) -> dict[str, Any] | None:
    return next((item for item in document.objects
                 if item.get("type") == "feature" and item.get("operation") in {"reconstruct_scan", "import_stl"}
                 and item.get("body_id") == body_id), None)

def _alignment_feature_source(self, feature: dict[str, Any], project_id: str | None) -> dict[str, Any]:
    if feature.get("operation") == "import_stl":
        geometry = feature.get("source_geometry")
        from .mesh_geometry import validate_mesh_geometry
        validate_mesh_geometry(geometry)
        return geometry
    source = feature.get("source")
    parameters = feature.get("parameters")
    if (not isinstance(source, dict) or not isinstance(source.get("sha256"), str)
            or not isinstance(parameters, dict)):
        raise ValidationError("Existing scan alignment cannot be recomputed because source metadata is unavailable.")
    source_path = self._scan_source_path(source, project_id)
    geometry, stats, source_hash = self.scan_processor.reconstruct(
        source_path, parameters, expected_sha256=source["sha256"], progress=None,
    )
    feature["stats"] = stats
    feature["source"] = {**source, "sha256": source_hash}
    return geometry

def _align_scan(self, document, command: dict[str, Any], *, progress=None, project_id=None) -> dict[str, Any]:
    body = _mesh_body(document, command.get("body_id"))
    feature = _scan_feature(document, body["id"])
    if feature is None:
        raise ValidationError("align_scan requires an original imported PLY or STL reference body.")
    if progress:
        progress({"phase": "inspect_scan_alignment"})
    raw_geometry = _alignment_feature_source(self, feature, project_id) if feature.get("alignment") else body["geometry"]
    if feature.get("operation") == "import_stl" and "source_geometry" not in feature:
        feature["source_geometry"] = raw_geometry
    raw_mesh = tessellate_mesh_geometry(raw_geometry)
    source_bounds = _bounds(raw_mesh["positions"])
    record, _origin = _alignment_from_command(raw_mesh["positions"], command)
    record["source_bounds"] = source_bounds
    record["source_span"] = _span(source_bounds)
    if progress:
        progress({"phase": "transform_scan"})
    geometry, aligned_bounds = _apply_alignment(raw_geometry, record)
    ground_z = command.get("ground_z", True)
    if type(ground_z) is not bool:
        raise ValidationError("ground_z must be a boolean.")
    if ground_z:
        record["translation"] = list(record["translation"])
        record["translation"][2] -= aligned_bounds["min"][2]
        geometry, aligned_bounds = _apply_alignment(raw_geometry, record)
    record["ground_z"] = ground_z
    record["aligned_bounds"] = aligned_bounds
    record["aligned_span"] = _span(aligned_bounds)
    body["geometry"] = geometry
    if "name" in command:
        name = command.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValidationError("Aligned scan name must be a non-empty string of at most 120 characters.")
        body["name"] = name.strip()
    if "raw_artifact" not in feature and isinstance(feature.get("artifact"), dict):
        feature["raw_artifact"] = feature["artifact"]
    source = feature.get("source")
    artifact = None
    if isinstance(source, dict) and (
        "project_file" in source or all(isinstance(source.get(key), str) for key in ("folder", "file"))
    ):
        source_filename = source.get("file", source.get("project_file"))
        if isinstance(source_filename, str):
            artifact = self._publish_scan_source_output(source, source_filename, geometry, progress, project_id)
        feature["artifact"] = artifact
    feature["alignment"] = record
    return {
        "body_id": body["id"],
        "feature_id": feature["id"],
        "alignment": record,
        "artifact": artifact,
        "vertex_count": geometry["vertex_count"],
        "triangle_count": geometry["triangle_count"],
    }

def _section_at(self, document, body: dict[str, Any], offset: float, options: dict[str, Any],
                name: str, *, progress=None) -> dict[str, Any]:
    mesh = tessellate_mesh_geometry(body["geometry"])
    axis = options["axis"]
    loops, rejected = _slice_mesh(mesh["positions"], mesh["triangles"], _axis_index(axis), offset,
                                  options["join_tolerance"])
    contours: list[tuple[list[tuple[float, float]], dict[str, Any]]] = []
    for loop in loops:
        points = [_project(point, axis) for point in loop]
        points = _simplify_closed(points, options["simplify_tolerance"])
        points = _resample_closed(points, options["max_points"])
        metrics = _contour_metrics(points)
        if metrics["area"] <= 1e-12 or metrics["area"] < options["min_area"] or metrics["perimeter"] < options["min_perimeter"]:
            rejected += 1
            continue
        contours.append((points, metrics))
    contours.sort(key=lambda item: item[1]["area"], reverse=True)
    max_contours = options["max_contours"]
    dropped = max(0, len(contours) - max_contours) if max_contours is not None else 0
    if dropped:
        rejected += dropped
        contours = contours[:max_contours]
    if not contours:
        raise ValidationError(f"Scan section at {offset:g} produced no closed contour after filtering.")

    feature = _scan_feature(document, body["id"])
    units = feature.get("alignment", {}).get("units", "unitless") if feature else "unitless"
    sketch_ids: list[str] = []
    contour_results = []
    for index, (points, metrics) in enumerate(contours, start=1):
        sketch_id = f"sketch:{uuid4()}"
        sketch_name = name if len(contours) == 1 else f"{name} · {index}"
        sketch = {
            "id": sketch_id,
            "type": "sketch",
            "name": sketch_name,
            "plane": "XZ" if axis == "Y" else "XY",
            "section_normal_axis": axis,
            "section_offset": offset,
            "scan_source_body_id": body["id"],
            "scan_section": {
                "version": 1,
                "join_tolerance": options["join_tolerance"],
                "simplify_tolerance": options["simplify_tolerance"],
                "units": units,
            },
        }
        document.objects.append(sketch)
        if progress:
            progress({"phase": "create_scan_section_sketch", "offset": offset, "contour": index})
        result = self._append_sketch_polyline(
            document, sketch, points, closed=True, operation="create_sketch_from_scan_section",
            progress=None,
        )
        sketch_ids.append(sketch_id)
        contour_results.append({"sketch_id": sketch_id, **metrics,
                                "point_count": len(result["point_ids"])})
    return {
        "offset": offset,
        "normal_axis": axis,
        "sketch_ids": sketch_ids,
        "contours": contour_results,
        "rejected_components": rejected,
        "units": units,
    }

def _create_sections(self, document, command: dict[str, Any], *, progress=None, singular: bool = False) -> dict[str, Any]:
    body = _mesh_body(document, command.get("body_id"))
    mesh = tessellate_mesh_geometry(body["geometry"])
    options = _section_options(command, mesh["positions"])
    if singular:
        offsets = [_finite(command.get("offset"), "offset")]
    else:
        raw_offsets = command.get("offsets")
        if (not isinstance(raw_offsets, list) or not raw_offsets
                or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value))
                       for value in raw_offsets)):
            raise ValidationError("offsets must contain at least one finite number.")
        offsets = [float(value) for value in raw_offsets]
        if any(right <= left for left, right in zip(offsets, offsets[1:])):
            raise ValidationError("offsets must be strictly increasing.")
    axis = options["axis"]
    bounds = _bounds(mesh["positions"])
    axis_index = _axis_index(axis)
    if any(offset < bounds["min"][axis_index] or offset > bounds["max"][axis_index] for offset in offsets):
        raise ValidationError("Every section offset must lie inside the current scan bounds.")
    base_name = command.get("name", "Scan section")
    if not isinstance(base_name, str) or not base_name.strip() or len(base_name.strip()) > 100:
        raise ValidationError("Scan section name must be a non-empty string of at most 100 characters.")
    stations = []
    if progress:
        progress({"phase": "slice_scan", "station_count": len(offsets)})
    for station_index, offset in enumerate(offsets, start=1):
        name = (base_name.strip() if len(offsets) == 1
                else f"{base_name.strip()} · {station_index:02d} @ {offset:g}")
        stations.append(_section_at(self, document, body, offset, options, name, progress=progress))
    result = {
        "body_id": body["id"],
        "normal_axis": axis,
        "offsets": offsets,
        "section_groups": [station["sketch_ids"] for station in stations],
        "sketch_ids": [sketch_id for station in stations for sketch_id in station["sketch_ids"]],
        "stations": stations,
    }
    if singular:
        result.update({"offset": offsets[0], "contours": stations[0]["contours"],
                       "rejected_components": stations[0]["rejected_components"]})
    return result

def _compare(self, document, command: dict[str, Any], *, progress=None) -> dict[str, Any]:
    scan = _mesh_body(document, command.get("scan_body_id"))
    body_id = command.get("body_id")
    body = next((item for item in document.objects
                 if item.get("id") == body_id and item.get("type") == "body"), None)
    if body is None:
        raise NotFoundError("Comparison body was not found.")
    geometry = body.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("kernel") == "cadyx-mesh-v1":
        raise ValidationError("compare_body_to_scan needs an editable non-mesh B-rep body_id.")
    max_samples = command.get("max_samples", 4096)
    if type(max_samples) is not int or not 64 <= max_samples <= 20000:
        raise ValidationError("max_samples must be an integer from 64 to 20000.")
    tolerance = command.get("tolerance")
    if tolerance is not None:
        tolerance = _positive(tolerance, "tolerance")
    bidirectional = command.get("bidirectional", False)
    if type(bidirectional) is not bool:
        raise ValidationError("bidirectional must be a boolean.")
    if progress:
        progress({"phase": "tessellate_comparison_body"})
    target_mesh = self.kernel.tessellate(geometry)
    scan_mesh = tessellate_mesh_geometry(scan["geometry"])
    if progress:
        progress({"phase": "measure_scan_deviation"})
    forward = _deviation(scan_mesh["positions"], target_mesh, max_samples, tolerance)
    result: dict[str, Any] = {
        "body_id": body["id"],
        "scan_body_id": scan["id"],
        "method": "sampled-point-to-tessellated-triangle-bvh-v1",
        "scan_to_body": forward,
        "bidirectional": bidirectional,
    }
    feature = _scan_feature(document, scan["id"])
    if feature and isinstance(feature.get("alignment"), dict):
        result["units"] = feature["alignment"].get("units", "unitless")
    else:
        result["units"] = "unitless"
    if bidirectional:
        if progress:
            progress({"phase": "measure_reverse_deviation"})
        result["body_to_scan"] = _deviation(target_mesh["positions"], scan_mesh, max_samples, tolerance)
        result["hausdorff_sampled_max"] = max(forward["max"], result["body_to_scan"]["max"])
    return result

def _reapply_alignment_after_parameter_edit(self, document, command: dict[str, Any], original, *, progress=None, project_id=None):
    feature_id = command.get("feature_id")
    feature = next((item for item in document.objects
                    if item.get("id") == feature_id and item.get("type") == "feature"
                    and item.get("operation") == "reconstruct_scan"), None)
    alignment = dict(feature["alignment"]) if feature and isinstance(feature.get("alignment"), dict) else None
    result = original(document, command, progress=progress, project_id=project_id)
    if alignment is None:
        return result
    feature = next(item for item in document.objects if item.get("id") == feature_id)
    body = _mesh_body(document, feature["body_id"])
    raw_artifact = feature.get("artifact")
    if progress:
        progress({"phase": "reapply_scan_alignment"})
    geometry, aligned_bounds = _apply_alignment(body["geometry"], alignment)
    body["geometry"] = geometry
    alignment["aligned_bounds"] = aligned_bounds
    alignment["aligned_span"] = _span(aligned_bounds)
    feature["alignment"] = alignment
    if isinstance(raw_artifact, dict):
        feature["raw_artifact"] = raw_artifact
    source = feature.get("source")
    if isinstance(source, dict) and (
        "project_file" in source or all(isinstance(source.get(key), str) for key in ("folder", "file"))
    ):
        source_filename = source.get("file", source.get("project_file"))
        if not isinstance(source_filename, str):
            return result
        artifact = self._publish_scan_source_output(source, source_filename, geometry, progress, project_id)
        feature["artifact"] = artifact
        if isinstance(result, dict):
            result["artifact"] = artifact
            result["alignment_reapplied"] = True
    return result

def _execute(self, document, command: dict[str, Any], *, progress=None, project_id=None):
    kind = command.get("type")
    if kind == "align_scan":
        return _align_scan(self, document, command, progress=progress, project_id=project_id)
    if kind == "create_sketch_from_scan_section":
        return _create_sections(self, document, command, progress=progress, singular=True)
    if kind == "create_scan_sections":
        return _create_sections(self, document, command, progress=progress, singular=False)
    if kind == "compare_body_to_scan":
        return _compare(self, document, command, progress=progress)
    raise ValidationError(f"Unsupported scan reverse-engineering command: {kind!r}.")


def execute_with_alignment(next_execute, self, document, command, *, progress=None, project_id=None):
    """Reapply saved scan alignment after the base parameter command succeeds."""
    if command.get("type") == "set_scan_parameter":
        return _reapply_alignment_after_parameter_edit(
            self, document, command, next_execute, progress=progress, project_id=project_id,
        )
    return next_execute(document, command, progress=progress, project_id=project_id)
