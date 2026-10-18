"""Resolve topology fingerprints to current face/edge indices immediately before a command."""
from __future__ import annotations

import math
from typing import Any

from .errors import ConflictError, ValidationError



def _keys(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        raise ValidationError(f"{name} must be a non-empty list of semantic topology keys.")
    if len(set(value)) != len(value):
        raise ValidationError(f"{name} must not contain duplicates.")
    return value


def _resolve(kernel, geometry: dict[str, Any], *, keys: list[str], collection: str) -> list[int]:
    topology = kernel.topology(geometry)
    items = topology.get(collection)
    if not isinstance(items, list):
        raise ValidationError(f"Current topology does not expose {collection}.")
    by_key: dict[str, list[int]] = {}
    for item in items:
        if (isinstance(item, dict) and isinstance(item.get("semantic_key"), str)
                and type(item.get("index")) is int):
            by_key.setdefault(item["semantic_key"], []).append(item["index"])
    missing = [key for key in keys if key not in by_key]
    if missing:
        raise ConflictError(
            "Topology changed and semantic selectors no longer resolve: " + ", ".join(missing)
            + ". Inspect topology again before editing.", code="TOPOLOGY_CHANGED",
            diagnostics={"category": "topology_selection", "entity_ids": missing,
                         "failed_precondition": "every semantic selector must resolve against current topology",
                         "correction_hint": "Refresh the current topology and review the unmatched selectors before editing."},
        )
    ambiguous = [key for key in keys if len(by_key[key]) != 1]
    if ambiguous:
        raise ConflictError(
            "Topology selection is ambiguous: " + ", ".join(ambiguous)
            + ". Use explicit indices with the current topology_revision.", code="TOPOLOGY_AMBIGUOUS",
            diagnostics={"category": "topology_selection", "entity_ids": ambiguous,
                         "failed_precondition": "each semantic selector must resolve to exactly one current entity",
                         "correction_hint": "Inspect the candidates and select a unique entity at the current topology revision."},
        )
    return [by_key[key][0] for key in keys]


def capture_edge_references(kernel, geometry: dict[str, Any], indices: list[int]) -> list[dict[str, Any]]:
    """Capture geometric edge descriptors so editable fillets can survive dimension changes."""
    edges = kernel.topology(geometry).get("edges")
    if not isinstance(edges, list):
        raise ValidationError("Current topology does not expose edges.")
    by_index = {edge.get("index"): edge for edge in edges if isinstance(edge, dict)}
    references = []
    for index in indices:
        edge = by_index.get(index)
        if edge is None:
            raise ValidationError(f"Current topology has no edge index {index}.")
        reference = {key: edge[key] for key in ("curve", "center", "length", "start", "end",
                     "direction", "radius", "origin") if key in edge}
        references.append(reference)
    return references


def resolve_edge_references(kernel, geometry: dict[str, Any], references: list[dict[str, Any]]) -> list[int]:
    """Resolve stored geometric edge intent after upstream topology IDs have changed."""
    topology = kernel.topology(geometry)
    edges = topology.get("edges")
    if not isinstance(edges, list) or not references:
        raise ValidationError("Stored edge references or current topology are unavailable.")
    bounds = topology.get("bounds", {})
    low, high = bounds.get("min"), bounds.get("max")
    diagonal = math.dist(low, high) if isinstance(low, list) and isinstance(high, list) else 1.0
    max_distance = max(2.0, diagonal * 0.25)
    chosen: list[int] = []
    for reference in references:
        candidates = []
        for edge in edges:
            if not isinstance(edge, dict) or edge.get("curve") != reference.get("curve"):
                continue
            center, old_center = edge.get("center"), reference.get("center")
            if not isinstance(center, list) or not isinstance(old_center, list):
                continue
            distance = math.dist(center, old_center)
            if distance > max_distance:
                continue
            old_length, length = reference.get("length"), edge.get("length")
            if (isinstance(old_length, (int, float)) and isinstance(length, (int, float))
                    and abs(length - old_length) > max(5.0, abs(old_length) * 0.5)):
                continue
            old_direction, direction = reference.get("direction"), edge.get("direction")
            orientation_cost = 0.0
            if isinstance(old_direction, list) and isinstance(direction, list):
                dot = abs(sum(a * b for a, b in zip(old_direction, direction, strict=True)))
                if dot < 0.9:
                    continue
                orientation_cost = 1.0 - dot
            score = distance / max(max_distance, 1e-9) + orientation_cost
            candidates.append((score, edge.get("index")))
        candidates.sort(key=lambda item: (item[0], item[1]))
        match = next((index for _, index in candidates if index not in chosen), None)
        if match is None:
            raise ValidationError("Stored edge intent no longer matches a unique edge; inspect topology again.")
        chosen.append(match)
    return chosen


def execute_with_selection(next_execute, self, document, command, *, progress=None, project_id=None):
    semantic_keys = None
    edge_indices = None
    edge_source_geometry = None
    if isinstance(command, dict):
        kind = command.get("type")
        mapping = {
            "shell_body": ("face_keys", "face_indices", "faces"),
            "draft_faces": ("face_keys", "face_indices", "faces"),
            "chamfer_edges": ("edge_keys", "edge_indices", "edges"),
            "variable_fillet_edges": ("edge_keys", "edge_indices", "edges"),
            "fillet_edges": ("edge_keys", "edge_indices", "edges"),
        }
        selector = mapping.get(kind)
        if selector:
            body_id = command.get("body_id")
            source = next((item for item in document.objects
                           if item.get("id") == body_id and item.get("type") == "body"), None)
            if source is None or not isinstance(source.get("geometry"), dict):
                raise ValidationError("Semantic selection requires an existing editable body.")
            edge_source_geometry = source["geometry"]
            if "topology_revision" in command:
                revision = command["topology_revision"]
                if type(revision) is not int or revision < 0:
                    raise ValidationError("topology_revision must be a non-negative integer.")
                if revision != document.revision:
                    raise ConflictError("Topology revision is stale; inspect again before editing.",
                                        code="TOPOLOGY_CHANGED", diagnostics={
                                            "category": "topology_selection",
                                            "revision": document.revision,
                                            "expected_topology_revision": revision,
                                            "failed_precondition": "the selected topology revision must match the current document revision",
                                            "correction_hint": "Inspect the body's current topology and recreate the selection against that revision."})
            if selector[0] in command:
                resolved = _resolve(self.kernel, edge_source_geometry,
                                    keys=_keys(command[selector[0]], selector[0]), collection=selector[2])
                semantic_keys = list(command[selector[0]])
                edge_indices = resolved if selector[2] == "edges" else None
                command = {**command, selector[1]: resolved}
            elif selector[1] in command:
                raw_indices = command[selector[1]]
                if (not isinstance(raw_indices, list) or not raw_indices
                        or any(type(index) is not int or index < 0 for index in raw_indices)
                        or len(set(raw_indices)) != len(raw_indices)):
                    raise ValidationError(f"{selector[1]} must contain distinct non-negative integers.")
                edge_indices = raw_indices if selector[2] == "edges" else None
    result = next_execute(document, command, progress=progress, project_id=project_id)
    if edge_indices and isinstance(result, dict) and isinstance(result.get("feature_id"), str):
        feature_id = result["feature_id"]
        feature = next((item for item in document.objects
                        if item.get("id") == feature_id and item.get("type") == "feature"), None)
        if feature is not None:
            feature["edge_references"] = capture_edge_references(self.kernel, edge_source_geometry, edge_indices)
            if semantic_keys:
                feature["edge_keys"] = semantic_keys
    return result


def refine_capabilities(contracts):
    rewritten = []
    for item in contracts:
        item = dict(item)
        kind = item.get("type")
        if kind in {"shell_body", "draft_faces"}:
            item["optional"] = list(dict.fromkeys([*(item.get("optional") or []), "face_keys", "topology_revision"]))
            item["constraints"] = [*(item.get("constraints") or []),
                                   "face_keys may replace face_indices using unique semantic_key values; topology_revision fences inspected face choices."]
        elif kind in {"chamfer_edges", "variable_fillet_edges", "fillet_edges"}:
            item["optional"] = list(dict.fromkeys([*(item.get("optional") or []), "edge_keys", "topology_revision"]))
            item["constraints"] = [*(item.get("constraints") or []),
                                   "edge_keys may replace edge_indices using unique semantic_key values; topology_revision fences inspected edge choices."]
        rewritten.append(item)
    return tuple(rewritten)
