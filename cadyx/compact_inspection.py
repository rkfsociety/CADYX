"""Revision-bound, bounded inspections without copying CAD payloads into responses."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .errors import ConflictError, ValidationError

_MAX_PAGE = 100
_REF_FIELDS = (
    "body_id", "source_body_id", "base_body_id", "tool_body_id",
    "sketch_id", "source_sketch_id", "source_sketch_ids", "feature_id",
    "dependent_feature_ids", "point_id", "start_id", "end_id", "center_id",
)
_ENTITY_FIELDS = (
    "id", "type", "name", "operation", "plane", "body_id", "sketch_id",
    "source_body_id", "source_sketch_id", "element_type", "constraint_type",
    "kind", "value", "construction",
)
_TOPOLOGY_FIELDS = (
    "index", "semantic_key", "surface", "curve", "radius", "length", "area",
    "center", "normal", "direction", "axis_origin", "axis_direction",
)
_METRIC_FIELDS = ("volume", "surface_area", "bounds", "topology")


def _integer(value: Any, name: str, *, minimum: int = 0, maximum: int = 1_000_000) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValidationError(f"{name} must be an integer between {minimum} and {maximum}.")
    return value


def _page(items: list[dict[str, Any]], offset: int, limit: int) -> dict[str, Any]:
    total = len(items)
    return {
        "total": total, "offset": offset, "limit": limit,
        "returned": min(limit, max(0, total - offset)),
        "has_more": offset + limit < total,
        "omitted": max(0, total - offset - limit),
        "items": items[offset:offset + limit],
    }


def _references(item: dict[str, Any]) -> list[str]:
    found: set[str] = set()
    for key in _REF_FIELDS:
        value = item.get(key)
        if isinstance(value, str) and ":" in value:
            found.add(value)
        elif isinstance(value, list):
            found.update(entry for entry in value if isinstance(entry, str) and ":" in entry)
    return sorted(found - {item.get("id")})


def _feature_references(history_entry: dict[str, Any]) -> list[str]:
    """Recorded references, not a reconstructed historical feature graph."""
    found: set[str] = set()
    for payload in (history_entry.get("command"), history_entry.get("result")):
        if not isinstance(payload, dict):
            continue
        for key in ("feature_id", "upstream_feature_id", "downstream_feature_id"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith("feature:"):
                found.add(value)
        for key in ("feature_ids", "dependent_feature_ids"):
            value = payload.get(key)
            if isinstance(value, list):
                found.update(v for v in value if isinstance(v, str) and v.startswith("feature:"))
    return sorted(found)


def inspect_compact(
    project: dict[str, Any], kernel: Any, *,
    section: str = "summary", expected_revision: int | None = None,
    kind: str | None = None, body_id: str | None = None,
    shape: str | None = None, since_revision: int | None = None,
    offset: int = 0, limit: int = 25, include_metrics: bool = False,
) -> dict[str, Any]:
    """Project data comes from the existing validated workspace record.

    Only a requested topology page invokes the native topology adapter.
    Historical deltas intentionally never claim complete geometry differences:
    command history contains references, not old feature snapshots.
    """
    if section not in {"summary", "entities", "parameters", "topology", "changes"}:
        raise ValidationError("section must be summary, entities, parameters, topology or changes.")
    offset = _integer(offset, "offset")
    limit = _integer(limit, "limit", minimum=1, maximum=_MAX_PAGE)
    if type(include_metrics) is not bool:
        raise ValidationError("include_metrics must be boolean.")
    document = project["document"]
    revision = document["revision"]
    if expected_revision is not None:
        _integer(expected_revision, "expected_revision")
        if expected_revision != revision:
            raise ConflictError("Document revision changed; restart the inspection at the current revision.",
                                code="REVISION_CONFLICT")
    if kind is not None and (not isinstance(kind, str) or not kind):
        raise ValidationError("kind must be a nonempty string.")
    if shape is not None and (not isinstance(shape, str) or not shape):
        raise ValidationError("shape must be a nonempty string.")
    if section != "entities" and section != "topology" and kind is not None:
        raise ValidationError("kind is only valid for entities or topology.")
    if section != "topology" and (body_id is not None or shape is not None):
        raise ValidationError("body_id and shape are only valid for topology.")
    if section != "changes" and since_revision is not None:
        raise ValidationError("since_revision is only valid for changes.")
    if section != "entities" and include_metrics:
        raise ValidationError("include_metrics is only valid for entities.")
    common: dict[str, Any] = {
        "schema_version": 1, "project_id": project["id"],
        "revision": revision, "section": section,
    }
    objects = document["objects"]
    if section == "summary":
        counts = Counter(item["type"] for item in objects)
        return {**common, "name": project["name"],
                "object_count": len(objects), "object_counts": dict(sorted(counts.items())),
                "constraint_count": sum(count for name, count in counts.items() if "constraint" in name),
                "body_count": counts.get("body", 0),
                "parameter_count": len(document.get("model_parameters", {})),
                "binding_count": len(document.get("parameter_bindings", [])),
                "units": {"model": "unspecified",
                          "parameter_units": sorted({item["unit"] for item in
                                                     document.get("model_parameters", {}).values()})},
                "materials": {"status": "not_modeled"},
                "geometry_metrics": {"status": "on_demand",
                                     "source": "entities?kind=body&include_metrics=1"},
                "history_count": len(project["history"]),
                "omissions": ["object_geometry", "mesh", "brep", "command_payloads"]}
    if section == "parameters":
        if kind is not None:
            raise ValidationError("kind is only valid for entities or topology.")
        items = [
            {"name": name, "value": data["value"], "unit": data["unit"]}
            for name, data in sorted(document.get("model_parameters", {}).items())
        ]
        return {**common, **_page(items, offset, limit)}
    if section == "entities":
        if shape is not None or body_id is not None:
            raise ValidationError("shape and body_id are only valid for topology.")
        selected = sorted((obj for obj in objects if kind is None or obj["type"] == kind),
                          key=lambda item: item["id"])
        rows: list[dict[str, Any]] = []
        for obj in selected[offset:offset + limit]:
            row = {key: obj[key] for key in _ENTITY_FIELDS
                   if key in obj and type(obj[key]) in (str, int, float, bool)}
            dependencies = _references(obj)
            row["dependencies"] = dependencies[:20]
            row["dependencies_total"] = len(dependencies)
            row["dependencies_truncated"] = len(dependencies) > 20
            if obj["type"] == "body":
                row["feature_count"] = len(obj.get("feature_ids", []))
                if include_metrics:
                    metrics = kernel.metrics(obj["geometry"])
                    row["geometry_metrics"] = {key: metrics[key] for key in _METRIC_FIELDS if key in metrics}
            elif obj["type"] == "feature":
                parameters = obj.get("parameters", {})
                row["parameter_names"] = sorted(parameters)[:30] if isinstance(parameters, dict) else []
                row["parameter_names_truncated"] = isinstance(parameters, dict) and len(parameters) > 30
            rows.append(row)
        total = len(selected)
        return {**common, "kind": kind, "include_metrics": include_metrics,
                "total": total, "offset": offset, "limit": limit,
                "returned": len(rows), "has_more": offset + limit < total,
                "omitted": max(0, total - offset - limit), "items": rows,
                "omissions": ["object_geometry", "mesh", "brep", "large_parameter_values"]}
    if section == "topology":
        if not isinstance(body_id, str) or not body_id.startswith("body:"):
            raise ValidationError("topology requires a body_id.")
        if kind not in ("face", "edge"):
            raise ValidationError("topology kind must be face or edge.")
        if since_revision is not None or include_metrics:
            raise ValidationError("Invalid topology inspection options.")
        body = next((obj for obj in objects if obj["type"] == "body" and obj["id"] == body_id), None)
        if body is None:
            raise ValidationError("Topology body_id does not exist in the saved document.")
        topology = kernel.topology(body["geometry"])
        candidates = topology.get("faces" if kind == "face" else "edges", [])
        if not isinstance(candidates, list):
            raise ValidationError("Kernel topology did not return an entity collection.")
        matching = sorted((item for item in candidates if isinstance(item, dict) and
                           type(item.get("index")) is int and
                           (shape is None or item.get("surface" if kind == "face" else "curve") == shape)),
                          key=lambda item: item["index"])
        items = [{key: obj[key] for key in _TOPOLOGY_FIELDS if key in obj}
                 for obj in matching[offset:offset + limit]]
        total = len(matching)
        return {**common, "body_id": body_id, "kind": kind, "shape": shape,
                "total": total, "offset": offset, "limit": limit,
                "returned": len(items), "has_more": offset + limit < total,
                "omitted": max(0, total - offset - limit), "items": items,
                "index_stability": "revision_only",
                "omissions": ["brep", "tessellation", "unselected_topology"]}
    # Historical records have no old B-rep snapshots. Return only safe, bounded
    # references; callers must inspect current entities to see their present state.
    if since_revision is None:
        raise ValidationError("changes requires since_revision.")
    _integer(since_revision, "since_revision")
    if since_revision > revision:
        raise ValidationError("since_revision exceeds the saved document revision.")
    entries = project["history"][since_revision:]
    items = [{"revision": entry["revision"],
              "timestamp": entry["timestamp"],
              "command": entry["command"].get("type"),
              "feature_ids": _feature_references(entry)}
             for entry in entries[offset:offset + limit]]
    return {**common, "since_revision": since_revision,
            "total": len(entries), "offset": offset, "limit": limit,
            "returned": len(items), "has_more": offset + limit < len(entries),
            "omitted": max(0, len(entries) - offset - limit),
            "items": items, "feature_change_coverage": "explicit_history_references_only",
            "complete_geometry_diff": False,
            "omissions": ["command_arguments", "command_results", "indirect_rebuilds", "past_geometry"]}
