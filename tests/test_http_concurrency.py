from __future__ import annotations

import json
import socket
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

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
    ) -> tuple[CadyxHTTPServer, threading.Thread, str]:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        core = CadyxCore(str(Path(temporary_directory.name) / "projects"))
        server = CadyxHTTPServer(
            ("127.0.0.1", 0),
            make_request_handler(core),
            max_http_requests=max_http_requests,
            max_sse_streams=max_sse_streams,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self._stop_live_server, server, thread)
        return server, thread, f"http://127.0.0.1:{server.server_port}"

    @staticmethod
    def _wait_for_sse_capacity(server: CadyxHTTPServer, timeout: float = 1.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if server.try_acquire_sse_slot():
                server.release_sse_slot()
                return True
            time.sleep(0.01)
        return False

    def test_request_and_sse_budgets_are_independent(self) -> None:
        self.assertTrue(self.server.try_acquire_request_slot())
        self.assertFalse(self.server.try_acquire_request_slot())
        self.assertTrue(self.server.try_acquire_sse_slot())
        self.assertFalse(self.server.try_acquire_sse_slot())

        self.server.release_sse_slot()
        self.server.release_request_slot()

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
            server, _, base_url = self._start_live_server()
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
