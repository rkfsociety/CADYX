from __future__ import annotations

import socket
import unittest
from http.server import BaseHTTPRequestHandler

from cadyx.web import CadyxHTTPServer


class HTTPConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = CadyxHTTPServer(
            ("127.0.0.1", 0),
            BaseHTTPRequestHandler,
            max_http_requests=1,
            max_sse_streams=1,
        )
        self.addCleanup(self.server.server_close)

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
