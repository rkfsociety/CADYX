"""Assembly commands; the Core owns the shared revision/lease/save transaction."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable
from uuid import uuid4

from .assemblies import IDENTITY_ROTATION, IDENTITY_TRANSLATION, validate_assembly
from .assembly_mates import IDENTITY_FRAME, solve_assembly_mates
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
                "body_id": body["id"], "material": None}
        document.assembly["parts"].append(part)
        return {"part_id": part["id"], "body_id": part["body_id"]}

    @staticmethod
    def _set_assembly_part_material(document: Document, command: dict[str, Any]) -> dict[str, Any]:
        _fields(command, {"part_id", "material"})
        part = next((entry for entry in document.assembly["parts"]
                     if entry["id"] == command["part_id"]), None)
        if part is None:
            raise NotFoundError("Assembly part does not exist.")
        from .assemblies import validate_material

        validate_material(command["material"])
        part["material"] = deepcopy(command["material"])
        return {"part_id": part["id"], "material": deepcopy(part["material"])}

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
            "grounded": False, "visible": True,
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
        if instance["grounded"]:
            raise ValidationError("A grounded assembly instance cannot be moved; unground it first.")
        for key in ("translation", "rotation"):
            if key in command:
                instance[key] = command[key].copy() if isinstance(command[key], list) else command[key]
        diagnostics = solve_assembly_mates(
            document.assembly, anchor_instance_id=instance["id"], require_unfixed_anchor=True
        )
        validate_assembly(document.assembly, document.objects)
        return {"instance_id": instance["id"], "translation": instance["translation"],
                "rotation": instance["rotation"], "kinematics": diagnostics}

    @staticmethod
    def _set_assembly_instance_properties(document: Document, command: dict[str, Any]) -> dict[str, Any]:
        _fields(command, {"instance_id"}, {"name", "grounded", "visible"})
        if len(command) == 2:
            raise ValidationError("Instance properties require at least one changed field.")
        instance = next((item for item in document.assembly["instances"]
                         if item["id"] == command["instance_id"]), None)
        if instance is None:
            raise NotFoundError("Assembly instance does not exist.")
        if "name" in command:
            instance["name"] = _text(command["name"], "Instance name")
        for key in ("grounded", "visible"):
            if key in command:
                if type(command[key]) is not bool:
                    raise ValidationError(f"Instance {key} must be boolean.")
                instance[key] = command[key]
        diagnostics = solve_assembly_mates(document.assembly)
        validate_assembly(document.assembly, document.objects)
        return {"instance_id": instance["id"], "name": instance["name"],
                "grounded": instance["grounded"], "visible": instance["visible"],
                "kinematics": diagnostics}

    @staticmethod
    def _remove_assembly_instance(document: Document, command: dict[str, Any]) -> dict[str, Any]:
        _fields(command, {"instance_id"})
        entries = document.assembly["instances"]
        index = next((i for i, entry in enumerate(entries) if entry["id"] == command["instance_id"]), None)
        if index is None:
            raise NotFoundError("Assembly instance does not exist.")
        if any(mate["a_instance_id"] == command["instance_id"]
               or mate["b_instance_id"] == command["instance_id"]
               for mate in document.assembly["mates"]):
            raise ValidationError("Remove the attached assembly mates before removing the instance.")
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

    @staticmethod
    def _add_assembly_mate(document: Document, command: dict[str, Any]) -> dict[str, Any]:
        _fields(command, {"kind", "a_instance_id", "b_instance_id"},
                {"name", "frame_a", "frame_b", "angle_deg", "offset_mm", "orientation"})
        mate = {
            "id": f"mate:{uuid4()}",
            "name": _text(command.get("name", command["kind"]), "Mate name"),
            "kind": command["kind"],
            "a_instance_id": command["a_instance_id"],
            "b_instance_id": command["b_instance_id"],
            "frame_a": deepcopy(command.get("frame_a", IDENTITY_FRAME)),
            "frame_b": deepcopy(command.get("frame_b", IDENTITY_FRAME)),
            "angle_deg": command.get("angle_deg", 0.0),
            "offset_mm": command.get("offset_mm", 0.0),
            "orientation": deepcopy(command.get("orientation", IDENTITY_ROTATION)),
        }
        document.assembly["mates"].append(mate)
        diagnostics = solve_assembly_mates(document.assembly, anchor_instance_id=mate["a_instance_id"])
        validate_assembly(document.assembly, document.objects)
        return {"mate_id": mate["id"], "kind": mate["kind"], "kinematics": diagnostics}

    @staticmethod
    def _set_assembly_mate(document: Document, command: dict[str, Any]) -> dict[str, Any]:
        _fields(command, {"mate_id"},
                {"name", "frame_a", "frame_b", "angle_deg", "offset_mm", "orientation"})
        if len(command) == 2:
            raise ValidationError("Mate update requires at least one changed field.")
        mate = next((item for item in document.assembly["mates"]
                     if item["id"] == command["mate_id"]), None)
        if mate is None:
            raise NotFoundError("Assembly mate does not exist.")
        for key in ("frame_a", "frame_b", "orientation", "angle_deg", "offset_mm"):
            if key in command:
                mate[key] = deepcopy(command[key])
        if "name" in command:
            mate["name"] = _text(command["name"], "Mate name")
        diagnostics = solve_assembly_mates(document.assembly, anchor_instance_id=mate["a_instance_id"])
        validate_assembly(document.assembly, document.objects)
        return {"mate_id": mate["id"], "kinematics": diagnostics}

    @staticmethod
    def _remove_assembly_mate(document: Document, command: dict[str, Any]) -> dict[str, Any]:
        _fields(command, {"mate_id"})
        mates = document.assembly["mates"]
        index = next((i for i, mate in enumerate(mates)
                      if mate["id"] == command["mate_id"]), None)
        if index is None:
            raise NotFoundError("Assembly mate does not exist.")
        return {"removed_mate_id": mates.pop(index)["id"]}

    @staticmethod
    def _solve_assembly_mates(document: Document, command: dict[str, Any]) -> dict[str, Any]:
        _fields(command, set(), {"anchor_instance_id"})
        report = solve_assembly_mates(document.assembly,
                                      anchor_instance_id=command.get("anchor_instance_id"))
        validate_assembly(document.assembly, document.objects)
        return report


def _call(method: str) -> Callable[..., dict[str, Any]]:
    def handler(self: AssemblyCommands, document: Document, command: dict[str, Any], *,
                progress: Callable[[dict[str, Any]], None] | None = None,
                project_id: str | None = None) -> dict[str, Any]:
        return getattr(self, method)(document, command)
    return handler


COMMAND_HANDLERS = {
    "create_assembly_part": _call("_create_assembly_part"),
    "set_assembly_part_material": _call("_set_assembly_part_material"),
    "create_subassembly": _call("_create_subassembly"),
    "add_assembly_instance": _call("_add_assembly_instance"),
    "transform_assembly_instance": _call("_transform_assembly_instance"),
    "set_assembly_instance_properties": _call("_set_assembly_instance_properties"),
    "remove_assembly_instance": _call("_remove_assembly_instance"),
    "remove_assembly_component": _call("_remove_assembly_component"),
    "add_assembly_mate": _call("_add_assembly_mate"),
    "set_assembly_mate": _call("_set_assembly_mate"),
    "remove_assembly_mate": _call("_remove_assembly_mate"),
    "solve_assembly_mates": _call("_solve_assembly_mates"),
}
