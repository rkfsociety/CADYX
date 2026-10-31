from __future__ import annotations

import json
import socket
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import cadyx.web as web_module
from cadyx.core import CadyxCore
from cadyx.web import CadyxHTTPServer, make_request_handler


class HTTPConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = CadyxHTTPServer(
            ("127.0.0.1", 0),
            BaseHTTPRequestHandler,
            max_http_requests=1,
            max_sse_streams=1,
        )
        self.addCleanup(self.server.server_close)

    @staticmethod
    def _stop_live_server(server: CadyxHTTPServer, thread: threading.Thread) -> None:
        if thread.is_alive():
            server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    def _start_live_server(
        self,
        *,
        max_http_requests: int = 2,
        max_sse_streams: int = 1,
        auth_token: str | None = None,
    ) -> tuple[CadyxHTTPServer, threading.Thread, str]:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        core = CadyxCore(str(Path(temporary_directory.name) / "projects"))
        server = CadyxHTTPServer(
            ("127.0.0.1", 0),
            make_request_handler(core, auth_token=auth_token),
            max_http_requests=max_http_requests,
            max_sse_streams=max_sse_streams,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self._stop_live_server, server, thread)
        return server, thread, f"http://127.0.0.1:{server.server_port}"

    @staticmethod
    def _wait_for_sse_capacity(
        server: CadyxHTTPServer, timeout: float = 1.0, count: int = 1,
    ) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            acquired = 0
            try:
                for _ in range(count):
                    if not server.try_acquire_sse_slot():
                        break
                    acquired += 1
                if acquired == count:
                    return True
            finally:
                for _ in range(acquired):
                    server.release_sse_slot()
            time.sleep(0.01)
        return False

    @staticmethod
    def _wait_for_http_capacity(server: CadyxHTTPServer, timeout: float = 2.0) -> bool:
        """Wait for the previous HTTP handler to finish releasing its request slot."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if server.try_acquire_request_slot():
                server.release_request_slot()
                return True
            time.sleep(0.01)
        return False

    def test_incomplete_headers_cannot_hold_http_slot_indefinitely(self) -> None:
        with patch.object(web_module, "_HTTP_SOCKET_TIMEOUT_SECONDS", 0.2):
            server, _, base_url = self._start_live_server(max_http_requests=1)
            accepted = threading.Event()
            original_process_request = server.process_request

            def record_request(request: socket.socket, address: tuple[str, int]) -> None:
                original_process_request(request, address)
                accepted.set()

            with patch.object(server, "process_request", side_effect=record_request):
                stalled = socket.create_connection(("127.0.0.1", server.server_port), timeout=5)
                try:
                    stalled.sendall(b"GET /api/v1/health HTTP/1.1\r\nHost: localhost\r\n")
                    self.assertTrue(accepted.wait(5), "stalled request was not accepted")
                    self.assertTrue(self._wait_for_http_capacity(server, timeout=5))
                    with urlopen(base_url + "/api/v1/health", timeout=5) as response:
                        self.assertEqual(response.status, 200)
                finally:
                    stalled.close()

    def test_nonfinite_json_numbers_are_rejected_before_routing(self) -> None:
        server, _, base_url = self._start_live_server(max_http_requests=1)
        for body in (
            b'{"value":NaN}',
            b'{"value":Infinity}',
            b'{"value":-Infinity}',
            b'{"value":1e999}',
            b'{"nested":{"value":-1e999}}',
        ):
            with self.subTest(body=body):
                self.assertTrue(self._wait_for_http_capacity(server))
                request = Request(base_url + "/api/v1/unknown", data=body, method="POST")
                with self.assertRaises(HTTPError) as caught:
                    urlopen(request, timeout=2)
                with caught.exception as response:
                    self.assertEqual(response.code, 400)
                    self.assertEqual(json.loads(response.read())["error"], "VALIDATION_ERROR")

        self.assertTrue(self._wait_for_http_capacity(server))
        request = Request(base_url + "/api/v1/unknown", data=b'{"value":1.5}', method="POST")
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2)
        with caught.exception as response:
            self.assertEqual(response.code, 404)

    def test_request_and_sse_budgets_are_independent(self) -> None:
        self.assertTrue(self.server.try_acquire_request_slot())
        self.assertFalse(self.server.try_acquire_request_slot())
        self.assertTrue(self.server.try_acquire_sse_slot())
        self.assertFalse(self.server.try_acquire_sse_slot())

        self.server.release_sse_slot()
        self.server.release_request_slot()

    def test_expected_peer_disconnects_do_not_hide_other_server_errors(self) -> None:
        with patch.object(ThreadingHTTPServer, "handle_error") as parent_handler:
            for error in (ConnectionAbortedError(), ConnectionResetError(), BrokenPipeError()):
                with self.subTest(error=type(error).__name__):
                    try:
                        raise error
                    except type(error):
                        self.server.handle_error(None, ("127.0.0.1", 0))
                parent_handler.assert_not_called()

            try:
                raise RuntimeError("unexpected handler bug")
            except RuntimeError:
                self.server.handle_error(None, ("127.0.0.1", 0))
            parent_handler.assert_called_once_with(None, ("127.0.0.1", 0))

        self.assertTrue(self.server.try_acquire_request_slot())
        self.assertTrue(self.server.try_acquire_sse_slot())
        self.server.release_sse_slot()
        self.server.release_request_slot()

    def test_overload_response_is_stable_service_unavailable(self) -> None:
        server_side, client_side = socket.socketpair()
        self.addCleanup(client_side.close)

        self.server._reject_overloaded_request(server_side)
        response = client_side.recv(4096)

        self.assertIn(b"HTTP/1.1 503 Service Unavailable", response)
        self.assertIn(b"Retry-After: 1", response)
        self.assertIn(b'"error":"SERVICE_UNAVAILABLE"', response)
        self.assertIn(b'"message":"HTTP concurrency limit reached."', response)

    def test_sse_limit_returns_503_and_disconnect_releases_capacity(self) -> None:
        with (
            patch.object(web_module, "_SSE_KEEPALIVE_SECONDS", 0.05),
            patch.object(web_module, "_SSE_SHUTDOWN_POLL_SECONDS", 0.01),
        ):
            server, _, base_url = self._start_live_server(max_http_requests=1)
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
            connection.request("GET", "/api/v1/events")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.readline(), b": CADYX event stream\n")
            self.assertEqual(response.readline(), b"\n")

            try:
                with self.assertRaises(HTTPError) as caught:
                    urlopen(base_url + "/api/v1/events", timeout=2)
                with caught.exception as error:
                    self.assertEqual(error.code, 503)
                    self.assertEqual(
                        json.loads(error.read()),
                        {
                            "error": "SERVICE_UNAVAILABLE",
                            "message": "SSE concurrency limit reached.",
                        },
                    )
            finally:
                response.close()
                connection.close()

            self.assertTrue(self._wait_for_sse_capacity(server))
            self.assertTrue(self._wait_for_http_capacity(server))
            with urlopen(base_url + "/api/v1/health", timeout=2) as healthy:
                self.assertEqual(healthy.status, 200)

    def test_sse_observers_do_not_starve_http_requests(self) -> None:
        with (
            patch.object(web_module, "_SSE_KEEPALIVE_SECONDS", 0.05),
            patch.object(web_module, "_SSE_SHUTDOWN_POLL_SECONDS", 0.01),
        ):
            server, _, base_url = self._start_live_server(max_http_requests=1, max_sse_streams=2)
            streams: list[tuple[HTTPConnection, object]] = []
            try:
                for _ in range(2):
                    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
                    connection.request("GET", "/api/v1/events")
                    response = connection.getresponse()
                    streams.append((connection, response))
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.readline(), b": CADYX event stream\n")
                    self.assertEqual(response.readline(), b"\n")

                self.assertFalse(server.try_acquire_sse_slot())
                for path in ("/api/v1/health", "/api/v1/projects"):
                    self.assertTrue(self._wait_for_http_capacity(server))
                    with urlopen(base_url + path, timeout=2) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIsInstance(json.loads(response.read()), dict)
            finally:
                for connection, response in streams:
                    response.close()
                    connection.close()
            self.assertTrue(self._wait_for_sse_capacity(server, count=2))

    def test_unauthenticated_and_failed_sse_enrollment_release_capacity(self) -> None:
        server, _, base_url = self._start_live_server(
            max_http_requests=1, max_sse_streams=1, auth_token="test-secret",
        )
        with self.assertRaises(HTTPError) as unauthorized:
            urlopen(base_url + "/api/v1/events", timeout=2)
        with unauthorized.exception as error:
            self.assertEqual(error.code, 401)

        self.assertTrue(self._wait_for_http_capacity(server))
        with self.assertRaises(HTTPError) as missing:
            urlopen(Request(
                base_url + "/api/v1/events?project_id=missing",
                headers={"Authorization": "Bearer test-secret"},
            ), timeout=2)
        with missing.exception as error:
            self.assertEqual(error.code, 404)

        self.assertTrue(self._wait_for_http_capacity(server))
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        try:
            connection.request("GET", "/api/v1/events", headers={"Authorization": "Bearer test-secret"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.readline(), b": CADYX event stream\n")
            self.assertEqual(response.readline(), b"\n")
            self.assertTrue(self._wait_for_http_capacity(server))
            with urlopen(base_url + "/api/v1/health", timeout=2) as health:
                self.assertEqual(health.status, 200)
            response.close()
        finally:
            connection.close()
        server.shutdown()
        self.assertTrue(self._wait_for_sse_capacity(server))

    def test_shutdown_terminates_active_sse_loop_and_releases_capacity(self) -> None:
        with patch.object(web_module, "_SSE_SHUTDOWN_POLL_SECONDS", 0.01):
            server, thread, _ = self._start_live_server(max_http_requests=1)
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
            connection.request("GET", "/api/v1/events")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.readline(), b": CADYX event stream\n")
            self.assertEqual(response.readline(), b"\n")

            server.shutdown()
            thread.join(timeout=1)

            try:
                self.assertFalse(thread.is_alive())
                self.assertTrue(self._wait_for_sse_capacity(server))
            finally:
                response.close()
                connection.close()

    def test_ordinary_api_request_succeeds_below_limit(self) -> None:
        _, _, base_url = self._start_live_server(max_http_requests=1, max_sse_streams=1)
        with urlopen(base_url + "/api/v1/health", timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["status"], "ok")

    def test_server_close_signals_active_streams_to_stop(self) -> None:
        self.assertFalse(self.server.shutdown_event.is_set())

        self.server.server_close()

        self.assertTrue(self.server.shutdown_event.is_set())

    def test_limits_must_be_positive_integers(self) -> None:
        for keyword, value in (
            ("max_http_requests", 0),
            ("max_http_requests", True),
            ("max_sse_streams", 0),
            ("max_sse_streams", True),
        ):
            options = {"max_http_requests": 1, "max_sse_streams": 1, keyword: value}
            with self.subTest(keyword=keyword, value=value):
                with self.assertRaises(ValueError):
                    CadyxHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler, **options)


if __name__ == "__main__":
    unittest.main()
