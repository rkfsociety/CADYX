"""Pure route-table tests for the stdlib HTTP transport."""

from __future__ import annotations

import unittest

from cadyx.errors import ValidationError
from cadyx.web_routes import _scanner_point_cloud_vertex_count, match_route, route_specs


class WebRouteTableTests(unittest.TestCase):
    def test_scanner_point_cloud_header_requires_bounded_ascii_points_without_faces(self) -> None:
        header = (
            b"ply\nformat ascii 1.0\nelement vertex 3\n"
            b"property float x\nproperty float y\nproperty float z\nend_header\n"
        )
        self.assertEqual(_scanner_point_cloud_vertex_count(header), 3)
        with self.assertRaisesRegex(ValidationError, "point-only PLY"):
            _scanner_point_cloud_vertex_count(
                header.replace(b"element vertex 3", b"element vertex 3\nelement face 1")
            )
        with self.assertRaisesRegex(ValidationError, "ASCII point-only PLY"):
            _scanner_point_cloud_vertex_count(header.replace(b"format ascii", b"format binary_little_endian"))

    def test_route_inventory_has_unique_method_templates_and_names(self) -> None:
        specs = route_specs()
        self.assertEqual(len({(spec.method, spec.template) for spec in specs}), len(specs))
        self.assertEqual(len({spec.name for spec in specs}), len(specs))

    def test_route_inventory_uses_known_domains_and_body_kinds(self) -> None:
        specs = route_specs()
        self.assertEqual(
            {spec.domain for spec in specs},
            {"system", "workspace", "scanner", "files", "project", "agent", "validation", "viewport", "static"},
        )
        self.assertLessEqual({spec.body_kind for spec in specs}, {"none", "json", "png", "project_source"})
        self.assertTrue(all(spec.body_kind == "none" for spec in specs if spec.method == "GET"))
        self.assertTrue(all(spec.body_kind == "none" for spec in specs if spec.method == "DELETE"))

    def test_matches_representative_route_families_without_socket(self) -> None:
        cases = (
            ("GET", "/api/v1/health", "health", {}),
            ("GET", "/api/v1/scanner/live-preview", "scanner_live_preview_status", {}),
            ("POST", "/api/v1/scanner/live-preview", "scanner_live_preview_start", {}),
            ("DELETE", "/api/v1/scanner/live-preview", "scanner_live_preview_stop", {}),
            ("GET", "/api/v1/scanner/turntable/settings", "turntable_settings", {}),
            ("POST", "/api/v1/scanner/turntable/settings", "turntable_settings_save", {}),
            ("GET", "/api/v1/scanner/turntable/map", "turntable_marker_map", {}),
            ("GET", "/api/v1/workspace/folders/source%20files/files/a.step", "folder_file",
             {"folder": "source%20files", "filename": "a.step"}),
            ("GET", "/api/v1/projects/p-1/viewport.png", "project_viewport_png", {"project_id": "p-1"}),
            ("POST", "/api/v1/agent/projects/p-2/validations/thickness", "agent_validate_thickness",
             {"project_id": "p-2"}),
            ("POST", "/api/v1/agent/projects/p-2/topology/query", "agent_topology_query",
             {"project_id": "p-2"}),
            ("POST", "/api/v1/agent/projects/p-2/leases/l-3/heartbeat", "agent_lease_heartbeat",
             {"project_id": "p-2", "lease_id": "l-3"}),
            ("DELETE", "/api/v1/agent/projects/p-2/files/input%20part.step", "agent_project_file_delete",
             {"project_id": "p-2", "filename": "input%20part.step"}),
            ("GET", "/assets/viewport.js", "asset_viewport", {}),
            ("GET", "/assets/scan-view.js", "asset_scan_view", {}),
        )
        for method, path, name, params in cases:
            with self.subTest(method=method, path=path):
                match = match_route(method, path)
                self.assertIsNotNone(match)
                assert match is not None
                self.assertEqual(match.spec.name, name)
                self.assertEqual(match.params, params)

    def test_every_declared_route_matches_its_own_template(self) -> None:
        replacements = {
            "folder": "folder%20name",
            "filename": "part%20name.step",
            "project_id": "project-123",
            "lease_id": "lease-456",
        }
        for spec in route_specs():
            path = spec.template
            for name, value in replacements.items():
                path = path.replace("{" + name + "}", value)
            with self.subTest(method=spec.method, template=spec.template):
                match = match_route(spec.method, path)
                self.assertIsNotNone(match)
                assert match is not None
                self.assertEqual(match.spec, spec)

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
        self.assertEqual(upload.spec.body_kind, "project_source")
        self.assertEqual(command.spec.body_kind, "json")


if __name__ == "__main__":
    unittest.main()
