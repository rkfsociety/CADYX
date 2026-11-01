from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cadyx.errors import NotFoundError, ValidationError
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

    def test_group_reads_do_not_acquire_schema_write_locks(self) -> None:
        store = ProjectGroupStore(self.root)
        self.assertTrue(store.database_path.is_file(), "schema is initialized at startup")
        statements: list[str] = []

        class TracedConnection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                statements.append(sql)
                return super().execute(sql, parameters)

        original_connect = sqlite3.connect

        def traced_connect(*args, **kwargs):
            return original_connect(*args, **kwargs, factory=TracedConnection)

        with patch("cadyx.workspace_groups.sqlite3.connect", side_effect=traced_connect):
            self.assertEqual(store.list_groups(), [])
            self.assertEqual(store.assignments(), {})
        self.assertFalse(any("CREATE TABLE" in sql for sql in statements))
        self.assertEqual(sum("SELECT" in sql for sql in statements), 2)

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

    def test_validation_listing_does_not_recreate_database_deleted_during_open(self) -> None:
        directory = self.root / "project"
        directory.mkdir()
        store = ValidationRecordStore()
        database_path = store.database_path(directory)
        self.assertEqual(store.list(directory), [])
        store.record(
            directory, "body_dimensions",
            {"revision": 1, "geometry_sha256": "a" * 64},
        )
        original_connect = sqlite3.connect

        def remove_before_open(*args, **kwargs):
            database_path.unlink()
            return original_connect(*args, **kwargs)

        with patch("cadyx.workspace_validations.sqlite3.connect", side_effect=remove_before_open):
            with self.assertRaisesRegex(ValidationError, "could not be read"):
                store.list(directory)
        self.assertFalse(database_path.exists())

    def test_validation_record_rejects_deleting_project_without_creating_database(self) -> None:
        directory = self.root / "project"
        directory.mkdir()
        marker = directory / ".cadyx-deleting"
        marker.touch()
        store = ValidationRecordStore()
        with self.assertRaises(NotFoundError):
            store.record(
                directory, "body_dimensions",
                {"revision": 1, "geometry_sha256": "a" * 64},
            )
        with self.assertRaises(NotFoundError):
            store.list(directory)
        self.assertFalse(store.database_path(directory).exists())

    def test_validation_record_rolls_back_if_deletion_starts_during_insert(self) -> None:
        directory = self.root / "project"
        directory.mkdir()
        store = ValidationRecordStore()
        result = {"revision": 1, "geometry_sha256": "a" * 64}
        first = store.record(directory, "body_dimensions", result)
        marker = directory / ".cadyx-deleting"
        original_connect = sqlite3.connect

        class DeletingConnection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                cursor = super().execute(sql, parameters)
                if sql.startswith("INSERT INTO validation_record "):
                    marker.touch()
                return cursor

        def connect_during_deletion(*args, **kwargs):
            return original_connect(*args, **kwargs, factory=DeletingConnection)

        with patch("cadyx.workspace_validations.sqlite3.connect", side_effect=connect_during_deletion):
            with self.assertRaises(NotFoundError):
                store.record(directory, "body_dimensions", result)
        marker.unlink()
        self.assertEqual([item["record_id"] for item in store.list(directory)], [first["record_id"]])

    def test_validation_record_does_not_recreate_database_deleted_during_open(self) -> None:
        directory = self.root / "project"
        directory.mkdir()
        store = ValidationRecordStore()
        database_path = store.database_path(directory)
        store.record(
            directory, "body_dimensions",
            {"revision": 1, "geometry_sha256": "a" * 64},
        )
        original_connect = sqlite3.connect

        def remove_before_open(*args, **kwargs):
            database_path.unlink()
            return original_connect(*args, **kwargs)

        with patch("cadyx.workspace_validations.sqlite3.connect", side_effect=remove_before_open):
            with self.assertRaisesRegex(ValidationError, "could not be recorded"):
                store.record(
                    directory, "body_dimensions",
                    {"revision": 2, "geometry_sha256": "b" * 64},
                )
        self.assertFalse(database_path.exists())

    def test_validation_listing_rejects_deletion_during_query(self) -> None:
        directory = self.root / "project"
        directory.mkdir()
        store = ValidationRecordStore()
        store.record(
            directory, "body_dimensions",
            {"revision": 1, "geometry_sha256": "a" * 64},
        )
        marker = directory / ".cadyx-deleting"
        original_connect = sqlite3.connect

        class DeletingConnection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                cursor = super().execute(sql, parameters)
                if sql.startswith("SELECT id, kind"):
                    marker.touch()
                return cursor

        def connect_during_deletion(*args, **kwargs):
            return original_connect(*args, **kwargs, factory=DeletingConnection)

        with patch("cadyx.workspace_validations.sqlite3.connect", side_effect=connect_during_deletion):
            with self.assertRaises(NotFoundError):
                store.list(directory)
        marker.unlink()
        self.assertEqual(len(store.list(directory)), 1)

    def test_artifact_store_lists_only_direct_user_files(self) -> None:
        directory = self.root / "project"
        directory.mkdir()
        directory = directory.resolve()
        (directory / "part.step").write_bytes(b"step")
        (directory / "project.json").write_text("{}", encoding="utf-8")
        (directory / ".cadyx-private").write_bytes(b"private")
        nested = directory / "nested"
        nested.mkdir()
        (nested / "hidden.txt").write_text("hidden", encoding="utf-8")
        store = WorkspaceArtifactStore(lambda _directory, name: name == "project.json")

        files = store.list_project_files(directory)

        self.assertEqual([item["name"] for item in files], ["part.step"])

    def test_artifact_store_lists_project_subfolders_and_their_direct_contents(self) -> None:
        directory = (self.root / "project-with-scan").resolve()
        series = directory / "revopoint_20261021T103419Z"
        depth = series / "depth_frames"
        directory.mkdir()
        depth.mkdir(parents=True)
        (depth / "frame_00000.raw").write_bytes(b"Y16")
        (depth / ".cadyx-private").write_bytes(b"private")
        (series / "capture_manifest.json").write_text("{}", encoding="utf-8")
        (directory / "outside.txt").write_text("root", encoding="utf-8")
        store = WorkspaceArtifactStore(lambda _directory, name: name.startswith(".cadyx"))

        folders = store.list_project_folders(directory)
        run_files = store.list_project_subfolder_files(directory, series.name)
        children = store.list_project_subfolders(directory, series.name)
        raw_files = store.list_project_subfolder_files(directory, f"{series.name}/depth_frames")
        manifest_path = store.resolve_project_subfolder_file(directory, series.name, "capture_manifest.json")

        self.assertEqual([item["name"] for item in folders], [series.name])
        self.assertEqual([item["name"] for item in run_files], ["capture_manifest.json"])
        self.assertEqual([item["name"] for item in children], ["depth_frames"])
        self.assertTrue(manifest_path.is_file())
        self.assertEqual([item["name"] for item in raw_files], ["frame_00000.raw"])
        for unsafe in ("..", "nested/name", ".private", "missing"):
            with self.subTest(folder_name=unsafe), self.assertRaises(NotFoundError):
                store.list_project_subfolder_files(directory, unsafe)


if __name__ == "__main__":
    unittest.main()
