"""Local stdio MCP adapter for CADYX's existing HTTP Agent API."""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import math
import os
import ssl
import struct
import sys
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import uuid4

from .api_contracts import VALIDATION_OPERATIONS
from .errors import public_data, public_text

_PROTOCOL_VERSION = "2025-11-25"
_MAX_FILE_BYTES = 64 * 1024 * 1024
_MAX_RESPONSE_BYTES = _MAX_FILE_BYTES
_JSON_RESPONSE_BYTES = 16 * 1024 * 1024
_FULL_JSON_RESPONSE_BYTES = 64 * 1024 * 1024
_COMPACT_RESPONSE_BYTES = 8 * 1024 * 1024
_PNG_RESPONSE_BYTES = 24 * 1024 * 1024
_THUMBNAIL_RESPONSE_BYTES = 8 * 1024 * 1024
_ERROR_RESPONSE_BYTES = 64 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_API_TIMEOUT_SECONDS = 600
_JSON = "application/json"
_TABLE_MAP_HEADER = struct.Struct("<4I")
_TABLE_MAP_RECORD = struct.Struct("<2i2f6d")
# Only these command HTTP responses prove that the submitted mutation was rejected.
_DEFINITIVE_COMMAND_REJECTION_STATUSES = frozenset({400, 401, 403, 404, 405, 409, 413, 415, 422})


class APIError(RuntimeError):
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self.payload = payload
        if isinstance(payload, dict):
            message = payload.get("message", json.dumps(payload, ensure_ascii=False))
            code = payload.get("error")
            super().__init__(f"CADYX API {status}" + (f" {code}" if code else "") + f": {message}")
        else:
            super().__init__(f"CADYX API {status}: {payload}")


class TransportError(RuntimeError):
    """A transport failure whose request outcome may be uncertain."""


class OutcomeUncertainError(RuntimeError):
    """A mutation response was ambiguous and requires inspection before retry."""


class ResponseTooLargeError(RuntimeError):
    """A response exceeded its route-specific MCP byte budget."""

    def __init__(self, kind: str, limit: int, alternative: str) -> None:
        self.kind = kind
        self.limit = limit
        self.alternative = alternative
        super().__init__(
            f"CADYX {kind} response exceeds the {limit} byte MCP read limit. {alternative}"
        )


def _response_budget(method: str, path: str) -> tuple[int, str, str]:
    """Limit the wire body before JSON decoding, UTF-8 conversion or base64 encoding."""
    route = urllib.parse.urlsplit(path).path
    if method == "GET" and "/files/" in route:
        return (
            _MAX_FILE_BYTES, "file",
            "Use the corresponding staged STEP/PLY import operation for large geometry; "
            "read smaller source files separately.",
        )
    if route.endswith("/viewport.png"):
        if urllib.parse.parse_qs(urllib.parse.urlsplit(path).query).get("thumbnail") == ["1"]:
            return _THUMBNAIL_RESPONSE_BYTES, "thumbnail PNG", "Use compact model inspection instead."
        return _PNG_RESPONSE_BYTES, "viewport PNG", "Use a project thumbnail or compact model inspection instead."
    if route.endswith("/inspection"):
        return (
            _COMPACT_RESPONSE_BYTES, "compact inspection JSON",
            "Request a smaller section/page with cadyx_inspect_compact (limit/offset).",
        )
    if (
        route.endswith(("/viewport", "/topology", "/commands", "/commands/execute",
                       "/commands/preview", "/commands/commit"))
        or (route.startswith("/api/v1/agent/projects/") and route.count("/") == 5)
    ):
        return (
            _FULL_JSON_RESPONSE_BYTES, "model JSON",
            "Use cadyx_inspect_compact with section, limit and offset to inspect a smaller result.",
        )
    return (
        _JSON_RESPONSE_BYTES, "JSON",
        "Use a filtered or paginated Agent API/MCP inspection where available; "
        "avoid requesting the entire model or activity history.",
    )


def _read_bounded(response: Any, limit: int, kind: str, alternative: str) -> bytes:
    """Read at most limit + 1 bytes, regardless of Content-Length accuracy."""
    declared = response.headers.get("Content-Length")
    if declared is not None:
        try:
            length = int(declared)
        except (TypeError, ValueError):
            pass
        else:
            if length > limit:
                raise ResponseTooLargeError(kind, limit, alternative)
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = response.read(min(_READ_CHUNK_BYTES, limit - size + 1))
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise ResponseTooLargeError(kind, limit, alternative)
        chunks.append(chunk)
    return b"".join(chunks)


class CadyxAPI:
    def __init__(self) -> None:
        self.base_url = os.environ.get("CADYX_API_URL", "http://127.0.0.1:8888").rstrip("/")
        token_file = os.environ.get("CADYX_API_TOKEN_FILE")
        token_env = os.environ.get("CADYX_API_TOKEN")
        if token_file and token_env:
            raise ValueError("Set only one CADYX API token source: CADYX_API_TOKEN or CADYX_API_TOKEN_FILE.")
        self.token = token_env
        if token_file:
            path = Path(token_file).expanduser()
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
                raise ValueError("CADYX_API_TOKEN_FILE must be a regular non-symlink file no larger than 4 KiB.")
            self.token = path.read_text(encoding="utf-8").strip()
            if not self.token:
                raise ValueError("CADYX_API_TOKEN_FILE is empty.")
        self._connections = threading.local()

    def _connection(self, parsed: urllib.parse.SplitResult) -> http.client.HTTPConnection:
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("CADYX_API_URL must be an HTTP or HTTPS URL with a host.")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        key = (parsed.scheme, parsed.hostname, port)
        connections = getattr(self._connections, "by_origin", None)
        if connections is None:
            connections = self._connections.by_origin = {}
        connection = connections.get(key)
        if connection is None or connection.sock is None:
            connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
            options = {"timeout": _API_TIMEOUT_SECONDS}
            if parsed.scheme == "https":
                options["context"] = ssl.create_default_context()
            connection = connection_type(parsed.hostname, port, **options)
            connections[key] = connection
        return connection

    def _drop_connection(self, parsed: urllib.parse.SplitResult) -> None:
        connections = getattr(self._connections, "by_origin", {})
        key = (parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        connection = connections.pop(key, None)
        if connection is not None:
            connection.close()

    def close(self) -> None:
        """Close the calling thread's idle keep-alive connections."""
        connections = getattr(self._connections, "by_origin", {})
        self._connections.by_origin = {}
        for connection in connections.values():
            connection.close()

    def request(self, method: str, path: str, body: dict[str, Any] | bytes | None = None) -> tuple[bytes, Any]:
        headers = {"Accept": "application/json, image/png, */*"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if isinstance(body, bytes):
            data = body
            headers["Content-Type"] = "application/octet-stream"
        elif body is not None:
            data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            headers["Content-Type"] = _JSON
        parsed = urllib.parse.urlsplit(self.base_url)
        target = urllib.parse.urlunsplit(("", "", parsed.path.rstrip("/") + path, "", ""))
        if not target.startswith("/"):
            target = "/" + target
        try:
            connection = self._connection(parsed)
            connection.request(method, target, body=data, headers=headers)
            response = connection.getresponse()
            limit, kind, alternative = _response_budget(method, path)
            limit = min(limit, _MAX_RESPONSE_BYTES)
            try:
                raw = _read_bounded(response, limit if response.status < 400 else _ERROR_RESPONSE_BYTES,
                                   kind if response.status < 400 else "HTTP error",
                                   alternative if response.status < 400 else
                                   "Inspect the HTTP status and request arguments; the error body was not retained.")
            except ResponseTooLargeError as oversized:
                self._drop_connection(parsed)
                if response.status >= 400:
                    raise APIError(response.status, {
                        "error": "HTTP_ERROR_BODY_TOO_LARGE", "message": str(oversized),
                    }) from oversized
                raise
            content_type = response.headers.get_content_type()
            metadata = {
                "content_type": content_type,
                "document_revision": response.headers.get("X-CADYX-Document-Revision"),
                "viewport_source": response.headers.get("X-CADYX-Viewport-Source"),
                "table_map_count": response.headers.get("X-CADYX-Table-Map-Count"),
            }
            if response.status >= 400:
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    payload = raw.decode("utf-8", errors="replace")
                raise APIError(response.status, payload)
        except APIError:
            raise
        except ResponseTooLargeError:
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            self._drop_connection(parsed)
            raise TransportError("CADYX API transport failed; the operation outcome may be uncertain.") from exc
        # Project and workspace files are user payloads. Return JSON-named files
        # as raw text to their caller instead of parsing them as Agent API replies.
        is_file_read = method == "GET" and "/files/" in urllib.parse.urlsplit(path).path
        if content_type == _JSON and not is_file_read:
            try:
                return raw, json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TransportError("CADYX API returned an invalid JSON response; the operation outcome may be uncertain.") from exc
        return raw, metadata

    def get(self, path: str) -> Any:
        return self.request("GET", path)[1]

    def post(self, path: str, body: dict[str, Any]) -> Any:
        return self.request("POST", path, body)[1]


def _decode_turntable_marker_map(data: bytes, declared_count: str | None) -> dict[str, Any]:
    """Decode the observed MKF record layout returned by the scanner API."""
    if len(data) < _TABLE_MAP_HEADER.size:
        raise TransportError("CADYX returned a truncated turntable marker map.")
    version, field_1, field_2, count = _TABLE_MAP_HEADER.unpack_from(data)
    expected_size = _TABLE_MAP_HEADER.size + count * _TABLE_MAP_RECORD.size
    if count < 1 or len(data) != expected_size:
        raise TransportError("CADYX returned a turntable marker map with an invalid record count.")
    if declared_count is not None and declared_count.isdecimal() and int(declared_count) != count:
        raise TransportError("CADYX turntable marker map count header does not match its payload.")
    markers = []
    for index in range(count):
        fields = _TABLE_MAP_RECORD.unpack_from(data, _TABLE_MAP_HEADER.size + index * _TABLE_MAP_RECORD.size)
        x, y, z, nx, ny, nz = fields[4:]
        if not all(math.isfinite(value) for value in (x, y, z, nx, ny, nz)):
            raise TransportError("CADYX returned non-finite turntable marker coordinates.")
        markers.append({"id": index + 1, "x": x, "y": y, "z": z,
                        "normal": [nx, ny, nz]})
    return {
        "version": version,
        "header_fields": [field_1, field_2],
        "count": count,
        "sha256": hashlib.sha256(data).hexdigest(),
        "coordinate_units": "unverified",
        "markers": markers,
    }


def _schema(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties or {}, "required": required or [], "additionalProperties": False}


_STRING = {"type": "string", "minLength": 1}
_NUMBER = {"type": "number"}
_INTEGER = {"type": "integer", "minimum": 0}
_OBJECT = {"type": "object"}
_COMMAND = {
    "type": "object",
    "properties": {"type": {"type": "string", "minLength": 1}},
    "required": ["type"],
    "additionalProperties": True,
}
TOOLS: list[dict[str, Any]] = [
    {"name": "cadyx_health", "description": "Check whether the local CADYX host is ready.", "inputSchema": _schema()},
    {"name": "cadyx_list_workspace", "description": "List saved CADYX projects and direct workspace folders with lock snapshots.", "inputSchema": _schema()},
    {"name": "cadyx_list_standard_parts", "description": "List immutable-versioned CADYX standard-part manifests with license provenance, parameter names, limitations and manifest SHA-256.", "inputSchema": _schema()},
    {"name": "cadyx_resolve_standard_part", "description": "Validate catalog version, pinned SHA-256, exact dimensions or locally staged STEP identity, and return a create_catalog_part command for the existing leased Command System.", "inputSchema": _schema({"catalog_id": _STRING, "catalog_version": _STRING, "catalog_sha256": _STRING, "parameters": _OBJECT, "name": _STRING}, ["catalog_id", "catalog_version", "catalog_sha256", "parameters"])},
    {"name": "cadyx_get_selection", "description": "Read the process-wide viewer selection. Concurrent chat sessions may change it; always use explicit project_id arguments to identify work targets.", "inputSchema": _schema()},
    {"name": "cadyx_select_resource", "description": "Set the process-wide viewer selection. Concurrent chat sessions may change it; always use explicit project_id arguments to identify work targets.", "inputSchema": _schema({"type": {"enum": ["project", "folder"]}, "project_id": _STRING, "folder_name": _STRING}, ["type"])},
    {"name": "cadyx_create_project", "description": "Create a single-model or assembly project with a user-facing name and optional English URL slug; set select=false to leave the current viewer selection unchanged.", "inputSchema": _schema({"name": {"type": "string", "minLength": 1, "maxLength": 120}, "url_slug": {"type": "string", "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$", "maxLength": 120}, "project_type": {"enum": ["single_model", "assembly"]}, "select": {"type": "boolean"}}, ["name"])},
    {"name": "cadyx_rename_project", "description": "Rename and move a FREE project folder through the Agent API while preserving its identity and model history; optionally set its English URL slug.", "inputSchema": _schema({"project_id": _STRING, "name": {"type": "string", "minLength": 1, "maxLength": 120}, "url_slug": {"type": "string", "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$", "maxLength": 120}}, ["project_id", "name"])},
    {"name": "cadyx_set_project_type", "description": "Change a FREE project between single_model and assembly through the Agent API; conversion to single_model requires an empty assembly graph.", "inputSchema": _schema({"project_id": _STRING, "project_type": {"enum": ["single_model", "assembly"]}}, ["project_id", "project_type"])},
    {"name": "cadyx_copy_project_files", "description": "Copy user files recursively from one FREE project into another through the Agent API, without copying project metadata or overwriting files.", "inputSchema": _schema({"target_project_id": _STRING, "source_project_id": _STRING}, ["target_project_id", "source_project_id"])},
    {"name": "cadyx_prune_project_files", "description": "Remove user files recursively from an empty FREE project, retaining only the named direct source files.", "inputSchema": _schema({"project_id": _STRING, "keep_files": {"type": "array", "items": _STRING, "minItems": 1}}, ["project_id", "keep_files"])},
    {"name": "cadyx_delete_project", "description": "Delete a FREE project through the Agent API after its needed data has been preserved elsewhere.", "inputSchema": _schema({"project_id": _STRING}, ["project_id"])},
    {"name": "cadyx_inspect_project", "description": "Read a compact saved-state summary and lock state without loading B-rep payloads. Use cadyx_inspect_compact for bounded entities, parameters, topology or changes; use cadyx_inspect_project_full only when full geometry or history is required.", "inputSchema": _schema({"project_id": _STRING}, ["project_id"])},
    {"name": "cadyx_inspect_project_full", "description": "Read the full saved model, B-rep geometry and command history. This response can be large; prefer cadyx_inspect_project for state and cadyx_inspect_compact for targeted inspection.", "inputSchema": _schema({"project_id": _STRING}, ["project_id"])},
    {"name": "cadyx_inspect_compact", "description": "Read a revision-bound compact project summary, paginated entities/parameters/topology or history-referenced feature changes without B-rep or mesh payloads. Use expected_revision to stabilize pagination. Historical change lists are explicitly incomplete for indirect rebuilds.", "inputSchema": _schema({"project_id": _STRING, "section": {"enum": ["summary", "entities", "parameters", "topology", "changes"]}, "expected_revision": _INTEGER, "kind": _STRING, "body_id": _STRING, "shape": _STRING, "since_revision": _INTEGER, "offset": _INTEGER, "limit": {"type": "integer", "minimum": 1, "maximum": 100}, "include_metrics": {"type": "boolean"}}, ["project_id"])},
    {"name": "cadyx_list_revision_checkpoints",
     "description": "List persisted model revisions available for exact comparison; historical revisions without checkpoints are unavailable.",
     "inputSchema": _schema({"project_id": _STRING, "offset": _INTEGER,
                             "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, ["project_id"])},
    {"name": "cadyx_compare_revisions",
     "description": "Read-only geometry-aware diff between saved checkpoints and/or process-local preview proposals at one expected current revision. Reports parameter, feature, assembly and per-body OCCT volume changes; unsupported topology correspondence and mesh comparisons are explicitly untested.",
     "inputSchema": _schema({"project_id": _STRING, "expected_revision": _INTEGER,
                             "before": {"type": "object", "properties": {"revision": _INTEGER, "proposal_id": _STRING},
                                        "minProperties": 1, "maxProperties": 1, "additionalProperties": False},
                             "after": {"type": "object", "properties": {"revision": _INTEGER, "proposal_id": _STRING},
                                       "minProperties": 1, "maxProperties": 1, "additionalProperties": False},
                             "absolute_tolerance": {"type": "number", "minimum": 0},
                             "relative_tolerance": {"type": "number", "minimum": 0}},
                            ["project_id", "expected_revision", "before", "after"])},
    {"name": "cadyx_capabilities", "description": "Discover supported Agent API routes, model commands, parameter names, documented constraints and error meanings.", "inputSchema": _schema()},
    {"name": "cadyx_propose_capability", "description": "Develop a persistent CADYX capability from a bounded composition of existing commands. CADYX tests it on isolated real geometry before it becomes available; arbitrary code is rejected.", "inputSchema": _schema({"project_id": _STRING, "definition": _OBJECT}, ["project_id", "definition"])},
    {"name": "cadyx_execute_capability", "description": "Run a geometry-tested persistent capability. Each contained Command System step receives its own revision-checked agent lease and visible operation event.", "inputSchema": _schema({"project_id": _STRING, "capability_id": _STRING, "expected_revision": _INTEGER, "arguments": _OBJECT}, ["project_id", "capability_id", "expected_revision", "arguments"])},
    {"name": "cadyx_rollback_capability", "description": "Restore the previous tested registry checkpoint while the project is idle.", "inputSchema": _schema({"project_id": _STRING, "capability_id": _STRING}, ["project_id", "capability_id"])},
    {"name": "cadyx_read_source_file", "description": "Read an approved CADYX source or test file and its SHA-256 for a source extension proposal.", "inputSchema": _schema({"path": _STRING}, ["path"])},
    {"name": "cadyx_list_source_extensions", "description": "List persistent source extensions and test status in the local CADYX installation.", "inputSchema": _schema({"project_id": _STRING}, ["project_id"])},
    {"name": "cadyx_develop_source_extension", "description": "Run focused source tests in a no-network Windows AppContainer, then checkpoint and apply the patch only on success.", "inputSchema": _schema({"project_id": _STRING, "proposal": _OBJECT}, ["project_id", "proposal"])},
    {"name": "cadyx_rollback_source_extension", "description": "Restore a checked source-extension checkpoint without overwriting files changed since integration.", "inputSchema": _schema({"project_id": _STRING, "extension_id": _STRING}, ["project_id", "extension_id"])},
    {"name": "cadyx_list_project_files", "description": "List user files in a saved project or an optional project subfolder.", "inputSchema": _schema({"project_id": _STRING, "folder_name": _STRING}, ["project_id"])},
    {"name": "cadyx_read_project_file", "description": "Read a project file through the read-only Agent API; optionally select a project subfolder.", "inputSchema": _schema({"project_id": _STRING, "name": _STRING, "folder_name": _STRING}, ["project_id", "name"])},
    {"name": "cadyx_remove_project_files", "description": "Remove explicitly listed, unused files from a project or project subfolder after checking the project is free.", "inputSchema": _schema({"project_id": _STRING, "folder_name": _STRING, "filenames": {"type": "array", "items": _STRING, "minItems": 1, "maxItems": 1000}}, ["project_id", "filenames"])},
    {"name": "cadyx_remove_project_folder", "description": "Remove one existing project subfolder recursively after checking it is unused and the project is free.", "inputSchema": _schema({"project_id": _STRING, "folder_name": _STRING}, ["project_id", "folder_name"])},
    {"name": "cadyx_move_project_files", "description": "Move explicitly listed direct project files into a scan-series subfolder after checking the project is free and files are unused.", "inputSchema": _schema({"project_id": _STRING, "folder_name": _STRING, "filenames": {"type": "array", "items": _STRING, "minItems": 1, "maxItems": 1000}}, ["project_id", "folder_name", "filenames"])},
    {"name": "cadyx_start_scan_360", "description": "Start direct-UVC 360-degree point capture in a free project. Set step_degrees (divisor of 360, 1–30; default 30) and align_to_markers=false to preserve camera-frame points for geometry-only registration. The physical turntable must be level.", "inputSchema": _schema({"project_id": _STRING, "step_degrees": {"type": "integer", "minimum": 1, "maximum": 30}, "align_to_markers": {"type": "boolean"}, "rotation_period_seconds": {"type": "number", "minimum": 18, "maximum": 90}, "scanner_device_id": _STRING, "turntable_device_id": _STRING}, ["project_id"])},
    {"name": "cadyx_get_scan_360_status", "description": "Read native 360-degree scan progress and the latest captured point-cloud preview for a project.", "inputSchema": _schema({"project_id": _STRING}, ["project_id"])},
    {"name": "cadyx_get_scanner_devices", "description": "List connected scanners and supported turntables with their device and profile IDs. Device discovery may take several seconds.", "inputSchema": _schema()},
    {"name": "cadyx_get_turntable_status", "description": "Read turntable angle, tilt, rotation period and motion/scan lock state. Supply the selected device_id and profile_id to avoid rediscovering Bluetooth devices.", "inputSchema": _schema({"device_id": _STRING, "profile_id": _STRING})},
    {"name": "cadyx_get_turntable_marker_map", "description": "Read the selected turntable's MKF marker map as marker coordinates and normals with a SHA-256. Map coordinate units are not established.", "inputSchema": _schema({"device_id": _STRING, "profile_id": _STRING})},
    {"name": "cadyx_control_turntable", "description": "Control the connected Revopoint table through CADYX API: query, bounded rotate (±30°), tilt (±10°), speed (18–90 s/revolution), zero, stop, or continuous spin start/stop. Use only a supported command and pass value for rotate, tilt or speed. Live preview may stay active; commands are locked during a 360 capture. Speed is saved to the device profile when resolved device/profile IDs are returned.", "inputSchema": _schema({"command": {"type": "string", "enum": ["query", "rotate", "tilt", "speed", "zero", "stop", "spin-start", "spin-stop"]}, "value": {"type": "number"}, "device_id": _STRING, "profile_id": _STRING}, ["command"])},
    {"name": "cadyx_get_scanner_live_preview_status", "description": "Read live scanner preview state and capture/render performance metrics without stopping the preview.", "inputSchema": _schema()},
    {"name": "cadyx_start_scanner_live_preview", "description": "Start the live-only depth preview (5, 10 or 15 requested FPS). The live preview is incompatible with 360 capture.", "inputSchema": _schema({"scanner_device_id": _STRING, "fps": {"type": "integer", "enum": [5, 10, 15]}})},
    {"name": "cadyx_stop_scanner_live_preview", "description": "Stop the live-only depth preview and remove its temporary frame queue.", "inputSchema": _schema()},
    {"name": "cadyx_upload_project_step", "description": "Copy a local STEP file into a project through the Agent API without overwriting existing files or changing the original.", "inputSchema": _schema({"project_id": _STRING, "source_path": _STRING}, ["project_id", "source_path"])},
    {"name": "cadyx_upload_project_scan", "description": "Copy a local PLY scan into a project through the Agent API without overwriting the source or changing the original.", "inputSchema": _schema({"project_id": _STRING, "source_path": _STRING}, ["project_id", "source_path"])},
    {"name": "cadyx_remove_staged_step", "description": "Remove an unused direct STEP reference file from a FREE project; CADYX preserves the original local source.", "inputSchema": _schema({"project_id": _STRING, "name": _STRING}, ["project_id", "name"])},
    {"name": "cadyx_list_folder_files", "description": "List direct user files in a workspace folder.", "inputSchema": _schema({"folder_name": _STRING}, ["folder_name"])},
    {"name": "cadyx_read_folder_file", "description": "Read a direct workspace file through the read-only API; small images are returned as image content.", "inputSchema": _schema({"folder_name": _STRING, "name": _STRING}, ["folder_name", "name"])},
    {"name": "cadyx_inspect_topology", "description": "Inspect current editable B-rep edges before a fillet. Refresh topology after any upstream edit.", "inputSchema": _schema({"project_id": _STRING, "body_id": _STRING}, ["project_id", "body_id"])},
    {"name": "cadyx_query_topology", "description": "Read-only exact-revision face/edge geometry filters; returns candidate count, ambiguity and sorted matches without automatic selection.", "inputSchema": _schema({"project_id": _STRING, "body_id": _STRING, "expected_revision": _INTEGER, "query": {"type": "object", "properties": {"entity": {"enum": ["face", "edge"]}}, "required": ["entity"], "additionalProperties": True}}, ["project_id", "body_id", "expected_revision", "query"])},
    *(
        {"name": operation["tool_name"], "description": operation["description"],
         "inputSchema": _schema(
             {"project_id": _STRING, **operation["input_schema"]["properties"]},
             ["project_id", *operation["input_schema"]["required"]],
         )}
        for operation in VALIDATION_OPERATIONS.values()
    ),
    {"name": "cadyx_list_validation_records", "description": "List recent durable validation records for a project without changing its document revision.", "inputSchema": _schema({"project_id": _STRING, "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, ["project_id"])},
    {"name": "cadyx_get_viewport", "description": "Read validated viewport scene data for the saved project revision.", "inputSchema": _schema({"project_id": _STRING}, ["project_id"])},
    {"name": "cadyx_get_viewport_image", "description": "Get the saved model's viewport PNG as an MCP image the model can inspect.", "inputSchema": _schema({"project_id": _STRING}, ["project_id"])},
    {"name": "cadyx_get_project_thumbnail_batch", "description": "Get server-rendered thumbnails for up to eight saved projects in one MCP call so the AI agent can visually compare them with a reference image. Returns each project ID and thumbnail revision; it does not rank or select projects.", "inputSchema": _schema({"project_ids": {"type": "array", "items": _STRING, "minItems": 1, "maxItems": 8, "uniqueItems": True}}, ["project_ids"])},
    {"name": "cadyx_move_camera", "description": "Animate the selected browser viewport camera; this does not mutate the CAD document.", "inputSchema": _schema({"project_id": _STRING, "yaw_delta": _NUMBER, "pitch_delta": _NUMBER, "zoom_factor": _NUMBER, "duration_ms": {"type": "integer", "minimum": 0, "maximum": 5000}}, ["project_id", "yaw_delta"])},
    {"name": "cadyx_get_dashboard", "description": "Read the project's persisted agent workflow and task report.", "inputSchema": _schema({"project_id": _STRING}, ["project_id"])},
    {"name": "cadyx_report_dashboard", "description": "Publish honest Russian workflow progress and queue state; this does not mutate the CAD document.", "inputSchema": _schema({"project_id": _STRING, "workflow": {"anyOf": [_OBJECT, {"type": "null"}]}, "tasks": {"type": "array", "items": _OBJECT}}, ["project_id", "workflow", "tasks"])},
    {"name": "cadyx_get_activity", "description": "Read recent real modeling operation states and results.", "inputSchema": _schema({"project_id": _STRING}, ["project_id"])},
    {"name": "cadyx_execute_command", "description": "Execute one Command System operation with a server-managed agent lease in one HTTP request. Checks expected_revision, releases the lease, and returns a compact receipt without B-rep payloads. Never retries a command whose response was lost.", "inputSchema": _schema({"project_id": _STRING, "expected_revision": _INTEGER, "command": _COMMAND}, ["project_id", "expected_revision", "command"])},
    {"name": "cadyx_preview_commands", "description": "Preview an ordered batch on an isolated document copy without saving project state or publishing files; returns a revision-bound single-use proposal.", "inputSchema": _schema({"project_id": _STRING, "expected_revision": _INTEGER, "commands": {"type": "array", "items": _COMMAND, "minItems": 1, "maxItems": 100}}, ["project_id", "expected_revision", "commands"])},
    {"name": "cadyx_commit_command_proposal", "description": "Atomically commit a preview proposal under a fresh agent lease and exact revision; never retry after an ambiguous response.", "inputSchema": _schema({"project_id": _STRING, "expected_revision": _INTEGER, "proposal_id": _STRING}, ["project_id", "expected_revision", "proposal_id"])},
    {"name": "cadyx_stop_agent_work", "description": "Set the existing safe stop fence for work owned by this MCP session; waits for any admitted atomic command and validates the saved state.", "inputSchema": _schema({"project_id": _STRING}, ["project_id"])},
    {"name": "cadyx_resume_agent_work", "description": "Resume after a confirmed FREE state and current revision; the next command uses the validated revision and releases the new lease.", "inputSchema": _schema({"project_id": _STRING, "expected_revision": _INTEGER}, ["project_id", "expected_revision"])},
    {"name": "cadyx_recover_expired_lease", "description": "Recover a stale lease only when the Agent API currently confirms it expired; Core validates the saved document before release.", "inputSchema": _schema({"project_id": _STRING}, ["project_id"])},
]



class MCPApplication:
    def __init__(self, api: CadyxAPI | None = None) -> None:
        self.api = api or CadyxAPI()
        self.owner_id = f"codex-mcp-{os.getpid()}-{uuid4()}"
        self._resumed_leases: dict[str, tuple[str, int]] = {}
        self._active_leases: dict[str, str] = {}
        self._stop_requests: set[str] = set()
        self._lease_guard = threading.Lock()

    @staticmethod
    def _project(project_id: str, suffix: str = "") -> str:
        return "/api/v1/agent/projects/" + urllib.parse.quote(project_id, safe="") + suffix

    def _project_summary(self, project_id: str) -> dict[str, Any]:
        return self.api.get(self._project(project_id, "/inspection?section=summary"))

    def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            value = self._dispatch(name, args)
            if isinstance(value, tuple) and value and value[0] == "image":
                return {"content": [
                    {"type": "text", "text": value[1]},
                    {"type": "image", "data": base64.b64encode(value[2]).decode("ascii"), "mimeType": value[3]},
                ]}
            if isinstance(value, tuple) and value and value[0] == "images":
                content = [{"type": "text", "text": value[1]}]
                for caption, image_bytes, mime_type in value[2]:
                    content.extend((
                        {"type": "text", "text": caption},
                        {"type": "image", "data": base64.b64encode(image_bytes).decode("ascii"), "mimeType": mime_type},
                    ))
                return {"content": content}
            return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, separators=(",", ":"))}], "structuredContent": value}
        except APIError as exc:
            payload = exc.payload if isinstance(exc.payload, dict) else {}
            error = {"error": payload.get("error", "HTTP_ERROR"),
                     "message": public_text(str(payload.get("message", "CADYX API request failed."))),
                     "http_status": exc.status}
            if isinstance(payload.get("diagnostics"), dict):
                error["diagnostics"] = public_data(payload["diagnostics"])
            return {"content": [{"type": "text", "text": json.dumps(error, ensure_ascii=False, separators=(",", ":"))}],
                    "structuredContent": error, "isError": True}
        except ResponseTooLargeError as exc:
            error = {
                "error": "RESPONSE_TOO_LARGE",
                "message": public_text(str(exc)),
                "diagnostics": {
                    "category": "resource_limit",
                    "failed_precondition": f"The {exc.kind} body exceeds the bounded MCP response budget.",
                    "measurements": {"byte_limit": exc.limit},
                    "correction_hint": exc.alternative,
                },
            }
            return {"content": [{"type": "text", "text": json.dumps(error, ensure_ascii=False, separators=(",", ":"))}],
                    "structuredContent": error, "isError": True}
        except TransportError as exc:
            error = {"error": "TRANSIENT_TRANSPORT", "message": public_text(str(exc)),
                     "diagnostics": {"category": "transport", "failed_precondition":
                                     "the Agent API request must complete before its result can be confirmed",
                                     "measurements": None,
                                     "correction_hint": "Inspect the project revision, history and lease before retrying a mutation."}}
            return {"content": [{"type": "text", "text": json.dumps(error, ensure_ascii=False, separators=(",", ":"))}],
                    "structuredContent": error, "isError": True}
        except OutcomeUncertainError as exc:
            error = {"error": "OPERATION_OUTCOME_UNCERTAIN", "message": public_text(str(exc)),
                     "diagnostics": {"category": "transport", "failed_precondition":
                                     "the saved result must be inspected after an ambiguous operation response",
                                     "measurements": None,
                                     "correction_hint": "Inspect the current revision and command history before deciding whether to retry."}}
            return {"content": [{"type": "text", "text": json.dumps(error, ensure_ascii=False, separators=(",", ":"))}],
                    "structuredContent": error, "isError": True}
        except (ValueError, TypeError) as exc:
            error = {"error": "VALIDATION_ERROR", "message": public_text(str(exc)),
                     "diagnostics": {"category": "validation", "failed_precondition":
                                     "tool arguments must satisfy the declared MCP contract",
                                     "measurements": None,
                                     "correction_hint": "Check the tool schema and supplied argument values."}}
            return {"content": [{"type": "text", "text": json.dumps(error, ensure_ascii=False, separators=(",", ":"))}],
                    "structuredContent": error, "isError": True}
        except Exception:
            error = {"error": "INTERNAL_ERROR", "message": "CADYX encountered an unexpected internal error."}
            return {"content": [{"type": "text", "text": json.dumps(error, ensure_ascii=False, separators=(",", ":"))}],
                    "structuredContent": error, "isError": True}

    def _dispatch(self, name: str, args: dict[str, Any]) -> Any:
        if name == "cadyx_health":
            return self.api.get("/api/v1/health")
        if name == "cadyx_list_workspace":
            return self.api.get("/api/v1/agent/projects")
        if name == "cadyx_list_standard_parts":
            return self.api.get("/api/v1/agent/catalog")
        if name == "cadyx_resolve_standard_part":
            return self.api.post("/api/v1/agent/catalog/resolve", args)
        if name == "cadyx_get_selection":
            return self.api.get("/api/v1/agent/workspace/selection")
        if name == "cadyx_select_resource":
            selection_type = args.get("type")
            selection = {"type": selection_type}
            key = "project_id" if selection_type == "project" else "folder_name"
            selection[key] = args.get(key)
            return self.api.post("/api/v1/agent/workspace/selection", selection)
        if name == "cadyx_create_project":
            select = args.get("select", True)
            payload = {"name": args.get("name"), "select": select}
            if "url_slug" in args:
                payload["url_slug"] = args["url_slug"]
            if "project_type" in args:
                payload["project_type"] = args["project_type"]
            return self.api.post("/api/v1/agent/projects", payload)
        if name == "cadyx_rename_project":
            payload = {"name": args["name"]}
            if "url_slug" in args:
                payload["url_slug"] = args["url_slug"]
            return self.api.post(self._project(args["project_id"], "/rename"), payload)
        if name == "cadyx_set_project_type":
            return self.api.post(
                self._project(args["project_id"], "/type"),
                {"project_type": args["project_type"]},
            )
        if name == "cadyx_copy_project_files":
            return self.api.post(
                self._project(args["target_project_id"], "/copy-files"),
                {"source_project_id": args["source_project_id"]},
            )
        if name == "cadyx_prune_project_files":
            return self.api.post(
                self._project(args["project_id"], "/prune-files"),
                {"keep_files": args["keep_files"]},
            )
        if name == "cadyx_delete_project":
            path = "/api/v1/projects/" + urllib.parse.quote(args["project_id"], safe="")
            raw, _ = self.api.request("DELETE", path)
            return json.loads(raw.decode("utf-8"))
        if name == "cadyx_inspect_project":
            summary = self.api.get(self._project(args["project_id"], "/inspection?section=summary"))
            return {
                "project_id": summary["project_id"],
                "name": summary["name"],
                "document": {
                    "id": summary["project_id"],
                    "revision": summary["revision"],
                    "object_count": summary["object_count"],
                    "object_counts": summary["object_counts"],
                },
                "lock": summary["lock"],
                "history_count": summary["history_count"],
            }
        if name == "cadyx_inspect_project_full":
            return self.api.get(self._project(args["project_id"]))
        if name == "cadyx_inspect_compact":
            options = {key: str(int(value)) if key == "include_metrics" else value
                       for key, value in args.items() if key != "project_id"}
            suffix = "/inspection" + ("?" + urllib.parse.urlencode(options) if options else "")
            return self.api.get(self._project(args["project_id"], suffix))
        if name == "cadyx_list_revision_checkpoints":
            values = {key: args[key] for key in ("offset", "limit") if key in args}
            suffix = "/revisions" + ("?" + urllib.parse.urlencode(values) if values else "")
            return self.api.get(self._project(args["project_id"], suffix))
        if name == "cadyx_compare_revisions":
            body = {key: args[key] for key in
                    ("expected_revision", "before", "after", "absolute_tolerance", "relative_tolerance")
                    if key in args}
            return self.api.post(self._project(args["project_id"], "/revisions/compare"), body)
        if name == "cadyx_capabilities":
            return self.api.get("/api/v1/agent/capabilities")
        if name == "cadyx_propose_capability":
            return self.api.post(
                self._project(args["project_id"], "/capabilities"),
                {"definition": args["definition"]},
            )
        if name == "cadyx_execute_capability":
            capability_id = urllib.parse.quote(args["capability_id"], safe="")
            return self.api.post(
                self._project(args["project_id"], f"/capabilities/{capability_id}/execute"),
                {
                    "owner_id": self.owner_id,
                    "expected_revision": args["expected_revision"],
                    "arguments": args["arguments"],
                },
            )
        if name == "cadyx_rollback_capability":
            capability_id = urllib.parse.quote(args["capability_id"], safe="")
            return self.api.post(
                self._project(args["project_id"], f"/capabilities/{capability_id}/rollback"),
                {},
            )
        if name == "cadyx_read_source_file":
            return self.api.post("/api/v1/agent/source-files/read", {"path": args["path"]})
        if name == "cadyx_list_source_extensions":
            return self.api.get(self._project(args["project_id"], "/source-extensions"))
        if name == "cadyx_develop_source_extension":
            return self.api.post(self._project(args["project_id"], "/source-extensions"), {"proposal": args["proposal"]})
        if name == "cadyx_rollback_source_extension":
            extension_id = urllib.parse.quote(args["extension_id"], safe="")
            return self.api.post(self._project(args["project_id"], f"/source-extensions/{extension_id}/rollback"), {})
        if name == "cadyx_list_project_files":
            if args.get("folder_name"):
                folder = urllib.parse.quote(args["folder_name"], safe="")
                return self.api.get(self._project(args["project_id"], f"/folders/{folder}/files"))
            return self.api.get(self._project(args["project_id"], "/files"))
        if name == "cadyx_read_project_file":
            if args.get("folder_name"):
                folder = urllib.parse.quote(args["folder_name"], safe="")
                path = self._project(args["project_id"], f"/folders/{folder}/files/{urllib.parse.quote(args['name'], safe='')}")
            else:
                path = self._project(args["project_id"], "/files/" + urllib.parse.quote(args["name"], safe=""))
            return self._read_file(path, args["name"])
        if name == "cadyx_remove_project_files":
            if args.get("folder_name"):
                folder = urllib.parse.quote(args["folder_name"], safe="")
                path = self._project(args["project_id"], f"/folders/{folder}/files/delete")
            else:
                path = self._project(args["project_id"], "/files/delete")
            raw, _ = self.api.request("POST", path, {"filenames": args["filenames"]})
            return json.loads(raw.decode("utf-8"))
        if name == "cadyx_remove_project_folder":
            folder = urllib.parse.quote(args["folder_name"], safe="")
            path = self._project(args["project_id"], f"/folders/{folder}/delete")
            return self.api.post(path, {})
        if name == "cadyx_move_project_files":
            return self.api.post(self._project(args["project_id"], "/files/move"), {
                "filenames": args["filenames"], "folder_name": args["folder_name"],
            })
        if name == "cadyx_start_scan_360":
            body = {key: args[key] for key in ("step_degrees", "align_to_markers", "rotation_period_seconds",
                                               "scanner_device_id", "turntable_device_id") if key in args}
            return self.api.post(self._project(args["project_id"], "/scans/360"), body)
        if name == "cadyx_get_scan_360_status":
            return self.api.get(self._project(args["project_id"], "/scans/360"))
        if name == "cadyx_get_scanner_devices":
            return self.api.get("/api/v1/scanner/devices")
        if name == "cadyx_get_turntable_status":
            query = urllib.parse.urlencode({key: args[key] for key in ("device_id", "profile_id") if key in args})
            suffix = f"?{query}" if query else ""
            return self.api.get("/api/v1/scanner/turntable/control" + suffix)
        if name == "cadyx_get_turntable_marker_map":
            query = urllib.parse.urlencode({key: args[key] for key in ("device_id", "profile_id") if key in args})
            suffix = f"?{query}" if query else ""
            data, metadata = self.api.request("GET", "/api/v1/scanner/turntable/map" + suffix)
            if not isinstance(metadata, dict) or metadata.get("content_type") != "application/octet-stream":
                raise TransportError("CADYX returned the turntable marker map in an unsupported format.")
            return {"marker_map": _decode_turntable_marker_map(data, metadata.get("table_map_count"))}
        if name == "cadyx_control_turntable":
            command = args["command"]
            body = {key: args[key] for key in ("command", "value", "device_id", "profile_id") if key in args}
            result = self.api.post("/api/v1/scanner/turntable/control", body)
            if command == "speed":
                control = result.get("result", {}) if isinstance(result, dict) else {}
                device_id = control.get("turntable_device_id") or args.get("device_id")
                profile_id = control.get("profile_id") or args.get("profile_id")
                if device_id and profile_id:
                    settings = self.api.post("/api/v1/scanner/turntable/settings", {
                        "turntable_device_id": device_id,
                        "profile_id": profile_id,
                        "rotation_period_seconds": args["value"],
                    })
                    result["saved_settings"] = settings
            return result
        if name == "cadyx_get_scanner_live_preview_status":
            return self.api.get("/api/v1/scanner/live-preview")
        if name == "cadyx_start_scanner_live_preview":
            body = {key: args[key] for key in ("scanner_device_id", "fps") if key in args}
            return self.api.post("/api/v1/scanner/live-preview", body)
        if name == "cadyx_stop_scanner_live_preview":
            raw, _ = self.api.request("DELETE", "/api/v1/scanner/live-preview")
            return json.loads(raw.decode("utf-8"))
        if name == "cadyx_upload_project_step":
            source = Path(args["source_path"]).expanduser()
            if source.suffix.casefold() not in {".step", ".stp"}:
                raise ValueError("Only .step and .stp files can be uploaded as STEP sources.")
            if source.is_symlink() or not source.is_file():
                raise ValueError("The STEP source must be a regular, non-symlink local file.")
            if source.stat().st_size < 1 or source.stat().st_size > _MAX_FILE_BYTES:
                raise ValueError("The STEP source must be between 1 byte and 64 MiB.")
            content = source.read_bytes()
            path = self._project(args["project_id"], "/files/" + urllib.parse.quote(source.name, safe=""))
            raw, _ = self.api.request("POST", path, content)
            return json.loads(raw.decode("utf-8"))
        if name == "cadyx_upload_project_scan":
            source = Path(args["source_path"]).expanduser()
            if source.suffix.casefold() != ".ply":
                raise ValueError("Only .ply files can be uploaded as project scan sources.")
            if source.is_symlink() or not source.is_file():
                raise ValueError("The PLY scan source must be a regular, non-symlink local file.")
            if source.stat().st_size < 1 or source.stat().st_size > _MAX_FILE_BYTES:
                raise ValueError("The PLY scan source must be between 1 byte and 64 MiB.")
            content = source.read_bytes()
            path = self._project(args["project_id"], "/files/" + urllib.parse.quote(source.name, safe=""))
            raw, _ = self.api.request("POST", path, content)
            return json.loads(raw.decode("utf-8"))
        if name == "cadyx_remove_staged_step":
            path = self._project(args["project_id"], "/files/" + urllib.parse.quote(args["name"], safe=""))
            raw, _ = self.api.request("DELETE", path)
            return json.loads(raw.decode("utf-8"))
        if name == "cadyx_list_folder_files":
            folder = urllib.parse.quote(args["folder_name"], safe="")
            return self.api.get(f"/api/v1/workspace/folders/{folder}/files")
        if name == "cadyx_read_folder_file":
            folder = urllib.parse.quote(args["folder_name"], safe="")
            filename = urllib.parse.quote(args["name"], safe="")
            return self._read_file(f"/api/v1/workspace/folders/{folder}/files/{filename}", args["name"])
        if name == "cadyx_inspect_topology":
            project_id = urllib.parse.quote(args["project_id"], safe="")
            body_id = urllib.parse.quote(args["body_id"], safe="")
            return self.api.get(f"/api/v1/agent/projects/{project_id}/topology?body_id={body_id}")
        if name == "cadyx_query_topology":
            return self.api.post(self._project(args["project_id"], "/topology/query"), {
                "body_id": args["body_id"], "expected_revision": args["expected_revision"],
                "query": args["query"],
            })
        for operation in VALIDATION_OPERATIONS.values():
            if name == operation["tool_name"]:
                body = {field: args[field] for field in operation["input_schema"]["required"]}
                for field in operation["input_schema"]["properties"]:
                    if field not in body and (field in args or field in operation["defaults"]):
                        body[field] = args.get(field, operation["defaults"].get(field))
                path = operation["path"].replace("{id}", urllib.parse.quote(args["project_id"], safe=""))
                return self.api.post(path, body)
        if name == "cadyx_list_validation_records":
            project_id = urllib.parse.quote(args["project_id"], safe="")
            limit = args.get("limit", 50)
            return self.api.get(f"/api/v1/agent/projects/{project_id}/validations?limit={limit}")
        if name == "cadyx_get_viewport":
            return self.api.get(self._project(args["project_id"], "/viewport"))
        if name == "cadyx_get_viewport_image":
            raw, meta = self.api.request("GET", self._project(args["project_id"], "/viewport.png"))
            return ("image", "CADYX viewport PNG (server or current browser frame).", raw, meta["content_type"])
        if name == "cadyx_get_project_thumbnail_batch":
            project_ids = args.get("project_ids")
            if (
                not isinstance(project_ids, list)
                or not 1 <= len(project_ids) <= 8
                or any(not isinstance(project_id, str) or not project_id.strip() for project_id in project_ids)
                or len(set(project_ids)) != len(project_ids)
            ):
                raise ValueError("project_ids must contain 1 to 8 unique non-empty project IDs.")
            images = []
            descriptions = []
            for project_id in project_ids:
                try:
                    raw, meta = self.api.request(
                        "GET", self._project(project_id, "/viewport.png?thumbnail=1"),
                    )
                    revision = meta.get("document_revision")
                    source = meta.get("viewport_source") or "server-thumbnail"
                    label = f"Project {project_id}; revision {revision or 'unknown'}; source {source}."
                    images.append((label, raw, meta["content_type"]))
                    descriptions.append({"project_id": project_id, "revision": revision, "status": "ready"})
                except Exception as exc:
                    descriptions.append({"project_id": project_id, "status": "unavailable",
                                         "error": public_text(str(exc))})
            summary = json.dumps(descriptions, ensure_ascii=False, separators=(",", ":"))
            return ("images", f"Project thumbnails in request order; compare them with the reference image as the AI agent. {summary}", images)
        if name == "cadyx_move_camera":
            keys = ("yaw_delta", "pitch_delta", "zoom_factor", "duration_ms")
            body = {key: args[key] for key in keys if key in args}
            return self.api.post(self._project(args["project_id"], "/viewport/camera"), body)
        if name == "cadyx_get_dashboard":
            return self.api.get(self._project(args["project_id"], "/dashboard"))
        if name == "cadyx_report_dashboard":
            return self.api.post(self._project(args["project_id"], "/dashboard"), {"workflow": args["workflow"], "tasks": args["tasks"]})
        if name == "cadyx_get_activity":
            return self.api.get(self._project(args["project_id"], "/activity"))
        if name == "cadyx_execute_command":
            try:
                result = self._execute_command(args["project_id"], args["expected_revision"], args["command"])
            except APIError as exc:
                message = exc.payload.get("message") if isinstance(exc.payload, dict) else None
                if not isinstance(message, str) or not message.startswith("Unsupported command type:"):
                    raise
                return self.api.post(
                    self._project(args["project_id"], "/capabilities/check"),
                    {"command_type": args["command"].get("type")},
                )
            return result
        if name == "cadyx_preview_commands":
            return self.api.post(self._project(args["project_id"], "/commands/preview"),
                                 {"expected_revision": args["expected_revision"], "commands": args["commands"]})
        if name == "cadyx_commit_command_proposal":
            return self._commit_command_proposal(
                args["project_id"], args["expected_revision"], args["proposal_id"],
            )
        if name == "cadyx_stop_agent_work":
            return self._stop_agent_work(args["project_id"])
        if name == "cadyx_resume_agent_work":
            return self._resume_agent_work(args["project_id"], args["expected_revision"])
        if name == "cadyx_recover_expired_lease":
            return self._recover_expired(args["project_id"])
        raise ValueError(f"Unknown CADYX MCP tool: {name}")

    def _read_file(self, path: str, filename: str) -> Any:
        raw, meta = self.api.request("GET", path)
        if len(raw) > _MAX_FILE_BYTES:
            raise ValueError(f"File is larger than the {_MAX_FILE_BYTES // (1024 * 1024)} MiB MCP read limit; use its supported CADYX import command instead.")
        mime_type = meta["content_type"]
        if mime_type.startswith("image/"):
            return ("image", f"Workspace image: {filename}", raw, mime_type)
        if mime_type.startswith("text/") or filename.casefold().endswith((".step", ".stp", ".md", ".txt", ".json", ".csv", ".ply")):
            try:
                return {"name": filename, "text": raw.decode("utf-8"), "content_type": mime_type}
            except UnicodeDecodeError as exc:
                raise ValueError("File is binary or not valid UTF-8; do not treat it as text. Use the matching CADYX import command.") from exc
        raise ValueError(f"File type {mime_type} is not exposed as text or image by this MCP adapter.")

    def _execute_command(self, project_id: str, expected_revision: int, command: dict[str, Any]) -> Any:
        if not isinstance(command, dict) or not isinstance(command.get("type"), str):
            raise ValueError("command must be an object with a string type.")
        with self._lease_guard:
            resumed = self._resumed_leases.get(project_id)
        payload = {"owner_id": self.owner_id, "expected_revision": expected_revision, "command": command}
        if resumed:
            lease_id, resume_revision = resumed
            if expected_revision != resume_revision:
                raise APIError(409, {"error": "REVISION_CONFLICT", "message": "The resumed lease is bound to a different revision; inspect the project before editing."})
            payload["lease_id"] = lease_id
            with self._lease_guard:
                self._resumed_leases.pop(project_id, None)
        try:
            result = self.api.post(self._project(project_id, "/commands/execute"), payload)
            return {"command_result": result, "revision": result.get("document", {}).get("revision"), "lease_released": True}
        except Exception as exc:
            # A lost or malformed response is ambiguous; never replay the command.
            if isinstance(exc, APIError) and exc.status in _DEFINITIVE_COMMAND_REJECTION_STATUSES:
                raise
            try:
                self._stop_agent_work(project_id)
            except Exception as stop_error:
                raise OutcomeUncertainError(
                    f"{exc}. Safe stop could not be confirmed: {stop_error}. "
                    "Inspect the current revision, history and lock before any retry."
                ) from exc
            raise OutcomeUncertainError(f"{exc}. The atomic command response was not confirmed; inspect the current revision, history and lock before any retry.") from exc

    def _commit_command_proposal(self, project_id: str, expected_revision: int, proposal_id: str) -> Any:
        lease_result = self.api.post(self._project(project_id, "/leases"), {"owner_id": self.owner_id})
        lease_id = lease_result["lease"]["lease_id"]
        with self._lease_guard:
            self._active_leases[project_id] = lease_id
        try:
            return self.api.post(self._project(project_id, "/commands/commit"), {
                "lease_id": lease_id, "expected_revision": expected_revision, "proposal_id": proposal_id,
            })
        except Exception as exc:
            if isinstance(exc, APIError) and exc.status in _DEFINITIVE_COMMAND_REJECTION_STATUSES:
                raise
            try:
                stopped = self.api.post(self._project(project_id, "/stop"), {"lease_id": lease_id})
                raise OutcomeUncertainError(
                    f"{exc}. CADYX safely stopped/validated the proposal boundary at revision "
                    f"{stopped.get('document', {}).get('revision')}; inspect the project before retrying."
                ) from exc
            except APIError as stop_error:
                raise OutcomeUncertainError(
                    f"{exc}. Stop did not confirm a safe proposal boundary ({stop_error}); "
                    "inspect revision, history and lease before any retry."
                ) from exc
        finally:
            with self._lease_guard:
                stop_in_progress = project_id in self._stop_requests
            if not stop_in_progress:
                try:
                    self.api.post(
                        self._project(project_id, f"/leases/{urllib.parse.quote(lease_id, safe='')}/release"), {},
                    )
                except (APIError, RuntimeError):
                    latest = self._project_summary(project_id)
                    if latest["lock"].get("state") != "FREE":
                        raise
            with self._lease_guard:
                if self._active_leases.get(project_id) == lease_id:
                    self._active_leases.pop(project_id, None)

    def _stop_agent_work(self, project_id: str) -> Any:
        with self._lease_guard:
            lease_id = self._active_leases.get(project_id)
            if lease_id is None:
                resumed = self._resumed_leases.get(project_id)
                lease_id = resumed[0] if resumed else None
        with self._lease_guard:
            self._stop_requests.add(project_id)
        try:
            return self.api.post(self._project(project_id, "/stop"),
                                 {"lease_id": lease_id} if lease_id else {"owner_id": self.owner_id})
        except APIError as exc:
            if isinstance(exc.payload, dict) and exc.payload.get("error") == "PROJECT_LOCKED":
                detail = self._project_summary(project_id)
                activities = self.api.get(self._project(project_id, "/activity")).get("activities", [])
                if detail["lock"].get("state") == "FREE" and not any(item.get("state") == "running" for item in activities):
                    return {"stopped": False, "command_already_finished": True, "revision": detail["revision"], "lock": detail["lock"]}
            raise
        finally:
            with self._lease_guard:
                self._stop_requests.discard(project_id)

    def _resume_agent_work(self, project_id: str, expected_revision: int) -> Any:
        detail = self._project_summary(project_id)
        if detail["lock"].get("state") != "FREE" or detail["lock"].get("recovery_required"):
            raise APIError(409, {"error": "PROJECT_LOCKED", "message": "Resume requires a confirmed FREE project with no stale lease."})
        result = self.api.post(self._project(project_id, "/resume"), {
            "owner_id": self.owner_id,
            "expected_revision": expected_revision,
        })
        with self._lease_guard:
            self._resumed_leases[project_id] = (result["lease"]["lease_id"], expected_revision)
            self._active_leases[project_id] = result["lease"]["lease_id"]
        return {"resumed": result.get("resumed"), "revision": result["document"]["revision"], "lease": result["lease"], "next_command_must_use_expected_revision": expected_revision}

    def _recover_expired(self, project_id: str) -> Any:
        detail = self._project_summary(project_id)
        if not detail["lock"].get("recovery_required"):
            raise APIError(409, {"error": "LEASE_NOT_EXPIRED", "message": "The API does not confirm an expired lease; recovery was not attempted."})
        return self.api.post(self._project(project_id, "/leases/recover"), {})


def _response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle_message(message: dict[str, Any], app: MCPApplication) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method")
    if not isinstance(method, str):
        return _error(request_id, -32600, "Invalid JSON-RPC request") if request_id is not None else None
    params = message.get("params") or {}
    try:
        if method == "initialize":
            requested = params.get("protocolVersion")
            protocol_version = requested if requested == _PROTOCOL_VERSION else _PROTOCOL_VERSION
            result = {"protocolVersion": protocol_version, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "cadyx-operator", "version": "1.0.0"}, "instructions": "Use CADYX tools for every project/model operation; never use shell, direct filesystem writes, or browser input to edit CAD. Separate chat sessions may work concurrently on distinct projects; each project remains exclusive under its own edit lease. Every model operation must use its explicit project_id; process-wide workspace selection is only a shared viewer hint and may be changed by another session. For every modeling task, inspect cadyx_capabilities and reuse a compatible registered capability. If a required operation is missing, first determine whether a tested declarative capability can express it. If not, read the relevant source, propose focused tests and a bounded source patch with cadyx_develop_source_extension; CADYX runs tests only in its no-network Windows AppContainer and applies source only after success. A successful source update requires CADYX host restart before it is loaded; the saved model remains in its project. Inspect the compact project summary and exact revision before each command; use bounded compact inspection for details and full inspection only when geometry or history is required. cadyx_execute_command checks the revision and acquires/releases its agent lease inside one API request. Never replay a command after a lost response; inspect history, lock, and geometry. Use cadyx_stop_agent_work to fence active work."}
        elif method == "notifications/initialized" or method.startswith("notifications/"):
            return None
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            if not isinstance(args, dict):
                return _error(request_id, -32602, "Tool arguments must be an object")
            result = app.call_tool(name, args)
        else:
            return _error(request_id, -32601, f"Method not found: {method}") if request_id is not None else None
        return _response(request_id, result) if request_id is not None else None
    except Exception as exc:
        return _error(request_id, -32603, public_text(str(exc))) if request_id is not None else None


def main() -> int:
    """Serve newline-framed JSON-RPC on stdio; stdout is reserved for protocol frames."""
    app = MCPApplication()
    write_lock = threading.Lock()
    pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="cadyx-mcp")

    def send(future: Any, request_id: Any) -> None:
        try:
            value = future.result()
        except Exception as exc:
            value = _error(request_id, -32603, public_text(str(exc)))
        if value is not None:
            with write_lock:
                sys.stdout.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
                sys.stdout.flush()

    try:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    response = _error(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid JSON-RPC request")
                    with write_lock:
                        sys.stdout.write(json.dumps(response) + "\n")
                        sys.stdout.flush()
                    continue
                if message.get("method") == "tools/call":
                    request_id = message.get("id")
                    future = pool.submit(handle_message, message, app)
                    future.add_done_callback(lambda completed, rid=request_id: send(completed, rid))
                else:
                    response = handle_message(message, app)
                    if response is not None:
                        with write_lock:
                            sys.stdout.write(json.dumps(response, ensure_ascii=False, allow_nan=False) + "\n")
                            sys.stdout.flush()
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                response = _error(None, -32700, f"Parse error: {exc}")
                with write_lock:
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
    finally:
        pool.shutdown(wait=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
