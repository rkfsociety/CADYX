"""Direct tests for CadyxCore collaborators."""
from __future__ import annotations
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4
from cadyx.core import CadyxCore
from cadyx.core_services import CommandActivityService, EngineeringValidationService, ProjectLifecycleService, RenderingCacheService
from cadyx.runtime_cache import RuntimeCachePolicy

class CoreServiceCompositionTests(unittest.TestCase):
    def test_browser_snapshot_excludes_payloads_after_full_geometry_validation(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            core = CadyxCore(root, kernel=object())
            project_id = core.create_project("Snapshot payload")["project"]["id"]
            project = core.workspace.load(project_id)
            geometry = {"kernel": "occt-v1", "kind": "imported_brep", "data": "QUJD",
                        "volume": 1.0, "brep_valid": True}
            project["document"]["objects"] = [{"id": f"body:{uuid4()}", "type": "body",
                "name": "Part", "geometry": geometry, "feature_ids": []}]
            project["document"]["revision"] = 1
            project["history"] = [{"revision": 1, "actor_type": "system", "actor_id": "test",
                "timestamp": project["modified_at"], "command": {"type": "fixture"}, "result": {}}]
            core.workspace.save(project)
            with patch.object(core, "_validate_geometry_once") as validate:
                browser = core.inspect_project(project_id, browser=True)
                full = core.inspect_project(project_id)
                self.assertEqual(validate.call_count, 2)
            self.assertNotIn("data", browser["document"]["objects"][0]["geometry"])
            self.assertEqual(browser["document"]["objects"][0]["geometry"]["volume"], 1.0)
            self.assertEqual(full["document"]["objects"][0]["geometry"]["data"], "QUJD")
            self.assertEqual(browser["history"], [])
            self.assertEqual(len(full["history"]), 1)

    def test_project_listing_does_not_wait_for_scanner_device_io(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            core = CadyxCore(root, kernel=object())
            project_id = core.create_project("Independent listing")["project"]["id"]
            with ThreadPoolExecutor(max_workers=1) as pool:
                # The request runs on another thread, as in the HTTP server.
                # Holding the scanner guard simulates a slow table query.
                with core._scan_capture._guard:
                    future = pool.submit(core.list_workspace)
                    listing = future.result(timeout=1)
                    availability = pool.submit(
                        core._project_availability, project_id, nonblocking=True,
                    ).result(timeout=1)
                    self.assertTrue(availability.running_operation)
                self.assertEqual(len(listing["projects"]), 1)
                self.assertFalse(core._scan_capture.is_running(project_id))

    def test_project_listing_reads_metadata_without_expanding_documents(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            core = CadyxCore(root, kernel=object())
            created = core.create_project("Metadata listing")
            with patch("cadyx.workspace_projects._unpack_project_geometries", side_effect=AssertionError):
                listing = core.list_workspace()
            self.assertEqual(listing["projects"][0]["project"], created["project"])
            self.assertEqual(listing["projects"][0]["lock"]["state"], "FREE")

    def test_missing_saved_feature_geometry_is_a_validation_error(self) -> None:
        # Corrupt snapshots must not leak AttributeError to API clients.
        from types import SimpleNamespace
        from cadyx.errors import ValidationError

        with tempfile.TemporaryDirectory() as root:
            core = CadyxCore(root, kernel=object())
            features = (
                ("create_ruled_shell", {"parameters": {"patches": [], "sewing_tolerance": 0.01}}),
                ("loft_sketches", {"source_sketch_ids": [], "parameters": {"section_sketch_ids": []}}),
                ("sweep_sketch", {"source_sketch_id": "sketch", "parameters": {"radius": 1.0}}),
            )
            for operation, fields in features:
                with self.subTest(operation=operation):
                    document = SimpleNamespace(revision=1, objects=[
                        {"id": "body", "type": "body", "geometry": None},
                        {"id": "feature", "type": "feature", "body_id": "body",
                         "operation": operation, "geometry": None, **fields},
                    ])
                    with self.assertRaises(ValidationError):
                        core._validate_geometry(document)

    def test_core_composes_services(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            core = CadyxCore(root, kernel=object())
            self.assertIsInstance(core._lifecycle, ProjectLifecycleService)
            self.assertIsInstance(core._command_activity, CommandActivityService)
            self.assertIsInstance(core._rendering, RenderingCacheService)
            self.assertIsInstance(core._engineering_validation, EngineeringValidationService)
    def test_lifecycle_state_is_owned_by_service(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            core = CadyxCore(root, kernel=object())
            created = core.create_project("service-owned-lifecycle")
            pid = created["project"]["id"]
            self.assertEqual(core.workspace_selection_snapshot(), {"type":"project","project_id":pid})
            self.assertNotIn("_workspace_selection", core.__dict__)
            self.assertIn("_workspace_selection", core._lifecycle.__dict__)
    def test_render_cache_state_is_owned_by_service(self) -> None:
        policy = RuntimeCachePolicy(validated_document_entries=2, scene_entries=3, png_entries=4, png_bytes=1024, viewport_snapshot_entries=5, viewport_snapshot_bytes=2048)
        with tempfile.TemporaryDirectory() as root:
            core = CadyxCore(root, kernel=object(), cache_policy=policy)
            self.assertIs(core._rendering._cache_policy, policy)
            self.assertNotIn("_scene_cache", core.__dict__)
            core._invalidate_project_runtime_caches("missing")

    def test_command_completed_event_is_versioned_bounded_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            core = CadyxCore(root, kernel=object())
            project_id = core.create_project("Command receipt")["project"]["id"]
            lease = core.acquire_lease(project_id, "agent", "receipt-test")
            subscriber_id, subscriber = core.events.subscribe(project_id)
            geometry = {"positions": [1.0] * 20000, "triangles": [0] * 20000}
            result = {"feature_id": "feature:test-id", "geometry": geometry}
            try:
                with patch.object(core.commands, "execute", return_value=result):
                    response = core.execute_command(
                        project_id, lease["lease_id"], {"type": "create_sketch"},
                        expected_revision=0,
                    )
                published = []
                while not subscriber.empty():
                    published.append(subscriber.get_nowait())
                started = next(item for item in published if item.event == "command.started")
                completed = next(item for item in published if item.event == "command.completed")
                self.assertEqual(completed.data, {
                    "schema_version": 2,
                    "operation_id": started.data["operation_id"],
                    "revision": 1,
                    "command": "create_sketch",
                    "outcome": "completed",
                    "feature_id": "feature:test-id",
                })
                self.assertLess(len(json.dumps(completed.as_dict())), 512)
                self.assertEqual(response["history_entry"]["result"], result)
                self.assertEqual(core.inspect_project(project_id)["history"][-1]["result"], result)
                self.assertIn("document.changed", [item.event for item in published])
                self.assertLess(
                    [item.event for item in published].index("document.changed"),
                    [item.event for item in published].index("command.completed"),
                )

                with patch.object(core.commands, "execute", return_value={}):
                    core.execute_command(
                        project_id, lease["lease_id"], {"type": "create_sketch"},
                        expected_revision=1,
                    )
                subsequent = []
                while not subscriber.empty():
                    subsequent.append(subscriber.get_nowait())
                no_feature = next(item for item in subsequent if item.event == "command.completed")
                self.assertNotIn("feature_id", no_feature.data)
                self.assertEqual(no_feature.data["revision"], 2)
            finally:
                core.events.unsubscribe(subscriber_id)
                core.release_lease(project_id, lease["lease_id"])


if __name__ == "__main__": unittest.main()
