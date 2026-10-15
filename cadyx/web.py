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
from .capabilities import agent_api_capabilities
from .core import CadyxCore
from .errors import CadyxError, NotFoundError, ValidationError
from .models import OwnerType

_MAX_BODY_BYTES = 1_000_000
_MAX_STEP_UPLOAD_BYTES = 64 * 1024 * 1024
_MAX_VIEWPORT_SNAPSHOT_BYTES = 16 * 1024 * 1024
_WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
_UI_ASSETS = ("index.html", "app.js", "style.css", "viewport.js", "ply-preview.js", "favicon.svg")
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
                    agent_work = core.events.agent_work_snapshot()
                    snapshot: dict[str, Any] = {
                        "ui_version": _ui_asset_version(),
                        "workspace_revision": core.workspace.workspace_revision_token(),
                        "project_revision": None,
                        "project_lock": None,
                        "activity_revision": None,
                        "dashboard_revision": None,
                        "agent_work_active": agent_work["active"],
                        "agent_work_active_project_ids": agent_work["active_project_ids"],
                        "agent_work_revision": agent_work["revision"],
                        "folder_revision": None,
                    }
                    if project_id:
                        snapshot["project_revision"] = core.workspace.project_revision_token(project_id)
                        snapshot["project_lock"] = core.leases.snapshot_for_listing(project_id)
                        snapshot["activity_revision"] = core.events.activity_revision(project_id)
                        snapshot["dashboard_revision"] = core.events.agent_dashboard_revision(project_id)
                    if folder_name:
                        snapshot["folder_revision"] = core.workspace.folder_revision_token(folder_name)
                    self._json(200, snapshot)
                    return
                if parsed.path == "/api/v1/agent/workspace/selection":
                    self._json(200, {"selection": core.workspace_selection_snapshot()})
                    return
                if parsed.path == "/api/v1/agent/capabilities":
                    self._json(200, agent_api_capabilities())
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
                project_file_prefix = agent_prefix if parts[:4] == agent_prefix else prefix
                if (len(parts) in {len(project_file_prefix) + 2, len(project_file_prefix) + 3}
                        and parts[:len(project_file_prefix)] == project_file_prefix
                        and parts[len(project_file_prefix) + 1] == "files"):
                    project_id = parts[len(project_file_prefix)]
                    if len(parts) == len(project_file_prefix) + 2:
                        self._json(200, {"files": core.workspace.list_project_files(project_id)})
                    else:
                        file_name = unquote(parts[len(project_file_prefix) + 2])
                        self._binary_file(core.workspace.resolve_project_file(project_id, file_name))
                    return
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
                    encoded, source, revision = core.render_project_png_with_source(parts[3])
                    self._png(200, encoded, extra_headers=(
                        ("X-CADYX-Viewport-Source", source),
                        ("X-CADYX-Document-Revision", str(revision)),
                    ))
                    return
                if len(parts) == 6 and parts[:4] == agent_prefix and parts[5] == "viewport.png":
                    encoded, source, revision = core.observe_agent_operation(
                        parts[4], "Получает изображение модели",
                        lambda: core.render_project_png_with_source(parts[4]),
                    )
                    self._png(200, encoded, extra_headers=(
                        ("X-CADYX-Viewport-Source", source),
                        ("X-CADYX-Document-Revision", str(revision)),
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
                parts = parsed.path.strip("/").split("/")
                if ((len(parts) == 7 and parts[:4] == ["api", "v1", "agent", "projects"]
                     and parts[5:] == ["viewport", "snapshot"])
                    or (len(parts) == 6 and parts[:3] == ["api", "v1", "projects"]
                        and parts[4:] == ["viewport", "snapshot"])):
                    project_id = parts[4] if parts[2] == "agent" else parts[3]
                    revision_text = self.headers.get("X-CADYX-Document-Revision")
                    try:
                        revision = int(revision_text) if revision_text is not None else -1
                    except ValueError as exc:
                        raise ValidationError("X-CADYX-Document-Revision must be an integer.") from exc
                    snapshot = core.publish_viewport_snapshot(
                        project_id, revision, self._read_viewport_png(),
                    )
                    self._json(200, snapshot)
                    return
                if (len(parts) == 7 and parts[:4] == ["api", "v1", "agent", "projects"]
                        and parts[5] == "files"):
                    project_id = parts[4]
                    filename = unquote(parts[6])
                    result = core.upload_project_step(project_id, filename, self._read_step_upload())
                    self._json(201, {"project_id": project_id, "file": result})
                    return
                body = self._read_json()
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
                if agent_api:
                    if not body.keys() <= {"name", "select"} or "name" not in body:
                        raise ValidationError("Agent project creation requires name and accepts optional select.")
                    select = body.get("select", True)
                    if not isinstance(select, bool):
                        raise ValidationError("select must be a boolean.")
                else:
                    if set(body) != {"name"}:
                        raise ValidationError("Project creation requires only a name field.")
                    select = True
                result = core.create_project(body["name"], select=select)
                self._json(201, result)
                return
            project_id = parts[0]
            if agent_api and len(parts) == 2 and parts[1] == "rename":
                if body is None or set(body) != {"name"}:
                    raise ValidationError("Project rename requires only a name field.")
                self._json(200, core.rename_project(project_id, body["name"]))
                return
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
            if filename == "index.html":
                version = _ui_asset_version()
                for asset in ("style.css", "viewport.js", "ply-preview.js", "app.js"):
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
