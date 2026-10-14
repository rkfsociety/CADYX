from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cadyx.core import CadyxCore
from cadyx.web import CadyxHTTPServer, make_request_handler


class WebApiTests(unittest.TestCase):
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

    def test_legacy_migration_uses_both_api_command_paths(self) -> None:
        for prefix, owner in (("/api/v1/projects", "human"), ("/api/v1/agent/projects", "agent")):
            with self.subTest(owner=owner):
                _, created = self.request("/api/v1/projects", {"name": "Legacy API"})
                project_id = created["project"]["id"]
                path = self.core.workspace.root / project_id / "project.json"
                legacy = json.loads(path.read_text())
                legacy["document"]["version"] = 1
                path.write_text(json.dumps(legacy))
                before = path.read_bytes()
                status, detail = self.request(f"{prefix}/{project_id}")
                self.assertEqual(status, 200)
                self.assertEqual(detail["document"]["version"], 2)
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
                self.assertEqual(saved["document"]["version"], 2)
                self.assertEqual(saved["history"][0]["actor_type"], owner)

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

    def test_web_assets_are_served(self) -> None:
        with urlopen(self.base_url + "/", timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"CADYX", response.read())

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


if __name__ == "__main__":
    unittest.main()
