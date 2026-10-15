"""Resolve topology fingerprints to current face/edge indices immediately before a command."""
from __future__ import annotations

from typing import Any

from .errors import ValidationError

_INSTALLED = False


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
    by_key = {item.get("semantic_key"): item.get("index") for item in items if isinstance(item, dict)}
    missing = [key for key in keys if key not in by_key]
    if missing:
        raise ValidationError(
            "Topology changed and semantic selectors no longer resolve: " + ", ".join(missing)
            + ". Inspect topology again before editing."
        )
    return [by_key[key] for key in keys]


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from . import capabilities, commands
    original = commands.CommandSystem.execute

    def execute(self, document, command, *, progress=None, project_id=None):
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
            if selector and selector[0] in command and selector[1] not in command:
                body_id = command.get("body_id")
                source = next((item for item in document.objects
                               if item.get("id") == body_id and item.get("type") == "body"), None)
                if source is None or not isinstance(source.get("geometry"), dict):
                    raise ValidationError("Semantic selection requires an existing editable body.")
                resolved = _resolve(self.kernel, source["geometry"],
                                    keys=_keys(command[selector[0]], selector[0]), collection=selector[2])
                command = {**command, selector[1]: resolved}
        return original(self, document, command, progress=progress, project_id=project_id)

    commands.CommandSystem.execute = execute

    rewritten = []
    for item in capabilities.COMMAND_CAPABILITIES:
        item = dict(item)
        kind = item.get("type")
        if kind in {"shell_body", "draft_faces"}:
            item["optional"] = list(dict.fromkeys([*(item.get("optional") or []), "face_keys"]))
            item["constraints"] = [*(item.get("constraints") or []),
                                   "face_keys may replace face_indices using semantic_key values from topology."]
        elif kind in {"chamfer_edges", "variable_fillet_edges", "fillet_edges"}:
            item["optional"] = list(dict.fromkeys([*(item.get("optional") or []), "edge_keys"]))
            item["constraints"] = [*(item.get("constraints") or []),
                                   "edge_keys may replace edge_indices using semantic_key values from topology."]
        rewritten.append(item)
    capabilities.COMMAND_CAPABILITIES = tuple(rewritten)
    commands.COMMAND_CAPABILITIES = capabilities.COMMAND_CAPABILITIES
    _INSTALLED = True
