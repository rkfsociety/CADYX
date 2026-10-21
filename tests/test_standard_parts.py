"""Native-free catalog contract checks. Run in CI, not during this change."""
from __future__ import annotations

from copy import deepcopy
import unittest
from types import SimpleNamespace

from cadyx.errors import ValidationError
from cadyx.kernel import PrimitiveKernelAdapter
from cadyx.standard_parts import list_standard_parts, resolve_standard_part, _execute
from cadyx.capabilities import agent_api_capabilities
from cadyx.web_routes import match_route
from cadyx.mcp_server import TOOLS


class _StubCommands:
    def _create_box(self, document, command):
        self.called = ("box", command)
        document.objects.append({"id": "feature:test", "type": "feature"})
        return {"body_id": "body:test", "feature_id": "feature:test", "geometry": {}}

    def _create_metric_thread(self, document, command, *, progress=None):
        self.called = ("thread", command)
        document.objects.append({"id": "feature:test", "type": "feature"})
        return {"body_id": "body:test", "feature_id": "feature:test"}

    def _add_sketch_circle(self, document, command, *, progress=None):
        self.circles.append(command)

    def _add_sketch_polyline(self, document, command, *, progress=None):
        self.polyline = command

    def _create_profile_feature(self, document, command, operation, *, progress=None):
        self.called = (operation, command)
        document.objects.append({"id": "feature:test", "type": "feature"})
        return {"body_id": "body:test", "feature_id": "feature:test",
                "source_sketch_id": command["sketch_id"]}

    def _import_step(self, document, command, *, project_id, progress=None):
        self.called = ("step", command)
        document.objects.append({"id": "feature:test", "type": "feature"})
        return {"body_id": "body:test", "feature_id": "feature:test",
                "source": {"sha256": self.file_sha}}


class StandardPartsTests(unittest.TestCase):
    def setUp(self):
        self.items = {item["id"]: item for item in list_standard_parts()["items"]}

    def request(self, catalog_id, parameters):
        entry = self.items[catalog_id]
        return {"catalog_id": catalog_id, "catalog_version": entry["version"],
                "catalog_sha256": entry["sha256"], "parameters": parameters}

    def test_manifests_are_stable_and_owned(self):
        snapshot = list_standard_parts()
        self.assertEqual(5, len(snapshot["items"]))
        before = self.items["cadyx.hex-bar"]["sha256"]
        snapshot["items"][0]["assumptions"].append("mutation")
        self.assertEqual(before, {i["id"]: i for i in list_standard_parts()["items"]}["cadyx.hex-bar"]["sha256"])
        self.assertTrue(all(len(item["sha256"]) == 64 for item in self.items.values()))

    def test_catalog_routes_and_mcp_are_discoverable(self):
        for method, path in (("GET", "/api/v1/agent/catalog"),
                             ("POST", "/api/v1/agent/catalog/resolve")):
            self.assertIsNotNone(match_route(method, path))
            self.assertIn(path, [route["path"] for route in agent_api_capabilities()["routes"]])
        tool_names = {entry["name"] for entry in TOOLS}
        self.assertIn("cadyx_list_standard_parts", tool_names)
        self.assertIn("cadyx_resolve_standard_part", tool_names)
        self.assertIn("create_catalog_part", {item["type"] for item in agent_api_capabilities()["commands"]})

    def test_exact_revision_and_parameter_contract(self):
        request = self.request("cadyx.rectangular-extrusion",
                               {"width": 10, "height": 20, "length": 100})
        result = resolve_standard_part(request)
        self.assertEqual("create_catalog_part", result["command"]["type"])
        self.assertEqual(10.0, result["command"]["parameters"]["width"])
        self.assertEqual(result["instance_sha256"], resolve_standard_part(request)["instance_sha256"])
        for change in ({"catalog_version": "0.0.0"},
                       {"catalog_sha256": "0" * 64},
                       {"parameters": {"width": 10, "height": 20, "length": 100, "code": "run"}},
                       {"parameters": {"width": True, "height": 20, "length": 100}}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                resolve_standard_part({**request, **change})

    def test_annular_and_thread_constraints(self):
        for request in (
            self.request("cadyx.ring-spacer", {"outer_diameter": 10, "inner_diameter": 10, "length": 5}),
            self.request("cadyx.threaded-stud", {"major_diameter": 5, "pitch": 5, "length": 20}),
        ):
            with self.assertRaises(ValidationError):
                resolve_standard_part(request)

    def test_geometry_dispatch_and_creation_provenance(self):
        for catalog_id, params, expected in (
            ("cadyx.rectangular-extrusion", {"width": 10, "height": 20, "length": 30}, "box"),
            ("cadyx.threaded-stud", {"major_diameter": 8, "pitch": 1.25, "length": 30}, "thread"),
            ("cadyx.hex-bar", {"across_flats": 13, "length": 20}, "extrude_sketch"),
            ("cadyx.ring-spacer", {"outer_diameter": 30, "inner_diameter": 10, "length": 7}, "extrude_sketch"),
        ):
            with self.subTest(catalog_id=catalog_id):
                fake = _StubCommands()
                fake.kernel = PrimitiveKernelAdapter()
                fake.circles = []
                document = SimpleNamespace(objects=[])
                command = resolve_standard_part(self.request(catalog_id, params))["command"]
                result = _execute(fake, document, command)
                self.assertEqual(expected, fake.called[0])
                self.assertEqual(catalog_id, result["catalog_origin"]["catalog_id"])
                self.assertEqual(result["catalog_origin"],
                                 document.objects[-1]["catalog_origin"])
                if catalog_id == "cadyx.ring-spacer":
                    self.assertEqual(2, len(fake.circles))

    def test_staged_step_needs_attested_pinned_source(self):
        params = {"file": "bearing.step", "file_sha256": "a" * 64,
                  "supplier": "Example", "part_number": "A-001",
                  "license_id": "Proprietary", "usage_rights_confirmed": True}
        request = self.request("cadyx.staged-step-reference", params)
        fake = _StubCommands()
        document = SimpleNamespace(objects=[])
        fake.file_sha = "b" * 64
        with self.assertRaises(ValidationError):
            _execute(fake, document, resolve_standard_part(request)["command"], project_id="test")
        fake.file_sha = "a" * 64
        document.objects.clear()
        result = _execute(fake, document, resolve_standard_part(request)["command"], project_id="test")
        self.assertEqual("a" * 64, result["catalog_origin"]["parameters_at_creation"]["file_sha256"])
        for wrong in ({"usage_rights_confirmed": False},
                      {"file_sha256": "bad"}, {"license_id": "Unknown"}):
            with self.assertRaises(ValidationError):
                resolve_standard_part(self.request("cadyx.staged-step-reference", {**params, **wrong}))


if __name__ == "__main__":
    unittest.main()
