"""Revision diff and checkpoint regressions (run by CI, not locally)."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from cadyx.core import CadyxCore
from cadyx.errors import ValidationError
from cadyx.mcp_server import MCPApplication, TOOLS
from cadyx.revision_diff import RevisionArchive, compare_documents
from cadyx.web_routes import match_route
from cadyx.workspace import ProjectWorkspace


def document(revision: int = 0, *, objects: list[dict] | None = None) -> dict:
    return {
        "id": "document:comparison", "version": 11, "revision": revision,
        "objects": copy.deepcopy(objects or []),
        "model_parameters": {}, "parameter_bindings": [],
        "assembly": {"version": 4, "parts": [], "subassemblies": [],
                     "instances": [], "mates": []},
    }


class FakeKernel:
    def __init__(self) -> None:
        self.calls = 0

    def metrics(self, geometry: dict) -> dict:
        return {"volume": geometry["volume"], "topology": {"faces": geometry["faces"]}}

    def compare_preserved_volume(self, original: dict, modified: dict) -> dict:
        self.calls += 1
        a, b = original["volume"], modified["volume"]
        return {
            "reference_volume": a, "candidate_volume": b,
            "preserved_volume": min(a, b), "lost_volume": max(0, a - b),
            "added_volume": max(0, b - a),
            "estimated_volume_resolution": 1e-9, "deflection": 1e-3,
        }


class RevisionArchiveTests(unittest.TestCase):
    def test_archive_preserves_committed_snapshots(self):
        with tempfile.TemporaryDirectory() as path:
            workspace = ProjectWorkspace(path)
            project = workspace.create("Prototype")
            project_id = project["id"]
            revision_zero = copy.deepcopy(project["document"])
            project["document"]["revision"] = 1
            project["history"].append({
                "actor_type": "agent", "actor_id": "tester",
                "command": {"type": "noop"}, "result": {},
                "timestamp": "2026-10-19T00:00:00Z", "revision": 1,
            })
            workspace.save(project)
            saved = workspace.load(project_id)
            archive = workspace.revision_archive(project_id)
            self.assertEqual(archive.load(saved, 0), revision_zero)
            self.assertEqual(archive.load(saved, 1), saved["document"])
            self.assertEqual([entry["revision"] for entry in archive.list(saved)["checkpoints"]], [1, 0])
            self.assertTrue((workspace._project_dir(project_id) / ".cadyx-revisions.sqlite3").is_file())
            self.assertFalse(any(item["name"].startswith(".cadyx-revisions") for item in
                                 workspace.list_project_files(project_id)))

    def test_uncommitted_or_rewritten_history_cannot_expose_staged_document(self):
        with tempfile.TemporaryDirectory() as path:
            archive = RevisionArchive(Path(path))
            original = {"id": "p", "document": document(),
                        "history": []}
            staged = {"id": "p", "document": document(1),
                      "history": [{"type": "command-a"}]}
            archive.record(original, staged)
            with self.assertRaisesRegex(ValidationError, "no saved model checkpoint"):
                archive.load(original, 1)
            divergent = {"id": "p", "document": document(2),
                         "history": [{"type": "command-b"}, {"type": "command-c"}]}
            with self.assertRaisesRegex(ValidationError, "matching committed checkpoint"):
                archive.load(divergent, 1)

    def test_missing_legacy_revisions_are_not_invented(self):
        with tempfile.TemporaryDirectory() as path:
            archive = RevisionArchive(Path(path))
            legacy = {"id": "p", "document": document(4),
                      "history": [{}, {}, {}, {}]}
            self.assertEqual([row["revision"] for row in archive.list(legacy)["checkpoints"]], [4])
            with self.assertRaises(ValidationError):
                archive.load(legacy, 2)


class RevisionGeometryTests(unittest.TestCase):
    def setUp(self):
        self.kernel = FakeKernel()

    def test_diff_reports_parameters_features_assembly_and_measured_volumes(self):
        geometry = {"kernel": "occt-v1", "volume": 10, "faces": 6}
        other = {"kernel": "occt-v1", "volume": 12, "faces": 8}
        before = document(1, objects=[
            {"id": "body:one", "type": "body", "geometry": geometry},
            {"id": "feature:one", "type": "feature", "operation": "extrude", "parameters": {"depth": 10}},
        ])
        after = document(2, objects=[
            {"id": "body:one", "type": "body", "geometry": other},
            {"id": "feature:one", "type": "feature", "operation": "extrude", "parameters": {"depth": 12}},
        ])
        before["model_parameters"]["depth"] = {"value": 10, "unit": "mm"}
        after["model_parameters"]["depth"] = {"value": 12, "unit": "mm"}
        before["assembly"]["instances"] = [{
            "id": "instance:a", "translation": [0, 0, 0], "rotation": [1, 0, 0, 0],
        }]
        after["assembly"]["instances"] = [{
            "id": "instance:a", "translation": [3, 0, 0], "rotation": [1, 0, 0, 0],
        }]
        result = compare_documents(before, after, self.kernel)
        self.assertEqual(result["parameters"]["changed"], ["depth"])
        self.assertEqual(result["feature_graph"]["changed"], ["feature:one"])
        self.assertEqual(result["assembly"]["moved_instances"][0]["after"]["translation"], [3, 0, 0])
        self.assertEqual(result["geometry"]["reports"][0]["volumes"]["added_volume"], 2)
        self.assertEqual(result["geometry"]["reports"][0]["topology_metrics_changed"], True)
        self.assertEqual(result["visual_highlights"]["changed_body_ids"], ["body:one"])
        self.assertEqual(self.kernel.calls, 1)
        self.assertNotIn("geometry", result["geometry"]["reports"][0])

    def test_identical_geometry_does_not_require_native_computation(self):
        before = document(objects=[{"id": "body:x", "type": "body", "geometry": {"kind": "box"}}])
        after = copy.deepcopy(before)
        after["revision"] = 1
        result = compare_documents(before, after, self.kernel)
        self.assertEqual(result["geometry"]["reports"], [])
        self.assertEqual(self.kernel.calls, 0)

    def test_new_and_removed_body_volume_is_separate_not_global_union(self):
        original = document(objects=[
            {"id": "body:old", "type": "body",
             "geometry": {"kernel": "occt-v1", "volume": 7, "faces": 6}},
        ])
        updated = document(1, objects=[
            {"id": "body:new", "type": "body",
             "geometry": {"kernel": "occt-v1", "volume": 13, "faces": 8}},
        ])
        report = compare_documents(original, updated, self.kernel)
        unmatched = report["geometry"]["unmatched_body_reports"]
        self.assertEqual([(row["change"], row["volume"]) for row in unmatched],
                         [("removed", 7), ("added", 13)])
        self.assertEqual(report["geometry"]["added_removed_bodies"],
                         "standalone_volume_only_no_global_union")
        self.assertEqual(report["status"], "partial")
        self.assertEqual(self.kernel.calls, 0)

    def test_mesh_explicitly_untested_and_tolerance_validated(self):
        old = document(objects=[{"id": "body:m", "type": "body",
                                "geometry": {"kernel": "cadyx-mesh-v1", "vertices": [1]}}])
        new = document(1, objects=[{"id": "body:m", "type": "body",
                                   "geometry": {"kernel": "cadyx-mesh-v1", "vertices": [2]}}])
        result = compare_documents(old, new, self.kernel)
        self.assertEqual(result["geometry"]["reports"][0]["status"], "untested")
        self.assertEqual(result["status"], "partial")
        with self.assertRaises(ValidationError):
            compare_documents(old, new, self.kernel, absolute_tolerance=True)


class RevisionIntegrationTests(unittest.TestCase):
    def test_api_routes_and_mcp_schema_present(self):
        listing = match_route("GET", "/api/v1/agent/projects/p/revisions")
        comparison = match_route("POST", "/api/v1/agent/projects/p/revisions/compare")
        self.assertEqual(listing.spec.name, "agent_revisions")
        self.assertEqual(comparison.spec.name, "agent_revision_compare")
        available = {tool["name"] for tool in TOOLS}
        self.assertIn("cadyx_list_revision_checkpoints", available)
        self.assertIn("cadyx_compare_revisions", available)

    def test_core_compare_current_with_proposal_and_stale_revision(self):
        with tempfile.TemporaryDirectory() as path:
            core = CadyxCore(path, kernel=FakeKernel())
            project = core.workspace.create("Preview comparison")
            project_id = project["id"]
            staged = copy.deepcopy(project["document"])
            staged["revision"] = 1
            core._command_proposals["p1"] = {
                "project_id": project_id, "revision": 0,
                "document": staged, "results": [], "commands": [],
                "created_at": 0.0,
            }
            result = core.compare_model_revisions(project_id, {"revision": 0},
                                                  {"proposal_id": "p1"}, expected_revision=0)
            self.assertEqual((result["before"]["revision"], result["after"]["revision"]), (0, 1))
            self.assertTrue(result["read_only"])
            self.assertEqual(core.workspace.load(project_id)["document"]["revision"], 0)
            with self.assertRaises(ValidationError):
                core.compare_model_revisions(project_id, {"revision": 0},
                                             {"proposal_id": "missing"}, expected_revision=0)


if __name__ == "__main__":
    unittest.main()
