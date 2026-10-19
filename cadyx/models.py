"""Small, versioned domain models used by CADYX Core."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
import math
import re
from typing import Any

from .errors import ValidationError
from .assemblies import empty_assembly, validate_assembly
from .persisted_schemas import OBJECT_SCHEMAS, validate_persisted_object
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
    assembly: dict[str, Any] = field(default_factory=empty_assembly)
    _command_object_index: dict[str, dict[str, Any]] | None = field(default=None, init=False, repr=False)

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
            assembly=value.get("assembly", empty_assembly()),
        )
        document.validate()
        return document

    @classmethod
    def from_current_dict(cls, value: dict[str, Any]) -> Document:
        """Bind and validate a current-version snapshot without migrating or copying it."""
        if not isinstance(value, dict) or value.get("version") != DOCUMENT_VERSION:
            raise ValidationError("Document is not in the current version.")
        document = cls(
            id=value.get("id", ""),
            version=value["version"],
            revision=value.get("revision", 0),
            objects=value.get("objects", []),
            model_parameters=value.get("model_parameters", {}),
            parameter_bindings=value.get("parameter_bindings", []),
            assembly=value.get("assembly", empty_assembly()),
        )
        document.validate()
        return document

    @classmethod
    def from_validated_dict(cls, value: dict[str, Any]) -> Document:
        """Bind a current snapshot already validated by ProjectRecordStore.load."""
        if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != DOCUMENT_VERSION:
            raise ValidationError("Document is not in the current version.")
        return cls(
            id=value.get("id", ""),
            version=value["version"],
            revision=value.get("revision", 0),
            objects=value.get("objects", []),
            model_parameters=value.get("model_parameters", {}),
            parameter_bindings=value.get("parameter_bindings", []),
            assembly=value.get("assembly", empty_assembly()),
        )

    def to_dict(self, *, copy_objects: bool = True) -> dict[str, Any]:
        objects = deepcopy(self.objects) if copy_objects else self.objects
        model_parameters = deepcopy(self.model_parameters) if copy_objects else self.model_parameters
        parameter_bindings = deepcopy(self.parameter_bindings) if copy_objects else self.parameter_bindings
        assembly = deepcopy(self.assembly) if copy_objects else self.assembly
        return {
            "id": self.id,
            "version": self.version,
            "revision": self.revision,
            "objects": objects,
            "model_parameters": model_parameters,
            "parameter_bindings": parameter_bindings,
            "assembly": assembly,
        }

    def clone(self) -> Document:
        """Return an isolated copy of an already-current, already-validated document."""
        return Document(
            id=self.id,
            version=self.version,
            revision=self.revision,
            objects=deepcopy(self.objects),
            model_parameters=deepcopy(self.model_parameters),
            parameter_bindings=deepcopy(self.parameter_bindings),
            assembly=deepcopy(self.assembly),
        )

    def find_object(self, object_id: str, object_type: str | None = None) -> dict[str, Any] | None:
        """Find an object, using the command-scoped id index when one is active."""
        if self._command_object_index is not None:
            item = self._command_object_index.get(object_id)
            if item is not None and item.get("id") != object_id:
                item = None
            if item is None:
                # Commands can create objects or replace an id while running.
                item = next((candidate for candidate in self.objects
                             if candidate.get("id") == object_id), None)
        else:
            item = next((candidate for candidate in self.objects
                         if candidate.get("id") == object_id), None)
        if item is not None and object_type is not None and item.get("type") != object_type:
            return None
        return item

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
            if not isinstance(kind, str) or kind not in OBJECT_SCHEMAS:
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
                validate_persisted_object(item, by_id)
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
                validate_persisted_object(item, by_id)
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
        validate_assembly(self.assembly, self.objects)


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
