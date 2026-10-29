"""Explicit, sequential migrations of JSON project and document snapshots.

Migration never writes files or modifies the caller's snapshot. Version numbers
for the project envelope and its nested document are independent.
"""

from copy import deepcopy
from typing import Any, Callable

from .errors import ValidationError

PROJECT_VERSION = 1
DOCUMENT_VERSION = 14


def _document_v1_to_v2(value: dict[str, Any]) -> None:
    objects = value.get("objects")
    if not isinstance(objects, list):
        raise ValidationError("Document objects must be a list.")
    if any(not isinstance(item, dict) or item.get("type") not in ("body", "feature") for item in objects):
        raise ValidationError("Version 1 supports only bodies and features.")
    # Existing solids use the same schema; v2 adds sketches, not new solid data.
    value["version"] = 2


def _document_v2_to_v3(value: dict[str, Any]) -> None:
    objects = value.get("objects")
    if not isinstance(objects, list):
        raise ValidationError("Document objects must be a list.")
    if any(not isinstance(item, dict) for item in objects):
        raise ValidationError("Document objects must be objects.")
    # v3 extends sketches to XZ and adds sketch-driven solid features.
    value["version"] = 3


def _document_v3_to_v4(value: dict[str, Any]) -> None:
    objects = value.get("objects")
    if not isinstance(objects, list) or any(not isinstance(item, dict) for item in objects):
        raise ValidationError("Document objects must be a list of objects.")
    value["version"] = 4


def _document_v4_to_v5(value: dict[str, Any]) -> None:
    objects = value.get("objects")
    if not isinstance(objects, list) or any(not isinstance(item, dict) for item in objects):
        raise ValidationError("Document objects must be a list of objects.")
    # v5 adds analytic ellipse sketch objects without changing existing geometry.
    value["version"] = 5


def _document_v5_to_v6(value: dict[str, Any]) -> None:
    objects = value.get("objects")
    if not isinstance(objects, list) or any(not isinstance(item, dict) for item in objects):
        raise ValidationError("Document objects must be a list of objects.")
    # v6 adds analytic partial-ellipse arcs to the ellipse object contract.
    value["version"] = 6


def _document_v6_to_v7(value: dict[str, Any]) -> None:
    objects = value.get("objects")
    if not isinstance(objects, list) or any(not isinstance(item, dict) for item in objects):
        raise ValidationError("Document objects must be a list of objects.")
    # v7 adds point-interpolating spline sketch objects without changing legacy geometry.
    value["version"] = 7


def _document_v7_to_v8(value: dict[str, Any]) -> None:
    objects = value.get("objects")
    if not isinstance(objects, list) or any(not isinstance(item, dict) for item in objects):
        raise ValidationError("Document objects must be a list of objects.")
    if "model_parameters" not in value:
        value["model_parameters"] = {}
    if "parameter_bindings" not in value:
        value["parameter_bindings"] = []
    value["version"] = 8


def _document_v8_to_v9(value: dict[str, Any]) -> None:
    objects = value.get("objects")
    if not isinstance(objects, list) or any(not isinstance(item, dict) for item in objects):
        raise ValidationError("Document objects must be a list of objects.")
    if "assembly" not in value:
        # Build the schema that v9 actually introduced, not today's default.
        value["assembly"] = {
            "version": 1, "units": "mm", "parts": [],
            "subassemblies": [], "instances": [],
        }
    value["version"] = 9

def _document_v9_to_v10(value: dict[str, Any]) -> None:
    graph = value.get("assembly")
    if not isinstance(graph, dict) or graph.get("version") not in (1, 2):
        raise ValidationError("Document v9 must have a supported assembly graph.")
    if graph["version"] == 1:
        if set(graph) != {"version", "units", "parts", "subassemblies", "instances"}:
            raise ValidationError("Document v9 assembly graph has unexpected fields.")
        graph["version"] = 2
        graph["mates"] = []
    value["version"] = 10


def _document_v10_to_v11(value: dict[str, Any]) -> None:
    graph = value.get("assembly")
    if (not isinstance(graph, dict) or graph.get("version") != 2
            or not isinstance(graph.get("parts"), list)):
        raise ValidationError("Document v10 requires an assembly v2 graph.")
    for part in graph["parts"]:
        if not isinstance(part, dict) or set(part) != {"id", "name", "body_id"}:
            raise ValidationError("Document v10 has an invalid part definition.")
        part["material"] = None
    graph["version"] = 3
    value["version"] = 11

def _document_v11_to_v12(value: dict[str, Any]) -> None:
    objects = value.get("objects")
    if not isinstance(objects, list) or any(not isinstance(item, dict) for item in objects):
        raise ValidationError("Document v11 requires an object list.")
    # Preserve legacy XY/XZ coordinates, section offsets, and solid descriptors.
    # v12 introduces only an optional local-frame sketch placement contract.
    value["version"] = 12


def _document_v12_to_v13(value: dict[str, Any]) -> None:
    graph = value.get("assembly")
    if (not isinstance(graph, dict) or graph.get("version") != 3
            or not isinstance(graph.get("instances"), list)):
        raise ValidationError("Document v12 requires an assembly v3 graph.")
    for instance in graph["instances"]:
        if not isinstance(instance, dict) or set(instance) != {
            "id", "name", "parent_id", "target_id", "translation", "rotation"
        }:
            raise ValidationError("Document v12 has an invalid assembly instance.")
        instance["grounded"] = False
        instance["visible"] = True
    graph["version"] = 4
    value["version"] = 13


def _document_v13_to_v14(value: dict[str, Any]) -> None:
    graph = value.get("assembly")
    if (not isinstance(graph, dict) or graph.get("version") != 4
            or not isinstance(graph.get("mates"), list)):
        raise ValidationError("Document v13 requires an assembly v4 graph.")
    legacy_mate_fields = {
        "id", "name", "kind", "a_instance_id", "b_instance_id",
        "frame_a", "frame_b", "angle_deg", "offset_mm", "orientation",
    }
    for mate in graph["mates"]:
        if not isinstance(mate, dict) or set(mate) != legacy_mate_fields:
            raise ValidationError("Document v13 has an invalid assembly mate.")
        mate["limits"] = {
            "angle_min_deg": None, "angle_max_deg": None,
            "offset_min_mm": None, "offset_max_mm": None,
        }
    graph["version"] = 5
    value["version"] = 14


_DOCUMENT_STEPS = {1: _document_v1_to_v2, 2: _document_v2_to_v3,
                   3: _document_v3_to_v4, 4: _document_v4_to_v5,
                   5: _document_v5_to_v6, 6: _document_v6_to_v7,
                   7: _document_v7_to_v8, 8: _document_v8_to_v9,
                   9: _document_v9_to_v10, 10: _document_v10_to_v11,
                   11: _document_v11_to_v12, 12: _document_v12_to_v13,
                   13: _document_v13_to_v14}
_PROJECT_STEPS: dict[int, Callable[[dict[str, Any]], None]] = {}


def _migrate(value: Any, *, kind: str, current: int,
             steps: dict[int, Callable[[dict[str, Any]], None]],
             copy_value: bool = True) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"The saved {kind} must be an object.")
    version = value.get("version")
    if type(version) is not int or not 1 <= version <= current:
        raise ValidationError(f"Unsupported {kind} version: {version!r}.")
    migrated = deepcopy(value) if copy_value else value
    while version < current:
        step = steps.get(version)
        if step is None:
            raise ValidationError(f"No {kind} migration from version {version}.")
        step(migrated)
        if type(migrated.get("version")) is not int or migrated["version"] != version + 1:
            raise ValidationError(f"Invalid {kind} migration from version {version}.")
        version += 1
    return migrated


def _migrate_document(value: Any, *, copy_value: bool) -> dict[str, Any]:
    if isinstance(value, dict) and not {"id", "revision", "objects"} <= value.keys():
        raise ValidationError("Saved document requires id, revision and objects.")
    return _migrate(
        value,
        kind="document",
        current=DOCUMENT_VERSION,
        steps=_DOCUMENT_STEPS,
        copy_value=copy_value,
    )


def migrate_document(value: Any) -> dict[str, Any]:
    """Return the current schema snapshot; domain validation follows in Document."""
    return _migrate_document(value, copy_value=True)


def migrate_project(value: Any) -> dict[str, Any]:
    """Migrate the envelope first, then the independently versioned document."""
    if not isinstance(value, dict) or value.get("format") != "cadyx-project":
        raise ValidationError("Unsupported project format.")
    migrated = _migrate(value, kind="project", current=PROJECT_VERSION, steps=_PROJECT_STEPS)
    # The project migration already deep-copied the entire nested document.
    # Migrate that owned copy in place instead of cloning large B-rep/mesh payloads again.
    migrated["document"] = _migrate_document(migrated.get("document"), copy_value=False)
    return migrated
