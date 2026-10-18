"""Bounded compact-inspection regressions; execution belongs to CI."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from cadyx.compact_inspection import inspect_compact
from cadyx.core import CadyxCore
from cadyx.errors import ConflictError, ValidationError
from cadyx.mcp_server import CadyxAPI, MCPApplication, TOOLS
from cadyx.web import CadyxHTTPServer, make_request_handler


class FakeKernel:
    def __init__(self) -> None:
        self.topology_calls = 0
        self.metric_calls = 0

    def metrics(self, geometry: dict) -> dict:
        self.metric_calls += 1
        return {"volume": 24.0, "bounds": {"min": [0, 0, 0], "max": [2, 3, 4]},
                "unused_payload": "ignore"}

    def topology(self, geometry: dict) -> dict:
        self.topology_calls += 1
        return {"faces": [{"index": n, "surface": "plane", "area": n + 1,
                           "brep_data": "never return"} for n in (3, 1, 2)],
                "edges": [{"index": 0, "curve": "line", "length": 2.0}]}


def fixture() -> dict:
    objects = [
        {"id": "feature:b", "type": "feature", "body_id": "body:a",
         "source_sketch_id": "sketch:s", "name": "Extrusion",
         "parameters": {"depth": 4}, "huge": "X" * 50000},
        {"id": "body:a", "type": "body", "name": "Plate",
         "feature_ids": ["feature:b"], "geometry": {"encoded_brep": "X" * 50000}},
        {"id": "sketch:s", "type": "sketch", "name": "Sketch"},
    ]
    history = [
        {"revision": 1, "timestamp": "2026-10-18", "command": {"type": "create_sketch"},
         "result": {"huge": "Y" * 50000}},
        {"revision": 2, "timestamp": "2026-10-18", "command": {"type": "extrude_sketch"},
         "result": {"feature_id": "feature:b", "data": "Z" * 50000}},
        {"revision": 3, "timestamp": "2026-10-18", "command": {"type": "set_parameter", "feature_id": "feature:b"},
         "result": {"huge": "Q" * 50000}},
    ]
    return {"id": "project-test", "name": "Example", "document": {
        "revision": 3, "objects": objects,
        "model_parameters": {"depth": {"value": 4, "unit": "mm"}},
        "parameter_bindings": [{"object_id": "feature:b", "path": "parameters.depth",
                                "expression": "depth"}]},
        "history": history}


class CompactInspectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = fixture()
        self.kernel = FakeKernel()

    def inspect(self, **options):
        return inspect_compact(self.project, self.kernel, **options)

    def test_summary_parameters_and_no_native_work(self):
        result = self.inspect()
        self.assertEqual((result["revision"], result["body_count"], result["parameter_count"]), (3, 1, 1))
        self.assertEqual(result["materials"]["status"], "not_modeled")
        self.assertEqual(result["units"]["parameter_units"], ["mm"])
        self.assertNotIn("encoded_brep", json.dumps(result))
        parameter = self.inspect(section="parameters", limit=1)
        self.assertEqual(parameter["items"], [{"name": "depth", "value": 4, "unit": "mm"}])
        self.assertEqual((self.kernel.topology_calls, self.kernel.metric_calls), (0, 0))

    def test_entity_paging_dependencies_and_on_demand_metrics(self):
        first = self.inspect(section="entities", limit=1)
        self.assertEqual((first["total"], first["omitted"], first["items"][0]["id"]),
                         (3, 2, "body:a"))
        second = self.inspect(section="entities", limit=1, offset=1, expected_revision=3)
        self.assertEqual(second["items"][0]["id"], "feature:b")
        self.assertEqual(second["items"][0]["dependencies"], ["body:a", "sketch:s"])
        self.assertEqual(second["items"][0]["parameter_names"], ["depth"])
        selected = self.inspect(section="entities", kind="body", include_metrics=True)
        self.assertEqual(selected["items"][0]["geometry_metrics"]["volume"], 24.0)
        self.assertEqual(self.kernel.metric_calls, 1)
        self.assertNotIn("unused_payload", json.dumps(selected))
        self.assertNotIn("encoded_brep", json.dumps(first))

    def test_topology_slices_are_filtered_sorted_and_revision_pinned(self):
        page = self.inspect(section="topology", kind="face", body_id="body:a",
                            shape="plane", limit=2, offset=1, expected_revision=3)
        self.assertEqual([item["index"] for item in page["items"]], [2, 3])
        self.assertEqual((page["total"], page["has_more"]), (3, False))
        self.assertEqual(self.kernel.topology_calls, 1)
        self.assertNotIn("brep_data", json.dumps(page))
        with self.assertRaises(ConflictError):
            self.inspect(section="topology", kind="edge", body_id="body:a", expected_revision=2)

    def test_change_deltas_only_report_recorded_feature_references(self):
        changes = self.inspect(section="changes", since_revision=1, limit=1)
        self.assertEqual(changes["total"], 2)
        self.assertTrue(changes["has_more"])
        self.assertEqual(changes["items"][0]["feature_ids"], ["feature:b"])
        self.assertFalse(changes["complete_geometry_diff"])
        self.assertEqual(changes["feature_change_coverage"], "explicit_history_references_only")
        tail = self.inspect(section="changes", since_revision=1, limit=1, offset=1)
        self.assertEqual(tail["items"][0]["revision"], 3)
        self.assertNotIn("Z" * 100, json.dumps(changes))

    def test_unsupported_options_fail_closed_and_payload_is_bounded(self):
        bad = (
            {"section": "changes"}, {"section": "changes", "since_revision": 4},
            {"section": "topology", "kind": "body", "body_id": "body:a"},
            {"section": "entities", "limit": 101}, {"section": "entities", "offset": True},
            {"section": "summary", "shape": "plane"},
        )
        for options in bad:
            with self.subTest(options=options), self.assertRaises(ValidationError):
                self.inspect(**options)
        full_bytes = len(json.dumps(self.project).encode())
        compact_bytes = len(json.dumps(self.inspect(section="entities")).encode())
        self.assertLess(compact_bytes * 20, full_bytes)


class CompactInspectionTransportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.core = CadyxCore(str(Path(self.directory.name) / "projects"))
        self.server = CadyxHTTPServer(("127.0.0.1", 0), make_request_handler(self.core))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)
        self.project_id = self.core.create_project("Inspection")["project"]["id"]
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def _stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_http_mcp_discovery_validation_and_no_mutation(self):
        path = f"/api/v1/agent/projects/{self.project_id}/inspection"
        with urlopen(self.base + path + "?section=summary", timeout=5) as response:
            summary = json.loads(response.read())
        self.assertEqual((summary["revision"], summary["object_count"]), (0, 0))
        with urlopen(self.base + path + "?section=entities&limit=1", timeout=5) as response:
            entities = json.loads(response.read())
        self.assertEqual(entities["items"], [])
        for query in ("?section=changes", "?section=summary&limit=999",
                      "?section=summary&expected_revision=1", "?invalid=1"):
            with self.subTest(query=query), self.assertRaises(HTTPError) as raised:
                urlopen(self.base + path + query, timeout=5)
            self.assertEqual(raised.exception.code, 409 if "expected_revision" in query else 400)
        tool = next(item for item in TOOLS if item["name"] == "cadyx_inspect_compact")
        self.assertIn("limit", tool["inputSchema"]["properties"])
        api = CadyxAPI()
        api.base_url = self.base
        mcp = MCPApplication(api)
        result = mcp.call_tool("cadyx_inspect_compact", {
            "project_id": self.project_id, "section": "changes", "since_revision": 0,
        })
        self.assertNotIn("isError", result)
        self.assertEqual(result["structuredContent"]["items"], [])
        self.assertEqual(self.core.workspace.load(self.project_id)["document"]["revision"], 0)


if __name__ == "__main__":
    unittest.main()
