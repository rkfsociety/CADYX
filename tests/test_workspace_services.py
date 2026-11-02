from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
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

    def test_validation_listing_rejects_corrupt_persisted_results(self) -> None:
        directory = self.root / "project"
        directory.mkdir()
        store = ValidationRecordStore()
        store.record(directory, "body_dimensions", {
            "revision": 1, "geometry_sha256": "a" * 64,
        })
        database = store.database_path(directory)
        prefix = '{"revision":1,"geometry_sha256":"' + "a" * 64 + '"'
        payloads = (
            prefix + ',"value":NaN}',
            prefix + ',"value":Infinity}',
            prefix + ',"value":-Infinity}',
            prefix + ',"value":1e999}',
            '{"revision":2,"geometry_sha256":"' + "a" * 64 + '"}',
            '{"revision":1,"geometry_sha256":"' + "b" * 64 + '"}',
            "[]",
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                with closing(sqlite3.connect(database)) as connection, connection:
                    connection.execute("UPDATE validation_record SET result_json = ?", (payload,))
                with self.assertRaisesRegex(ValidationError, "could not be read"):
                    store.list(directory)

        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute(
                "UPDATE validation_record SET result_json = ?, revision = -1",
                (prefix + "}",),
            )
        with self.assertRaisesRegex(ValidationError, "could not be read"):
            store.list(directory)

    def test_validation_record_rejects_json_encoder_recursion(self) -> None:
        directory = self.root / "project"
        directory.mkdir()
        store = ValidationRecordStore()
        result = {"revision": 1, "geometry_sha256": "a" * 64}
        with patch("cadyx.workspace_validations.json.dumps", side_effect=RecursionError("too deep")):
            with self.assertRaisesRegex(ValidationError, "cannot be serialized"):
                store.record(directory, "body_dimensions", result)
        self.assertFalse(store.database_path(directory).exists())

    def test_validation_record_rejects_invalid_revisions_before_sqlite_open(self) -> None:
        directory = self.root / "project"
        directory.mkdir()
        store = ValidationRecordStore()
        for revision in (-1, True, 1 << 63, 10**400):
            with self.subTest(revision=revision):
                with self.assertRaises(ValidationError):
                    store.record(directory, "body_dimensions",
                                 {"revision": revision, "geometry_sha256": "a" * 64})
                self.assertFalse(store.database_path(directory).exists())

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

    def test_artifact_paths_accept_noncanonical_parent_aliases(self) -> None:
        directory = self.root / "project"
        directory.mkdir()
        (directory / "alias").mkdir()
        (directory / "visible.txt").write_bytes(b"user")
        (directory / "samples").mkdir()
        (directory / "samples" / "frame.ply").write_bytes(b"scan")
        aliased = directory / "alias" / ".."
        store = WorkspaceArtifactStore(lambda _folder, _name: False)

        self.assertEqual(
            [item["name"] for item in store.list_project_files(aliased)], ["visible.txt"]
        )
        self.assertEqual(
            [item["name"] for item in store.list_folder_files(aliased)], ["visible.txt"]
        )
        self.assertEqual(
            [item["name"] for item in store.list_project_folders(aliased)],
            ["alias", "samples"],
        )
        self.assertEqual(store.resolve_project_file(aliased, "visible.txt").read_bytes(), b"user")
        self.assertEqual(
            [item["name"] for item in store.list_project_subfolder_files(aliased, "samples")],
            ["frame.ply"],
        )
        self.assertEqual(
            store.resolve_project_subfolder_file(aliased, "samples", "frame.ply").read_bytes(),
            b"scan",
        )

    def test_project_merge_rejects_overlapping_directories(self) -> None:
        source = self.root / "source"
        source.mkdir()
        (source / "part.step").write_bytes(b"source")
        nested = source / "nested"
        nested.mkdir()
        store = WorkspaceArtifactStore(lambda _folder, _name: False)

        for target in (source, nested, self.root):
            with self.subTest(target=target), self.assertRaisesRegex(
                ValidationError, "non-nested"
            ):
                store.copy_project_files("source-id", "target-id", source, target)
        self.assertEqual([entry.name for entry in nested.iterdir()], [])
        self.assertEqual((source / "part.step").read_bytes(), b"source")

    def test_project_merge_rolls_back_when_source_changes_before_copy(self) -> None:
        source = self.root / "source"
        target = self.root / "target"
        source.mkdir()
        target.mkdir()
        source_file = source / "part.step"
        source_file.write_bytes(b"original")
        original_open = Path.open

        def changed_source_on_open(path, *args, **kwargs):
            if path == source_file and args and args[0] == "rb":
                source_file.write_bytes(b"changed content")
            return original_open(path, *args, **kwargs)

        store = WorkspaceArtifactStore(lambda _folder, _name: False)
        with patch.object(Path, "open", changed_source_on_open):
            with self.assertRaisesRegex(ValidationError, "source changed"):
                store.copy_project_files("source-id", "target-id", source, target)
        self.assertEqual(list(target.iterdir()), [])
        self.assertEqual(source_file.read_bytes(), b"changed content")

    def test_artifact_batch_operations_reject_casefold_collisions(self) -> None:
        directory = self.root / "project"
        directory.mkdir()
        (directory / "Part.step").write_bytes(b"original")
        subfolder = directory / "samples"
        subfolder.mkdir()
        (subfolder / "Part.step").write_bytes(b"original")
        store = WorkspaceArtifactStore(lambda _folder, _name: False)

        operations = (
            lambda: store.remove_project_files(directory, ["Part.step", "part.step"]),
            lambda: store.remove_project_subfolder_files(
                directory, "samples", ["Part.step", "part.step"]
            ),
            lambda: store.move_project_files_to_subfolder(
                directory, ["Part.step", "part.step"], "target"
            ),
            lambda: store.prune_project_files(
                "project", directory, ["Part.step", "part.step"]
            ),
            lambda: store.publish_cad_delivery(
                directory, {"Part.step": b"one", "part.step": b"two"}
            ),
        )
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(ValidationError):
                operation()
        self.assertEqual((directory / "Part.step").read_bytes(), b"original")
        self.assertEqual((subfolder / "Part.step").read_bytes(), b"original")
        self.assertFalse((directory / "target").exists())

    def test_artifact_move_rejects_casefold_collisions_from_different_folders(self) -> None:
        directory = self.root / "project"
        (directory / "first").mkdir(parents=True)
        (directory / "second").mkdir()
        (directory / "first" / "Part.step").write_bytes(b"one")
        (directory / "second" / "part.step").write_bytes(b"two")
        store = WorkspaceArtifactStore(lambda _folder, _name: False)

        with self.assertRaises(ValidationError):
            store.move_project_files_to_subfolder(
                directory, ["first/Part.step", "second/part.step"], "target"
            )
        self.assertFalse((directory / "target").exists())
        self.assertTrue((directory / "first" / "Part.step").exists())
        self.assertTrue((directory / "second" / "part.step").exists())

    def test_artifact_reserved_paths_are_case_insensitive(self) -> None:
        directory = self.root / "project"
        directory.mkdir()
        (directory / "PROJECT.JSON").write_bytes(b"metadata")
        (directory / ".CADYX-PRIVATE").write_bytes(b"internal")
        (directory / "visible.txt").write_bytes(b"user")
        store = WorkspaceArtifactStore(lambda _folder, _name: False)

        self.assertEqual(
            [item["name"] for item in store.list_project_files(directory)], ["visible.txt"]
        )
        self.assertEqual(
            [item["name"] for item in store.list_folder_files(directory)],
            ["PROJECT.JSON", "visible.txt"]
        )
        for filename in ("PROJECT.JSON", ".CADYX-PRIVATE"):
            with self.subTest(filename=filename), self.assertRaises(NotFoundError):
                store.resolve_project_file(directory, filename)
        with self.assertRaises(NotFoundError):
            store.resolve_folder_file(directory, ".CADYX-PRIVATE")
        for filename in ("PROJECT.JSON", ".CADYX-HIDDEN.txt"):
            with self.subTest(filename=filename), self.assertRaises(ValidationError):
                store.publish_project_file(directory, filename, b"data")
        with self.assertRaises(ValidationError):
            store.publish_project_step(directory, ".CADYX-EXPORT.STEP", b"data")
        with self.assertRaises(ValidationError):
            store.rename_project_file(directory, "visible.txt", "PROJECT.JSON")
        self.assertEqual((directory / "visible.txt").read_bytes(), b"user")

    def test_artifact_rejects_windows_device_and_path_alias_names(self) -> None:
        directory = self.root / "project"
        directory.mkdir()
        store = WorkspaceArtifactStore(lambda _folder, _name: False)
        unsafe = (
            "CON", "con.txt", "NUL.log", "AUX.txt", "COM1.txt",
            "LPT9.txt", "COM¹.txt", "CONIN$.txt", "project.json.",
            "PROJECT.JSON ", ".CADYX-LEASES.SQLITE3.", "file.txt ",
            "bad\nname.txt",
        )
        for filename in unsafe:
            with self.subTest(filename=filename):
                with self.assertRaises(ValidationError):
                    store.publish_project_file(directory, filename, b"data")
                with self.assertRaises(NotFoundError):
                    store.resolve_project_file(directory, filename)
        self.assertEqual(list(directory.iterdir()), [])

    def test_artifact_upload_rejects_reserved_metadata_and_invalid_names(self) -> None:
        directory = self.root / "project"
        directory.mkdir()
        for filename in ("project.json", ".cadyx-leases.sqlite3", None, 42):
            with self.subTest(filename=filename), self.assertRaises(ValidationError):
                WorkspaceArtifactStore.publish_project_file(directory, filename, b"data")
        with self.assertRaises(ValidationError):
            WorkspaceArtifactStore.publish_scan_output(
                directory, "project", ".cadyx-private.ply", b"scan"
            )
        self.assertEqual(list(directory.iterdir()), [])

    def test_validation_listing_rechecks_deletion_after_missing_database(self) -> None:
        directory = self.root / "project"
        directory.mkdir()
        marker = directory / ".cadyx-deleting"
        database = ValidationRecordStore.database_path(directory)
        original_exists = Path.exists

        def deletion_during_exists(path):
            if path == database:
                marker.touch()
                return False
            return original_exists(path)

        with patch.object(Path, "exists", deletion_during_exists):
            with self.assertRaises(NotFoundError):
                ValidationRecordStore().list(directory)

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
