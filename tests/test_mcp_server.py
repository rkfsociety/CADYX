from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from cadyx.capabilities import COMMAND_CAPABILITIES
from cadyx.core import CadyxCore
from cadyx.errors import ValidationError
from cadyx.mcp_server import APIError, CadyxAPI, MCPApplication, TOOLS, handle_message
from cadyx.web import CadyxHTTPServer, make_request_handler


class CadyxMCPServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.core = CadyxCore(str(Path(self.temporary_directory.name) / "projects"))
        self.server = CadyxHTTPServer(("127.0.0.1", 0), make_request_handler(self.core))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        self.environment = patch.dict(os.environ, {"CADYX_API_URL": f"http://127.0.0.1:{self.server.server_port}"}, clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.app = MCPApplication(CadyxAPI())

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _create_project(self, name: str = "MCP test") -> str:
        result = self.app.call_tool("cadyx_create_project", {"name": name})
        self.assertNotIn("isError", result)
        return result["structuredContent"]["project"]["id"]

    def _tool_value(self, name: str, args: dict | None = None):
        result = self.app.call_tool(name, args or {})
        self.assertNotIn("isError", result, result)
        return result.get("structuredContent")

    def test_protocol_discovery_and_image_content(self) -> None:
        initialize = handle_message({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
        }, self.app)
        self.assertEqual(initialize["result"]["protocolVersion"], "2025-11-25")
        self.assertEqual(initialize["result"]["serverInfo"]["name"], "cadyx-operator")
        listed = handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, self.app)
        self.assertEqual({tool["name"] for tool in listed["result"]["tools"]}, {tool["name"] for tool in TOOLS})
        capabilities = self._tool_value("cadyx_capabilities")
        self.assertEqual({item["type"] for item in capabilities["commands"]}, {item["type"] for item in COMMAND_CAPABILITIES})
        api_commands = {item["type"]: item for item in capabilities["commands"]}
        registry_commands = {item["type"]: item for item in COMMAND_CAPABILITIES}
        self.assertEqual(
            api_commands["create_box"]["parameter_schemas"],
            registry_commands["create_box"]["parameter_schemas"],
        )
        self.assertEqual(
            {
                command_type: capability["parameter_schemas"]
                for command_type, capability in api_commands.items()
                if "parameter_schemas" in capability
            },
            {
                command_type: capability["parameter_schemas"]
                for command_type, capability in registry_commands.items()
                if "parameter_schemas" in capability
            },
        )
        project_id = self._create_project()
        image = self.app.call_tool("cadyx_get_viewport_image", {"project_id": project_id})
        self.assertEqual([item["type"] for item in image["content"]], ["text", "image"])
        self.assertEqual(image["content"][1]["mimeType"], "image/png")
        self.assertTrue(image["content"][1]["data"])

    def test_topology_query_mcp_forwards_revision_and_read_only_results(self) -> None:
        project_id = self._create_project("Query geometry")
        created = self._tool_value("cadyx_execute_command", {
            "project_id": project_id, "expected_revision": 0,
            "command": {"type": "create_box", "width": 4, "height": 6, "depth": 8},
        })
        body_id = created["command_result"]["history_entry"]["result"]["body_id"]
        revision = created["revision"]
        response = self._tool_value("cadyx_query_topology", {
            "project_id": project_id, "body_id": body_id, "expected_revision": revision,
            "query": {"entity": "face", "surface": "plane"},
        })
        self.assertEqual(response["revision"], revision)
        self.assertEqual(response["status"], "ambiguous")
        self.assertEqual(response["match_count"], 6)
        stale = self.app.call_tool("cadyx_query_topology", {
            "project_id": project_id, "body_id": body_id, "expected_revision": revision - 1,
            "query": {"entity": "face"},
        })
        self.assertTrue(stale["isError"])
        self.assertIn("REVISION_CONFLICT", stale["content"][0]["text"])
        detail = self._tool_value("cadyx_inspect_project", {"project_id": project_id})
        self.assertEqual(detail["document"]["revision"], revision)
        self.assertEqual(detail["lock"]["state"], "FREE")

    def test_global_thickness_method_is_exposed_and_dispatched(self) -> None:
        tool = next(tool for tool in TOOLS if tool["name"] == "cadyx_validate_body_thickness")
        method_schema = tool["inputSchema"]["properties"]["method"]
        self.assertEqual(method_schema["enum"], ["sampled", "global_minimum"])
        self.assertNotIn("method", tool["inputSchema"]["required"])

        project_id = self._create_project("MCP global thickness")
        lease = self.core.acquire_lease(project_id, "agent", "mcp-thickness-test")
        created = self.core.execute_command(
            project_id, lease["lease_id"],
            {"type": "create_box", "width": 2.0, "height": 3.0, "depth": 5.0},
        )
        result = self._tool_value("cadyx_validate_body_thickness", {
            "project_id": project_id,
            "body_id": created["history_entry"]["result"]["body_id"],
            "expected_revision": created["document"]["revision"],
            "minimum_thickness": 1.9,
            "tolerance": 0.0,
            "method": "global_minimum",
        })
        self.assertTrue(result["certified"])
        self.assertEqual(result["status"], "pass")

    def test_execute_command_schema_tracks_discovered_command_types(self) -> None:
        tool = next(tool for tool in TOOLS if tool["name"] == "cadyx_execute_command")
        command_schema = tool["inputSchema"]["properties"]["command"]
        branches = command_schema["oneOf"]
        branches_by_type = {
            branch["properties"]["type"]["enum"][0]: branch
            for branch in branches
        }
        coordinate_schema = {
            "type": "object",
            "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
            "required": ["x", "y"],
            "additionalProperties": False,
        }
        spline_points_schema = {
            "type": "array", "minItems": 3, "items": coordinate_schema,
        }
        expected_parameter_schemas = {
            "create_box": {
                "width": {"type": "number", "exclusiveMinimum": 0},
                "height": {"type": "number", "exclusiveMinimum": 0},
                "depth": {"type": "number", "exclusiveMinimum": 0},
                "name": {"type": "string"},
            },
            "create_sketch": {
                "name": {"type": "string"},
                "plane": {"type": "string", "enum": ["XY", "XZ"]},
                "section_normal_axis": {"type": "string", "enum": ["Y", "Z"]},
                "section_offset": {"type": "number"},
            },
            "add_sketch_point": {
                "sketch_id": {"type": "string"},
                "x": {"type": "number"},
                "y": {"type": "number"},
            },
            "add_sketch_polyline": {
                "sketch_id": {"type": "string"},
                "points": {
                    "type": "array",
                    "minItems": 2,
                    "items": {
                        "type": "object",
                        "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
                        "required": ["x", "y"],
                        "additionalProperties": False,
                    },
                },
                "closed": {"type": "boolean"},
            },
            "add_sketch_rectangle": {
                "sketch_id": {"type": "string"},
                "x": {"type": "number"},
                "y": {"type": "number"},
                "width": {"type": "number", "exclusiveMinimum": 0},
                "height": {"type": "number", "exclusiveMinimum": 0},
            },
            "add_sketch_circle": {
                "sketch_id": {"type": "string"},
                "x": {"type": "number"},
                "y": {"type": "number"},
                "radius": {"type": "number", "exclusiveMinimum": 0},
            },
            **{
                command_type: {
                    "sketch_id": {"type": "string"},
                    "start": coordinate_schema,
                    "end": coordinate_schema,
                }
                for command_type in (
                    "add_sketch_segment", "add_sketch_line", "add_sketch_infinite_line",
                    "add_sketch_centerline", "add_sketch_construction_line",
                )
            },
            **{
                command_type: {
                    "sketch_id": {"type": "string"},
                    "points": spline_points_schema,
                    "construction": {"type": "boolean"},
                }
                for command_type in (
                    "add_sketch_spline", "add_sketch_closed_spline",
                    "add_sketch_spline_control_points", "add_sketch_bspline",
                    "add_sketch_bezier_curve",
                )
            },
            "add_sketch_nurbs": {
                "sketch_id": {"type": "string"},
                "points": spline_points_schema,
                "degree": {"type": "integer"},
                "knots": {"type": "array", "items": {"type": "number"}},
                "weights": {
                    "type": "array", "items": {"type": "number", "exclusiveMinimum": 0},
                },
                "construction": {"type": "boolean"},
            },
            "add_sketch_ellipse": {
                "sketch_id": {"type": "string"},
                "major_axis_start": coordinate_schema,
                "major_axis_end": coordinate_schema,
                "minor_radius": {"type": "number", "exclusiveMinimum": 0},
            },
            "add_sketch_ellipse_center": {
                "sketch_id": {"type": "string"},
                "center": coordinate_schema,
                "major_radius": {"type": "number", "exclusiveMinimum": 0},
                "minor_radius": {"type": "number", "exclusiveMinimum": 0},
                "rotation_degrees": {"type": "number"},
            },
            "add_sketch_ellipse_three_points": {
                "sketch_id": {"type": "string"},
                "center": coordinate_schema,
                "major_axis_endpoint": coordinate_schema,
                "minor_axis_endpoint": coordinate_schema,
            },
            "add_sketch_circle_diameter": {
                "sketch_id": {"type": "string"},
                "x": {"type": "number"},
                "y": {"type": "number"},
                "diameter": {"type": "number", "exclusiveMinimum": 0},
            },
            "add_sketch_circle_two_points": {
                "sketch_id": {"type": "string"},
                "first": coordinate_schema,
                "second": coordinate_schema,
            },
            "add_sketch_arc_three_points": {
                "sketch_id": {"type": "string"},
                "start": coordinate_schema,
                "through": coordinate_schema,
                "end": coordinate_schema,
            },
            "extrude_sketch": {
                "sketch_id": {"type": "string"},
                "depth": {"type": "number", "exclusiveMinimum": 0},
                "name": {"type": "string"},
                "centered": {"type": "boolean"},
            },
            "revolve_sketch": {
                "sketch_id": {"type": "string"},
                "name": {"type": "string"},
                "angle_degrees": {"type": "number", "exclusiveMinimum": 0, "maximum": 360},
            },
        }

        self.assertEqual(command_schema["type"], "object")
        self.assertEqual(set(branches_by_type), {item["type"] for item in COMMAND_CAPABILITIES})
        capabilities_by_type = {item["type"]: item for item in COMMAND_CAPABILITIES}
        for command_type, schemas in expected_parameter_schemas.items():
            with self.subTest(parameter_schemas=command_type):
                self.assertEqual(capabilities_by_type[command_type].get("parameter_schemas"), schemas)
                properties = branches_by_type[command_type]["properties"]
                for field, schema in schemas.items():
                    self.assertEqual(properties[field], schema)
        for capability in COMMAND_CAPABILITIES:
            with self.subTest(command_type=capability["type"]):
                branch = branches_by_type[capability["type"]]
                properties = branch["properties"]
                expected_fields = {
                    "type", *capability.get("required", []), *capability.get("optional", []),
                }
                self.assertEqual(set(properties), expected_fields)
                self.assertEqual(branch["required"], ["type"])

    def test_command_uses_expected_revision_and_releases_lease(self) -> None:
        project_id = self._create_project()
        first = self._tool_value("cadyx_execute_command", {
            "project_id": project_id,
            "expected_revision": 0,
            "command": {"type": "create_box", "name": "Test box", "width": 10, "height": 8, "depth": 4},
        })
        self.assertEqual(first["revision"], 1)
        self.assertEqual(first["command_result"]["document"]["object_counts"], {"body": 1, "feature": 1})
        self.assertNotIn("objects", first["command_result"]["document"])
        self.assertEqual(first["command_result"]["history_entry"]["result"]["body_id"].split(":", 1)[0], "body")
        detail = self._tool_value("cadyx_inspect_project", {"project_id": project_id})
        self.assertEqual(detail["lock"]["state"], "FREE")
        self.assertEqual(detail["document"]["revision"], 1)
        stale = self.app.call_tool("cadyx_execute_command", {
            "project_id": project_id,
            "expected_revision": 0,
            "command": {"type": "create_box", "width": 1, "height": 1, "depth": 1},
        })
        self.assertTrue(stale["isError"])
        detail = self._tool_value("cadyx_inspect_project", {"project_id": project_id})
        self.assertEqual(detail["document"]["revision"], 1)
        self.assertEqual(detail["lock"]["state"], "FREE")

    def test_selection_can_return_to_the_only_active_workflow_project(self) -> None:
        project = self.core.workspace.create("Resumable workflow")
        project_id = project["id"]
        self.core.report_agent_dashboard(project_id, {
            "workflow": {
                "title": "Продолжаем модель",
                "progress_percent": 40,
                "eta_seconds": None,
                "phases": [
                    {"id": "build", "title": "Построение модели", "state": "in_progress"},
                    {"id": "verify", "title": "Проверка модели", "state": "pending"},
                ],
            },
            "tasks": [],
        })
        self.assertIsNone(self.core.workspace_selection_snapshot())
        selected = self._tool_value("cadyx_select_resource", {"type": "project", "project_id": project_id})
        self.assertEqual(selected["selection"], {"type": "project", "project_id": project_id})

        other = self.core.workspace.create("Other project")
        blocked = self.app.call_tool("cadyx_select_resource", {"type": "project", "project_id": other["id"]})
        self.assertTrue(blocked["isError"])
        self.assertIn("PROJECT_BUSY", blocked["content"][0]["text"])
        self.assertEqual(self.core.workspace_selection_snapshot(), {"type": "project", "project_id": project_id})

    def test_can_stage_step_in_unselected_project_while_other_workflow_is_active(self) -> None:
        active = self.core.create_project("Active workflow")
        active_id = active["project"]["id"]
        self.core.report_agent_dashboard(active_id, {
            "workflow": {
                "title": "Текущая работа",
                "progress_percent": 40,
                "eta_seconds": None,
                "phases": [{"id": "build", "title": "Построение", "state": "in_progress"}],
            },
            "tasks": [],
        })
        staged = self._tool_value("cadyx_create_project", {"name": "Staged STEP", "select": False})
        staged_id = staged["project"]["id"]
        self.assertEqual(self.core.workspace_selection_snapshot(), {"type": "project", "project_id": active_id})

        source = Path(self.temporary_directory.name) / "source model.stp"
        source.write_bytes(b"ISO-10303-21;source")
        uploaded = self._tool_value("cadyx_upload_project_step", {
            "project_id": staged_id,
            "source_path": str(source),
        })
        self.assertEqual(uploaded["project_id"], staged_id)
        self.assertEqual((self.core.workspace._project_dir(staged_id) / source.name).read_bytes(), source.read_bytes())
        self.assertEqual(self.core.workspace_selection_snapshot(), {"type": "project", "project_id": active_id})

    def test_errors_keep_saved_document_and_free_the_lease(self) -> None:
        project_id = self._create_project()
        with patch.object(self.app.api, "post", wraps=self.app.api.post) as post:
            invalid = self.app.call_tool("cadyx_execute_command", {
                "project_id": project_id,
                "expected_revision": 0,
                "command": {"type": "create_box", "width": 0, "height": 2, "depth": 3},
            })
        self.assertTrue(invalid["isError"])
        self.assertFalse(any(call.args[0].endswith("/stop") for call in post.call_args_list),
                         "A definite 4xx rejection must not trigger agent safe-stop.")
        detail = self._tool_value("cadyx_inspect_project", {"project_id": project_id})
        self.assertEqual(detail["document"]["revision"], 0)
        self.assertEqual(detail["lock"]["state"], "FREE")

    def test_missing_command_enters_capability_development_flow(self) -> None:
        project_id = self._create_project()
        missing = self._tool_value("cadyx_execute_command", {
            "project_id": project_id,
            "expected_revision": 0,
            "command": {"type": "create_internal_gear", "module": 2, "tooth_count": 60},
        })
        self.assertEqual(missing["error"], "CAPABILITY_MISSING")
        self.assertEqual(missing["command_type"], "create_internal_gear")
        names = {tool["name"] for tool in TOOLS}
        self.assertTrue({"cadyx_propose_capability", "cadyx_execute_capability",
                         "cadyx_rollback_capability"} <= names)
        detail = self._tool_value("cadyx_inspect_project", {"project_id": project_id})
        self.assertEqual(detail["document"]["revision"], 0)
        self.assertEqual(detail["lock"]["state"], "FREE")

    def test_definitive_http_command_rejections_preserve_original_error_without_stop(self) -> None:
        project_id = self._create_project()
        original_post = self.app.api.post
        command = {"type": "create_box", "width": 10, "height": 8, "depth": 4}
        for status in (400, 401, 403, 404, 405, 409, 413, 415, 422):
            with self.subTest(status=status):
                rejection = APIError(status, {"error": "REJECTED", "message": "definite rejection"})

                def reject_command(path: str, body: dict):
                    if path.endswith("/commands"):
                        raise rejection
                    return original_post(path, body)

                with patch.object(self.app.api, "post", side_effect=reject_command) as post:
                    with self.assertRaises(APIError) as error:
                        self.app._execute_command(project_id, 0, command)
                self.assertIs(error.exception, rejection)
                paths = [call.args[0] for call in post.call_args_list]
                self.assertFalse(any(path.endswith("/stop") for path in paths))
                self.assertTrue(any(path.endswith("/release") for path in paths))
                self.assertNotIn(project_id, self.app._active_leases)
        detail = self._tool_value("cadyx_inspect_project", {"project_id": project_id})
        self.assertEqual(detail["document"]["revision"], 0)
        self.assertEqual(detail["lock"]["state"], "FREE")

    def test_http_408_command_rejection_is_ambiguous_and_fenced(self) -> None:
        project_id = self._create_project()
        original_post = self.app.api.post

        def timeout_command(path: str, body: dict):
            if path.endswith("/commands"):
                raise APIError(408, {"error": "TIMEOUT", "message": "outcome uncertain"})
            return original_post(path, body)

        with patch.object(self.app.api, "post", side_effect=timeout_command) as post:
            result = self.app.call_tool("cadyx_execute_command", {
                "project_id": project_id,
                "expected_revision": 0,
                "command": {"type": "create_box", "width": 10, "height": 8, "depth": 4},
            })
        self.assertTrue(result["isError"])
        self.assertIn("safely stopped/validated", result["content"][0]["text"])
        self.assertEqual(sum(call.args[0].endswith("/stop") for call in post.call_args_list), 1)
        detail = self._tool_value("cadyx_inspect_project", {"project_id": project_id})
        self.assertEqual(detail["document"]["revision"], 0)
        self.assertEqual(detail["lock"]["state"], "FREE")

    def test_unparseable_success_response_is_fenced_as_ambiguous(self) -> None:
        project_id = self._create_project()
        original_post = self.app.api.post

        def corrupt_command_response(path: str, body: dict):
            if path.endswith("/commands"):
                original_post(path, body)  # Command commits; only its response is lost.
                raise json.JSONDecodeError("invalid response JSON", "{", 1)
            return original_post(path, body)

        with patch.object(self.app.api, "post", side_effect=corrupt_command_response) as post:
            result = self.app.call_tool("cadyx_execute_command", {
                "project_id": project_id,
                "expected_revision": 0,
                "command": {"type": "create_box", "width": 10, "height": 8, "depth": 4},
            })
        self.assertTrue(result["isError"])
        self.assertIn("inspect history and geometry before deciding whether to retry", result["content"][0]["text"])
        self.assertEqual(sum(call.args[0].endswith("/stop") for call in post.call_args_list), 1)
        detail = self._tool_value("cadyx_inspect_project", {"project_id": project_id})
        self.assertEqual(detail["document"]["revision"], 1)
        self.assertEqual(len(detail["history"]), 1)
        self.assertEqual(detail["lock"]["state"], "FREE")

    def test_server_error_still_fences_ambiguous_command(self) -> None:
        project_id = self._create_project()
        original_post = self.app.api.post

        def fail_command(path: str, body: dict):
            if path.endswith("/commands"):
                raise APIError(500, {"error": "INTERNAL_ERROR", "message": "simulated server failure"})
            return original_post(path, body)

        with patch.object(self.app.api, "post", side_effect=fail_command):
            result = self.app.call_tool("cadyx_execute_command", {
                "project_id": project_id,
                "expected_revision": 0,
                "command": {"type": "create_box", "width": 10, "height": 8, "depth": 4},
            })
        self.assertTrue(result["isError"])
        self.assertIn("safely stopped/validated", result["content"][0]["text"])
        detail = self._tool_value("cadyx_inspect_project", {"project_id": project_id})
        self.assertEqual(detail["document"]["revision"], 0)
        self.assertEqual(detail["lock"]["state"], "FREE")

    def test_compact_text_content_preserves_structured_result(self) -> None:
        response = self.app.call_tool("cadyx_health", {})
        self.assertEqual(
            response["content"][0]["text"],
            json.dumps(response["structuredContent"], ensure_ascii=False, separators=(",", ":")),
        )

    def test_dropped_command_response_is_stopped_and_never_replayed(self) -> None:
        project_id = self._create_project()
        original_post = self.app.api.post
        dropped = False

        def drop_command_response(path: str, body: dict):
            nonlocal dropped
            result = original_post(path, body)
            if path.endswith("/commands") and not dropped:
                dropped = True
                raise RuntimeError("simulated lost command response")
            return result

        with patch.object(self.app.api, "post", side_effect=drop_command_response):
            result = self.app.call_tool("cadyx_execute_command", {
                "project_id": project_id,
                "expected_revision": 0,
                "command": {"type": "create_box", "width": 10, "height": 8, "depth": 4},
            })
        self.assertTrue(result["isError"])
        self.assertIn("inspect history and geometry before deciding whether to retry", result["content"][0]["text"])
        detail = self._tool_value("cadyx_inspect_project", {"project_id": project_id})
        self.assertEqual(detail["document"]["revision"], 1)
        self.assertEqual(len(detail["history"]), 1)
        self.assertEqual(detail["lock"]["state"], "FREE")

    def test_kernel_failure_is_reported_without_changing_saved_revision(self) -> None:
        project_id = self._create_project()
        with patch.object(self.core, "_validate_geometry", side_effect=ValidationError("simulated kernel validation failure")):
            result = self.app.call_tool("cadyx_execute_command", {
                "project_id": project_id,
                "expected_revision": 0,
                "command": {"type": "create_box", "width": 10, "height": 8, "depth": 4},
            })
        self.assertTrue(result["isError"])
        self.assertIn("simulated kernel validation failure", result["content"][0]["text"])
        detail = self._tool_value("cadyx_inspect_project", {"project_id": project_id})
        self.assertEqual(detail["document"]["revision"], 0)
        self.assertEqual(detail["history"], [])
        self.assertEqual(detail["lock"]["state"], "FREE")

    def test_selection_refuses_busy_current_or_target_project(self) -> None:
        source_id = self._create_project("Source")
        target_id = self._create_project("Target")
        self._tool_value("cadyx_select_resource", {"type": "project", "project_id": source_id})
        held = self.core.acquire_lease(target_id, "agent", "another-agent")
        try:
            result = self.app.call_tool("cadyx_select_resource", {"type": "project", "project_id": target_id})
            self.assertTrue(result["isError"])
            self.assertEqual(self.core.workspace_selection_snapshot(), {"type": "project", "project_id": source_id})
        finally:
            self.core.release_lease(target_id, held["lease_id"])
        held = self.core.acquire_lease(source_id, "agent", "another-agent")
        try:
            result = self.app.call_tool("cadyx_select_resource", {"type": "project", "project_id": target_id})
            self.assertTrue(result["isError"])
            self.assertEqual(self.core.workspace_selection_snapshot(), {"type": "project", "project_id": source_id})
        finally:
            self.core.release_lease(source_id, held["lease_id"])

    def test_resume_command_stop_and_expired_recovery_gate(self) -> None:
        project_id = self._create_project()
        resumed = self._tool_value("cadyx_resume_agent_work", {"project_id": project_id, "expected_revision": 0})
        self.assertTrue(resumed["resumed"])
        result = self._tool_value("cadyx_execute_command", {
            "project_id": project_id, "expected_revision": 0,
            "command": {"type": "create_box", "width": 1, "height": 1, "depth": 1},
        })
        self.assertEqual(result["revision"], 1)
        self.assertEqual(self._tool_value("cadyx_inspect_project", {"project_id": project_id})["lock"]["state"], "FREE")
        no_recovery = self.app.call_tool("cadyx_recover_expired_lease", {"project_id": project_id})
        self.assertTrue(no_recovery["isError"])

    def test_stop_fences_an_admitted_command_and_resolves_lease(self) -> None:
        project_id = self._create_project()
        entered = threading.Event()
        finish = threading.Event()
        fenced = threading.Event()
        original_execute = self.core.commands.execute
        original_stop = self.core.leases.request_agent_stop

        def slow_execute(*args, **kwargs):
            entered.set()
            if not finish.wait(timeout=5):
                raise TimeoutError("test operation did not receive release")
            return original_execute(*args, **kwargs)

        def observed_stop(*args, **kwargs):
            original_stop(*args, **kwargs)
            fenced.set()

        self.core.commands.execute = slow_execute
        self.core.leases.request_agent_stop = observed_stop
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                command = pool.submit(self.app._execute_command, project_id, 0, {"type": "create_box", "width": 1, "height": 1, "depth": 1})
                self.assertTrue(entered.wait(timeout=3))
                stop = pool.submit(self.app._stop_agent_work, project_id)
                self.assertTrue(fenced.wait(timeout=3))
                finish.set()
                stopped = stop.result(timeout=5)
                self.assertTrue(stopped["stopped"])
                self.assertEqual(stopped["document"]["revision"], 1)
                self.assertEqual(command.result(timeout=5)["revision"], 1)
        finally:
            finish.set()
            self.core.commands.execute = original_execute
            self.core.leases.request_agent_stop = original_stop
        detail = self._tool_value("cadyx_inspect_project", {"project_id": project_id})
        self.assertEqual(detail["lock"]["state"], "FREE")
        self.assertEqual(detail["document"]["revision"], 1)

    def test_file_read_is_limited_to_read_only_api_types(self) -> None:
        project_id = self._create_project()
        project_path = self.core.workspace._project_dir(project_id)
        (project_path / "reference.md").write_text("Reference notes", encoding="utf-8")
        result = self._tool_value("cadyx_read_project_file", {"project_id": project_id, "name": "reference.md"})
        self.assertEqual(result["text"], "Reference notes")
        (project_path / "opaque.bin").write_bytes(b"\x00\x01\xff")
        rejected = self.app.call_tool("cadyx_read_project_file", {"project_id": project_id, "name": "opaque.bin"})
        self.assertTrue(rejected["isError"])


if __name__ == "__main__":
    unittest.main()
