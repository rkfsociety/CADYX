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
from cadyx.mcp_server import CadyxAPI, MCPApplication, TOOLS, handle_message
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
        project_id = self._create_project()
        image = self.app.call_tool("cadyx_get_viewport_image", {"project_id": project_id})
        self.assertEqual([item["type"] for item in image["content"]], ["text", "image"])
        self.assertEqual(image["content"][1]["mimeType"], "image/png")
        self.assertTrue(image["content"][1]["data"])

    def test_command_uses_expected_revision_and_releases_lease(self) -> None:
        project_id = self._create_project()
        first = self._tool_value("cadyx_execute_command", {
            "project_id": project_id,
            "expected_revision": 0,
            "command": {"type": "create_box", "name": "Test box", "width": 10, "height": 8, "depth": 4},
        })
        self.assertEqual(first["revision"], 1)
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

    def test_errors_keep_saved_document_and_free_the_lease(self) -> None:
        project_id = self._create_project()
        invalid = self.app.call_tool("cadyx_execute_command", {
            "project_id": project_id,
            "expected_revision": 0,
            "command": {"type": "not_a_command"},
        })
        self.assertTrue(invalid["isError"])
        detail = self._tool_value("cadyx_inspect_project", {"project_id": project_id})
        self.assertEqual(detail["document"]["revision"], 0)
        self.assertEqual(detail["lock"]["state"], "FREE")

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
