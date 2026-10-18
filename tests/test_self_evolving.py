from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cadyx.capability_registry import CapabilityRegistry
from cadyx.core import CadyxCore
from cadyx.errors import ValidationError
from cadyx.kernel import PrimitiveKernelAdapter
from cadyx.models import OwnerType
from cadyx.self_evolving import SelfEvolvingCapabilityEngine


class SelfEvolvingCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        root = Path(self.temporary_directory.name)
        self.core = CadyxCore(str(root / "projects"), kernel=PrimitiveKernelAdapter())
        self.engine = SelfEvolvingCapabilityEngine(
            self.core,
            CapabilityRegistry(root / "projects" / ".cadyx-capabilities.sqlite3"),
        )

    @staticmethod
    def definition(*, width: float = 2, version: str = "1.0.0") -> dict:
        return {
            "id": "test_box_builder",
            "title": "Test box builder",
            "description": "Builds a tested box using the existing CADYX box command.",
            "version": version,
            "input_schema": {
                "type": "object",
                "properties": {"width": {"type": "number", "minimum": 0.1}},
                "required": ["width"],
                "additionalProperties": False,
            },
            "output_schema": {"type": "object", "properties": {"body_id": {"type": "string"}},
                              "required": ["body_id"], "additionalProperties": False},
            "outputs": {"body_id": {"step": 0, "field": "body_id"}},
            "dependencies": [],
            "steps": [{"command": {
                "type": "create_box",
                "width": {"$input": "width"},
                "height": 3,
                "depth": 4,
                "name": "Capability test body",
            }}],
            "test_input": {"width": width},
        }

    def test_missing_commands_are_reported_and_validated_recipes_persist(self) -> None:
        self.assertEqual(
            self.engine.missing_command({"type": "create_planetary_ring"})["error"],
            "CAPABILITY_MISSING",
        )
        self.assertIsNone(self.engine.missing_command({"type": "create_box"}))

        result = self.engine.register_proposal(self.definition())
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["test_result"]["bodies_validated"], 1)
        restored = SelfEvolvingCapabilityEngine(self.core, self.engine.registry)
        self.assertEqual(restored.available()[0]["id"], "test_box_builder")
        self.assertEqual(self.engine.registry.get_ready(
            "test_box_builder", command_contract_hash=result["compatibility"]["command_contract_hash"],
        )["version"], "1.0.0")

    def test_failed_geometry_proposal_never_enters_ready_registry(self) -> None:
        definition = self.definition()
        definition["steps"][0]["command"]["width"] = -1
        with self.assertRaisesRegex(ValidationError, "geometry test failed"):
            self.engine.register_proposal(definition)
        self.assertEqual(self.engine.available(), [])

    def test_recipe_rejects_file_access_and_arbitrary_code(self) -> None:
        for command in (
            {"type": "import_step", "file": "source.step"},
            {"type": "execute_python", "code": "import os"},
        ):
            definition = self.definition()
            definition["steps"][0]["command"] = command
            with self.subTest(command=command):
                with self.assertRaisesRegex(ValidationError, "unavailable or unsafe"):
                    self.engine.register_proposal(definition)

    def test_checkpoint_rollback_restores_previous_validated_version(self) -> None:
        original = self.engine.register_proposal(self.definition(width=2))
        updated = self.engine.register_proposal(self.definition(width=3, version="1.0.1"))
        self.assertEqual(updated["version"], "1.0.1")
        restored = self.engine.registry.rollback("test_box_builder")
        self.assertTrue(restored["restored"])
        current = self.engine.available()[0]
        self.assertEqual(current["test_result"]["test_input"], {"width": 2})
        self.assertEqual(current["id"], original["id"])

    def test_recipe_runs_against_a_model_through_command_api(self) -> None:
        registered = self.engine.register_proposal(self.definition())
        project_id = self.core.create_project("Capability execution") ["project"]["id"]

        def execute(arguments):
            state = self.core.inspect_project(project_id)
            revision = state["document"]["revision"]
            resumed = self.core.resume_agent(project_id, "capability-test", revision)
            lease_id = resumed["lease"]["lease_id"]
            try:
                return self.core.execute_command(
                    project_id, lease_id, arguments["command"],
                    expected_owner_type=OwnerType.AGENT, expected_revision=revision,
                )
            finally:
                self.core.stop_agent(project_id, lease_id)

        result = self.engine.execute(registered["id"], {"width": 5}, execute)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.core.inspect_project(project_id)["document"]["revision"], 1)


if __name__ == "__main__":
    unittest.main()
