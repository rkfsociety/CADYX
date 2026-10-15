"""Tighten machine-readable contracts for extension commands with alternative selectors."""
from __future__ import annotations

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from . import capabilities, commands

    selector_contracts = {
        "shell_body": ("face_indices", "face_keys", "Exactly one of face_indices or face_keys is required."),
        "draft_faces": ("face_indices", "face_keys", "Exactly one of face_indices or face_keys is required."),
        "chamfer_edges": ("edge_indices", "edge_keys", "Exactly one of edge_indices or edge_keys is required."),
        "variable_fillet_edges": ("edge_indices", "edge_keys", "Exactly one of edge_indices or edge_keys is required."),
        "fillet_edges": ("edge_indices", "edge_keys", "Exactly one of edge_indices or edge_keys is required."),
    }
    refined = []
    for entry in capabilities.COMMAND_CAPABILITIES:
        item = dict(entry)
        contract = selector_contracts.get(item.get("type"))
        if contract:
            index_name, key_name, note = contract
            required = [name for name in item.get("required", []) if name != index_name]
            optional = list(dict.fromkeys([*(item.get("optional") or []), index_name, key_name]))
            constraints = list(dict.fromkeys([*(item.get("constraints") or []), note]))
            item.update(required=required, optional=optional, constraints=constraints)
        refined.append(item)
    capabilities.COMMAND_CAPABILITIES = tuple(refined)
    commands.COMMAND_CAPABILITIES = capabilities.COMMAND_CAPABILITIES
    _INSTALLED = True
