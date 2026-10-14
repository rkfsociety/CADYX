from __future__ import annotations

import base64
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cadyx.auth import (
    AuthenticationConfigError,
    authorization_matches,
    is_loopback_host,
    load_auth_token,
    validate_bind_auth,
)
from cadyx.core import CadyxCore
from cadyx.web import CadyxHTTPServer, make_request_handler

_TOKEN = "0123456789abcdef0123456789abcdef"


class AuthenticationConfigTests(unittest.TestCase):
    def test_loopback_detection_is_fail_closed(self) -> None:
        for host in ("127.0.0.1", "127.0.0.42", "::1", "[::1]", "localhost", "LOCALHOST."):
            with self.subTest(host=host):
                self.assertTrue(is_loopback_host(host))
        for host in ("0.0.0.0", "::", "192.168.1.20", "cadyx.local", ""):
            with self.subTest(host=host):
                self.assertFalse(is_loopback_host(host))

    def test_non_loopback_bind_requires_authentication(self) -> None:
        validate_bind_auth("127.0.0.1", None)
        validate_bind_auth("0.0.0.0", _TOKEN)
        with self.assertRaises(AuthenticationConfigError):
            validate_bind_auth("0.0.0.0", None)

    def test_token_loading_requires_one_strong_source(self) -> None:
        self.assertEqual(load_auth_token(_TOKEN, None), _TOKEN)
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "token.txt"
            token_file.write_text(_TOKEN + "\n", encoding="utf-8")
            self.assertEqual(load_auth_token(None, str(token_file)), _TOKEN)
            with self.assertRaises(AuthenticationConfigError):
                load_auth_token(_TOKEN, str(token_file))
        with self.assertRaises(AuthenticationConfigError):
            load_auth_token("too-short", None)
        with self.assertRaises(AuthenticationConfigError):
            load_auth_token("x" * 31 + " ", None)

    def test_bearer_and_basic_credentials(self) -> None:
        basic = base64.b64encode(f"cadyx:{_TOKEN}".encode()).decode()
        self.assertTrue(authorization_matches(f"Bearer {_TOKEN}", _TOKEN))
        self.assertTrue(authorization_matches(f"Basic {basic}", _TOKEN))
        self.assertFalse(authorization_matches("Bearer wrong", _TOKEN))
        self.assertFalse(authorization_matches("Basic !!!", _TOKEN))
        self.assertFalse(authorization_matches(None, _TOKEN))
        self.assertTrue(authorization_matches(None, None))


class AuthenticatedWebApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.core = CadyxCore(str(Path(self.temporary_directory.name) / "projects"))
        self.server = CadyxHTTPServer(
            ("127.0.0.1", 0),
            make_request_handler(self.core, auth_token=_TOKEN),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.addCleanup(self._stop_server)

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(
        self,
        path: str,
        *,
        authorization: str | None = None,
        value: dict | None = None,
    ) -> tuple[int, bytes, object]:
        data = None if value is None else json.dumps(value).encode("utf-8")
        headers = {}
        if authorization is not None:
            headers["Authorization"] = authorization
        request = Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method="GET" if data is None else "POST",
        )
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, response.read(), response.headers
        except HTTPError as error:
            with error:
                return error.code, error.read(), error.headers

    def test_health_is_public_but_application_routes_are_protected(self) -> None:
        status, body, _ = self.request("/api/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "ok")

        for path in ("/", "/api/v1/projects", "/api/v1/agent/projects"):
            with self.subTest(path=path):
                status, body, headers = self.request(path)
                self.assertEqual(status, 401)
                self.assertEqual(json.loads(body)["error"], "AUTHENTICATION_REQUIRED")
                self.assertTrue(headers.get_all("WWW-Authenticate"))

    def test_bearer_authentication_covers_get_and_post(self) -> None:
        bearer = f"Bearer {_TOKEN}"
        status, body, _ = self.request("/api/v1/projects", authorization=bearer)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["projects"], [])

        status, body, _ = self.request(
            "/api/v1/agent/projects",
            authorization=bearer,
            value={"name": "Authenticated project"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(body)["project"]["name"], "Authenticated project")

    def test_basic_authentication_supports_browser_navigation(self) -> None:
        encoded = base64.b64encode(f"cadyx:{_TOKEN}".encode("utf-8")).decode("ascii")
        status, body, _ = self.request("/", authorization=f"Basic {encoded}")
        self.assertEqual(status, 200)
        self.assertIn(b"CADYX", body)

    def test_invalid_credentials_do_not_reach_project_routes(self) -> None:
        before = self.core.list_workspace()
        status, body, _ = self.request(
            "/api/v1/agent/projects",
            authorization="Bearer invalid",
            value={"name": "Must not exist"},
        )
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body)["error"], "AUTHENTICATION_REQUIRED")
        self.assertEqual(self.core.list_workspace(), before)


if __name__ == "__main__":
    unittest.main()
