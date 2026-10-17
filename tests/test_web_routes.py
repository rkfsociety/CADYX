"""Pure route-table tests for the stdlib HTTP transport."""

from __future__ import annotations

import unittest

from cadyx.web_routes import match_route, route_specs


class WebRouteTableTests(unittest.TestCase):
    def test_route_inventory_has_unique_method_templates_and_names(self) -> None:
        specs = route_specs()
        self.assertEqual(len({(spec.method, spec.template) for spec in specs}), len(specs))
        self.assertEqual(len({spec.name for spec in specs}), len(specs))

    def test_matches_representative_route_families_without_socket(self) -> None:
        cases = (
            ("GET", "/api/v1/health", "health", {}),
            ("GET", "/api/v1/workspace/folders/source%20files/files/a.step", "folder_file",
             {"folder": "source%20files", "filename": "a.step"}),
            ("GET", "/api/v1/projects/p-1/viewport.png", "project_viewport_png", {"project_id": "p-1"}),
            ("POST", "/api/v1/agent/projects/p-2/validations/thickness", "agent_validate_thickness",
             {"project_id": "p-2"}),
            ("POST", "/api/v1/agent/projects/p-2/leases/l-3/heartbeat", "agent_lease_heartbeat",
             {"project_id": "p-2", "lease_id": "l-3"}),
            ("DELETE", "/api/v1/agent/projects/p-2/files/input%20part.step", "agent_project_file_delete",
             {"project_id": "p-2", "filename": "input%20part.step"}),
            ("GET", "/assets/viewport.js", "asset_viewport", {}),
        )
        for method, path, name, params in cases:
            with self.subTest(method=method, path=path):
                match = match_route(method, path)
                self.assertIsNotNone(match)
                assert match is not None
                self.assertEqual(match.spec.name, name)
                self.assertEqual(match.params, params)

    def test_rejects_wrong_method_shape_and_unknown_paths(self) -> None:
        self.assertIsNone(match_route("DELETE", "/api/v1/health"))
        self.assertIsNone(match_route("GET", "/api/v1/projects/p-1/viewport/snapshot"))
        self.assertIsNone(match_route("POST", "/api/v1/agent/projects/p-1/files"))
        self.assertIsNone(match_route("GET", "/api/v1/not-real"))

    def test_body_kind_marks_binary_routes_before_json_parsing(self) -> None:
        snapshot = match_route("POST", "/api/v1/projects/p-1/viewport/snapshot")
        upload = match_route("POST", "/api/v1/agent/projects/p-1/files/source.step")
        command = match_route("POST", "/api/v1/projects/p-1/commands")
        assert snapshot is not None and upload is not None and command is not None
        self.assertEqual(snapshot.spec.body_kind, "png")
        self.assertEqual(upload.spec.body_kind, "step")
        self.assertEqual(command.spec.body_kind, "json")


if __name__ == "__main__":
    unittest.main()
