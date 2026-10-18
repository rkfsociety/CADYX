"""Small stdlib HTTP API and server-sent-event transport."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socket import socket
from typing import Any
from urllib.parse import urlsplit

from .auth import authorization_matches
from .core import CadyxCore
from .errors import CadyxError, NotFoundError, ValidationError
from .web_routes import dispatch_route, match_route

_MAX_BODY_BYTES = 1_000_000
_MAX_STEP_UPLOAD_BYTES = 64 * 1024 * 1024
_MAX_VIEWPORT_SNAPSHOT_BYTES = 16 * 1024 * 1024
_DEFAULT_MAX_HTTP_REQUESTS = 64
_DEFAULT_MAX_SSE_STREAMS = 8
_SSE_KEEPALIVE_SECONDS = 15.0
_SSE_SHUTDOWN_POLL_SECONDS = 0.25
_SOCKET_REQUEST = socket | tuple[bytes, socket]
_WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
_UI_ASSETS = (
    "index.html", "scanner.html", "app.js", "style.css", "viewport.js",
    "ply-parser.js", "ply-preview.js", "scanner-camera.js", "favicon.svg",
)
_CLIENT_API_CONTRACT_VERSION = "viewport-snapshot-v1"
_UI_ASSET_VERSION_LOCK = threading.Lock()
_UI_ASSET_VERSION_CACHE: tuple[tuple[tuple[str, int, int], ...], str] | None = None


def _ui_asset_version() -> str:
    global _UI_ASSET_VERSION_CACHE
    with _UI_ASSET_VERSION_LOCK:
        signature = tuple(
            (filename, stat.st_mtime_ns, stat.st_size)
            for filename in _UI_ASSETS
            for stat in [(_WEB_ROOT / filename).stat()]
        )
        if _UI_ASSET_VERSION_CACHE is not None and _UI_ASSET_VERSION_CACHE[0] == signature:
            return _UI_ASSET_VERSION_CACHE[1]
        digest = hashlib.sha256()
        digest.update(_CLIENT_API_CONTRACT_VERSION.encode("ascii"))
        digest.update(b"\0")
        for filename in _UI_ASSETS:
            digest.update(filename.encode("utf-8"))
            digest.update(b"\0")
            digest.update((_WEB_ROOT / filename).read_bytes())
            digest.update(b"\0")
        version = digest.hexdigest()
        _UI_ASSET_VERSION_CACHE = (signature, version)
        return version


class CadyxHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server with bounded request/SSE admission and shutdown signaling."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler_class: type[BaseHTTPRequestHandler],
        *,
        max_http_requests: int = _DEFAULT_MAX_HTTP_REQUESTS,
        max_sse_streams: int = _DEFAULT_MAX_SSE_STREAMS,
    ) -> None:
        if not isinstance(max_http_requests, int) or isinstance(max_http_requests, bool) or max_http_requests < 1:
            raise ValueError("max_http_requests must be a positive integer.")
        if not isinstance(max_sse_streams, int) or isinstance(max_sse_streams, bool) or max_sse_streams < 1:
            raise ValueError("max_sse_streams must be a positive integer.")
        self.max_http_requests = max_http_requests
        self.max_sse_streams = max_sse_streams
        self.shutdown_event = threading.Event()
        self._request_slots = threading.BoundedSemaphore(max_http_requests)
        self._sse_slots = threading.BoundedSemaphore(max_sse_streams)
        self._request_slot_state = threading.local()
        super().__init__(server_address, request_handler_class)

    def try_acquire_request_slot(self) -> bool:
        return self._request_slots.acquire(blocking=False)

    def release_request_slot(self) -> None:
        self._request_slots.release()

    def release_request_slot_for_sse(self) -> None:
        """Transfer an authenticated SSE subscriber out of the HTTP request budget."""
        if not getattr(self._request_slot_state, "held", False):
            raise RuntimeError("SSE request does not own an HTTP slot.")
        self._request_slot_state.held = False
        self.release_request_slot()

    def try_acquire_sse_slot(self) -> bool:
        return self._sse_slots.acquire(blocking=False)

    def release_sse_slot(self) -> None:
        self._sse_slots.release()

    def process_request(self, request: _SOCKET_REQUEST, client_address: tuple[str, int]) -> None:
        if not self.try_acquire_request_slot():
            self._reject_overloaded_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.release_request_slot()
            raise

    def process_request_thread(self, request: _SOCKET_REQUEST, client_address: tuple[str, int]) -> None:
        self._request_slot_state.held = True
        try:
            super().process_request_thread(request, client_address)
        finally:
            if self._request_slot_state.held:
                self.release_request_slot()
            del self._request_slot_state.held

    def _reject_overloaded_request(self, request: _SOCKET_REQUEST) -> None:
        body = (
            b'{"error":"SERVICE_UNAVAILABLE","message":'
            b'"HTTP concurrency limit reached."}'
        )
        response = (
            b"HTTP/1.1 503 Service Unavailable\r\n"
            b"Content-Type: application/json; charset=utf-8\r\n"
            + f"Content-Length: {len(body)}\r\n".encode("ascii")
            + b"Cache-Control: no-store\r\n"
            + b"Retry-After: 1\r\n"
            + b"Connection: close\r\n\r\n"
            + body
        )
        connection = request[1] if isinstance(request, tuple) else request
        try:
            connection.sendall(response)
        except OSError:
            pass
        finally:
            self.shutdown_request(request)

    def shutdown(self) -> None:
        self.shutdown_event.set()
        super().shutdown()

    def server_close(self) -> None:
        self.shutdown_event.set()
        super().server_close()


def make_request_handler(
    core: CadyxCore,
    auth_token: str | None = None,
) -> type[BaseHTTPRequestHandler]:
    class CadyxRequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            route = match_route("GET", parsed.path)
            try:
                if route is not None and route.spec.name == "health":
                    dispatch_route(self, core, route, parsed)
                    return
                if not self._require_auth():
                    return
                if route is None:
                    self._not_found()
                    return
                dispatch_route(self, core, route, parsed)
            except CadyxError as exc:
                self._json(exc.http_status, exc.as_dict())
            except Exception:
                self._json(500, {"error": "INTERNAL_ERROR", "message": "CADYX encountered an unexpected internal error."})

        def do_POST(self) -> None:
            parsed = urlsplit(self.path)
            try:
                if not self._require_auth():
                    return
                route = match_route("POST", parsed.path)
                if route is None:
                    # Preserve the historical POST contract: malformed JSON is
                    # reported before a not-found response for unknown routes.
                    self._read_json()
                    self._not_found()
                    return
                dispatch_route(self, core, route, parsed)
            except CadyxError as exc:
                self._json(exc.http_status, exc.as_dict())
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._json(400, {"error": "INVALID_JSON", "message": "Request body must be valid UTF-8 JSON."})
            except Exception:
                self._json(500, {"error": "INTERNAL_ERROR", "message": "CADYX encountered an unexpected internal error."})

        def do_DELETE(self) -> None:
            parsed = urlsplit(self.path)
            try:
                if not self._require_auth():
                    return
                route = match_route("DELETE", parsed.path)
                if route is None:
                    self._not_found()
                    return
                dispatch_route(self, core, route, parsed)
            except CadyxError as exc:
                self._json(exc.http_status, exc.as_dict())
            except Exception:
                self._json(500, {"error": "INTERNAL_ERROR", "message": "CADYX encountered an unexpected internal error."})

        def _not_found(self) -> None:
            self._json(404, {"error": "NOT_FOUND", "message": "Route was not found."})

        def _ui_asset_version(self) -> str:
            return _ui_asset_version()

        def _require_auth(self) -> bool:
            if authorization_matches(self.headers.get("Authorization"), auth_token):
                return True
            # Do not reuse a connection whose unauthenticated request body was not read.
            self.close_connection = True
            self._json(
                401,
                {
                    "error": "AUTHENTICATION_REQUIRED",
                    "message": "Valid CADYX authentication credentials are required.",
                },
                extra_headers=(
                    ("WWW-Authenticate", 'Basic realm="CADYX", charset="UTF-8"'),
                    ("WWW-Authenticate", 'Bearer realm="CADYX"'),
                    ("Connection", "close"),
                ),
            )
            return False

        def _read_json(self) -> dict[str, Any] | None:
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                return None
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise ValidationError("Content-Length is invalid.") from exc
            if length < 0 or length > _MAX_BODY_BYTES:
                raise ValidationError("Request body exceeds the 1 MB limit.")
            raw = self.rfile.read(length)
            if not raw:
                return None
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValidationError("Request JSON must be an object.")
            return value

        def _json(
            self,
            status: int,
            value: dict[str, Any],
            *,
            extra_headers: tuple[tuple[str, str], ...] = (),
        ) -> None:
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            for name, header_value in extra_headers:
                self.send_header(name, header_value)
            self.end_headers()
            self.wfile.write(encoded)

        def _read_viewport_png(self) -> bytes:
            content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if content_type != "image/png":
                raise ValidationError("Viewport snapshots must use Content-Type image/png.")
            raw_length = self.headers.get("Content-Length")
            try:
                length = int(raw_length) if raw_length is not None else 0
            except ValueError as exc:
                raise ValidationError("Content-Length is invalid.") from exc
            if length < 24 or length > _MAX_VIEWPORT_SNAPSHOT_BYTES:
                self.close_connection = True
                raise ValidationError("Viewport snapshot must be between 24 bytes and 16 MiB.")
            encoded = self.rfile.read(length)
            if len(encoded) != length:
                self.close_connection = True
                raise ValidationError("Viewport snapshot body was incomplete.")
            return encoded

        def _read_step_upload(self) -> bytes:
            content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if content_type != "application/octet-stream":
                raise ValidationError("STEP uploads must use Content-Type application/octet-stream.")
            raw_length = self.headers.get("Content-Length")
            try:
                length = int(raw_length) if raw_length is not None else 0
            except ValueError as exc:
                raise ValidationError("Content-Length is invalid.") from exc
            if length < 1 or length > _MAX_STEP_UPLOAD_BYTES:
                self.close_connection = True
                raise ValidationError("STEP upload must be between 1 byte and 64 MiB.")
            content = self.rfile.read(length)
            if len(content) != length:
                self.close_connection = True
                raise ValidationError("STEP upload body was incomplete.")
            return content

        def _png(
            self,
            status: int,
            encoded: bytes,
            *,
            extra_headers: tuple[tuple[str, str], ...] = (),
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Disposition", "inline")
            for name, header_value in extra_headers:
                self.send_header(name, header_value)
            self.end_headers()
            self.wfile.write(encoded)

        def _static(self, filename: str, content_type: str) -> None:
            data = (_WEB_ROOT / filename).read_bytes()
            if filename in {"index.html", "scanner.html"}:
                version = _ui_asset_version()
                assets = (
                    ("style.css", "viewport.js", "ply-parser.js", "ply-preview.js", "app.js")
                    if filename == "index.html" else ("style.css", "scanner-camera.js")
                )
                for asset in assets:
                    reference = f'"/assets/{asset}"'.encode("ascii")
                    versioned = f'"/assets/{asset}?v={version}"'.encode("ascii")
                    data = data.replace(reference, versioned)
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)

        def _binary_file(self, path: Path) -> None:
            try:
                stream = path.open("rb")
            except OSError as exc:
                raise NotFoundError("Project file was not found.") from exc
            with stream:
                size = os.fstat(stream.fileno()).st_size
                content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Disposition", "inline")
                self.end_headers()
                try:
                    while chunk := stream.read(64 * 1024):
                        self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def _stream_events(self, project_id: str | None) -> None:
            if not isinstance(self.server, CadyxHTTPServer):
                raise RuntimeError("CADYX request handler requires CadyxHTTPServer.")
            server = self.server
            if not server.try_acquire_sse_slot():
                self.close_connection = True
                self._json(
                    503,
                    {
                        "error": "SERVICE_UNAVAILABLE",
                        "message": "SSE concurrency limit reached.",
                    },
                    extra_headers=(("Retry-After", "1"), ("Connection", "close")),
                )
                return
            subscriber_id: int | None = None
            try:
                if project_id:
                    core.inspect_project(project_id)
                subscriber_id, subscriber = core.events.subscribe(project_id)
                # SSE owns its separate permit after authentication and enrollment.
                # The connection is never reused as an ordinary HTTP request.
                self.close_connection = True
                server.release_request_slot_for_sse()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                self.wfile.write(b": CADYX event stream\n\n")
                self.wfile.flush()
                next_keepalive = time.monotonic() + _SSE_KEEPALIVE_SECONDS
                while not server.shutdown_event.is_set():
                    now = time.monotonic()
                    timeout = min(
                        _SSE_SHUTDOWN_POLL_SECONDS,
                        max(0.0, next_keepalive - now),
                    )
                    try:
                        item = subscriber.get(timeout=timeout)
                    except queue.Empty:
                        if server.shutdown_event.is_set():
                            break
                        if time.monotonic() < next_keepalive:
                            continue
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        next_keepalive = time.monotonic() + _SSE_KEEPALIVE_SECONDS
                        continue
                    payload = json.dumps(item.as_dict(), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                    frame = f"id: {item.id}\nevent: {item.event}\ndata: {payload}\n\n"
                    self.wfile.write(frame.encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                if subscriber_id is not None:
                    core.events.unsubscribe(subscriber_id)
                server.release_sse_slot()

        def log_message(self, format: str, *args: Any) -> None:
            super().log_message(format, *args)

    return CadyxRequestHandler
