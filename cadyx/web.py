"""Small stdlib HTTP API and server-sent-event transport."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .auth import authorization_matches
from .core import CadyxCore
from .errors import CadyxError, NotFoundError, ValidationError
from .models import OwnerType

_MAX_BODY_BYTES = 1_000_000
_WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
_UI_ASSETS = ("index.html", "app.js", "style.css", "viewport.js", "ply-preview.js", "favicon.svg")
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
        for filename in _UI_ASSETS:
            digest.update(filename.encode("utf-8"))
            digest.update(b"\0")
            digest.update((_WEB_ROOT / filename).read_bytes())
            digest.update(b"\0")
        version = digest.hexdigest()
        _UI_ASSET_VERSION_CACHE = (signature, version)
        return version


class CadyxHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_request_handler(
    core: CadyxCore,
    auth_token: str | None = None,
) -> type[BaseHTTPRequestHandler]:
    class CadyxRequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler method name
            parsed = urlsplit(self.path)
            try:
                if parsed.path == "/api/v1/health":
                    self._json(200, {"status": "ok", "api_version": 1})
                    return
                if not self._require_auth():
                    return
                if parsed.path == "/api/v1/events":
                    self._stream_events(parse_qs(parsed.query).get("project_id", [None])[0])
                    return
                if parsed.path == "/api/v1/updates":
                    query = parse_qs(parsed.query)
                    project_id = query.get("project_id", [None])[0]
                    folder_name = query.get("folder", [None])[0]
                    snapshot: dict[str, Any] = {
                        "ui_version": _ui_asset_version(),
                        "workspace_revision": core.workspace.workspace_revision_token(),
                        "project_revision": None,
                        "activity_revision": None,
                        "dashboard_revision": None,
                        "folder_revision": None,
                    }
                    if project_id:
                        snapshot["project_revision"] = core.workspace.project_revision_token(project_id)
                        snapshot["activity_revision"] = core.events.activity_revision(project_id)
                        snapshot["dashboard_revision"] = core.events.agent_dashboard_revision(project_id)
                    if folder_name:
                        snapshot["folder_revision"] = core.workspace.folder_revision_token(folder_name)
                    self._json(200, snapshot)
                    return
                if parsed.path == "/api/v1/agent/workspace/selection":
                    self._json(200, {"selection": core.workspace_selection_snapshot()})
                    return
                if parsed.path == "/api/v1/projects":
                    self._json(200, core.list_workspace())
                    return
                if parsed.path == "/api/v1/agent/projects":
                    self._json(200, core.list_workspace())
                    return
                parts = parsed.path.strip("/").split("/")
                folder_files_prefix = ["api", "v1", "workspace", "folders"]
                if (
                    len(parts) in {6, 7}
                    and parts[:4] == folder_files_prefix
                    and parts[5] == "files"
                ):
                    folder_name = unquote(parts[4])
                    if len(parts) == 6:
                        self._json(200, {"files": core.workspace.list_folder_files(folder_name)})
                    else:
                        file_name = unquote(parts[6])
                        self._binary_file(core.workspace.resolve_folder_file(folder_name, file_name))
                    return
                prefix = ["api", "v1", "projects"]
                agent_prefix = ["api", "v1", "agent", "projects"]
                comparison_prefix = agent_prefix if parts[:4] == agent_prefix else prefix
                if len(parts) == 5 and parts[:3] == prefix and parts[4] == "dashboard":
                    self._json(200, core.agent_dashboard_snapshot(parts[3]))
                    return
                if len(parts) == 6 and parts[:4] == agent_prefix and parts[5] == "dashboard":
                    self._json(200, core.agent_dashboard_snapshot(parts[4]))
                    return
                if len(parts) == len(comparison_prefix) + 2 and parts[:len(comparison_prefix)] == comparison_prefix and parts[-1] == "comparison":
                    query = parse_qs(parsed.query)
                    self._json(200, core.compare_bodies(parts[-2],
                        query.get("original_body_id", [None])[0],
                        query.get("modified_body_id", [None])[0]))
                    return
                if len(parts) == 5 and parts[:3] == prefix and parts[4] == "viewport.png":
                    self._png(200, core.render_project_png(parts[3]))
                    return
                if len(parts) == 6 and parts[:4] == agent_prefix and parts[5] == "viewport.png":
                    self._png(200, core.observe_agent_operation(
                        parts[4], "Получает изображение модели", lambda: core.render_project_png(parts[4]),
                    ))
                    return
                if len(parts) == 5 and parts[:3] == prefix and parts[4] == "viewport":
                    self._json(200, core.render_project(parts[3]))
                    return
                if len(parts) == 6 and parts[:4] == agent_prefix and parts[5] == "viewport":
                    self._json(200, core.observe_agent_operation(
                        parts[4], "Получает 3D-сцену", lambda: core.render_project(parts[4]),
                    ))
                    return
                if len(parts) == 6 and parts[:4] == agent_prefix and parts[5] == "topology":
                    project_id = parts[4]
                    body_id = parse_qs(parsed.query).get("body_id", [None])[0]
                    self._json(200, core.observe_agent_operation(
                        project_id, "Изучает рёбра модели",
                        lambda: core.inspect_body_topology(project_id, body_id),
                    ))
                    return
                if len(parts) == 5 and parts[:3] == prefix and parts[4] == "activity":
                    activities, revision = core.events.activity_state_snapshot(parts[3])
                    if not activities:
                        core.workspace.load(parts[3])
                    self._json(200, {"activities": activities, "revision": revision})
                    return
                if len(parts) == 4 and parts[:3] == prefix:
                    self._json(200, core.inspect_project(parts[3]))
                    return
                if len(parts) == 6 and parts[:4] == agent_prefix and parts[5] == "activity":
                    activities, revision = core.events.activity_state_snapshot(parts[4])
                    if not activities:
                        core.workspace.load(parts[4])
                    self._json(200, {"activities": activities, "revision": revision})
                    return
                if len(parts) == 5 and parts[:4] == agent_prefix:
                    self._json(200, core.observe_agent_operation(
                        parts[4], "Изучает модель", lambda: core.inspect_project(parts[4]),
                    ))
                    return
                if parsed.path == "/":
                    self._static("index.html", "text/html; charset=utf-8")
                    return
                if parsed.path == "/favicon.svg":
                    self._static("favicon.svg", "image/svg+xml")
                    return
                if parsed.path == "/assets/app.js":
                    self._static("app.js", "text/javascript; charset=utf-8")
                    return
                if parsed.path == "/assets/viewport.js":
                    self._static("viewport.js", "text/javascript; charset=utf-8")
                    return
                if parsed.path == "/assets/ply-preview.js":
                    self._static("ply-preview.js", "text/javascript; charset=utf-8")
                    return
                if parsed.path == "/assets/style.css":
                    self._static("style.css", "text/css; charset=utf-8")
                    return
                self._json(404, {"error": "NOT_FOUND", "message": "Route was not found."})
            except CadyxError as exc:
                self._json(exc.http_status, exc.as_dict())

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler method name
            parsed = urlsplit(self.path)
            try:
                if not self._require_auth():
                    return
                body = self._read_json()
                parts = parsed.path.strip("/").split("/")
                if parts == ["api", "v1", "agent", "workspace", "selection"]:
                    if body is None:
                        raise ValidationError("A JSON request body is required.")
                    selection = core.select_workspace_resource(body)
                    self._json(200, {"selection": selection})
                    return
                if parts[:3] == ["api", "v1", "projects"]:
                    self._route_project(parts[3:], body, agent_api=False)
                    return
                if parts[:4] == ["api", "v1", "agent", "projects"]:
                    self._route_project(parts[4:], body, agent_api=True)
                    return
                self._json(404, {"error": "NOT_FOUND", "message": "Route was not found."})
            except CadyxError as exc:
                self._json(exc.http_status, exc.as_dict())
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._json(400, {"error": "INVALID_JSON", "message": "Request body must be valid UTF-8 JSON."})

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

        def _route_project(self, parts: list[str], body: dict[str, Any] | None, *, agent_api: bool) -> None:
            if not parts:
                if body is None:
                    raise ValidationError("A JSON request body is required.")
                if "name" not in body:
                    raise ValidationError("Project name is required.")
                result = core.create_project(body["name"])
                self._json(201, result)
                return
            project_id = parts[0]
            if agent_api and len(parts) == 2 and parts[1] == "dashboard":
                if body is None:
                    raise ValidationError("A JSON request body is required.")
                self._json(200, core.report_agent_dashboard(project_id, body))
                return
            if agent_api and len(parts) == 3 and parts[1:] == ["viewport", "camera"]:
                if body is None:
                    raise ValidationError("A JSON request body is required.")
                allowed = {"yaw_delta", "pitch_delta", "zoom_factor", "duration_ms"}
                if not body.keys() <= allowed or "yaw_delta" not in body:
                    raise ValidationError("Camera requests need yaw_delta and only supported camera fields.")
                result = core.rotate_view(
                    project_id,
                    yaw_delta=body["yaw_delta"],
                    pitch_delta=body.get("pitch_delta", 0.0),
                    zoom_factor=body.get("zoom_factor", 1.0),
                    duration_ms=body.get("duration_ms", 1200),
                )
                self._json(200, {"camera": result})
                return
            if agent_api and len(parts) == 2 and parts[1] in {"stop", "resume"}:
                if body is None:
                    raise ValidationError("A JSON request body is required.")
                if parts[1] == "stop":
                    result = core.stop_agent(project_id, body.get("lease_id"))
                else:
                    result = core.resume_agent(project_id, body.get("owner_id"), body.get("expected_revision"))
                self._json(200, result)
                return
            if len(parts) == 2 and parts[1] == "leases":
                if body is None:
                    raise ValidationError("A JSON request body is required.")
                owner_type = "agent" if agent_api else body.get("owner_type")
                owner_id = body.get("owner_id")
                self._json(201, {"lease": core.acquire_lease(project_id, owner_type, owner_id)})
                return
            if len(parts) == 2 and parts[1] == "commands":
                if body is None:
                    raise ValidationError("A JSON request body is required.")
                command = body.get("command")
                expected = OwnerType.AGENT if agent_api else OwnerType.HUMAN
                self._json(200, core.execute_command(project_id, body.get("lease_id"), command,
                           expected_owner_type=expected, expected_revision=body.get("expected_revision")))
                return
            if len(parts) == 3 and parts[1:] == ["leases", "recover"]:
                self._json(200, core.recover_expired_lease(project_id))
                return
            if len(parts) == 4 and parts[1] == "leases" and parts[3] in {"heartbeat", "release"}:
                lease_id = parts[2]
                if parts[3] == "heartbeat":
                    self._json(200, {"lease": core.heartbeat(project_id, lease_id)})
                else:
                    self._json(200, core.release_lease(project_id, lease_id))
                return
            raise NotFoundError("Route was not found.")

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
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            for name, header_value in extra_headers:
                self.send_header(name, header_value)
            self.end_headers()
            self.wfile.write(encoded)

        def _png(self, status: int, encoded: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Disposition", "inline")
            self.end_headers()
            self.wfile.write(encoded)

        def _static(self, filename: str, content_type: str) -> None:
            data = (_WEB_ROOT / filename).read_bytes()
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
            if project_id:
                # Confirm the filter names a real project before opening a stream.
                core.inspect_project(project_id)
            subscriber_id, subscriber = core.events.subscribe(project_id)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.close_connection = True
            try:
                self.wfile.write(b": CADYX event stream\n\n")
                self.wfile.flush()
                while True:
                    try:
                        item = subscriber.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        continue
                    payload = json.dumps(item.as_dict(), ensure_ascii=False, allow_nan=False)
                    frame = f"id: {item.id}\nevent: {item.event}\ndata: {payload}\n\n"
                    self.wfile.write(frame.encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                core.events.unsubscribe(subscriber_id)

        def log_message(self, format: str, *args: Any) -> None:
            # Keep request logging concise while retaining normal server diagnostics.
            super().log_message(format, *args)

    return CadyxRequestHandler
