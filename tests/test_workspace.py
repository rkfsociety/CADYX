from __future__ import annotations

import ctypes
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from cadyx.errors import ConflictError, NotFoundError, ValidationError
from cadyx.workspace import ProjectWorkspace
from cadyx.workspace_projects import ProjectRecordStore


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

    def test_url_slug_is_separate_from_project_title_and_survives_rename(self) -> None:
        project = self.workspace.create("Гипсовая фигурка — 5 ракурсов", url_slug="gypsum-figurine-5-views")

        renamed = self.workspace.rename(project["id"], "Гипсовая фигурка — 5 ракурсов v2")
        self.assertEqual(renamed["name"], "Гипсовая фигурка — 5 ракурсов v2")
        self.assertEqual(renamed["url_slug"], "gypsum-figurine-5-views")
        self.assertEqual(self.workspace.load(project["id"])["url_slug"], "gypsum-figurine-5-views")

    def test_summary_listing_skips_model_validation_and_refreshes_saved_metadata(self) -> None:
        project = self.workspace.create("Original")
        path = self.workspace._project_dir(project["id"]) / "project.json"
        with patch("cadyx.workspace_projects._unpack_project_geometries", side_effect=AssertionError), \
                patch.object(ProjectRecordStore, "validate_current", side_effect=AssertionError):
            summary = self.workspace.list(summary_only=True)[0]
            self.assertEqual(summary["id"], project["id"])
            self.assertNotIn("document", summary)
            self.assertNotIn("history", summary)
            summary["name"] = "Caller change"
            self.assertEqual(self.workspace.list(summary_only=True)[0]["name"], "Original")
            project["name"] = "External rename"
            project["project_type"] = "assembly"
            path.write_text(json.dumps(project), encoding="utf-8")
            updated = self.workspace.list(summary_only=True)[0]
            self.assertEqual(updated["name"], "External rename")
            self.assertEqual(updated["project_type"], "assembly")

    def test_summary_listing_preserves_legacy_defaults_and_rejects_invalid_metadata(self) -> None:
        project = self.workspace.create("Legacy")
        path = self.workspace._project_dir(project["id"]) / "project.json"
        project.pop("url_slug")
        project.pop("project_type")
        path.write_text(json.dumps(project), encoding="utf-8")
        summary = self.workspace.list(summary_only=True)[0]
        self.assertEqual(summary["url_slug"], "legacy")
        self.assertEqual(summary["project_type"], "single_model")
        self.assertNotIn("url_slug", json.loads(path.read_text(encoding="utf-8")))
        project["name"] = ""
        path.write_text(json.dumps(project), encoding="utf-8")
        with self.assertRaises(ValidationError):
            self.workspace.list(summary_only=True)

    def test_project_type_persists_and_legacy_type_is_inferred(self) -> None:
        assembly = self.workspace.create("Assembly project", project_type="assembly")
        self.assertEqual(self.workspace.load(assembly["id"])["project_type"], "assembly")
        self.assertEqual(
            next(item for item in self.workspace.list() if item["id"] == assembly["id"])["project_type"],
            "assembly",
        )

        legacy = self.workspace.create("Legacy project")
        record_path = self.workspace._project_dir(legacy["id"]) / "project.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record.pop("project_type")
        record_path.write_text(json.dumps(record), encoding="utf-8")
        self.assertEqual(self.workspace.load(legacy["id"])["project_type"], "single_model")
        self.assertEqual(
            ProjectRecordStore.infer_project_type({
                "document": {"assembly": {"parts": [{}], "subassemblies": [], "instances": [], "mates": []}}
            }),
            "assembly",
        )

        with self.assertRaises(ValidationError):
            self.workspace.create("Invalid project type", project_type="group")

    def test_project_type_change_persists_and_rejects_nonempty_assembly_graph(self) -> None:
        project = self.workspace.create("HBOT", project_type="assembly")
        changed = self.workspace.set_project_type(project["id"], "single_model")
        self.assertEqual(changed["project_type"], "single_model")
        self.assertEqual(self.workspace.load(project["id"])["project_type"], "single_model")
        self.assertEqual(self.workspace.load(project["id"])["document"], project["document"])

        blocked = self.workspace.create("Assembly with parts", project_type="assembly")
        record_path = self.workspace._project_dir(blocked["id"]) / "project.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["document"]["assembly"]["subassemblies"].append({
            "id": "subassembly:test", "name": "Fixture",
        })
        ProjectRecordStore.write_atomic(record_path, record)
        with self.assertRaisesRegex(ConflictError, "assembly components"):
            self.workspace.set_project_type(blocked["id"], "single_model")
        self.assertEqual(self.workspace.load(blocked["id"])["project_type"], "assembly")

    def test_legacy_project_gets_ascii_url_slug(self) -> None:
        project = self.workspace.create("Гипсовая фигурка — 5 ракурсов")
        self.assertEqual(project["url_slug"], "gipsovaya-figurka-5-rakursov")
        self.assertRegex(project["url_slug"], r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

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

    def test_rename_preserves_external_history_and_geometry_blobs(self) -> None:
        project = self.workspace.create("Stored project")
        project = self.workspace.load(project["id"])
        project["document"]["revision"] = 1
        body_id, feature_id = f"body:{uuid4()}", f"feature:{uuid4()}"
        geometry = {"kernel": "occt-v1", "kind": "imported_brep", "data": "QUJD", "volume": 1.0,
                    "brep_valid": True}
        project["document"]["objects"] = [
            {"id": body_id, "type": "body", "name": "Imported", "feature_ids": [feature_id],
             "geometry": geometry},
            {"id": feature_id, "type": "feature", "operation": "test_geometry", "body_id": body_id,
             "geometry": geometry},
        ]
        project["history"].append({
            "actor_type": "system", "actor_id": "test", "command": {"type": "import_step"},
            "timestamp": "2026-10-19T00:00:00+00:00", "revision": 1,
            "result": {"geometry": geometry},
        })
        self.workspace.save(project)

        renamed = self.workspace.rename(project["id"], "Renamed stored project")
        restored = ProjectWorkspace(self.root).load(project["id"])

        self.assertEqual(renamed["name"], "Renamed stored project")
        self.assertEqual(restored["history"][0]["result"]["geometry"], geometry)

        # Card metadata remains available without loading external geometry/history;
        # inspecting the document must still detect a missing geometry blob.
        blob = next(self.workspace._project_dir(project["id"]).glob(".cadyx-brep-*.json"))
        blob_content = blob.read_bytes()
        blob.unlink()
        try:
            self.assertEqual(self.workspace.list(summary_only=True)[0]["id"], project["id"])
            with self.assertRaises(ValidationError):
                self.workspace.load(project["id"])
        finally:
            blob.write_bytes(blob_content)
        self.assertEqual(len(list(self.workspace._project_dir(project["id"]).glob(".cadyx-brep-*.json"))), 1)

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

    def test_project_storage_deduplicates_repeated_imported_brep_and_loads_full_history(self) -> None:
        project = self.workspace.create("B-rep storage")
        geometry = {
            "kernel": "occt-v1", "kind": "imported_brep", "data": "A" * 12000,
            "volume": 1.0, "brep_valid": True,
        }
        body_id, feature_id = f"body:{uuid4()}", f"feature:{uuid4()}"
        project["document"]["revision"] = 1
        project["document"]["objects"] = [
            {"id": body_id, "type": "body", "name": "Imported", "feature_ids": [feature_id],
             "geometry": geometry},
            {"id": feature_id, "type": "feature", "operation": "test_geometry", "body_id": body_id,
             "geometry": geometry},
        ]
        project["history"] = [{
            "actor_type": "system", "actor_id": "test", "command": {"type": "import_step"},
            "timestamp": "2026-10-19T00:00:00+00:00", "revision": 1,
            "result": {"body_id": body_id, "geometry": geometry},
        }]

        self.workspace.save(project)
        project_file = self.workspace._project_dir(project["id"]) / "project.json"
        stored = json.loads(project_file.read_text(encoding="utf-8"))
        restored = self.workspace.load(project["id"])

        self.assertEqual(stored["$cadyx_storage"]["history"]["count"], 1)
        geometry_ref = stored["document"]["objects"][0]["geometry"]
        self.assertEqual(geometry_ref, stored["document"]["objects"][1]["geometry"])
        self.assertEqual(len(list(project_file.parent.glob(".cadyx-brep-*.json"))), 1)
        history_path = project_file.parent / ".cadyx-history.jsonl"
        history_record = json.loads(history_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(history_record["result"]["geometry"], geometry_ref)
        self.assertNotIn("A" * 1000, project_file.read_text(encoding="utf-8"))
        self.assertNotIn("A" * 1000, history_path.read_text(encoding="utf-8"))
        self.assertEqual(restored["document"]["objects"][0]["geometry"], geometry)
        self.assertEqual(restored["document"]["objects"][1]["geometry"], geometry)
        self.assertEqual(restored["history"][0]["result"]["geometry"], geometry)

        with history_path.open("ab") as stream:
            stream.write(b'{"torn":')
        loaded = self.workspace.load(project["id"], include_history=False)
        self.assertEqual(loaded["history"], [])
        self.assertEqual(loaded["_history_count"], 1)
        loaded["document"]["revision"] = 2
        next_entry = dict(restored["history"][0])
        next_entry["revision"] = 2
        loaded["history"].append(next_entry)
        with patch("cadyx.workspace_projects.ProjectRecordStore.write_atomic", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                self.workspace.save(loaded, validated_history_prefix=1)

        self.assertEqual(len(self.workspace.load(project["id"])["history"]), 1)
        loaded = self.workspace.load(project["id"], include_history=False)
        loaded["document"]["revision"] = 2
        next_entry = dict(restored["history"][0])
        next_entry["revision"] = 2
        loaded["history"].append(next_entry)
        self.workspace.save(loaded, validated_history_prefix=1)

        recovered = self.workspace.load(project["id"])
        self.assertEqual([entry["revision"] for entry in recovered["history"]], [1, 2])
        self.assertTrue(history_path.read_bytes().endswith(b"\n"))

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

    def test_project_files_can_browse_nested_scan_series_without_following_links(self) -> None:
        project = self.workspace.create("Continuous scan")
        root = self.workspace._project_dir(project["id"])
        series = root / "revopoint_20261021T103419Z"
        depth = series / "depth_frames"
        depth.mkdir(parents=True)
        (series / "capture_manifest.json").write_text("{}", encoding="utf-8")
        (depth / "frame_00000.raw").write_bytes(b"Y16")

        self.assertEqual([item["name"] for item in self.workspace.list_project_folders(project["id"])],
                         [series.name])
        self.assertEqual([item["name"] for item in self.workspace.list_project_subfolder_files(
            project["id"], series.name)], ["capture_manifest.json"])
        self.assertEqual([item["name"] for item in self.workspace.list_project_subfolders(
            project["id"], series.name)], ["depth_frames"])
        self.assertEqual(self.workspace.resolve_project_subfolder_file(
            project["id"], f"{series.name}/depth_frames", "frame_00000.raw"), depth / "frame_00000.raw")
        with self.assertRaises(NotFoundError):
            self.workspace.list_project_subfolder_files(project["id"], f"{series.name}/../outside")


if __name__ == "__main__":
    unittest.main()
