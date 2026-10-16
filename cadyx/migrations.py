"""Explicit, sequential migrations of JSON project and document snapshots.

Migration never writes files or modifies the caller's snapshot. Version numbers
for the project envelope and its nested document are independent.
"""

from copy import deepcopy
from typing import Any, Callable

from .errors import ValidationError

PROJECT_VERSION = 1
DOCUMENT_VERSION = 7


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


_DOCUMENT_STEPS = {1: _document_v1_to_v2, 2: _document_v2_to_v3,
                   3: _document_v3_to_v4, 4: _document_v4_to_v5,
                   5: _document_v5_to_v6, 6: _document_v6_to_v7}
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
