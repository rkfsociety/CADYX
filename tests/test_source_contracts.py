"""Fast public-contract checks; no native runtime or SDK is required."""
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

from cadyx.api_contracts import VALIDATION_OPERATIONS, validate_request_fields
from cadyx.capabilities import COMMAND_CAPABILITIES, agent_api_capabilities
from cadyx.errors import ValidationError
from cadyx.mcp_server import MCPApplication, TOOLS
from cadyx.migrations import DOCUMENT_VERSION


class SourceContractTests(unittest.TestCase):
    def test_validation_operations_match_discovery_and_mcp(self):
        discovered = agent_api_capabilities()["operations"]
        self.assertEqual(discovered, list(VALIDATION_OPERATIONS.values()))
        tools = {tool["name"]: tool for tool in TOOLS}
        for operation in discovered:
            with self.subTest(operation=operation["tool_name"]):
                schema = operation["input_schema"]
                tool_schema = tools[operation["tool_name"]]["inputSchema"]
                self.assertEqual(tool_schema["required"], ["project_id", *schema["required"]])
                self.assertEqual({k: v for k, v in tool_schema["properties"].items()
                                  if k != "project_id"}, schema["properties"])
                self.assertFalse(tool_schema["additionalProperties"])
                self.assertLessEqual(operation["defaults"].keys(), schema["properties"].keys())
                self.assertIn(
                    {key: operation[key] for key in ("method", "path", "description")},
                    agent_api_capabilities()["routes"],
                )

    def test_validation_request_fields_reject_missing_and_unknown_fields(self):
        for name, operation in VALIDATION_OPERATIONS.items():
            body = dict.fromkeys(operation["input_schema"]["required"], "placeholder")
            validate_request_fields(name, body)
            for invalid in [None, {**body, "unexpected": 1}, *(
                {key: value for key, value in body.items() if key != missing}
                for missing in body
            )]:
                with self.subTest(operation=name, body=invalid):
                    with self.assertRaises(ValidationError) as error:
                        validate_request_fields(name, invalid)
                    self.assertEqual(str(error.exception), operation["field_error"])

    def test_validation_mcp_forwards_required_optional_and_default_values(self):
        for operation in VALIDATION_OPERATIONS.values():
            for explicit in (False, True):
                with self.subTest(operation=operation["tool_name"], explicit=explicit):
                    api = Mock()
                    app = MCPApplication(api)
                    body = dict.fromkeys(operation["input_schema"]["required"], "value")
                    expected = {**body, **operation["defaults"]}
                    if explicit:
                        body.update(persist=True)
                        if "method" in operation["defaults"]:
                            body["method"] = "global_minimum"
                        expected.update(body)
                    app._dispatch(operation["tool_name"], {"project_id": "a/b", **body})
                    api.post.assert_called_once_with(
                        operation["path"].replace("{id}", "a%2Fb"), expected,
                    )

    def test_discovery_returns_independent_nested_contracts(self):
        original = deepcopy(VALIDATION_OPERATIONS)
        result = agent_api_capabilities()
        result["operations"][0]["input_schema"]["properties"].clear()
        result["commands"][0]["required"].clear()
        self.assertEqual(VALIDATION_OPERATIONS, original)
        self.assertEqual(agent_api_capabilities()["commands"], list(COMMAND_CAPABILITIES))

    def test_discovery_contracts_exist_and_use_current_document(self):
        root = Path(__file__).resolve().parents[1]
        contracts = agent_api_capabilities()["contracts"]
        current = f".codex/docs/document-model-v{DOCUMENT_VERSION}.md"
        self.assertIn(current, contracts)
        for reference in contracts:
            path = reference.split("#", 1)[0]
            with self.subTest(reference=reference):
                self.assertTrue((root / path).is_file(), reference)
                if Path(path).name.startswith("document-model-v"):
                    self.assertEqual(path, current)

    def test_command_discovery_and_mcp_schema_parity(self):
        commands = {item["type"]: item for item in COMMAND_CAPABILITIES}
        self.assertEqual(len(commands), len(COMMAND_CAPABILITIES), "Duplicate command types")
        self.assertEqual(agent_api_capabilities()["commands"], list(COMMAND_CAPABILITIES))
        tool = next(item for item in TOOLS if item["name"] == "cadyx_execute_command")
        command_schema = tool["inputSchema"]["properties"]["command"]
        self.assertEqual(command_schema["properties"]["type"], {"type": "string", "minLength": 1})
        self.assertTrue(command_schema["additionalProperties"])
        # The generic tool accepts commands registered after MCP startup; Core
        # remains the authoritative validator for both built-in and new fields.
        self.assertIn("add_sketch_involute_gear", commands)
