"""In-memory assembly validation contract; safe for the source AppContainer."""
from __future__ import annotations

import unittest

from cadyx.commands import CommandSystem
from cadyx.kernel import PrimitiveKernelAdapter
from cadyx.models import Document
from cadyx.project_checks import check_project


class _MemoryWorkspace:
    def __init__(self, document: Document) -> None:
        self.document = document

    def load(self, _project_id: str) -> dict:
        return {"document": self.document.to_dict()}


class _MemoryCore:
    def __init__(self, document: Document) -> None:
        self.workspace = _MemoryWorkspace(document)
        self.kernel = object()

    @staticmethod
    def _check_revision(document: Document, expected: int) -> None:
        if document.revision != expected:
            raise ValueError("revision mismatch")

    @staticmethod
    def _validate_geometry_once(_project_id: str, document: Document) -> None:
        document.validate()


class AssemblyProjectCheckContractTests(unittest.TestCase):
    def test_valid_saved_assembly_is_measured_instead_of_marked_unsupported(self) -> None:
        document = Document.create()
        commands = CommandSystem(PrimitiveKernelAdapter())

        def execute(command_type: str, **fields: object) -> dict:
            return commands.execute(document, {"type": command_type, **fields})

        body_id = execute("create_box", width=2, height=2, depth=2)["body_id"]
        part_id = execute("create_assembly_part", body_id=body_id)["part_id"]
        first = execute("add_assembly_instance", target_id=part_id)["instance_id"]
        second = execute("add_assembly_instance", target_id=part_id)["instance_id"]
        execute("add_assembly_mate", kind="fixed", a_instance_id=first,
                b_instance_id=second)

        report = check_project(_MemoryCore(document), "project:memory", document.revision,
                               require_assembly=True)
        stage = next(item for item in report["stages"] if item["id"] == "assembly")
        self.assertEqual(stage["status"], "pass")
        self.assertEqual(stage["evidence"]["instance_count"], 2)
        self.assertEqual(stage["evidence"]["mate_count"], 1)


if __name__ == "__main__":
    unittest.main()
