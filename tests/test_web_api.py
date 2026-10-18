from __future__ import annotations

import json
import os
import tempfile
import struct
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from cadyx.core import CadyxCore
from cadyx.api_contracts import VALIDATION_OPERATIONS
from cadyx.models import Document
from cadyx.web import CadyxHTTPServer, make_request_handler
import cadyx.web as web_module


class WebApiTests(unittest.TestCase):
    def test_validation_routes_preserve_field_errors_and_core_delegation(self) -> None:
        _, created = self.request("/api/v1/agent/projects", {"name": "Validation contracts"})
        project_id = created["project"]["id"]
        for name, operation in VALIDATION_OPERATIONS.items():
            path = operation["path"].replace("{id}", project_id)
            body = dict.fromkeys(operation["input_schema"]["required"], "value")
            with self.subTest(operation=name):
                for invalid in ({}, {**body, "unexpected": True}):
                    status, error = self.request(path, invalid)
                    self.assertEqual(status, 400)
                    self.assertEqual(error["message"], operation["field_error"])
                with patch.object(self.core, "validate_body_" + name,
                                  return_value={"status": "untested"}) as validate:
                    status, result = self.request(path, body)
                    self.assertEqual(status, 200)
                    self.assertEqual(result, {"status": "untested"})
                    validate.assert_called_once()
                    self.assertFalse(validate.call_args.kwargs["persist"])
                    if name == "thickness":
                        self.assertEqual(validate.call_args.kwargs["method"], "sampled")

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.core = CadyxCore(str(Path(self.temporary_directory.name) / "projects"))
        self.server = CadyxHTTPServer(("127.0.0.1", 0), make_request_handler(self.core))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.addCleanup(self._stop_server)

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path: str, value: dict | None = None) -> tuple[int, dict]:
        data = None if value is None else json.dumps(value).encode("utf-8")
        request = Request(self.base_url + path, data=data, method="GET" if data is None else "POST")
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def upload(self, path: str, filename: str, content: bytes) -> tuple[int, dict]:
        request = Request(
            self.base_url + path + "/" + quote(filename, safe=""),
            data=content,
            headers={"Content-Type": "application/octet-stream"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def test_agent_project_can_be_created_without_changing_selection_and_receive_step(self) -> None:
        _, current = self.request("/api/v1/agent/projects", {"name": "Current project"})
        current_id = current["project"]["id"]
        status, created = self.request("/api/v1/agent/projects", {"name": "Staged project", "select": False})
        self.assertEqual(status, 201)
        project_id = created["project"]["id"]
        self.assertEqual(self.core.workspace_selection_snapshot(), {"type": "project", "project_id": current_id})

        payload = b"ISO-10303-21;\x00source"
        status, uploaded = self.upload(f"/api/v1/agent/projects/{project_id}/files", "source model.stp", payload)
        self.assertEqual(status, 201)
        self.assertEqual(uploaded["file"]["size"], len(payload))
        source = self.core.workspace._project_dir(project_id) / "source model.stp"
        self.assertEqual(source.read_bytes(), payload)

        status, conflict = self.upload(f"/api/v1/agent/projects/{project_id}/files", "source model.stp", b"other")
        self.assertEqual((status, conflict["error"]), (409, "ARTIFACT_CONFLICT"))
        self.assertEqual(source.read_bytes(), payload)

    def test_agent_step_upload_rejects_invalid_filename_or_locked_project(self) -> None:
        _, created = self.request("/api/v1/agent/projects", {"name": "Upload constraints"})
        project_id = created["project"]["id"]
        status, error = self.upload(f"/api/v1/agent/projects/{project_id}/files", "source.txt", b"x")
        self.assertEqual((status, error["error"]), (400, "VALIDATION_ERROR"))
        self.core.acquire_lease(project_id, "agent", "upload-test")
        status, error = self.upload(f"/api/v1/agent/projects/{project_id}/files", "source.stp", b"x")
        self.assertEqual((status, error["error"]), (409, "PROJECT_BUSY"))

    def test_legacy_migration_uses_both_api_command_paths(self) -> None:
        for prefix, owner in (("/api/v1/projects", "human"), ("/api/v1/agent/projects", "agent")):
            with self.subTest(owner=owner):
                _, created = self.request("/api/v1/projects", {"name": "Legacy API"})
                project_id = created["project"]["id"]
                path = self.core.workspace._project_dir(project_id) / "project.json"
                legacy = json.loads(path.read_text())
                legacy["document"]["version"] = 1
                path.write_text(json.dumps(legacy))
                before = path.read_bytes()
                status, detail = self.request(f"{prefix}/{project_id}")
                self.assertEqual(status, 200)
                self.assertEqual(detail["document"]["version"], 8)
                _, acquired = self.request(f"{prefix}/{project_id}/leases",
                                          {"owner_type": owner, "owner_id": "migration-test"})
                lease = acquired["lease"]["lease_id"]
                status, _ = self.request(f"{prefix}/{project_id}/commands",
                                         {"lease_id": lease, "command": {"type": "create_sketch", "plane": "bad"}})
                self.assertEqual(status, 400)
                self.assertEqual(path.read_bytes(), before)
                status, _ = self.request(f"{prefix}/{project_id}/commands",
                                         {"lease_id": lease, "command": {"type": "create_sketch"}})
                self.assertEqual(status, 200)
                saved = json.loads(path.read_text())
                self.assertEqual(saved["version"], 1)
                self.assertEqual(saved["document"]["version"], 8)
                self.assertEqual(saved["history"][0]["actor_type"], owner)
                self.core.release_lease(project_id, lease)

    def test_agent_and_web_api_share_core_locking(self) -> None:
        status, created = self.request("/api/v1/projects", {"name": "API test"})
        self.assertEqual(status, 201)
        project_id = created["project"]["id"]
        status, acquired = self.request(
            f"/api/v1/agent/projects/{project_id}/leases",
            {"owner_id": "api-agent"},
        )
        self.assertEqual(status, 201)
        lease_id = acquired["lease"]["lease_id"]
        status, committed = self.request(
            f"/api/v1/agent/projects/{project_id}/commands",
            {"lease_id": lease_id, "command": {"type": "create_box", "width": 1, "height": 1, "depth": 1}},
        )
        self.assertEqual(status, 200)
        self.assertIn("document", committed)
        self.assertEqual(committed["history_entry"]["actor_type"], "agent")
        status, detail = self.request(f"/api/v1/agent/projects/{project_id}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["document"]["revision"], 1)
        status, activity = self.request(f"/api/v1/projects/{project_id}/activity")
        self.assertEqual(status, 200)
        self.assertEqual(activity["activities"][-1]["actor_type"], "agent")
        self.assertEqual(activity["activities"][-1]["state"], "completed")
        status, activity = self.request(f"/api/v1/agent/projects/{project_id}/activity")
        self.assertEqual((status, activity["activities"][-1]["state"]), (200, "completed"))

    def test_command_batch_preview_isolated_and_proposal_commits_once(self) -> None:
        _, created = self.request("/api/v1/agent/projects", {"name": "Batch preview"})
        project_id = created["project"]["id"]
        commands = [{"type": "create_sketch"}, {"type": "create_sketch", "plane": "XZ"}]
        status, preview = self.request(
            f"/api/v1/agent/projects/{project_id}/commands/preview",
            {"expected_revision": 0, "commands": commands},
        )
        self.assertEqual(status, 200)
        self.assertEqual(preview["revision"], 0)
        self.assertEqual(preview["prospective_revision"], 2)
        self.assertEqual(len(preview["diff"]["added_ids"]), 2)
        _, detail = self.request(f"/api/v1/agent/projects/{project_id}")
        self.assertEqual((detail["document"]["revision"], detail["history"]), (0, []))

        _, acquired = self.request(f"/api/v1/agent/projects/{project_id}/leases", {"owner_id": "batch-test"})
        lease_id = acquired["lease"]["lease_id"]
        status, committed = self.request(
            f"/api/v1/agent/projects/{project_id}/commands/commit",
            {"lease_id": lease_id, "expected_revision": 0, "proposal_id": preview["proposal_id"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(committed["revision"], 2)
        self.assertEqual(len(committed["document"]["objects"]), 2)
        self.assertEqual(len(self.core.workspace.load(project_id)["history"]), 2)
        status, error = self.request(
            f"/api/v1/agent/projects/{project_id}/commands/commit",
            {"lease_id": lease_id, "expected_revision": 2, "proposal_id": preview["proposal_id"]},
        )
        self.assertEqual(status, 409)
        self.assertEqual(error["error"], "PROPOSAL_STALE")
        self.core.release_lease(project_id, lease_id)

    def test_invalid_command_batch_preview_never_changes_saved_project(self) -> None:
        _, created = self.request("/api/v1/agent/projects", {"name": "Rejected batch preview"})
        project_id = created["project"]["id"]
        status, _ = self.request(
            f"/api/v1/agent/projects/{project_id}/commands/preview",
            {"expected_revision": 0, "commands": [{"type": "create_sketch"}, {"type": "not_a_command"}]},
        )
        self.assertEqual(status, 400)
        saved = self.core.workspace.load(project_id)
        self.assertEqual(saved["document"]["revision"], 0)
        self.assertEqual(saved["document"]["objects"], [])
        self.assertEqual(saved["history"], [])

    def test_command_validation_errors_include_revision_and_safe_diagnostics(self) -> None:
        _, created = self.request("/api/v1/agent/projects", {"name": "Command diagnostics"})
        project_id = created["project"]["id"]
        _, acquired = self.request(f"/api/v1/agent/projects/{project_id}/leases", {"owner_id": "diagnostics-test"})
        lease_id = acquired["lease"]["lease_id"]
        status, error = self.request(
            f"/api/v1/agent/projects/{project_id}/commands",
            {"lease_id": lease_id, "expected_revision": 0,
             "command": {"type": "create_box", "width": 0, "height": 2, "depth": 3}},
        )
        self.assertEqual((status, error["error"]), (400, "VALIDATION_ERROR"))
        self.assertEqual(error["diagnostics"]["command_type"], "create_box")
        self.assertEqual(error["diagnostics"]["revision"], 0)
        self.assertEqual(error["diagnostics"]["category"], "validation")
        self.assertIn("correction_hint", error["diagnostics"])
        self.core.release_lease(project_id, lease_id)

    def test_unexpected_http_error_uses_safe_generic_payload(self) -> None:
        _, created = self.request("/api/v1/agent/projects", {"name": "Safe error"})
        project_id = created["project"]["id"]
        with patch.object(self.core, "inspect_project",
                          side_effect=RuntimeError(r"failure at C:\private\token=abc123")):
            status, error = self.request(f"/api/v1/agent/projects/{project_id}")
        self.assertEqual((status, error["error"]), (500, "INTERNAL_ERROR"))
        self.assertNotIn("C:\\private", json.dumps(error))
        self.assertNotIn("abc123", json.dumps(error))

    def test_agent_global_thickness_validation_returns_polyhedron_certificate(self) -> None:
        _, created = self.request("/api/v1/agent/projects", {"name": "Global thickness"})
        project_id = created["project"]["id"]
        _, lease = self.request(
            f"/api/v1/agent/projects/{project_id}/leases", {"owner_id": "thickness-test"},
        )
        _, committed = self.request(
            f"/api/v1/agent/projects/{project_id}/commands",
            {"lease_id": lease["lease"]["lease_id"],
             "command": {"type": "create_box", "width": 2.0, "height": 3.0, "depth": 5.0}},
        )
        body_id = committed["history_entry"]["result"]["body_id"]
        status, result = self.request(
            f"/api/v1/agent/projects/{project_id}/validations/thickness",
            {"body_id": body_id, "expected_revision": 1, "minimum_thickness": 1.9,
             "tolerance": 0.0, "method": "global_minimum"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["certified"])
        self.assertAlmostEqual(result["minimum_global_thickness"], 2.0, places=7)

    def test_agent_project_rename_is_utf8_and_rejects_locked_projects(self) -> None:
        _, created = self.request("/api/v1/agent/projects", {"name": "Mojibake temporary"})
        project_id = created["project"]["id"]
        status, renamed = self.request(
            f"/api/v1/agent/projects/{project_id}/rename",
            {"name": "Приёмка Operator временный проект"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(renamed["project"]["name"], "Приёмка Operator временный проект")
        self.assertEqual(self.core.workspace._project_dir(project_id).name, "Приёмка Operator временный проект")

        self.core.acquire_lease(project_id, "agent", "rename-test")
        status, error = self.request(
            f"/api/v1/agent/projects/{project_id}/rename", {"name": "Should not move"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(error["error"], "PROJECT_LOCKED")
        self.assertEqual(self.core.workspace._project_dir(project_id).name, "Приёмка Operator временный проект")

    def test_agent_capabilities_are_read_only_and_command_names_are_current(self) -> None:
        before = self.core.workspace.workspace_revision_token()
        status, capabilities = self.request("/api/v1/agent/capabilities")
        self.assertEqual(status, 200)
        self.assertTrue(capabilities["read_only"])
        self.assertTrue(any(route["path"] == "/api/v1/agent/projects/{id}/commands" for route in capabilities["routes"]))
        self.assertTrue(any(route["path"] == "/api/v1/agent/projects/{id}/capabilities" for route in capabilities["routes"]))
        advertised = {item["type"] for item in capabilities["commands"]}
        self.assertEqual(len(advertised), len(capabilities["commands"]))

        self.assertIn("add_sketch_circle_tangent_to_line", advertised)
        self.assertIn("add_sketch_circle_tangent_to_two_lines", advertised)
        self.assertIn("add_sketch_circle_tangent_to_three_lines", advertised)
        self.assertIn("add_sketch_arc_center", advertised)
        self.assertIn("add_sketch_arc_three_points", advertised)
        self.assertIn("add_sketch_arc_start_center_end", advertised)
        self.assertIn("add_sketch_arc_radius", advertised)
        self.assertIn("add_sketch_arc_tangent", advertised)
        self.assertIn("add_sketch_ellipse", advertised)
        self.assertIn("add_sketch_ellipse_center", advertised)
        self.assertIn("add_sketch_ellipse_three_points", advertised)
        self.assertIn("add_sketch_elliptical_arc", advertised)
        self.assertIn("add_sketch_spline", advertised)
        self.assertIn("add_sketch_spline_control_points", advertised)
        self.assertIn("add_sketch_bspline", advertised)
        self.assertIn("add_sketch_bezier_curve", advertised)
        self.assertIn("add_sketch_nurbs", advertised)
        self.assertIn("add_sketch_spiral", advertised)
        self.assertIn("add_sketch_parametric_curve", advertised)
        self.assertIn("add_sketch_coordinate_curve", advertised)
        for command_type in (
            "add_sketch_regular_polygon", "add_sketch_inscribed_polygon",
            "add_sketch_circumscribed_polygon", "add_sketch_polygon_from_center",
            "add_sketch_slot", "add_sketch_slot_by_centers", "add_sketch_slot_total_length",
            "add_sketch_slot_centered", "add_sketch_arc_slot_three_points",
            "add_sketch_arc_slot_center", "add_sketch_arc_slot_tangent",
        ):
            self.assertIn(command_type, advertised)
        for command_type in ("add_sketch_arc_slot_three_points", "add_sketch_arc_slot_center",
                             "add_sketch_arc_slot_tangent"):
            self.assertIn(command_type, advertised)
        for command_type in advertised:
            with self.subTest(command_type=command_type):
                try:
                    self.core.commands.execute(Document.create(), {"type": command_type})
                except Exception as exc:
                    self.assertNotIn("Unsupported command type", str(exc))
        self.assertEqual(before, self.core.workspace.workspace_revision_token())

    def test_source_extension_read_discovery_and_restart_schedule(self) -> None:
        _, created = self.request("/api/v1/agent/projects", {"name": "Source extension API"})
        project_id = created["project"]["id"]
        status, source = self.request("/api/v1/agent/source-files/read", {"path": "cadyx/gear_kinematics.py"})
        self.assertEqual(status, 200)
        self.assertEqual(len(source["sha256"]), 64)
        self.assertIn("solve_planetary_gearset", source["content"])
        with urlopen(self.base_url + f"/api/v1/agent/projects/{project_id}/source-extensions", timeout=3) as response:
            status, extensions = response.status, json.loads(response.read())
        self.assertEqual(status, 200)
        self.assertEqual(extensions, [])
        self.assertTrue(any("source-extensions" in route["path"]
                            for route in self.core.agent_capabilities()["routes"]))

        with patch("cadyx.core.CadyxCore.develop_agent_source_extension",
                   return_value={"status": "ready", "restart_required": True}) as develop, \
             patch("cadyx.web_routes.subprocess.Popen") as restart:
            status, result = self.request(
                f"/api/v1/agent/projects/{project_id}/source-extensions",
                {"proposal": {"id": "api_test", "title": "test"}},
            )
        self.assertEqual(status, 201)
        self.assertTrue(result["restart_scheduled"])
        develop.assert_called_once()
        restart.assert_called_once()

    def test_agent_capability_lifecycle_is_available_through_agent_api(self) -> None:
        _, created = self.request("/api/v1/agent/projects", {"name": "Capability API"})
        project_id = created["project"]["id"]
        definition = {
            "id": "api_test_box",
            "title": "API test box",
            "description": "Creates a validated box.",
            "version": "1.0.0",
            "input_schema": {"type": "object", "properties": {"width": {"type": "number"}},
                             "required": ["width"], "additionalProperties": False},
            "output_schema": {"type": "object", "properties": {"body_id": {"type": "string"}},
                              "required": ["body_id"], "additionalProperties": False},
            "outputs": {"body_id": {"step": 0, "field": "body_id"}},
            "dependencies": [],
            "steps": [{"command": {"type": "create_box", "width": {"$input": "width"},
                                      "height": 3, "depth": 4, "name": "API capability box"}}],
            "test_input": {"width": 2},
        }
        status, result = self.request(
            f"/api/v1/agent/projects/{project_id}/capabilities", {"definition": definition},
        )
        self.assertEqual(status, 201)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["test_result"]["status"], "passed")
        activities = self.core.events.activity_snapshot(project_id)
        self.assertEqual(activities[-1]["detail"]["phase"], "integration")
        self.assertEqual(activities[-1]["detail"]["test_result"]["status"], "passed")

        status, execution = self.request(
            f"/api/v1/agent/projects/{project_id}/capabilities/api_test_box/execute",
            {"owner_id": "web-api-test", "expected_revision": 0, "arguments": {"width": 5}},
        )
        self.assertEqual(status, 200)
        self.assertEqual(execution["status"], "completed", execution)
        self.assertEqual(execution["revision"], 1)
        self.assertEqual(self.core.inspect_project(project_id)["lock"]["state"], "FREE")

        _, discovered = self.request("/api/v1/agent/capabilities")
        self.assertEqual(discovered["reusable_capabilities"][0]["id"], "api_test_box")

        status, rolled_back = self.request(
            f"/api/v1/agent/projects/{project_id}/capabilities/api_test_box/rollback", {},
        )
        self.assertEqual(status, 200)
        self.assertFalse(rolled_back["restored"])

    def _run_sketch_curve_agent_command(self, command_value: dict) -> tuple[dict, dict]:
        _, created = self.request("/api/v1/agent/projects", {"name": "Curve tool API"})
        project_id = created["project"]["id"]
        base = f"/api/v1/agent/projects/{project_id}"
        _, acquired = self.request(base + "/leases", {"owner_id": "curve-tool-api-test"})
        lease_id = acquired["lease"]["lease_id"]

        def command(value: dict) -> dict:
            status, response = self.request(base + "/commands", {
                "lease_id": lease_id, "command": value,
            })
            self.assertEqual(status, 200, response)
            return response["history_entry"]["result"]

        sketch_id = command({"type": "create_sketch"})["sketch_id"]
        result = command({"sketch_id": sketch_id, **command_value})
        status, detail = self.request(base)
        self.assertEqual(status, 200)
        return result, detail

    def test_archimedean_spiral_agent_api(self) -> None:
        result, detail = self._run_sketch_curve_agent_command({
            "type": "add_sketch_spiral", "center": {"x": 0, "y": 0},
            "initial_radius": 1, "pitch_per_turn": 2,
            "end_angle_degrees": 360, "samples": 12,
        })
        objects = {item["id"]: item for item in detail["document"]["objects"]}
        spline = objects[result["spline_id"]]
        self.assertEqual(result["curve_tool"], "add_sketch_spiral")
        self.assertEqual(len(spline["point_ids"]), 12)
        self.assertAlmostEqual(objects[spline["point_ids"][-1]]["x"], 3)

    def test_parametric_curve_agent_api(self) -> None:
        result, detail = self._run_sketch_curve_agent_command({
            "type": "add_sketch_parametric_curve", "x_expression": "cos(t)",
            "y_expression": "sin(t)", "parameter_start": 0,
            "parameter_end": 6.283185307179586, "samples": 8,
        })
        objects = {item["id"]: item for item in detail["document"]["objects"]}
        spline = objects[result["spline_id"]]
        self.assertEqual(result["curve_tool"], "add_sketch_parametric_curve")
        self.assertEqual(len(spline["point_ids"]), 8)
        self.assertAlmostEqual(objects[spline["point_ids"][0]]["x"], 1)
        self.assertAlmostEqual(objects[spline["point_ids"][0]]["y"], 0)

    def test_coordinate_curve_agent_api(self) -> None:
        coordinates = [{"x": 0, "y": 0}, {"x": 3, "y": 4}, {"x": 8, "y": 2}]
        result, detail = self._run_sketch_curve_agent_command({
            "type": "add_sketch_coordinate_curve", "points": coordinates,
        })
        objects = {item["id"]: item for item in detail["document"]["objects"]}
        spline = objects[result["spline_id"]]
        stored = [(objects[point_id]["x"], objects[point_id]["y"]) for point_id in spline["point_ids"]]
        self.assertEqual(result["curve_tool"], "add_sketch_coordinate_curve")
        self.assertEqual(stored, [(0, 0), (3, 4), (8, 2)])

    def _run_arc_slot_agent_command(self, value: dict, *, needs_line: bool = False) -> tuple[dict, dict]:
        _, created = self.request("/api/v1/agent/projects", {"name": "Arc slot API"})
        project_id = created["project"]["id"]
        base = f"/api/v1/agent/projects/{project_id}"
        _, acquired = self.request(base + "/leases", {"owner_id": "arc-slot-api-test"})
        lease_id = acquired["lease"]["lease_id"]

        def command(value: dict) -> dict:
            status, response = self.request(base + "/commands", {
                "lease_id": lease_id, "command": value,
            })
            self.assertEqual(status, 200, response)
            return response["history_entry"]["result"]

        sketch_id = command({"type": "create_sketch"})["sketch_id"]
        if needs_line:
            line = command({"type": "add_sketch_line", "sketch_id": sketch_id,
                            "start": {"x": -10, "y": 0}, "end": {"x": 10, "y": 0}})
            value = {**value, "line_id": line["line_id"]}
        result = command({"sketch_id": sketch_id, **value})
        status, detail = self.request(base)
        self.assertEqual(status, 200)
        objects = {item["id"]: item for item in detail["document"]["objects"]}
        self.assertTrue(all(objects[arc_id]["type"] == "circle" for arc_id in result["circle_ids"]))
        return result, detail

    def test_arc_slot_three_points_agent_api(self) -> None:
        result, _ = self._run_arc_slot_agent_command({
            "type": "add_sketch_arc_slot_three_points", "start": {"x": 5, "y": 0},
            "through": {"x": 0, "y": 5}, "end": {"x": -5, "y": 0}, "width": 2,
        })
        self.assertTrue(result["closed"])
        self.assertEqual((len(result["circle_ids"]), len(result["line_ids"])), (4, 0))

    def test_arc_slot_from_center_agent_api(self) -> None:
        result, _ = self._run_arc_slot_agent_command({
            "type": "add_sketch_arc_slot_center", "center": {"x": 20, "y": 0},
            "radius": 8, "start_angle_degrees": 10, "sweep_degrees": -90, "width": 2,
        })
        self.assertTrue(result["closed"])
        self.assertEqual((len(result["circle_ids"]), len(result["line_ids"])), (4, 0))
        self.assertLess(result["centerline"]["sweep_radians"], 0)

    def test_tangent_arc_slot_agent_api(self) -> None:
        result, _ = self._run_arc_slot_agent_command({
            "type": "add_sketch_arc_slot_tangent", "tangent_point": {"x": 0, "y": 0},
            "radius": 8, "sweep_degrees": 90, "width": 2,
        }, needs_line=True)
        self.assertTrue(result["closed"])
        self.assertEqual((len(result["circle_ids"]), len(result["line_ids"])), (4, 0))

    def test_agent_lease_acquire_and_release_publish_lock_events(self) -> None:
        _, created = self.request("/api/v1/projects", {"name": "Lock event"})
        project_id = created["project"]["id"]
        subscriber_id, events = self.core.events.subscribe(project_id)
        lease = None
        try:
            lease = self.core.acquire_lease(project_id, "agent", "lock-event-test")
            acquired = events.get(timeout=1)
            self.assertEqual((acquired.event, acquired.data["state"]), ("project.lock.changed", "AI_LOCKED"))
            self.core.release_lease(project_id, lease["lease_id"])
            released = events.get(timeout=1)
            self.assertEqual((released.event, released.data["state"]), ("project.lock.changed", "FREE"))
            lease = None
        finally:
            if lease is not None:
                self.core.release_lease(project_id, lease["lease_id"])
            self.core.events.unsubscribe(subscriber_id)

    def test_project_file_api_lists_and_serves_only_direct_user_files(self) -> None:
        _, created = self.request("/api/v1/projects", {"name": "Files API"})
        project_id = created["project"]["id"]
        directory = self.core.workspace._project_dir(project_id)
        source = directory / "part.stp"
        source.write_bytes(b"STEP fixture")
        (directory / ".cadyx-private.sqlite3").write_bytes(b"internal")
        nested = directory / "nested"
        nested.mkdir()
        (nested / "hidden.stp").write_bytes(b"nested")

        status, listing = self.request(f"/api/v1/projects/{project_id}/files")
        self.assertEqual(status, 200)
        self.assertEqual([item["name"] for item in listing["files"]], ["part.stp"])
        with urlopen(self.base_url + f"/api/v1/projects/{project_id}/files/part.stp", timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"STEP fixture")

    def test_project_listing_and_saved_snapshot_reads_do_not_wait_for_an_edit_lock(self) -> None:
        _, created = self.request("/api/v1/projects", {"name": "Reload persistence"})
        project_id = created["project"]["id"]
        entered = threading.Event()
        release = threading.Event()

        def hold_project_guard() -> None:
            with self.core.leases.guard(project_id):
                entered.set()
                release.wait(timeout=5)

        holder = threading.Thread(target=hold_project_guard, daemon=True)
        holder.start()
        self.assertTrue(entered.wait(timeout=2))
        try:
            started = time.monotonic()
            status, workspace = self.request("/api/v1/projects")
            self.assertEqual(status, 200)
            self.assertLess(time.monotonic() - started, 2)
            entry = next(item for item in workspace["projects"] if item["project"]["id"] == project_id)
            self.assertEqual(entry["project"]["name"], "Reload persistence")

            status, detail = self.request(f"/api/v1/projects/{project_id}")
            self.assertEqual(status, 200)
            self.assertEqual(detail["document"]["revision"], 0)
        finally:
            release.set()
            holder.join(timeout=2)

    def test_empty_activity_snapshot_checks_project_without_geometry_validation(self) -> None:
        _, created = self.request("/api/v1/projects", {"name": "Empty activity"})
        project_id = created["project"]["id"]
        with patch.object(self.core, "inspect_project", side_effect=AssertionError("unexpected geometry validation")):
            status, activity = self.request(f"/api/v1/projects/{project_id}/activity")
        self.assertEqual(status, 200)
        self.assertEqual(activity, {"activities": [], "revision": 0})

    def test_agent_api_does_not_accept_human_lease_for_command(self) -> None:
        _, created = self.request("/api/v1/projects", {"name": "Lease boundary"})
        project_id = created["project"]["id"]
        _, human = self.request(
            f"/api/v1/projects/{project_id}/leases",
            {"owner_type": "human", "owner_id": "web-user"},
        )
        status, error = self.request(
            f"/api/v1/agent/projects/{project_id}/commands",
            {"lease_id": human["lease"]["lease_id"], "command": {"type": "create_box", "width": 1, "height": 1, "depth": 1}},
        )
        self.assertEqual(status, 409)
        self.assertEqual(error["error"], "PROJECT_LOCKED")

    def test_web_api_does_not_accept_agent_lease_for_human_command(self) -> None:
        _, created = self.request("/api/v1/projects", {"name": "Human API boundary"})
        project_id = created["project"]["id"]
        _, agent = self.request(
            f"/api/v1/agent/projects/{project_id}/leases",
            {"owner_id": "api-agent"},
        )
        status, error = self.request(
            f"/api/v1/projects/{project_id}/commands",
            {"lease_id": agent["lease"]["lease_id"], "command": {"type": "create_box", "width": 1, "height": 1, "depth": 1}},
        )
        self.assertEqual(status, 409)
        self.assertEqual(error["error"], "PROJECT_LOCKED")

    def test_agent_workspace_selection_accepts_projects_and_folders(self) -> None:
        status, initial = self.request("/api/v1/agent/workspace/selection")
        self.assertEqual(status, 200)
        self.assertIsNone(initial["selection"])

        _, created = self.request("/api/v1/agent/projects", {"name": "Selectable project"})
        project_id = created["project"]["id"]
        self.assertEqual(
            self.core.workspace_selection_snapshot(),
            {"type": "project", "project_id": project_id},
        )
        status, selected_project = self.request(
            "/api/v1/agent/workspace/selection",
            {"type": "project", "project_id": project_id},
        )
        self.assertEqual(status, 200)
        self.assertEqual(selected_project["selection"], {"type": "project", "project_id": project_id})

        folder_name = "selectable-folder"
        (self.core.workspace.root / folder_name).mkdir()
        subscriber_id, events = self.core.events.subscribe()
        try:
            status, selected_folder = self.request(
                "/api/v1/agent/workspace/selection",
                {"type": "folder", "folder_name": folder_name},
            )
            self.assertEqual(status, 200)
            expected = {"type": "folder", "folder_name": folder_name}
            self.assertEqual(selected_folder["selection"], expected)
            self.assertEqual(self.core.workspace_selection_snapshot(), expected)
            status, snapshot = self.request("/api/v1/agent/workspace/selection")
            self.assertEqual((status, snapshot["selection"]), (200, expected))
            event = events.get(timeout=1)
            self.assertEqual((event.event, event.project_id, event.data),
                             ("workspace.selection.changed", None, expected))

            status, missing = self.request(
                "/api/v1/agent/workspace/selection",
                {"type": "folder", "folder_name": "missing-folder"},
            )
            self.assertEqual((status, missing["error"]), (404, "NOT_FOUND"))
            self.assertEqual(self.core.workspace_selection_snapshot(), expected)
        finally:
            self.core.events.unsubscribe(subscriber_id)

    def test_agent_selection_api_rejects_locked_projects_and_running_operations(self) -> None:
        _, current = self.request("/api/v1/agent/projects", {"name": "Current selection"})
        current_id = current["project"]["id"]
        _, target = self.request(
            "/api/v1/agent/projects",
            {"name": "Locked target", "select": False},
        )
        target_id = target["project"]["id"]
        current_folder = "some-folder"
        (self.core.workspace.root / current_folder).mkdir()

        current_lease = self.core.acquire_lease(current_id, "agent", "selection-current")
        status, error = self.request(
            "/api/v1/agent/workspace/selection",
            {"type": "folder", "folder_name": current_folder},
        )
        self.assertEqual((status, error["error"]), (409, "PROJECT_BUSY"))
        self.assertEqual(
            self.core.workspace_selection_snapshot(),
            {"type": "project", "project_id": current_id},
        )
        self.core.release_lease(current_id, current_lease["lease_id"])

        target_lease = self.core.acquire_lease(target_id, "human", "selection-target")
        status, error = self.request(
            "/api/v1/agent/workspace/selection",
            {"type": "project", "project_id": target_id},
        )
        self.assertEqual((status, error["error"]), (409, "PROJECT_BUSY"))
        self.assertEqual(
            self.core.workspace_selection_snapshot(),
            {"type": "project", "project_id": current_id},
        )
        self.core.release_lease(target_id, target_lease["lease_id"])

        self.core.events.publish("agent.activity", current_id, {
            "operation_id": "selection-operation",
            "state": "running",
        })
        folder_name = "selection-folder"
        (self.core.workspace.root / folder_name).mkdir()
        status, error = self.request(
            "/api/v1/agent/workspace/selection",
            {"type": "folder", "folder_name": folder_name},
        )
        self.assertEqual((status, error["error"]), (409, "PROJECT_BUSY"))
        self.assertEqual(
            self.core.workspace_selection_snapshot(),
            {"type": "project", "project_id": current_id},
        )
        self.core.events.publish("agent.activity", current_id, {
            "operation_id": "selection-operation",
            "state": "completed",
        })

    def test_selected_project_creation_respects_active_workflow_policy(self) -> None:
        _, current = self.request("/api/v1/agent/projects", {"name": "Active current project"})
        current_id = current["project"]["id"]
        self.core.report_agent_dashboard(current_id, {
            "workflow": {
                "title": "Текущая работа",
                "progress_percent": 40,
                "eta_seconds": None,
                "phases": [{"id": "build", "title": "Построение", "state": "in_progress"}],
            },
            "tasks": [],
        })
        project_ids_before = {item["project"]["id"] for item in self.core.list_projects()}

        status, error = self.request("/api/v1/agent/projects", {"name": "Must not be selected"})
        self.assertEqual((status, error["error"]), (409, "PROJECT_BUSY"))
        self.assertEqual(
            {item["project"]["id"] for item in self.core.list_projects()},
            project_ids_before,
        )

        status, staged = self.request(
            "/api/v1/agent/projects",
            {"name": "Unselected project", "select": False},
        )
        self.assertEqual(status, 201)
        self.assertEqual(staged["project"]["name"], "Unselected project")
        self.assertEqual(
            self.core.workspace_selection_snapshot(),
            {"type": "project", "project_id": current_id},
        )

    def test_project_listing_adopts_existing_directories_and_exposes_their_files(self) -> None:
        directory = self.core.workspace.root / "imported-scans"
        directory.mkdir()
        source = directory / "scan.ply"
        source.write_bytes(b"source mesh")

        status, workspace = self.request("/api/v1/projects")

        self.assertEqual(status, 200)
        entry = next(item for item in workspace["projects"] if item["project"]["name"] == directory.name)
        project_id = entry["project"]["id"]
        self.assertEqual(entry["lock"]["state"], "FREE")
        self.assertNotIn(directory.name, workspace["folders"])
        self.assertEqual(source.read_bytes(), b"source mesh")

        status, files = self.request(f"/api/v1/projects/{project_id}/files")

        self.assertEqual(status, 200)
        self.assertEqual([item["name"] for item in files["files"]], ["scan.ply"])
        status, selected = self.request(
            "/api/v1/agent/workspace/selection",
            {"type": "folder", "folder_name": directory.name},
        )
        self.assertEqual(status, 200)
        self.assertEqual(selected["selection"], {"type": "folder", "folder_name": directory.name})

    def test_agent_dashboard_snapshot_contains_the_reported_russian_plan(self) -> None:
        _, created = self.request("/api/v1/agent/projects", {"name": "Dashboard plan"})
        project_id = created["project"]["id"]
        report = {
            "workflow": {
                "title": "Модель крепления",
                "progress_percent": 0,
                "eta_seconds": 420,
                "phases": [
                    {"id": "inspect", "title": "Проверка исходных данных", "state": "pending"},
                    {"id": "geometry", "title": "Построение геометрии", "state": "pending"},
                ],
            },
            "tasks": [],
        }
        subscriber_id, events = self.core.events.subscribe()
        try:
            status, published = self.request(f"/api/v1/agent/projects/{project_id}/dashboard", report)
            self.assertEqual(status, 200)
            self.assertEqual(published["workflow"]["title"], report["workflow"]["title"])
            self.assertEqual(published["workflow"]["phases"], report["workflow"]["phases"])
            self.assertEqual(published["tasks"], [])
            self.assertIsNone(published["started_at"])
            self.assertIsNone(published["elapsed_seconds"])
            self.assertEqual(published["revision"], 1)

            status, snapshot = self.request(f"/api/v1/agent/projects/{project_id}/dashboard")
            self.assertEqual(status, 200)
            self.assertEqual(snapshot, published)
            event = events.get(timeout=1)
            self.assertEqual((event.event, event.project_id, event.data),
                             ("agent.dashboard", project_id, published))
        finally:
            self.core.events.unsubscribe(subscriber_id)

    def test_agent_work_lock_persists_and_releases_only_after_completed_queue(self) -> None:
        _, created = self.request("/api/v1/agent/projects", {"name": "Persistent work lock"})
        project_id = created["project"]["id"]
        report = {
            "workflow": {
                "title": "Проверенная модель",
                "progress_percent": 50,
                "eta_seconds": None,
                "phases": [
                    {"id": "build", "title": "Построение", "state": "in_progress"},
                    {"id": "verify", "title": "Проверка", "state": "pending"},
                ],
            },
            "tasks": [
                {"id": "model", "title": "Пересобрать модель", "state": "running"},
                {"id": "step", "title": "Проверить STEP", "state": "pending"},
            ],
        }
        status, active_report = self.request(
            f"/api/v1/agent/projects/{project_id}/dashboard", report,
        )
        self.assertEqual(status, 200)
        self.assertIsNotNone(active_report["started_at"])
        self.assertIsNone(active_report["elapsed_seconds"])
        status, active_updates = self.request("/api/v1/updates")
        self.assertEqual(status, 200)
        self.assertTrue(active_updates["agent_work_active"])
        self.assertEqual(active_updates["agent_work_active_project_ids"], [project_id])
        self.assertEqual(active_updates["agent_work_revision"], 1)
        status, _ = self.request(
            f"/api/v1/agent/projects/{project_id}/dashboard",
            {"workflow": None, "tasks": []},
        )
        self.assertEqual(status, 400)
        self.assertTrue(self.core.events.agent_work_snapshot()["active"])

        restarted_core = CadyxCore(str(self.core.workspace.root))
        self.assertEqual(restarted_core.agent_dashboard_snapshot(project_id), active_report)
        self.assertEqual(restarted_core.events.agent_work_snapshot(), {
            "active": True, "active_project_ids": [project_id], "revision": 1,
        })

        report["workflow"]["progress_percent"] = 100
        report["workflow"]["phases"] = [
            {"id": "build", "title": "Построение", "state": "completed"},
            {"id": "verify", "title": "Проверка", "state": "completed"},
        ]
        report["tasks"][0]["state"] = "completed"
        report["tasks"][1]["state"] = "pending"
        status, _ = self.request(f"/api/v1/agent/projects/{project_id}/dashboard", report)
        self.assertEqual(status, 400)
        report["workflow"]["progress_percent"] = 99
        self.request(f"/api/v1/agent/projects/{project_id}/dashboard", report)
        self.assertTrue(self.core.events.agent_work_snapshot()["active"])

        subscriber_id, events = self.core.events.subscribe()
        try:
            report["tasks"][1]["state"] = "completed"
            report["workflow"]["progress_percent"] = 100
            status, finished_report = self.request(f"/api/v1/agent/projects/{project_id}/dashboard", report)
            self.assertEqual(status, 200)
            self.assertIsNotNone(finished_report["elapsed_seconds"])
            status, completed_updates = self.request("/api/v1/updates")
            self.assertEqual(status, 200)
            self.assertFalse(completed_updates["agent_work_active"])
            self.assertEqual(completed_updates["agent_work_active_project_ids"], [])
            self.assertEqual(completed_updates["agent_work_revision"], 3)
            self.assertEqual(events.get(timeout=1).event, "agent.dashboard")
            work_event = events.get(timeout=1)
            self.assertEqual((work_event.event, work_event.project_id, work_event.data),
                             ("workspace.agent-work", None, {"active": False, "revision": 3, "project_ids": []}))
        finally:
            self.core.events.unsubscribe(subscriber_id)

        failed_report = {
            "workflow": {
                "title": "Interrupted acceptance",
                "progress_percent": 20,
                "eta_seconds": None,
                "phases": [
                    {"id": "inspect", "title": "Inspect project", "state": "failed"},
                    {"id": "model", "title": "Build model", "state": "failed"},
                ],
            },
            "tasks": [{"id": "acceptance", "title": "Acceptance blocked", "state": "failed"}],
        }
        status, _ = self.request(f"/api/v1/agent/projects/{project_id}/dashboard", failed_report)
        self.assertEqual(status, 200)
        self.assertFalse(self.core.events.agent_work_snapshot()["active"])

    def test_web_assets_are_served(self) -> None:
        with urlopen(self.base_url + "/", timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"CADYX", response.read())
        with urlopen(self.base_url + "/scanner", timeout=3) as response:
            scanner_page = response.read()
            self.assertEqual(response.status, 200)
            self.assertIn(b"scanner-camera.js?v=", scanner_page)
        with urlopen(self.base_url + "/assets/scanner-camera.js", timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"rotateScannerPoint", response.read())
        with urlopen(self.base_url + "/assets/ply-parser.js", timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"window.CadyxPlyPreview", response.read())

    def test_automatic_update_snapshot_tracks_project_activity_and_folder_files(self) -> None:
        status, initial = self.request("/api/v1/updates")
        self.assertEqual(status, 200)
        self.assertEqual(len(initial["ui_version"]), 64)

        _, created = self.request("/api/v1/projects", {"name": "Automatic updates"})
        project_id = created["project"]["id"]
        status, with_project = self.request("/api/v1/updates")
        self.assertEqual(status, 200)
        self.assertNotEqual(initial["workspace_revision"], with_project["workspace_revision"])

        status, with_free_lock = self.request(f"/api/v1/updates?project_id={project_id}")
        self.assertEqual(status, 200)
        self.assertEqual(with_free_lock["project_lock"]["state"], "FREE")

        lease = self.core.acquire_lease(project_id, "agent", "updates-test")
        self.core.execute_command(project_id, lease["lease_id"], {
            "type": "create_box", "width": 1, "height": 1, "depth": 1,
        })
        status, after_command = self.request(f"/api/v1/updates?project_id={project_id}")
        self.assertEqual(status, 200)
        self.assertGreater(after_command["activity_revision"], 0)
        self.assertIsNotNone(after_command["project_revision"])
        self.assertEqual(after_command["project_lock"]["state"], "AI_LOCKED")
        self.core.release_lease(project_id, lease["lease_id"])
        status, after_release = self.request(f"/api/v1/updates?project_id={project_id}")
        self.assertEqual(status, 200)
        self.assertEqual(after_release["project_lock"]["state"], "FREE")
        self.assertEqual(after_release["project_revision"], after_command["project_revision"])
        self.assertEqual(after_release["activity_revision"], after_command["activity_revision"])

        folder = self.core.workspace.root / "live-scans"
        folder.mkdir()
        source = folder / "source.ply"
        source.write_bytes(b"first")
        status, before_file_change = self.request("/api/v1/updates?folder=live-scans")
        self.assertEqual(status, 200)
        previous_mtime = source.stat().st_mtime_ns
        source.write_bytes(b"other")
        os.utime(source, ns=(previous_mtime + 2_000_000_000, previous_mtime + 2_000_000_000))
        status, after_file_change = self.request("/api/v1/updates?folder=live-scans")
        self.assertEqual(status, 200)
        self.assertNotEqual(before_file_change["folder_revision"], after_file_change["folder_revision"])

    def test_automatic_update_snapshot_detects_visual_asset_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for filename in web_module._UI_ASSETS:
                (root / filename).write_text(filename, encoding="utf-8")
            previous_root = web_module._WEB_ROOT
            web_module._WEB_ROOT = root
            try:
                status, before = self.request("/api/v1/updates")
                self.assertEqual(status, 200)
                (root / "style.css").write_text("updated stylesheet", encoding="utf-8")
                status, after = self.request("/api/v1/updates")
                self.assertEqual(status, 200)
                self.assertNotEqual(before["ui_version"], after["ui_version"])
            finally:
                web_module._WEB_ROOT = previous_root

    def test_shared_viewport_api_is_read_only_and_identical(self) -> None:
        _, created = self.request("/api/v1/projects", {"name": "Viewport"})
        project = created["project"]["id"]
        lease = self.core.acquire_lease(project, "agent", "renderer")
        self.core.execute_command(project, lease["lease_id"],
                                  {"type": "create_box", "width": 2, "height": 3, "depth": 5})
        before = self.core.inspect_project(project)
        status, web = self.request(f"/api/v1/projects/{project}/viewport")
        agent_status, agent = self.request(f"/api/v1/agent/projects/{project}/viewport")
        self.assertEqual((status, agent_status), (200, 200))
        self.assertEqual(web, agent)
        self.assertEqual(web["revision"], 1)
        self.assertEqual(self.core.inspect_project(project), before)
        self.assertEqual(self.request("/api/v1/projects/invalid/viewport")[0], 404)
        with urlopen(self.base_url + "/assets/viewport.js", timeout=3) as response:
            self.assertIn(b"CadyxViewport", response.read())

    def test_revision_bound_topology_query_does_not_mutate_project(self) -> None:
        _, created = self.request("/api/v1/agent/projects", {"name": "Query B-rep topology"})
        project_id = created["project"]["id"]
        lease = self.core.acquire_lease(project_id, "agent", "query-agent")
        box = self.core.execute_command(project_id, lease["lease_id"], {
            "type": "create_box", "width": 10, "height": 12, "depth": 14,
        })
        body_id = box["history_entry"]["result"]["body_id"]
        revision = box["document"]["revision"]
        path = f"/api/v1/agent/projects/{project_id}/topology/query"
        before = self.core.inspect_project(project_id)
        status, found = self.request(path, {
            "body_id": body_id, "expected_revision": revision,
            "query": {"entity": "face", "surface": "plane", "max_results": 2},
        })
        self.assertEqual(status, 200)
        self.assertEqual(found["revision"], revision)
        self.assertEqual(found["status"], "ambiguous")
        self.assertEqual(found["match_count"], 6)
        self.assertTrue(found["truncated"])
        self.assertEqual([item["index"] for item in found["candidates"]], sorted(
            item["index"] for item in found["candidates"]))
        self.assertEqual(self.core.inspect_project(project_id), before)
        status, error = self.request(path, {
            "body_id": body_id, "expected_revision": revision + 1,
            "query": {"entity": "face"},
        })
        self.assertEqual((status, error["error"]), (409, "REVISION_CONFLICT"))
        status, error = self.request(path, {
            "body_id": body_id, "expected_revision": revision,
            "query": {"entity": "edge", "normal": {"vector": [0, 0, 1]}},
        })
        self.assertEqual((status, error["error"]), (400, "VALIDATION_ERROR"))
        self.assertEqual(self.core.inspect_project(project_id), before)

    def test_agent_can_inspect_stable_unique_brep_edges_for_fillets(self) -> None:
        _, created = self.request("/api/v1/agent/projects", {"name": "B-rep topology"})
        project_id = created["project"]["id"]
        lease = self.core.acquire_lease(project_id, "agent", "topology-agent")
        box = self.core.execute_command(project_id, lease["lease_id"], {
            "type": "create_box", "width": 10, "height": 12, "depth": 14,
        })["history_entry"]["result"]
        status, topology = self.request(
            f"/api/v1/agent/projects/{project_id}/topology?body_id={box['body_id']}"
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(topology["edges"]), 12)
        self.assertEqual({edge["index"] for edge in topology["edges"]}, set(range(12)))

    def test_agent_api_serves_read_only_scene_image_without_browser(self) -> None:
        status, created = self.request("/api/v1/agent/projects", {"name": "Server image"})
        self.assertEqual(status, 201)
        project_id = created["project"]["id"]
        status, acquired = self.request(
            f"/api/v1/agent/projects/{project_id}/leases",
            {"owner_id": "image-test-agent"},
        )
        self.assertEqual(status, 201)
        status, _ = self.request(
            f"/api/v1/agent/projects/{project_id}/commands",
            {"lease_id": acquired["lease"]["lease_id"],
             "command": {"type": "create_box", "width": 2, "height": 3, "depth": 5}},
        )
        self.assertEqual(status, 200)
        before = self.core.inspect_project(project_id)

        responses = []
        for prefix in ("/api/v1/projects", "/api/v1/agent/projects"):
            with urlopen(f"{self.base_url}{prefix}/{project_id}/viewport.png", timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers.get_content_type(), "image/png")
                image = response.read()
            self.assertEqual(image[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", image[16:24]), (960, 720))
            responses.append(image)

        self.assertEqual(responses[0], responses[1])
        self.assertEqual(before, self.core.inspect_project(project_id))

    def test_project_thumbnail_uses_compact_16_9_server_frame(self) -> None:
        _, created = self.request("/api/v1/projects", {"name": "Thumbnail"})
        project_id = created["project"]["id"]
        with urlopen(
            f"{self.base_url}/api/v1/projects/{project_id}/viewport.png?thumbnail=1", timeout=3,
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get("X-CADYX-Viewport-Source"), "server-thumbnail")
            self.assertEqual(struct.unpack(">II", response.read()[16:24]), (480, 270))

    def test_agent_scene_image_falls_back_when_browser_snapshot_is_stale(self) -> None:
        _, created = self.request("/api/v1/agent/projects", {"name": "Stale browser image"})
        project_id = created["project"]["id"]
        old_revision = self.core.inspect_project(project_id)["document"]["revision"]
        old_image = self.core._render_project_png_server(project_id)
        self.core.publish_viewport_snapshot(project_id, old_revision, old_image)

        lease = self.core.acquire_lease(project_id, "agent", "stale-image-test")
        self.core.execute_command(project_id, lease["lease_id"], {
            "type": "create_box", "width": 2, "height": 3, "depth": 5,
        })

        with urlopen(
            f"{self.base_url}/api/v1/agent/projects/{project_id}/viewport.png", timeout=3,
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get("X-CADYX-Viewport-Source"), "server-fallback")
            self.assertEqual(response.headers.get("X-CADYX-Document-Revision"), str(old_revision + 1))
            self.assertEqual(response.read()[:8], b"\x89PNG\r\n\x1a\n")

    def test_agent_stop_resume_revision_contract(self) -> None:
        _, created = self.request("/api/v1/projects", {"name": "Stop API"})
        project = created["project"]["id"]
        base = f"/api/v1/agent/projects/{project}"
        _, acquired = self.request(base + "/leases", {"owner_id": "agent"})
        status, stopped = self.request(base + "/stop", {"lease_id": acquired["lease"]["lease_id"]})
        self.assertEqual(status, 200)
        self.assertTrue(stopped["stopped"])
        self.assertEqual(stopped["lock"]["state"], "FREE")
        self.assertEqual(self.request(base + "/resume", {"owner_id": "agent"})[0], 400)
        self.assertEqual(self.request(base + "/resume", {"owner_id": "agent", "expected_revision": 2})[0], 409)
        status, resumed = self.request(base + "/resume", {"owner_id": "agent", "expected_revision": 0})
        self.assertEqual(status, 200)
        command = {"lease_id": resumed["lease"]["lease_id"], "command": {"type": "create_sketch"}}
        self.assertEqual(self.request(base + "/commands", command)[0], 400)
        command["expected_revision"] = 0
        self.assertEqual(self.request(base + "/commands", command)[0], 200)
        status, error = self.request(base + "/commands", command)
        self.assertEqual((status, error["error"]), (409, "REVISION_CONFLICT"))
        self.assertEqual(self.request(base)[1]["document"]["revision"], 1)

    def test_sketch_commands_through_both_api_surfaces(self) -> None:
        for surface, owner in [("projects", "human"), ("agent/projects", "agent")]:
            with self.subTest(surface=surface):
                _, created = self.request("/api/v1/projects", {"name": "Sketch API"})
                project = created["project"]["id"]
                base = f"/api/v1/{surface}/{project}"
                _, acquired = self.request(base + "/leases", {"owner_type": owner, "owner_id": "test"})
                lease = acquired["lease"]["lease_id"]

                def command(value, expected=200):
                    status, result = self.request(base + "/commands", {"lease_id": lease, "command": value})
                    self.assertEqual(status, expected, result)
                    return result

                sketch = command({"type": "create_sketch"})["history_entry"]["result"]["sketch_id"]
                points = [command({"type": "add_sketch_element", "sketch_id": sketch, "element_type": "point", "x": x, "y": 0})["history_entry"]["result"]["object_id"] for x in [0, 3]]
                line = command({"type": "add_sketch_element", "sketch_id": sketch, "element_type": "line", "start_id": points[0], "end_id": points[1]})["history_entry"]["result"]["object_id"]
                command({"type": "add_sketch_constraint", "sketch_id": sketch, "kind": "horizontal", "references": [line]})
                _, before = self.request(base)
                command({"type": "update_sketch_elements", "updates": [{"object_id": points[1], "changes": {"y": 1}}]}, 400)
                _, after = self.request(base)
                self.assertEqual(before["document"], after["document"])
                self.assertEqual(before["history"], after["history"])
                command({"type": "update_sketch_elements", "updates": [{"object_id": point, "changes": {"y": 2}} for point in points]})
                self.assertEqual(self.core.inspect_project(project)["document"]["revision"], 6)
                self.core.release_lease(project, lease)

    def test_agent_profile_operations_and_camera_orbit(self) -> None:
        _, created = self.request("/api/v1/agent/projects", {"name": "Mug API"})
        project_id = created["project"]["id"]
        base = f"/api/v1/agent/projects/{project_id}"
        _, acquired = self.request(base + "/leases", {"owner_id": "mug-agent"})
        lease_id = acquired["lease"]["lease_id"]

        def command(value: dict) -> dict:
            status, result = self.request(base + "/commands", {"lease_id": lease_id, "command": value})
            self.assertEqual(status, 200, result)
            return result["history_entry"]["result"]

        sketch_id = command({"type": "create_sketch", "name": "Cup section", "plane": "XZ"})["sketch_id"]
        values = [(0, 0), (10, 0), (10, 20), (0, 20)]
        points = [command({"type": "add_sketch_element", "sketch_id": sketch_id,
                           "element_type": "point", "x": x, "y": y})["object_id"]
                  for x, y in values]
        for index, point_id in enumerate(points):
            command({"type": "add_sketch_element", "sketch_id": sketch_id,
                     "element_type": "line", "start_id": point_id,
                     "end_id": points[(index + 1) % len(points)]})
        solid = command({"type": "revolve_sketch", "sketch_id": sketch_id,
                         "angle_degrees": 360})
        self.assertEqual(solid["geometry"]["kind"], "revolved_profile")
        before_revision = self.core.inspect_project(project_id)["document"]["revision"]

        subscriber_id, events = self.core.events.subscribe(project_id)
        self.addCleanup(self.core.events.unsubscribe, subscriber_id)
        status, orbit = self.request(base + "/viewport/camera", {
            "yaw_delta": 1.2, "pitch_delta": 0.1, "zoom_factor": 1.1, "duration_ms": 700,
        })
        self.assertEqual(status, 200)
        self.assertEqual(orbit["camera"]["yaw_delta"], 1.2)
        after_revision = self.core.inspect_project(project_id)["document"]["revision"]
        self.assertEqual(after_revision, before_revision)
        event_names = []
        while not events.empty():
            event_names.append(events.get_nowait().event)
        self.assertIn("viewport.camera", event_names)
        activity = self.core.events.activity_snapshot(project_id)[-1]
        self.assertEqual((activity["kind"], activity["state"]), ("camera", "completed"))
        self.assertEqual(activity["steps"][-1]["phase"], "animate_camera")


if __name__ == "__main__":
    unittest.main()
