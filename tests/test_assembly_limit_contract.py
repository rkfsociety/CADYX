"""Assembly mate limit discovery contract shared by Agent clients."""
from __future__ import annotations

import unittest

from cadyx.capabilities import COMMAND_CAPABILITIES


class AssemblyMateLimitContractTests(unittest.TestCase):
    def test_add_and_set_mate_advertise_nullable_limit_fields(self) -> None:
        commands = {command["type"]: command for command in COMMAND_CAPABILITIES}
        fields = {"angle_min_deg", "angle_max_deg", "offset_min_mm", "offset_max_mm"}
        for command_name in ("add_assembly_mate", "set_assembly_mate"):
            with self.subTest(command=command_name):
                schema = commands[command_name]["parameter_schemas"]["limits"]
                self.assertEqual(set(schema["properties"]), fields)
                self.assertEqual(set(schema["required"]), fields)
                self.assertFalse(schema["additionalProperties"])
                self.assertTrue(all(schema["properties"][field]["type"] == ["number", "null"]
                                    for field in fields))


if __name__ == "__main__":
    unittest.main()
