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

    @classmethod
    def create(cls) -> Document:
        from uuid import uuid4

        return cls(id=f"document:{uuid4()}")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Document:
        value = migrate_document(value)
        document = cls(
            id=value.get("id", ""),
            version=value["version"],
            revision=value.get("revision", 0),
            objects=deepcopy(value.get("objects", [])),
        )
        document.validate()
        return document

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "revision": self.revision,
            "objects": deepcopy(self.objects),
        }

    def validate(self) -> None:
        typed_id(self.id, "document")
        if type(self.version) is not int or self.version != DOCUMENT_VERSION:
            raise ValidationError(f"Unsupported document version: {self.version!r}.")
        if not isinstance(self.revision, int) or isinstance(self.revision, bool) or self.revision < 0:
            raise ValidationError("Document revision must be a non-negative integer.")
        if not isinstance(self.objects, list):
            raise ValidationError("Document objects must be a list.")

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
            if not isinstance(kind, str) or kind not in {"body", "feature", "sketch", "point", "line", "circle", "ellipse", "constraint"}:
                raise ValidationError("Unsupported document object type.")
            typed_id(object_id, kind)
            seen.add(object_id)

        by_id = {item["id"]: item for item in self.objects}
        for item in self.objects:
            if item.get("type") == "body":
                feature_ids = item.get("feature_ids", [])
                if not isinstance(feature_ids, list):
                    raise ValidationError("Body feature_ids must be a list.")
                if len(set(feature_id for feature_id in feature_ids if isinstance(feature_id, str))) != len(feature_ids):
                    raise ValidationError("Body feature_ids must be unique strings.")
                for feature_id in feature_ids:
                    feature = by_id.get(feature_id)
                    if not feature or feature.get("type") != "feature" or feature.get("body_id") != item["id"]:
                        raise ValidationError("Body feature references must point to its own saved features.")
            elif item.get("type") == "feature":
                typed_id(item.get("body_id"), "body")
                body = by_id.get(item.get("body_id"))
                if not body or body.get("type") != "body" or not isinstance(body.get("feature_ids", []), list) or item["id"] not in body.get("feature_ids", []):
                    raise ValidationError("Feature must reference a body that includes the feature id.")
                if item.get("operation") in {"extrude_sketch", "revolve_sketch"}:
                    source_sketch_id = item.get("source_sketch_id")
                    typed_id(source_sketch_id, "sketch")
                    if by_id.get(source_sketch_id, {}).get("type") != "sketch":
                        raise ValidationError("Profile feature must reference an existing source sketch.")
                elif item.get("operation") == "sweep_sketch":
                    source_sketch_id = item.get("source_sketch_id")
                    typed_id(source_sketch_id, "sketch")
                    if by_id.get(source_sketch_id, {}).get("type") != "sketch":
                        raise ValidationError("Sweep feature must reference an existing source sketch.")
                elif item.get("operation") == "fillet_edges":
                    source_body_id = item.get("source_body_id")
                    typed_id(source_body_id, "body")
                    if source_body_id == item["body_id"] or by_id.get(source_body_id, {}).get("type") != "body":
                        raise ValidationError("Fillet feature must reference a different existing source body.")
                    edges = item.get("edge_indices")
                    if (not isinstance(edges, list) or not edges or len(edges) > 128
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
