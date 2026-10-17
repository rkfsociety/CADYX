"""Fast public-contract checks; no native runtime or SDK is required."""
import unittest
from pathlib import Path

from cadyx.capabilities import COMMAND_CAPABILITIES, agent_api_capabilities
from cadyx.mcp_server import TOOLS
from cadyx.migrations import DOCUMENT_VERSION


class SourceContractTests(unittest.TestCase):
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
        branches = tool["inputSchema"]["properties"]["command"]["oneOf"]
        schemas = {item["properties"]["type"]["enum"][0]: item for item in branches}
        self.assertEqual(len(schemas), len(branches), "Duplicate MCP branches")
        self.assertEqual(schemas.keys(), commands.keys())
        for name, command in commands.items():
            with self.subTest(command=name):
                fields = set(command.get("required", []) + command.get("optional", []))
                properties = schemas[name]["properties"]
                self.assertEqual(set(properties), fields | {"type"})
                self.assertLessEqual(set(command.get("parameter_schemas", {})), fields)
                for field in fields:
                    self.assertEqual(properties[field], command.get("parameter_schemas", {}).get(field, {}))
