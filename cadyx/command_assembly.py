"""Assembly commands; the Core owns the shared revision/lease/save transaction."""
from __future__ import annotations

from typing import Any, Callable
from uuid import uuid4

from .assemblies import IDENTITY_ROTATION, IDENTITY_TRANSLATION, validate_assembly
from .errors import NotFoundError, ValidationError
from .models import Document


def _fields(command: dict[str, Any], required: set[str], optional: set[str] = frozenset()) -> None:
    if not required <= command.keys() or not command.keys() <= required | optional | {"type"}:
        raise ValidationError("Assembly command has missing or unsupported fields.")


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 120:
        raise ValidationError(f"{label} must have 1–120 nonblank characters.")
    return value.strip()


class AssemblyCommands:
    @staticmethod
    def _create_assembly_part(document: Document, command: dict[str, Any]) -> dict[str, Any]:
        _fields(command, {"body_id"}, {"name"})
        body = next((obj for obj in document.objects
                     if obj.get("type") == "body" and obj.get("id") == command["body_id"]), None)
        if body is None:
            raise NotFoundError("Part source body does not exist.")
        if any(part["body_id"] == body["id"] for part in document.assembly["parts"]):
            raise ValidationError("Body already belongs to a part; reuse it through another instance.")
        part = {"id": f"part:{uuid4()}", "name": _text(command.get("name", body.get("name")), "Part name"),
                "body_id": body["id"]}
        document.assembly["parts"].append(part)
        return {"part_id": part["id"], "body_id": part["body_id"]}

    @staticmethod
    def _create_subassembly(document: Document, command: dict[str, Any]) -> dict[str, Any]:
        _fields(command, {"name"})
        sub = {"id": f"subassembly:{uuid4()}", "name": _text(command["name"], "Subassembly name")}
        document.assembly["subassemblies"].append(sub)
        return {"subassembly_id": sub["id"]}

    @staticmethod
    def _add_assembly_instance(document: Document, command: dict[str, Any]) -> dict[str, Any]:
        _fields(command, {"target_id"}, {"name", "parent_id", "translation", "rotation"})
        target = next((entry for key in ("parts", "subassemblies")
                       for entry in document.assembly[key] if entry["id"] == command["target_id"]), None)
        if target is None:
            raise NotFoundError("Assembly target part or subassembly does not exist.")
        parent_id = command.get("parent_id")
        if parent_id is not None and not any(
            sub["id"] == parent_id for sub in document.assembly["subassemblies"]
        ):
            raise NotFoundError("Assembly parent subassembly does not exist.")
        translation = command.get("translation", IDENTITY_TRANSLATION)
        rotation = command.get("rotation", IDENTITY_ROTATION)
        instance = {
            "id": f"instance:{uuid4()}",
            "name": _text(command.get("name", target["name"]), "Instance name"),
            "parent_id": parent_id, "target_id": target["id"],
            "translation": translation.copy() if isinstance(translation, list) else translation,
            "rotation": rotation.copy() if isinstance(rotation, list) else rotation,
        }
        document.assembly["instances"].append(instance)
        validate_assembly(document.assembly, document.objects)
        return {"instance_id": instance["id"], "target_id": target["id"], "parent_id": parent_id}

    @staticmethod
    def _transform_assembly_instance(document: Document, command: dict[str, Any]) -> dict[str, Any]:
        _fields(command, {"instance_id"}, {"translation", "rotation"})
        if "translation" not in command and "rotation" not in command:
            raise ValidationError("Instance transform requires translation or rotation.")
        instance = next((item for item in document.assembly["instances"]
                         if item["id"] == command["instance_id"]), None)
        if instance is None:
            raise NotFoundError("Assembly instance does not exist.")
        for key in ("translation", "rotation"):
            if key in command:
                instance[key] = command[key].copy() if isinstance(command[key], list) else command[key]
        validate_assembly(document.assembly, document.objects)
        return {"instance_id": instance["id"], "translation": instance["translation"],
                "rotation": instance["rotation"]}

    @staticmethod
    def _remove_assembly_instance(document: Document, command: dict[str, Any]) -> dict[str, Any]:
        _fields(command, {"instance_id"})
        entries = document.assembly["instances"]
        index = next((i for i, entry in enumerate(entries) if entry["id"] == command["instance_id"]), None)
        if index is None:
            raise NotFoundError("Assembly instance does not exist.")
        return {"removed_instance_id": entries.pop(index)["id"]}

    @staticmethod
    def _remove_assembly_component(document: Document, command: dict[str, Any]) -> dict[str, Any]:
        _fields(command, {"component_id"})
        target = command["component_id"]
        for instance in document.assembly["instances"]:
            if instance["target_id"] == target or instance["parent_id"] == target:
                raise ValidationError("Assembly component is still referenced by instances.")
        for key in ("parts", "subassemblies"):
            entries = document.assembly[key]
            index = next((i for i, item in enumerate(entries) if item["id"] == target), None)
            if index is not None:
                entries.pop(index)
                return {"removed_component_id": target}
        raise NotFoundError("Assembly part or subassembly does not exist.")


def _call(method: str) -> Callable[..., dict[str, Any]]:
    def handler(self: AssemblyCommands, document: Document, command: dict[str, Any], *,
                progress: Callable[[dict[str, Any]], None] | None = None,
                project_id: str | None = None) -> dict[str, Any]:
        return getattr(self, method)(document, command)
    return handler


COMMAND_HANDLERS = {
    "create_assembly_part": _call("_create_assembly_part"),
    "create_subassembly": _call("_create_subassembly"),
    "add_assembly_instance": _call("_add_assembly_instance"),
    "transform_assembly_instance": _call("_transform_assembly_instance"),
    "remove_assembly_instance": _call("_remove_assembly_instance"),
    "remove_assembly_component": _call("_remove_assembly_component"),
}
