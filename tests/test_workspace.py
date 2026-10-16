from __future__ import annotations

import ctypes
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cadyx.errors import NotFoundError
from cadyx.workspace import ProjectWorkspace


class ProjectWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name) / "projects"
        self.workspace = ProjectWorkspace(self.root)

    def test_project_folder_uses_a_safe_display_name_and_keeps_uuid_identity(self) -> None:
        project = self.workspace.create("  Тестовая кружка / верх: 3  ")
        project_directory = self.workspace._project_dir(project["id"])

        self.assertIn("Тестовая кружка", project_directory.name)
        self.assertNotRegex(project_directory.name, r'[<>:"/\\|?*]')
        self.assertNotEqual(project_directory.name, project["id"])
        self.assertEqual(self.workspace.load(project["id"])["id"], project["id"])
        self.assertEqual([item["id"] for item in self.workspace.list()], [project["id"]])

    def test_duplicate_display_names_get_unique_folders_without_overwriting_workspace_data(self) -> None:
        existing_folder = self.root / "Кружка"
        existing_folder.mkdir()
        sentinel = existing_folder / "reference.txt"
        sentinel.write_text("preserve", encoding="utf-8")

        project = self.workspace.create("Кружка")
        project_directory = self.workspace._project_dir(project["id"])

        self.assertEqual(project_directory.name, "Кружка (2)")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")
        self.assertEqual(self.workspace.list_folder_projects(), ["Кружка"])

    def test_rename_moves_project_folder_and_preserves_identity_and_history(self) -> None:
        project = self.workspace.create("РџСЂРёС‘РјРєР° Operator")
        source = self.workspace._project_dir(project["id"])
        document_before = project["document"]

        renamed = self.workspace.rename(project["id"], "Acceptance Operator temporary project")
        target = self.workspace._project_dir(project["id"])

        self.assertFalse(source.exists())
        self.assertEqual(target.name, "Acceptance Operator temporary project")
        self.assertEqual(renamed["id"], project["id"])
        self.assertEqual(renamed["name"], "Acceptance Operator temporary project")
        saved = self.workspace.load(project["id"])
        self.assertEqual(saved["document"], document_before)
        self.assertEqual(saved["history"], [])
        self.assertEqual([item["id"] for item in ProjectWorkspace(self.root).list()], [project["id"]])

    def test_rename_chooses_available_directory_without_overwriting(self) -> None:
        project = self.workspace.create("Old name")
        occupied = self.root / "New name"
        occupied.mkdir()
        sentinel = occupied / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")

        self.workspace.rename(project["id"], "New name")

        self.assertEqual(self.workspace._project_dir(project["id"]).name, "New name (2)")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_listing_adopts_workspace_directories_without_replacing_their_files(self) -> None:
        directory = self.root / "kobras1backcover"
        directory.mkdir()
        source = directory / "Merge_02_mesh.ply"
        source.write_bytes(b"source mesh")
        nested = directory / "references"
        nested.mkdir()
        (nested / "keep.txt").write_text("nested source", encoding="utf-8")

        project = next(item for item in self.workspace.list() if item["name"] == directory.name)
        project_id = project["id"]

        self.assertEqual(project["document"]["objects"], [])
        self.assertEqual((directory / ".cadyx-project-id").read_text(encoding="ascii").strip(), project_id)
        self.assertEqual(source.read_bytes(), b"source mesh")
        self.assertEqual((nested / "keep.txt").read_text(encoding="utf-8"), "nested source")
        self.assertEqual(
            [item["name"] for item in self.workspace.list_project_files(project_id)],
            ["Merge_02_mesh.ply"],
        )
        self.assertEqual(
            [item["name"] for item in self.workspace.list_folder_files(directory.name)],
            ["Merge_02_mesh.ply"],
        )
        self.assertEqual(self.workspace.list_folder_projects(), [])
        self.assertEqual(
            next(item["id"] for item in self.workspace.list() if item["name"] == directory.name),
            project_id,
        )
        self.assertEqual(
            next(item["id"] for item in ProjectWorkspace(self.root).list() if item["name"] == directory.name),
            project_id,
        )
        self.assertTrue(self.workspace.is_workspace_folder(directory.name))
        with self.assertRaises(NotFoundError):
            self.workspace.resolve_folder_file(directory.name, "project.json")

    def test_listing_does_not_replace_a_non_cadyx_project_json_file(self) -> None:
        directory = self.root / "unrecognized-data"
        directory.mkdir()
        project_file = directory / "project.json"
        project_file.write_text("preserve", encoding="utf-8")

        self.assertEqual(self.workspace.list(), [])
        self.assertEqual(project_file.read_text(encoding="utf-8"), "preserve")
        self.assertEqual(self.workspace.list_folder_projects(), [directory.name])

    def test_atomic_save_retries_a_transient_windows_file_lock(self) -> None:
        target = self.root / "retry.json"
        target.write_text('{"old": true}', encoding="utf-8")
        original_replace = os.replace
        calls = 0

        def replace_with_transient_lock(source: str, destination: str) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ctypes.WinError(5)
            original_replace(source, destination)

        with patch(
            "cadyx.workspace.os.replace",
            side_effect=replace_with_transient_lock,
        ) as replace:
            ProjectWorkspace._write_atomic(target, {"new": True})

        self.assertEqual(replace.call_count, 2)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"new": True})

    def test_legacy_uuid_folder_remains_readable_and_folder_api_hides_project_metadata(self) -> None:
        project = self.workspace.create("Старая кружка")
        named_directory = self.workspace._project_dir(project["id"])
        legacy_directory = self.root / project["id"]
        named_directory.rename(legacy_directory)

        self.assertEqual(self.workspace.load(project["id"])["name"], "Старая кружка")
        self.assertEqual([item["id"] for item in self.workspace.list()], [project["id"]])
        self.assertNotIn(project["id"], self.workspace.list_folder_projects())
        self.assertEqual(self.workspace.list_folder_files(project["id"]), [])
        self.assertTrue(self.workspace.is_workspace_folder(project["id"]))

    def test_project_file_resolver_accepts_only_direct_regular_files(self) -> None:
        project = self.workspace.create("STEP import")
        project_directory = self.workspace._project_dir(project["id"])
        source = project_directory / "part.stp"
        source.write_bytes(b"step source")

        self.assertEqual(self.workspace.resolve_project_file(project["id"], "part.stp"), source)
        for file_name in ("../part.stp", "nested/part.stp", "project.json", ".cadyx-leases.sqlite3"):
            with self.subTest(file_name=file_name), self.assertRaises(NotFoundError):
                self.workspace.resolve_project_file(project["id"], file_name)

    def test_project_file_listing_hides_internal_and_nested_files(self) -> None:
        project = self.workspace.create("Project files")
        project_directory = self.workspace._project_dir(project["id"])
        visible = project_directory / "part.stp"
        visible.write_bytes(b"step source")
        (project_directory / ".cadyx-private.sqlite3").write_bytes(b"internal")
        nested = project_directory / "nested"
        nested.mkdir()
        (nested / "hidden.txt").write_text("nested", encoding="utf-8")

        files = self.workspace.list_project_files(project["id"])

        self.assertEqual([item["name"] for item in files], ["part.stp"])
        self.assertEqual(files[0]["size"], len(b"step source"))
        self.assertIn("media_type", files[0])


if __name__ == "__main__":
    unittest.main()
