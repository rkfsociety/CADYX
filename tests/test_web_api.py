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
            return error.code, json.loads(error.read())

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


if __name__ == "__main__":
    unittest.main()
