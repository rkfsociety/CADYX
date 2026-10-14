"""Small, versioned domain models used by CADYX Core."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
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
            if not isinstance(kind, str) or kind not in {"body", "feature", "sketch", "point", "line", "circle", "constraint"}:
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
