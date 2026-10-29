"""Pure route-table tests for the stdlib HTTP transport."""

from __future__ import annotations

import unittest
import threading
from http.client import HTTPConnection
from email.message import Message
from io import BytesIO
import json
from types import SimpleNamespace
from unittest.mock import patch

from cadyx.errors import ValidationError
from cadyx.web import CadyxHTTPServer, make_request_handler
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
            ("POST", "/api/v1/scanner/live-preview/client-metrics", "scanner_live_preview_client_metrics", {}),
            ("DELETE", "/api/v1/scanner/live-preview", "scanner_live_preview_stop", {}),
            ("GET", "/api/v1/scanner/turntable/settings", "turntable_settings", {}),
            ("POST", "/api/v1/scanner/turntable/settings", "turntable_settings_save", {}),
            ("GET", "/api/v1/scanner/turntable/map", "turntable_marker_map", {}),
            ("GET", "/api/v1/workspace/folders/source%20files/files/a.step", "folder_file",
             {"folder": "source%20files", "filename": "a.step"}),
            ("GET", "/api/v1/projects/p-1/files", "project_files", {"project_id": "p-1"}),
            ("POST", "/api/v1/agent/projects/p-2/files/rename", "agent_project_file_rename",
             {"project_id": "p-2"}),
            ("GET", "/api/v1/projects/p-1/files/part%20model.stp/preview", "project_file_preview",
             {"project_id": "p-1", "filename": "part%20model.stp"}),
            ("GET", "/api/v1/projects/p-1/folders/revopoint_run/files", "project_subfolder_files",
             {"project_id": "p-1", "folder_name": "revopoint_run"}),
            ("GET", "/api/v1/projects/p-1/folders/revopoint_run%2Fdepth_frames/files/frame_00000.raw",
             "project_subfolder_file",
             {"project_id": "p-1", "folder_name": "revopoint_run%2Fdepth_frames",
              "filename": "frame_00000.raw"}),
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
            ("GET", "/assets/viewport-helpers.js", "asset_viewport_helpers", {}),
            ("GET", "/assets/browser-state.js", "asset_browser_state", {}),
            ("GET", "/assets/scan-view.js", "asset_scan_view", {}),
            ("GET", "/assets/scan-preview-worker.js", "asset_scan_preview_worker", {}),
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
            "folder_name": "folder%20name",
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


class HTTPJSONBodyFramingTests(unittest.TestCase):
    def test_transfer_encoding_rejected_for_all_methods(self) -> None:
        handler_type = make_request_handler(None)
        for body_allowed in (False, True):
            with self.subTest(body_allowed=body_allowed):
                headers = Message()
                headers.add_header("Transfer-Encoding", "chunked")
                request = SimpleNamespace(headers=headers, close_connection=False)
                with self.assertRaisesRegex(ValidationError, "Transfer-Encoding"):
                    handler_type._validate_request_framing(request, body_allowed=body_allowed)
                self.assertTrue(request.close_connection)

    def test_duplicate_content_length_rejected_even_if_equal(self) -> None:
        handler_type = make_request_handler(None)
        headers = Message()
        headers.add_header("Content-Length", "2")
        headers.add_header("Content-Length", "2")
        request = SimpleNamespace(headers=headers, close_connection=False)
        with self.assertRaisesRegex(ValidationError, "Duplicate Content-Length"):
            handler_type._validate_request_framing(request, body_allowed=True)
        self.assertTrue(request.close_connection)

    def test_non_decimal_content_length_rejected(self) -> None:
        handler_type = make_request_handler(None)
        for raw_length in ("+4", "-0", "4, 4", "0x4", "4.0", "４"):
            with self.subTest(raw_length=raw_length):
                headers = Message()
                headers.add_header("Content-Length", raw_length)
                request = SimpleNamespace(headers=headers, close_connection=False)
                with self.assertRaisesRegex(ValidationError, "Content-Length is invalid"):
                    handler_type._validate_request_framing(request, body_allowed=True)
                self.assertTrue(request.close_connection)

    def test_get_and_delete_reject_unread_bodies(self) -> None:
        handler_type = make_request_handler(None)
        for raw_length in ("1", "-1", "invalid"):
            with self.subTest(length=raw_length):
                headers = Message()
                headers.add_header("Content-Length", raw_length)
                request = SimpleNamespace(headers=headers, close_connection=False)
                with self.assertRaises(ValidationError):
                    handler_type._validate_request_framing(request, body_allowed=False)
                self.assertTrue(request.close_connection)

    def test_zero_length_and_valid_post_body_framing_allowed(self) -> None:
        handler_type = make_request_handler(None)
        for body_allowed, raw_length in ((False, "0"), (True, "42")):
            headers = Message()
            headers.add_header("Content-Length", raw_length)
            request = SimpleNamespace(headers=headers, close_connection=False)
            handler_type._validate_request_framing(request, body_allowed=body_allowed)
            self.assertFalse(request.close_connection)

    def test_short_json_body_is_rejected_even_if_prefix_is_valid(self) -> None:
        handler_type = make_request_handler(None)
        for payload, length in ((b"{}", 3), (b"", 1)):
            with self.subTest(payload=payload, length=length):
                request = SimpleNamespace(
                    headers={"Content-Length": str(length)},
                    rfile=BytesIO(payload),
                    close_connection=False,
                )
                with self.assertRaisesRegex(ValidationError, "incomplete"):
                    handler_type._read_json(request)
                self.assertTrue(request.close_connection)

    def test_exact_json_length_and_empty_body_remain_supported(self) -> None:
        handler_type = make_request_handler(None)
        for payload, expected in ((b'{"ok":true}', {"ok": True}), (b"", None)):
            with self.subTest(payload=payload):
                request = SimpleNamespace(
                    headers={"Content-Length": str(len(payload))},
                    rfile=BytesIO(payload),
                    close_connection=False,
                )
                self.assertEqual(handler_type._read_json(request), expected)
                self.assertFalse(request.close_connection)

    def test_post_errors_close_connection_with_unread_body(self) -> None:
        handler_type = make_request_handler(None)
        for error, expected_status in (
            (ValidationError("Invalid upload."), 400),
            (json.JSONDecodeError("Invalid JSON", "x", 0), 400),
            (RuntimeError("Unexpected failure"), 500),
        ):
            with self.subTest(error=type(error).__name__):
                headers = Message()
                headers.add_header("Content-Length", "4")
                responses = []
                # Use an actual handler instance so framing validation runs;
                # a SimpleNamespace lacks the handler's bound methods.
                request = handler_type.__new__(handler_type)
                request.path = "/api/v1/agent/projects/p-1/files/source.step"
                request.headers = headers
                request.close_connection = False
                request._require_auth = lambda: True
                request._json = lambda status, body: responses.append((status, body))
                # Simulate a route failing before it reads the four payload bytes.
                with patch("cadyx.web.dispatch_route", side_effect=error):
                    handler_type.do_POST(request)
                self.assertTrue(request.close_connection)
                self.assertEqual(responses[0][0], expected_status)

    def test_rejected_post_advertises_close_and_next_request_reconnects(self) -> None:
        # A rejected POST can leave unread bytes. HTTP/1.1 clients must be
        # told that the server will close, or their next request uses a dead socket.
        server = CadyxHTTPServer(("127.0.0.1", 0), make_request_handler(None))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                client.request("POST", "/not-a-route", body=b"x",
                               headers={"Content-Length": "+1"})
                rejected = client.getresponse()
                self.assertEqual(rejected.status, 400)
                self.assertEqual(rejected.getheader("Connection"), "close")
                rejected.read()

                # Reuse the same client object: it must establish a fresh socket.
                client.request("GET", "/not-a-route")
                followup = client.getresponse()
                self.assertEqual(followup.status, 404)
                followup.read()
            finally:
                client.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_binary_content_type_rejection_closes_unread_request(self) -> None:
        handler_type = make_request_handler(None)
        for reader in ("_read_viewport_png", "_read_project_source_upload"):
            with self.subTest(reader=reader):
                request = SimpleNamespace(
                    headers={"Content-Type": "text/plain", "Content-Length": "4"},
                    rfile=BytesIO(b"data"),
                    close_connection=False,
                )
                with self.assertRaisesRegex(ValidationError, "Content-Type"):
                    getattr(handler_type, reader)(request)
                self.assertTrue(request.close_connection)

    def test_invalid_or_oversized_json_length_closes_connection(self) -> None:
        handler_type = make_request_handler(None)
        for raw_length in ("invalid", "-1", "1000001"):
            with self.subTest(raw_length=raw_length):
                request = SimpleNamespace(
                    headers={"Content-Length": raw_length},
                    rfile=BytesIO(b""),
                    close_connection=False,
                )
                with self.assertRaises(ValidationError):
                    handler_type._read_json(request)
                self.assertTrue(request.close_connection)


if __name__ == "__main__":
    unittest.main()
