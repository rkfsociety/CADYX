"""Canonical persisted feature validators; graph identity/ownership stays in Document.

Validators preserve the saved JSON contract, including permissive legacy/extension
operations. Geometry validity remains the kernel's responsibility. Register a new
operation here, not in Document.validate; migrations continue to run before validation.
"""
from __future__ import annotations

import math
import re
from types import MappingProxyType
from typing import Any, Callable

from .errors import ValidationError
from .sketches import typed_id

PersistedValidator = Callable[[dict[str, Any], dict[str, dict[str, Any]]], None]


def _validate_legacy(item: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> None:
    """Existing operations without additional persisted-field restrictions."""


def _validate_profile(item: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> None:
    source_sketch_id = item.get("source_sketch_id")
    typed_id(source_sketch_id, "sketch")
    if by_id.get(source_sketch_id, {}).get("type") != "sketch":
        raise ValidationError("Profile feature must reference an existing source sketch.")


def _validate_pad(item: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> None:
    _validate_profile(item, by_id)
    source_sketch_id = item.get("source_sketch_id")
    source_body_id = item.get("source_body_id")
    typed_id(source_body_id, "body")
    parameters = item.get("parameters")
    if (source_body_id == item["body_id"] or by_id.get(source_body_id, {}).get("type") != "body"
            or not isinstance(parameters, dict)
            or set(parameters) != {"sketch_id", "depth", "centered"}
            or parameters.get("sketch_id") != source_sketch_id
            or isinstance(parameters.get("depth"), bool)
            or not isinstance(parameters.get("depth"), (int, float))
            or not math.isfinite(parameters["depth"]) or parameters["depth"] <= 0
            or type(parameters.get("centered")) is not bool):
        raise ValidationError("Pad feature source references or parameters are invalid.")


def _validate_sweep(item: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> None:
    source_sketch_id = item.get("source_sketch_id")
    typed_id(source_sketch_id, "sketch")
    if by_id.get(source_sketch_id, {}).get("type") != "sketch":
        raise ValidationError("Sweep feature must reference an existing source sketch.")


def _validate_loft(item: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> None:
    source_sketch_ids = item.get("source_sketch_ids")
    parameters = item.get("parameters")
    groups = parameters.get("section_sketch_groups", [[value] for value in source_sketch_ids]) \
        if isinstance(parameters, dict) and isinstance(source_sketch_ids, list) else None
    if (not isinstance(source_sketch_ids, list) or len(source_sketch_ids) < 2
            or any(not isinstance(value, str) for value in source_sketch_ids)
            or len(set(source_sketch_ids)) != len(source_sketch_ids)
            or not isinstance(parameters, dict)
            or set(parameters) not in ({"section_sketch_ids", "section_offsets"},
                                        {"section_sketch_ids", "section_offsets", "section_sketch_groups"},
                                        {"section_sketch_ids", "section_offsets", "section_axis"},
                                        {"section_sketch_ids", "section_offsets", "section_axis", "section_sketch_groups"})
            or parameters.get("section_sketch_ids") != source_sketch_ids
            or not isinstance(groups, list) or len(groups) < 2
            or any(not isinstance(group, list) or not group
                   or any(not isinstance(value, str) for value in group) for group in groups)
            or [value for group in groups for value in group] != source_sketch_ids):
        raise ValidationError("Loft feature section references are invalid.")
    offsets = parameters["section_offsets"]
    if (not isinstance(offsets, list) or len(offsets) != len(groups)
            or any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(value) for value in offsets)
            or any(right <= left for left, right in zip(offsets, offsets[1:]))):
        raise ValidationError("Loft feature section offsets are invalid.")
    for group, offset in zip(groups, offsets, strict=True):
        axes = set()
        for sketch_id in group:
            typed_id(sketch_id, "sketch")
            sketch = by_id.get(sketch_id)
            axis = sketch.get("section_normal_axis") if sketch else None
            expected_plane = "XZ" if axis == "Y" else "XY" if axis == "Z" else None
            if (sketch is None or sketch.get("type") != "sketch" or sketch.get("plane") != expected_plane
                    or sketch.get("section_offset") != offset):
                raise ValidationError("Loft feature source sketch is missing or has changed section metadata.")
            axes.add(axis)
        if len(axes) != 1 or axes.pop() != parameters.get("section_axis", "Y"):
            raise ValidationError("Loft feature section axis does not match its source sketches.")


def _validate_ruled_shell(item: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> None:
    parameters = item.get("parameters")
    if (not isinstance(parameters, dict)
            or set(parameters) != {"patches", "sewing_tolerance"}
            or not isinstance(parameters.get("patches"), list)
            or isinstance(parameters.get("sewing_tolerance"), bool)
            or not isinstance(parameters.get("sewing_tolerance"), (int, float))
            or not math.isfinite(parameters["sewing_tolerance"])):
        raise ValidationError("Ruled shell feature parameters are invalid.")


def _validate_fillet(item: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> None:
    source_body_id = item.get("source_body_id")
    typed_id(source_body_id, "body")
    if source_body_id == item["body_id"] or by_id.get(source_body_id, {}).get("type") != "body":
        raise ValidationError("Fillet feature must reference a different existing source body.")
    edges = item.get("edge_indices")
    if (not isinstance(edges, list) or not edges
            or any(type(index) is not int or index < 0 for index in edges)
            or len(set(edges)) != len(edges)):
        raise ValidationError("Fillet feature edge indices are invalid.")


def _validate_imported_brep(item: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> None:
    source_body_id = item.get("source_body_id")
    typed_id(source_body_id, "body")
    if source_body_id == item["body_id"] or by_id.get(source_body_id, {}).get("type") != "body":
        raise ValidationError("Imported B-rep feature must reference a different existing source body.")
    if not isinstance(item.get("parameters"), dict):
        raise ValidationError("Imported B-rep feature parameters are missing.")


def _validate_boolean(item: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> None:
    base_id, tool_id = item.get("base_body_id"), item.get("tool_body_id")
    typed_id(base_id, "body")
    typed_id(tool_id, "body")
    if base_id == tool_id or item["body_id"] in {base_id, tool_id}:
        raise ValidationError("Boolean feature must reference two different source bodies.")
    if any(by_id.get(source_id, {}).get("type") != "body" for source_id in (base_id, tool_id)):
        raise ValidationError("Boolean feature must reference existing source bodies.")


def _validate_step_import(item: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> None:
    source = item.get("source")
    if (not isinstance(source, dict)
            or not isinstance(source.get("file"), str)
            or source["file"] in {"", ".", "..", "project.json", ".cadyx-project-id"}
            or any(character in source["file"] for character in ("/", "\\", ":", "\x00"))
            or not source["file"].casefold().endswith((".step", ".stp"))
            or not isinstance(source.get("sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", source["sha256"]) is None
            or type(source.get("size")) is not int or not 0 < source["size"] <= 64 * 1024 * 1024):
        raise ValidationError("STEP source metadata is invalid.")
    metrics = item.get("source_metrics")
    if (not isinstance(metrics, dict)
            or set(metrics) != {
                "volume", "surface_area", "bounds", "topology", "brep_valid", "repaired",
            }
            or type(metrics.get("brep_valid")) is not bool
            or type(metrics.get("repaired")) is not bool):
        raise ValidationError("STEP source geometry metrics are invalid.")
    for key in ("volume", "surface_area"):
        value = metrics.get(key)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value <= 0):
            raise ValidationError("STEP source geometry measures are invalid.")
    bounds = metrics.get("bounds")
    if (not isinstance(bounds, dict) or set(bounds) != {"min", "max"}
            or any(not isinstance(bounds.get(key), list) or len(bounds[key]) != 3
                   or any(isinstance(value, bool) or not isinstance(value, (int, float))
                          or not math.isfinite(value) for value in bounds[key])
                   for key in ("min", "max"))):
        raise ValidationError("STEP source geometry bounds are invalid.")
    topology = metrics.get("topology")
    if (not isinstance(topology, dict)
            or set(topology) != {"solid_count", "face_count", "edge_count", "vertex_count"}
            or any(type(topology.get(key)) is not int or topology[key] < 0
                   for key in topology)
            or topology["solid_count"] != 1 or topology["face_count"] == 0):
        raise ValidationError("STEP source B-rep topology metrics are invalid.")

def _validate_cloud_processing(item: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> None:
    from .point_cloud import normalize_cloud_parameters
    normalize_cloud_parameters(item.get("method"), item.get("parameters"))
    previous = by_id.get(item.get("previous_feature_id"), {})
    if (previous.get("type") != "feature" or previous.get("body_id") != item["body_id"]
            or previous.get("operation") not in {"import_point_cloud", "process_point_cloud"}
            or previous["id"] == item["id"]):
        raise ValidationError("Point-cloud processing must reference its preceding cloud feature.")
    ownership = by_id.get(item["body_id"], {}).get("feature_ids", [])
    index = ownership.index(item["id"])
    if index == 0 or ownership[index - 1] != previous["id"]:
        raise ValidationError("Point-cloud processing history must follow body feature order.")
    for key in ("source", "artifact"):
        record = item.get(key)
        if not isinstance(record, dict) or not isinstance(record.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", record["sha256"]):
            raise ValidationError("Point-cloud stage requires hashed source and result artifacts.")
    stats = item.get("stats", {})
    if not isinstance(stats, dict) or any(type(stats.get(key)) is not int or stats[key] < 3 for key in ("input_points", "output_points")):
        raise ValidationError("Point-cloud stage counts are invalid.")


def _validate_mesh_processing(item: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> None:
    from .mesh_processing import normalize_mesh_parameters
    normalize_mesh_parameters(item.get("method"), item.get("parameters"))
    source = by_id.get(item.get("source_body_id"), {})
    previous = by_id.get(item.get("previous_feature_id"), {})
    if (source.get("type") != "body" or source.get("id") == item["body_id"]
            or source.get("geometry", {}).get("kind") != "triangle_mesh"
            or previous.get("body_id") != source.get("id")
            or previous.get("id") not in source.get("feature_ids", [])
            or item.get("source_geometry_sha256") != source.get("geometry", {}).get("sha256")):
        raise ValidationError("Mesh processing requires its unchanged source body and preceding feature.")
    artifact = item.get("artifact", {})
    if not isinstance(artifact, dict) or not isinstance(artifact.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", artifact["sha256"]):
        raise ValidationError("Mesh processing requires a hashed result artifact.")
    stats = item.get("stats")
    if not isinstance(stats, dict) or any(not isinstance(stats.get(key), dict) for key in ("before", "after")):
        raise ValidationError("Mesh processing requires before/after topology statistics.")


def _validate_mesh_recognition(item: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> None:
    from .mesh_recognition import normalize_recognition_parameters
    normalize_recognition_parameters(item.get("parameters"))
    body = by_id.get(item["body_id"], {})
    if body.get("geometry", {}).get("sha256") != item.get("source_geometry_sha256") or body.get("geometry", {}).get("kind") != "triangle_mesh":
        raise ValidationError("Recognition references require their unchanged source mesh.")
    report = item.get("report")
    if not isinstance(report, dict) or any(not isinstance(report.get(k),list) for k in ("planes","sections","circles","cylinder_candidates","unresolved")):
        raise ValidationError("Recognition needs a persisted measured-reference report.")
    sketches = item.get("source_sketch_ids")
    if not isinstance(sketches,list) or len(set(sketches)) != len(sketches) or any(by_id.get(i,{}).get("type") != "sketch" for i in sketches):
        raise ValidationError("Recognition references must own existing source sketches.")


FEATURE_SCHEMAS: MappingProxyType[str, PersistedValidator] = MappingProxyType({
    'recognize_mesh': _validate_mesh_recognition,
    'process_mesh': _validate_mesh_processing,
    'process_point_cloud': _validate_cloud_processing,
    'extrude_sketch': _validate_profile,
    'pad_sketch_to_body': _validate_pad,
    'revolve_sketch': _validate_profile,
    'sweep_sketch': _validate_sweep,
    'loft_sketches': _validate_loft,
    'create_ruled_shell': _validate_ruled_shell,
    'fillet_edges': _validate_fillet,
    'resize_imported_thread': _validate_imported_brep,
    'transform_imported_brep': _validate_imported_brep,
    'cut_bodies': _validate_boolean,
    'fuse_bodies': _validate_boolean,
    'import_step': _validate_step_import,
    'create_box': _validate_legacy,
    'metric_thread': _validate_legacy,
    'reconstruct_scan': _validate_legacy,
    'loft_profiles': _validate_legacy,
    'shell_body': _validate_legacy,
    'offset_body': _validate_legacy,
    'chamfer_edges': _validate_legacy,
    'variable_fillet_edges': _validate_legacy,
    'draft_faces': _validate_legacy,
    'mirror_body': _validate_legacy,
    'linear_pattern_body': _validate_legacy,
    'circular_pattern_body': _validate_legacy,
    'hole': _validate_legacy,
    'boss': _validate_legacy,
    'rib_from_sketch': _validate_legacy,
    'pocket_sketch': _validate_legacy,
    'through_hole': _validate_legacy,
    'counterbore_hole': _validate_legacy,
    'countersink_hole': _validate_legacy,
    'threaded_hole': _validate_legacy,
    'extrude_profile_on_plane': _validate_legacy,
})


def validate_feature(item: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> None:
    operation = item.get("operation")
    # Unknown string operations were historically accepted, including extensions.
    validator = FEATURE_SCHEMAS.get(operation, _validate_legacy)
    validator(item, by_id)


def _validate_body(item: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> None:
    if "visible" in item and type(item["visible"]) is not bool:
        raise ValidationError("Body visible must be a boolean when provided.")


# Sketch field/reference validation is delegated to validate_sketches as a batch;
# identity and body/feature ownership are checked by the document graph pass.
OBJECT_SCHEMAS: MappingProxyType[str, PersistedValidator] = MappingProxyType({
    "body": _validate_body,
    "feature": validate_feature,
    **dict.fromkeys(("sketch", "point", "line", "circle", "ellipse", "spline", "constraint"),
                    _validate_legacy),
})


def validate_persisted_object(item: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> None:
    OBJECT_SCHEMAS[item["type"]](item, by_id)
