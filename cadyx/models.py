"""Small, versioned domain models used by CADYX Core."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
import math
import re
from typing import Any

from .errors import ValidationError
from .migrations import DOCUMENT_VERSION, migrate_document
from .model_parameters import evaluate_expression, property_location
from .sketches import typed_id, validate_sketches


class OwnerType(StrEnum):
    AGENT = "agent"
    HUMAN = "human"
    SYSTEM = "system"


class LockState(StrEnum):
    FREE = "FREE"
    AI_LOCKED = "AI_LOCKED"
    HUMAN_LOCKED = "HUMAN_LOCKED"
    SYSTEM_LOCKED = "SYSTEM_LOCKED"


@dataclass(slots=True)
class Document:
    id: str
    version: int = DOCUMENT_VERSION
    revision: int = 0
    objects: list[dict[str, Any]] = field(default_factory=list)
    model_parameters: dict[str, dict[str, Any]] = field(default_factory=dict)
    parameter_bindings: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def create(cls) -> Document:
        from uuid import uuid4

        return cls(id=f"document:{uuid4()}")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Document:
        # migrate_document already returns an isolated deep-copied snapshot.
        # Reusing its object list avoids cloning large B-rep payloads twice on
        # every project read while preserving caller isolation.
        value = migrate_document(value)
        document = cls(
            id=value.get("id", ""),
            version=value["version"],
            revision=value.get("revision", 0),
            objects=value.get("objects", []),
            model_parameters=value.get("model_parameters", {}),
            parameter_bindings=value.get("parameter_bindings", []),
        )
        document.validate()
        return document

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "revision": self.revision,
            "objects": deepcopy(self.objects),
            "model_parameters": deepcopy(self.model_parameters),
            "parameter_bindings": deepcopy(self.parameter_bindings),
        }

    def validate(self) -> None:
        typed_id(self.id, "document")
        if type(self.version) is not int or self.version != DOCUMENT_VERSION:
            raise ValidationError(f"Unsupported document version: {self.version!r}.")
        if not isinstance(self.revision, int) or isinstance(self.revision, bool) or self.revision < 0:
            raise ValidationError("Document revision must be a non-negative integer.")
        if not isinstance(self.objects, list):
            raise ValidationError("Document objects must be a list.")
        if not isinstance(self.model_parameters, dict):
            raise ValidationError("Document model_parameters must be an object.")
        for name, parameter in self.model_parameters.items():
            if (not isinstance(name, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None
                    or not isinstance(parameter, dict) or set(parameter) != {"value", "unit"}
                    or isinstance(parameter.get("value"), bool)
                    or not isinstance(parameter.get("value"), (int, float))
                    or not math.isfinite(parameter["value"])
                    or not isinstance(parameter.get("unit"), str) or not parameter["unit"]):
                raise ValidationError("Document model parameter definitions are invalid.")
        if not isinstance(self.parameter_bindings, list):
            raise ValidationError("Document parameter_bindings must be a list.")

        seen: set[str] = set()
        for item in self.objects:
            if not isinstance(item, dict):
                raise ValidationError("Every document object must be an object.")
            object_id = item.get("id")
            if not isinstance(object_id, str) or ":" not in object_id:
                raise ValidationError("Every document object needs a stable typed id.")
            if object_id in seen:
                raise ValidationError(f"Duplicate document object id: {object_id}.")
            kind = item.get("type")
            if not isinstance(kind, str) or kind not in {"body", "feature", "sketch", "point", "line", "circle", "ellipse", "spline", "constraint"}:
                raise ValidationError("Unsupported document object type.")
            typed_id(object_id, kind)
            seen.add(object_id)

        by_id = {item["id"]: item for item in self.objects}
        body_feature_ids: dict[str, set[str]] = {}
        for item in self.objects:
            if item.get("type") != "body":
                continue
            feature_ids = item.get("feature_ids", [])
            if isinstance(feature_ids, list) and all(isinstance(feature_id, str) for feature_id in feature_ids):
                body_feature_ids[item["id"]] = set(feature_ids)

        for item in self.objects:
            if item.get("type") == "body":
                if "visible" in item and type(item["visible"]) is not bool:
                    raise ValidationError("Body visible must be a boolean when provided.")
                feature_ids = item.get("feature_ids", [])
                if not isinstance(feature_ids, list):
                    raise ValidationError("Body feature_ids must be a list.")
                feature_id_set = body_feature_ids.get(item["id"])
                if feature_id_set is None or len(feature_id_set) != len(feature_ids):
                    raise ValidationError("Body feature_ids must be unique strings.")
                for feature_id in feature_ids:
                    feature = by_id.get(feature_id)
                    if not feature or feature.get("type") != "feature" or feature.get("body_id") != item["id"]:
                        raise ValidationError("Body feature references must point to its own saved features.")
            elif item.get("type") == "feature":
                typed_id(item.get("body_id"), "body")
                body = by_id.get(item.get("body_id"))
                feature_ids = body.get("feature_ids", []) if body else []
                feature_id_set = body_feature_ids.get(item.get("body_id"))
                feature_is_linked = (
                    item["id"] in feature_id_set
                    if feature_id_set is not None
                    else item["id"] in feature_ids
                )
                if not body or body.get("type") != "body" or not isinstance(feature_ids, list) or not feature_is_linked:
                    raise ValidationError("Feature must reference a body that includes the feature id.")
                if item.get("operation") in {"extrude_sketch", "revolve_sketch", "pad_sketch_to_body"}:
                    source_sketch_id = item.get("source_sketch_id")
                    typed_id(source_sketch_id, "sketch")
                    if by_id.get(source_sketch_id, {}).get("type") != "sketch":
                        raise ValidationError("Profile feature must reference an existing source sketch.")
                    if item.get("operation") == "pad_sketch_to_body":
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
                elif item.get("operation") == "sweep_sketch":
                    source_sketch_id = item.get("source_sketch_id")
                    typed_id(source_sketch_id, "sketch")
                    if by_id.get(source_sketch_id, {}).get("type") != "sketch":
                        raise ValidationError("Sweep feature must reference an existing source sketch.")
                elif item.get("operation") == "loft_sketches":
                    source_sketch_ids = item.get("source_sketch_ids")
                    parameters = item.get("parameters")
                    groups = parameters.get("section_sketch_groups", [[value] for value in source_sketch_ids]) \
                        if isinstance(parameters, dict) and isinstance(source_sketch_ids, list) else None
                    if (not isinstance(source_sketch_ids, list) or len(source_sketch_ids) < 2
                            or any(not isinstance(value, str) for value in source_sketch_ids)
                            or len(set(source_sketch_ids)) != len(source_sketch_ids)
                            or not isinstance(parameters, dict)
                            or set(parameters) not in ({"section_sketch_ids", "section_offsets"},
                                                        {"section_sketch_ids", "section_offsets", "section_sketch_groups"})
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
                        for sketch_id in group:
                            typed_id(sketch_id, "sketch")
                            sketch = by_id.get(sketch_id)
                            if (sketch is None or sketch.get("type") != "sketch" or sketch.get("plane") != "XZ"
                                    or sketch.get("section_normal_axis") != "Y"
                                    or sketch.get("section_offset") != offset):
                                raise ValidationError("Loft feature source sketch is missing or has changed section metadata.")
                elif item.get("operation") == "create_ruled_shell":
                    parameters = item.get("parameters")
                    if (not isinstance(parameters, dict)
                            or set(parameters) != {"patches", "sewing_tolerance"}
                            or not isinstance(parameters.get("patches"), list)
                            or isinstance(parameters.get("sewing_tolerance"), bool)
                            or not isinstance(parameters.get("sewing_tolerance"), (int, float))
                            or not math.isfinite(parameters["sewing_tolerance"])):
                        raise ValidationError("Ruled shell feature parameters are invalid.")
                elif item.get("operation") == "fillet_edges":
                    source_body_id = item.get("source_body_id")
                    typed_id(source_body_id, "body")
                    if source_body_id == item["body_id"] or by_id.get(source_body_id, {}).get("type") != "body":
                        raise ValidationError("Fillet feature must reference a different existing source body.")
                    edges = item.get("edge_indices")
                    if (not isinstance(edges, list) or not edges
                            or any(type(index) is not int or index < 0 for index in edges)
                            or len(set(edges)) != len(edges)):
                        raise ValidationError("Fillet feature edge indices are invalid.")
                elif item.get("operation") in {"resize_imported_thread", "transform_imported_brep"}:
                    source_body_id = item.get("source_body_id")
                    typed_id(source_body_id, "body")
                    if source_body_id == item["body_id"] or by_id.get(source_body_id, {}).get("type") != "body":
                        raise ValidationError("Imported B-rep feature must reference a different existing source body.")
                    if not isinstance(item.get("parameters"), dict):
                        raise ValidationError("Imported B-rep feature parameters are missing.")
                elif item.get("operation") in {"fuse_bodies", "cut_bodies"}:
                    base_id, tool_id = item.get("base_body_id"), item.get("tool_body_id")
                    typed_id(base_id, "body")
                    typed_id(tool_id, "body")
                    if base_id == tool_id or item["body_id"] in {base_id, tool_id}:
                        raise ValidationError("Boolean feature must reference two different source bodies.")
                    if any(by_id.get(source_id, {}).get("type") != "body" for source_id in (base_id, tool_id)):
                        raise ValidationError("Boolean feature must reference existing source bodies.")
                elif item.get("operation") == "import_step":
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
        binding_targets: set[tuple[str, str]] = set()
        for binding in self.parameter_bindings:
            if (not isinstance(binding, dict) or set(binding) != {"object_id", "path", "expression"}
                    or not all(isinstance(binding.get(key), str)
                               for key in ("object_id", "path", "expression"))):
                raise ValidationError("Parameter binding records are invalid.")
            target_id, path = binding["object_id"], binding["path"]
            target = by_id.get(target_id)
            if target is None:
                raise ValidationError("Parameter binding target was not found.")
            target_key = (target_id, path)
            if target_key in binding_targets:
                raise ValidationError("Parameter binding targets must be unique.")
            binding_targets.add(target_key)
            parent, final = property_location(target, path)
            current = parent[final]
            expected = evaluate_expression(binding["expression"], self.model_parameters)
            if not math.isclose(float(current), expected, rel_tol=1e-12, abs_tol=1e-12):
                raise ValidationError("Bound model parameter value is out of date with its expression.")
        validate_sketches(self.objects)


@dataclass(frozen=True, slots=True)
class EditLease:
    project_id: str
    owner_type: OwnerType
    owner_id: str
    acquired_at: float
    heartbeat_at: float
    lease_id: str

    def as_dict(self, *, expired: bool = False) -> dict[str, Any]:
        from datetime import datetime, timezone

        def iso(timestamp: float) -> str:
            return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()

        return {
            "project_id": self.project_id,
            "owner_type": self.owner_type.value,
            "owner_id": self.owner_id,
            "acquired_at": iso(self.acquired_at),
            "heartbeat_at": iso(self.heartbeat_at),
            "lease_id": self.lease_id,
            "expired": expired,
        }


def lock_state_for(lease: EditLease | None) -> LockState:
    if lease is None:
        return LockState.FREE
    return {
        OwnerType.AGENT: LockState.AI_LOCKED,
        OwnerType.HUMAN: LockState.HUMAN_LOCKED,
        OwnerType.SYSTEM: LockState.SYSTEM_LOCKED,
    }[lease.owner_type]
