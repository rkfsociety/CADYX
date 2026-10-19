import tempfile
import unittest
from pathlib import Path

from cadyx.commands import CommandSystem
from cadyx.kernel import OCCTKernelAdapter
from cadyx.models import Document
from cadyx.project_checks import check_project
from cadyx.workspace_validations import ValidationRecordStore


class ProjectCheckPersistenceTests(unittest.TestCase):
    def test_project_check_fingerprint_can_be_persisted_and_read(self):
        kernel = OCCTKernelAdapter()
        document = Document.create()
        created = CommandSystem(kernel).execute(document, {
            "type": "create_box", "width": 2, "height": 3, "depth": 5,
        })

        class Workspace:
            def load(self, project_id):
                return {"document": document.to_dict()}

        class Core:
            def __init__(self):
                self.workspace = Workspace()
                self.kernel = kernel

            def _check_revision(self, current, expected):
                if current.revision != expected:
                    raise AssertionError("revision mismatch")

            def _validate_geometry_once(self, project_id, current):
                current.validate()
                for item in current.objects:
                    if item.get("type") == "body":
                        if self.kernel.metrics(item["geometry"]).get("brep_valid") is not True:
                            raise AssertionError("invalid B-rep")

        report = check_project(Core(), "project-fixture", document.revision,
                               export_body_id=created["body_id"], strict=True)
        self.assertRegex(report["geometry_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(report["status"], "pass")
        self.assertTrue(report["passed"])

        with tempfile.TemporaryDirectory() as directory:
            store = ValidationRecordStore()
            record = store.record(Path(directory), "project_check", report)
            self.assertEqual(record["geometry_sha256"], report["geometry_sha256"])
            saved = store.list(Path(directory))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["kind"], "project_check")
        self.assertEqual(saved[0]["result"], report)


if __name__ == "__main__":
    unittest.main()
