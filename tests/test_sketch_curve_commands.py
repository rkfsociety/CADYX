from __future__ import annotations

import json
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cadyx.core import CadyxCore
from cadyx.kernel import PrimitiveKernelAdapter
from cadyx.models import Document
from cadyx.web import CadyxHTTPServer, make_request_handler


class SketchCurveCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.core = CadyxCore(self.temp.name, kernel=PrimitiveKernelAdapter())
        self.project_id = self.core.create_project("Sketch curve commands")["project"]["id"]
        self.lease_id = self.core.acquire_lease(
            self.project_id, "agent", "sketch-curve-command-tests",
        )["lease_id"]
        self.server = CadyxHTTPServer(("127.0.0.1", 0), make_request_handler(self.core))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.addCleanup(self._stop_server)
        self.sketch_id = self.command({"type": "create_sketch"})["sketch_id"]
        self.project_file = self.core.workspace._project_dir(self.project_id) / "project.json"

    def command(self, value: dict) -> dict:
        status, response = self.request({"lease_id": self.lease_id, "command": value})
        if status != 200:
            raise AssertionError(f"Command request returned HTTP {status}: {response}")
        return response["history_entry"]["result"]

    def request(self, value: dict) -> tuple[int, dict]:
        request = Request(
            f"{self.base_url}/api/v1/agent/projects/{self.project_id}/commands",
            data=json.dumps(value).encode("utf-8"),
            method="POST",
        )
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_bspline_command_returns_api_result_and_persists_curve_document(self) -> None:
        status, response = self.request({"lease_id": self.lease_id, "command": {
            "type": "add_sketch_bspline",
            "sketch_id": self.sketch_id,
            "points": [{"x": 0, "y": 0}, {"x": 2, "y": 4},
                       {"x": 5, "y": 3}, {"x": 7, "y": 0}],
            "construction": True,
        }})

        self.assertEqual(status, 200)
        result = response["history_entry"]["result"]
        self.assertEqual(result["sketch_id"], self.sketch_id)
        self.assertEqual(result["mode"], "bspline")
        self.assertEqual(result["point_count"], 4)
        persisted = Document.from_dict(response["document"])
        by_id = {item["id"]: item for item in persisted.objects}
        spline = by_id[result["spline_id"]]
        self.assertEqual(spline["type"], "spline")
        self.assertEqual(spline["mode"], "bspline")
        self.assertEqual(spline["point_ids"], result["point_ids"])
        self.assertTrue(spline["construction"])
        self.assertTrue(all(by_id[point_id]["construction"] for point_id in result["point_ids"]))

    def test_bezier_command_returns_api_result_and_persists_curve_document(self) -> None:
        status, response = self.request({"lease_id": self.lease_id, "command": {
            "type": "add_sketch_bezier_curve",
            "sketch_id": self.sketch_id,
            "points": [{"x": -1, "y": 2}, {"x": 1, "y": 5},
                       {"x": 4, "y": -2}],
        }})

        self.assertEqual(status, 200)
        result = response["history_entry"]["result"]
        self.assertEqual(result["sketch_id"], self.sketch_id)
        self.assertEqual(result["mode"], "bezier")
        self.assertEqual(result["point_count"], 3)
        persisted = Document.from_dict(response["document"])
        by_id = {item["id"]: item for item in persisted.objects}
        spline = by_id[result["spline_id"]]
        self.assertEqual(spline["type"], "spline")
        self.assertEqual(spline["mode"], "bezier")
        self.assertEqual(spline["point_ids"], result["point_ids"])
        self.assertFalse(spline["construction"])

    def test_nurbs_command_returns_api_result_and_persists_curve_definition(self) -> None:
        status, response = self.request({"lease_id": self.lease_id, "command": {
            "type": "add_sketch_nurbs",
            "sketch_id": self.sketch_id,
            "points": [{"x": 0, "y": 0}, {"x": 2, "y": 4}, {"x": 5, "y": 0}],
            "degree": 2,
            "knots": [0, 0, 0, 1, 1, 1],
            "weights": [1, 0.5, 1],
        }})

        self.assertEqual(status, 200)
        result = response["history_entry"]["result"]
        self.assertEqual(result["mode"], "nurbs")
        persisted = Document.from_dict(response["document"])
        by_id = {item["id"]: item for item in persisted.objects}
        spline = by_id[result["spline_id"]]
        self.assertEqual(spline["degree"], 2)
        self.assertEqual(spline["knots"], [0, 0, 0, 1, 1, 1])
        self.assertEqual(spline["weights"], [1, 0.5, 1])

    def test_nurbs_command_rejects_invalid_definition_without_saving(self) -> None:
        before = self.project_file.read_bytes()
        status, _ = self.request({"lease_id": self.lease_id, "command": {
            "type": "add_sketch_nurbs",
            "sketch_id": self.sketch_id,
            "points": [{"x": 0, "y": 0}, {"x": 2, "y": 4}, {"x": 5, "y": 0}],
            "degree": 2,
            "knots": [0, 0, 0, 1, 1],
            "weights": [1, 1, 1],
        }})
        self.assertEqual(status, 400)
        self.assertEqual(self.project_file.read_bytes(), before)

    def test_invalid_curve_commands_leave_saved_project_unchanged(self) -> None:
        before = self.project_file.read_bytes()
        for kind in ("add_sketch_bspline", "add_sketch_bezier_curve"):
            with self.subTest(command=kind):
                status, _ = self.request({"lease_id": self.lease_id, "command": {
                        "type": kind,
                        "sketch_id": self.sketch_id,
                        "points": [{"x": 0, "y": 0}, {"x": 1, "y": 1},
                                   {"x": 1, "y": 1}],
                    }})
                self.assertEqual(status, 400)
                self.assertEqual(self.project_file.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
