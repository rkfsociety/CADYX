from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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

    def test_legacy_uuid_folder_remains_readable_and_project_folder_is_not_a_generic_folder(self) -> None:
        project = self.workspace.create("Старая кружка")
        named_directory = self.workspace._project_dir(project["id"])
        legacy_directory = self.root / project["id"]
        named_directory.rename(legacy_directory)

        self.assertEqual(self.workspace.load(project["id"])["name"], "Старая кружка")
        self.assertEqual([item["id"] for item in self.workspace.list()], [project["id"]])
        self.assertNotIn(project["id"], self.workspace.list_folder_projects())
        with self.assertRaises(NotFoundError):
            self.workspace.list_folder_files(project["id"])

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
