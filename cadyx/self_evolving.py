"""Safe capability discovery, real-geometry recipe tests and reuse.

Providers may propose declarative compositions of existing CADYX commands.
The proposal language has no code execution, shell, file access, imports or
dependency installation. A proposal becomes available only after an isolated
CAD document passes the current Command System and kernel validators.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from typing import Any, Mapping

from .capabilities import COMMAND_CAPABILITIES
from .capability_registry import CapabilityRegistry
from .errors import ValidationError
from .models import Document

_BLOCKED_COMMANDS = {
    "clear_model", "remove_body", "remove_objects", "import_scan", "set_scan_parameter",
    "import_step", "export_brep", "export_stl", "export_mesh",
}
_MAX_DEFINITION_BYTES = 256 * 1024


def command_contract_hash() -> str:
    payload = json.dumps(COMMAND_CAPABILITIES, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SelfEvolvingCapabilityEngine:
    """Host the provider-facing capability proposal and execution lifecycle."""

    def __init__(self, core: Any, registry: CapabilityRegistry) -> None:
        self.core = core
        self.registry = registry
        self._commands = {entry["type"] for entry in COMMAND_CAPABILITIES} - _BLOCKED_COMMANDS
        self._command_hash = command_contract_hash()

    def available(self) -> list[dict[str, Any]]:
        return self.registry.list(ready_only=True)

    @property
    def built_in_command_types(self) -> list[str]:
        return sorted(self._commands)

    def missing_command(self, command: Any) -> dict[str, Any] | None:
        if not isinstance(command, dict) or not isinstance(command.get("type"), str):
            return None
        if command["type"] in self._commands:
            return None
        return {
            "error": "CAPABILITY_MISSING",
            "command_type": command["type"],
            "message": "No built-in command has this type. Check reusable capabilities, then propose a tested declarative composition if existing commands can express it.",
            "available_capabilities": [item["id"] for item in self.available()],
        }

    def register_proposal(self, definition: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self.prepare_proposal(definition)
        test_result = self._test_definition(normalized)
        return self.register_tested(normalized, test_result)

    def prepare_proposal(self, definition: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self._validate_definition(definition)
        current = self.registry.get_ready(normalized["id"], command_contract_hash=self._command_hash)
        if current is not None:
            version = tuple(int(part) for part in normalized["version"].split("."))
            current_version = tuple(int(part) for part in current["version"].split("."))
            if version <= current_version:
                raise ValidationError("A capability replacement must use a newer MAJOR.MINOR.PATCH version.")
        for dependency in normalized["dependencies"]:
            if dependency == normalized["id"]:
                raise ValidationError("A capability cannot depend on itself.")
            if self.registry.get_ready(dependency, command_contract_hash=self._command_hash) is None:
                raise ValidationError(f"Capability dependency {dependency!r} is missing or incompatible.")
        graph = {
            item["id"]: set(item["dependencies"])
            for item in self.registry.list(ready_only=True)
        }
        graph[normalized["id"]] = set(normalized["dependencies"])
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(capability_id: str) -> None:
            if capability_id in visiting:
                raise ValidationError("Capability dependencies cannot contain cycles.")
            if capability_id in visited:
                return
            visiting.add(capability_id)
            for dependency in graph.get(capability_id, set()):
                visit(dependency)
            visiting.remove(capability_id)
            visited.add(capability_id)

        for capability_id in graph:
            visit(capability_id)
        return normalized

    def test_proposal(self, definition: dict[str, Any]) -> dict[str, Any]:
        return self._test_definition(definition)

    def register_tested(self, definition: dict[str, Any], test_result: dict[str, Any]) -> dict[str, Any]:
        normalized = copy.deepcopy(definition)
        if test_result.get("status") != "passed":
            raise ValidationError("Capability tests did not pass.")
        normalized["compatibility"] = {"command_contract_hash": self._command_hash}
        normalized["invocation_tool"] = self._tool_name(normalized["id"])
        return self.registry.promote(normalized, test_result=test_result)

    def execute(self, capability_id: str, arguments: Mapping[str, Any], execute_command) -> dict[str, Any]:
        capability = self.registry.get_ready(capability_id, command_contract_hash=self._command_hash)
        if capability is None:
            raise ValidationError("Capability is missing, unvalidated, or incompatible with this CADYX command contract.")
        self._validate_input(capability["input_schema"], arguments)
        results = []
        for index, step in enumerate(capability["steps"]):
            command = self._resolve(step["command"], arguments, results)
            try:
                result = execute_command({"command": command})
            except Exception as exc:
                return {
                    "status": "partial",
                    "capability_id": capability_id,
                    "failed_step": index,
                    "results": results,
                    "error": {"error": type(exc).__name__, "message": str(exc)},
                }
            if isinstance(result, dict) and "error" in result:
                return {"status": "partial", "failed_step": index, "results": results, "error": result}
            results.append(self._compact_command_result(result))
        outputs = self._collect_outputs(capability, results)
        self._validate_input(capability["output_schema"], outputs)
        return {"status": "completed", "capability_id": capability_id, "outputs": outputs, "results": results}

    def _test_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
        self._validate_input(definition["input_schema"], definition["test_input"])
        document = Document.create()
        results: list[dict[str, Any]] = []
        for index, step in enumerate(definition["steps"]):
            command = self._resolve(step["command"], definition["test_input"], results)
            try:
                result = self.core.commands.execute(document, command)
                document.validate()
                for obj in document.objects:
                    if obj.get("type") == "body" and isinstance(obj.get("geometry"), dict):
                        self.core.kernel.validate(obj["geometry"])
            except Exception as exc:
                raise ValidationError(f"Capability geometry test failed at step {index}: {exc}.") from exc
            results.append(result)
        bodies = [obj for obj in document.objects if obj.get("type") == "body"]
        if not bodies:
            raise ValidationError("Capability test must create at least one validated body.")
        outputs = self._collect_outputs(definition, results)
        self._validate_input(definition["output_schema"], outputs)
        return {
            "status": "passed",
            "test_input": definition["test_input"],
            "steps_passed": len(results),
            "bodies_validated": len(bodies),
            "outputs": outputs,
            "document_revision": document.revision,
        }

    def _validate_definition(self, value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ValidationError("Capability proposal must be an object.")
        required = {"id", "title", "description", "version", "input_schema", "output_schema", "outputs", "dependencies", "steps", "test_input"}
        if set(value) != required:
            raise ValidationError("Capability proposal has missing or unexpected fields.")
        data = copy.deepcopy(dict(value))
        if not isinstance(data["id"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", data["id"]):
            raise ValidationError("Capability id must be a lowercase stable identifier.")
        for field in ("title", "description", "version"):
            if not isinstance(data[field], str) or not data[field].strip() or len(data[field]) > 500:
                raise ValidationError(f"Capability {field} must be non-empty text of at most 500 characters.")
        if not re.fullmatch(r"[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}", data["version"]):
            raise ValidationError("Capability version must use MAJOR.MINOR.PATCH numeric form.")
        try:
            encoded_definition = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValidationError("Capability proposal must contain finite JSON data.") from exc
        if len(encoded_definition.encode("utf-8")) > _MAX_DEFINITION_BYTES:
            raise ValidationError("Capability proposal exceeds the 256 KiB definition limit.")
        self._validate_schema(data["input_schema"])
        self._validate_schema(data["output_schema"])
        if not isinstance(data["steps"], list) or not data["steps"]:
            raise ValidationError("Capability steps must contain at least one command.")
        if not isinstance(data["outputs"], dict) or set(data["outputs"]) != set(data["output_schema"]["properties"]):
            raise ValidationError("Capability outputs must map every declared output field to a command result.")
        for output in data["outputs"].values():
            if (not isinstance(output, dict) or set(output) != {"step", "field"}
                    or type(output["step"]) is not int or not 0 <= output["step"] < len(data["steps"])
                    or not isinstance(output["field"], str)):
                raise ValidationError("Capability output references must select a command result field.")
        if not isinstance(data["dependencies"], list) or len(data["dependencies"]) > 32 or any(not isinstance(x, str) for x in data["dependencies"]):
            raise ValidationError("Capability dependencies must be a list of capability ids.")
        for step in data["steps"]:
            if not isinstance(step, dict) or set(step) != {"command"} or not isinstance(step["command"], dict):
                raise ValidationError("Each capability step must contain one Command System command.")
            command_type = step["command"].get("type")
            if command_type not in self._commands:
                raise ValidationError(f"Capability step command is unavailable or unsafe: {command_type!r}.")
            self._validate_references(step["command"], len(data["steps"]))
        if not isinstance(data["test_input"], dict):
            raise ValidationError("Capability test_input must be an object.")
        data["compatibility"] = {"command_contract_hash": self._command_hash}
        data["invocation_tool"] = self._tool_name(data["id"])
        return data

    @staticmethod
    def _validate_schema(schema: Any) -> None:
        if not isinstance(schema, dict) or set(schema) - {"type", "properties", "required", "additionalProperties"}:
            raise ValidationError("Only a bounded JSON object input schema is supported.")
        properties = schema.get("properties")
        required = schema.get("required", [])
        if schema.get("type") != "object" or schema.get("additionalProperties", False) is not False:
            raise ValidationError("Capability input schema must be a closed JSON object schema.")
        if not isinstance(properties, dict) or len(properties) > 32 or not isinstance(required, list):
            raise ValidationError("Capability schema properties or required fields are invalid.")
        for name, spec in properties.items():
            if (not isinstance(name, str) or not isinstance(spec, dict)
                    or set(spec) - {"type", "minimum", "maximum"}
                    or spec.get("type") not in {"string", "number", "integer", "boolean"}):
                raise ValidationError("Capability inputs support only bounded scalar string, number, integer and boolean values.")
            for bound in ("minimum", "maximum"):
                if bound in spec and (
                    spec["type"] not in {"number", "integer"}
                    or isinstance(spec[bound], bool)
                    or not isinstance(spec[bound], (int, float))
                    or not math.isfinite(spec[bound])
                ):
                    raise ValidationError("Capability numeric bounds must be finite numbers.")
            if "minimum" in spec and "maximum" in spec and spec["minimum"] > spec["maximum"]:
                raise ValidationError("Capability input minimum cannot exceed maximum.")
        if (any(not isinstance(name, str) or name not in properties for name in required)
                or len(set(required)) != len(required)):
            raise ValidationError("Capability required inputs must be declared properties.")

    def _validate_input(self, schema: dict[str, Any], arguments: Mapping[str, Any]) -> None:
        if not isinstance(arguments, Mapping):
            raise ValidationError("Capability arguments must be an object.")
        properties = schema["properties"]
        required = schema.get("required", [])
        if set(arguments) - set(properties) or set(required) - set(arguments):
            raise ValidationError("Capability arguments do not match its declared input schema.")
        for name, value in arguments.items():
            kind = properties[name]["type"]
            good = (
                (kind == "string" and isinstance(value, str))
                or (kind == "boolean" and isinstance(value, bool))
                or (kind == "integer" and type(value) is int)
                or (kind == "number" and not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value))
            )
            if not good:
                raise ValidationError(f"Capability input {name!r} must be {kind}.")
            if kind in {"number", "integer"}:
                if "minimum" in properties[name] and value < properties[name]["minimum"]:
                    raise ValidationError(f"Capability input {name!r} is below its minimum.")
                if "maximum" in properties[name] and value > properties[name]["maximum"]:
                    raise ValidationError(f"Capability input {name!r} exceeds its maximum.")

    @staticmethod
    def _validate_references(value: Any, step_count: int) -> None:
        if isinstance(value, dict):
            if "$input" in value:
                if set(value) != {"$input"} or not isinstance(value["$input"], str):
                    raise ValidationError("Capability input reference is malformed.")
            elif "$step" in value:
                if set(value) != {"$step", "field"} or type(value["$step"]) is not int or not 0 <= value["$step"] < step_count or not isinstance(value["field"], str):
                    raise ValidationError("Capability step reference is malformed.")
            else:
                for child in value.values():
                    SelfEvolvingCapabilityEngine._validate_references(child, step_count)
        elif isinstance(value, list):
            for child in value:
                SelfEvolvingCapabilityEngine._validate_references(child, step_count)

    @staticmethod
    def _resolve(value: Any, inputs: Mapping[str, Any], results: list[dict[str, Any]]) -> Any:
        if isinstance(value, dict) and set(value) == {"$input"}:
            name = value["$input"]
            if name not in inputs:
                raise ValidationError(f"Capability input reference {name!r} is not defined.")
            return copy.deepcopy(inputs[name])
        if isinstance(value, dict) and set(value) == {"$step", "field"}:
            index = value["$step"]
            if index >= len(results) or value["field"] not in results[index]:
                raise ValidationError("Capability references a missing output from an earlier command.")
            return copy.deepcopy(results[index][value["field"]])
        if isinstance(value, dict):
            return {key: SelfEvolvingCapabilityEngine._resolve(child, inputs, results) for key, child in value.items()}
        if isinstance(value, list):
            return [SelfEvolvingCapabilityEngine._resolve(child, inputs, results) for child in value]
        return copy.deepcopy(value)

    @staticmethod
    def _collect_outputs(definition: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
        outputs = {}
        for name, reference in definition["outputs"].items():
            index = reference["step"]
            field = reference["field"]
            if index >= len(results) or field not in results[index]:
                raise ValidationError(f"Capability output {name!r} is missing from its command result.")
            outputs[name] = copy.deepcopy(results[index][field])
        return outputs

    @staticmethod
    def _tool_name(capability_id: str) -> str:
        return "cadyx_capability_" + re.sub(r"[^a-z0-9_]", "_", capability_id)

    @staticmethod
    def _compact_command_result(result: dict[str, Any]) -> dict[str, Any]:
        command_result = result.get("command_result")
        if not isinstance(command_result, dict):
            history = result.get("history_entry")
            command_result = history.get("result") if isinstance(history, dict) else None
        compact = {
            key: copy.deepcopy(value) for key, value in result.items()
            if key not in {"document", "history_entry", "command_result"}
        }
        if isinstance(command_result, dict):
            compact.update({
                key: copy.deepcopy(value) for key, value in command_result.items()
                if key not in {"geometry", "document", "history_entry", "command_result"}
            })
        document = result.get("document")
        if isinstance(document, dict):
            objects = document.get("objects", [])
            counts: dict[str, int] = {}
            if isinstance(objects, list):
                for item in objects:
                    if isinstance(item, dict) and isinstance(item.get("type"), str):
                        counts[item["type"]] = counts.get(item["type"], 0) + 1
            compact["document"] = {
                "revision": document.get("revision"),
                "object_count": len(objects) if isinstance(objects, list) else 0,
                "object_counts": counts,
            }
        return compact
