from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cadyx.workspace_files import WorkspaceArtifactStore
from cadyx.workspace_groups import ProjectGroupStore
from cadyx.workspace_projects import ProjectRecordStore
from cadyx.workspace_validations import ValidationRecordStore


class WorkspaceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

    def test_project_record_store_owns_project_json_round_trip(self) -> None:
        directory = self.root / "project"
        directory.mkdir()
        project_id = "11111111-1111-1111-1111-111111111111"
        store = ProjectRecordStore()
        project = store.new_record(project_id, "Round trip", timestamp="2026-10-17T00:00:00+00:00")

        store.save(directory, project)

        self.assertEqual(store.load(directory, project_id), project)
        self.assertTrue((directory / "project.json").is_file())

    def test_project_group_store_owns_schema_and_assignments(self) -> None:
        store = ProjectGroupStore(self.root)
        group = store.create("Fixtures")
        project_id = "22222222-2222-2222-2222-222222222222"

        store.assign(project_id, group["id"])

        self.assertEqual(store.list_groups(), [group])
        self.assertEqual(store.assignments(), {project_id: group["id"]})
        self.assertTrue(store.database_path.is_file())

    def test_validation_store_owns_per_project_database(self) -> None:
        directory = self.root / "project"
        directory.mkdir()
        store = ValidationRecordStore()
        result = {
            "revision": 3,
            "geometry_sha256": "a" * 64,
            "status": "pass",
        }

        record = store.record(directory, "body_dimensions", result)

        listed = store.list(directory)
        self.assertEqual(listed[0]["record_id"], record["record_id"])
        self.assertEqual(listed[0]["result"], result)
        self.assertTrue(store.database_path(directory).is_file())

    def test_artifact_store_lists_only_direct_user_files(self) -> None:
        directory = self.root / "project"
        directory.mkdir()
        (directory / "part.step").write_bytes(b"step")
        (directory / "project.json").write_text("{}", encoding="utf-8")
        (directory / ".cadyx-private").write_bytes(b"private")
        nested = directory / "nested"
        nested.mkdir()
        (nested / "hidden.txt").write_text("hidden", encoding="utf-8")
        store = WorkspaceArtifactStore(lambda _directory, name: name == "project.json")

        files = store.list_project_files(directory)

        self.assertEqual([item["name"] for item in files], ["part.step"])


if __name__ == "__main__":
    unittest.main()
