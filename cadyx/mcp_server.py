"""Local stdio MCP adapter for CADYX's existing HTTP Agent API."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import uuid4

_PROTOCOL_VERSION = "2025-11-25"
_MAX_FILE_BYTES = 4 * 1024 * 1024
_JSON = "application/json"


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

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[bytes, Any]:
        headers = {"Accept": "application/json, image/png, */*"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            headers["Content-Type"] = _JSON
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                raw = response.read()
                content_type = response.headers.get_content_type()
        except urllib.error.HTTPError as exc:
            with exc:
                raw = exc.read()
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = raw.decode("utf-8", errors="replace")
            raise APIError(exc.code, payload) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"CADYX API connection failed: {exc}") from exc
        if content_type == _JSON:
            return raw, json.loads(raw.decode("utf-8"))
        return raw, {"content_type": content_type}

    def get(self, path: str) -> Any:
        return self.request("GET", path)[1]

    def post(self, path: str, body: dict[str, Any]) -> Any:
        return self.request("POST", path, body)[1]


def _schema(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties or {}, "required": required or [], "additionalProperties": False}


_STRING = {"type": "string", "minLength": 1}
_NUMBER = {"type": "number"}
_INTEGER = {"type": "integer", "minimum": 0}
_OBJECT = {"type": "object"}
TOOLS: list[dict[str, Any]] = [
    {"name": "cadyx_health", "description": "Check whether the local CADYX host is ready.", "inputSchema": _schema()},
    {"name": "cadyx_list_workspace", "description": "List saved CADYX projects and direct workspace folders with lock snapshots.", "inputSchema": _schema()},
    {"name": "cadyx_get_selection", "description": "Read the local host's currently selected project or folder.", "inputSchema": _schema()},
    {"name": "cadyx_select_resource", "description": "Select an existing project or workspace folder through the Agent API.", "inputSchema": _schema({"type": {"enum": ["project", "folder"]}, "project_id": _STRING, "folder_name": _STRING}, ["type"])},
    {"name": "cadyx_create_project", "description": "Create and select a project through the Agent API.", "inputSchema": _schema({"name": {"type": "string", "minLength": 1, "maxLength": 120}}, ["name"])},
    {"name": "cadyx_rename_project", "description": "Rename and move a FREE project folder through the Agent API while preserving its identity and model history.", "inputSchema": _schema({"project_id": _STRING, "name": {"type": "string", "minLength": 1, "maxLength": 120}}, ["project_id", "name"])},
    {"name": "cadyx_inspect_project", "description": "Read the saved model, revision, command history and lock state without acquiring a lease.", "inputSchema": _schema({"project_id": _STRING}, ["project_id"])},
    {"name": "cadyx_capabilities", "description": "Discover supported Agent API routes, model commands, parameter names, documented constraints and error meanings.", "inputSchema": _schema()},
    {"name": "cadyx_list_project_files", "description": "List direct user files in a saved project.", "inputSchema": _schema({"project_id": _STRING}, ["project_id"])},
    {"name": "cadyx_read_project_file", "description": "Read a direct project file through the read-only Agent API; small images are returned as image content.", "inputSchema": _schema({"project_id": _STRING, "name": _STRING}, ["project_id", "name"])},
    {"name": "cadyx_list_folder_files", "description": "List direct user files in a workspace folder.", "inputSchema": _schema({"folder_name": _STRING}, ["folder_name"])},
    {"name": "cadyx_read_folder_file", "description": "Read a direct workspace file through the read-only API; small images are returned as image content.", "inputSchema": _schema({"folder_name": _STRING, "name": _STRING}, ["folder_name", "name"])},
    {"name": "cadyx_inspect_topology", "description": "Inspect current editable B-rep edges before a fillet. Refresh topology after any upstream edit.", "inputSchema": _schema({"project_id": _STRING, "body_id": _STRING}, ["project_id", "body_id"])},
    {"name": "cadyx_get_viewport", "description": "Read validated viewport scene data for the saved project revision.", "inputSchema": _schema({"project_id": _STRING}, ["project_id"])},
    {"name": "cadyx_get_viewport_image", "description": "Get the saved model's viewport PNG as an MCP image the model can inspect.", "inputSchema": _schema({"project_id": _STRING}, ["project_id"])},
    {"name": "cadyx_move_camera", "description": "Animate the selected browser viewport camera; this does not mutate the CAD document.", "inputSchema": _schema({"project_id": _STRING, "yaw_delta": _NUMBER, "pitch_delta": _NUMBER, "zoom_factor": _NUMBER, "duration_ms": {"type": "integer", "minimum": 0, "maximum": 5000}}, ["project_id", "yaw_delta"])},
    {"name": "cadyx_get_dashboard", "description": "Read the project's persisted agent workflow and task report.", "inputSchema": _schema({"project_id": _STRING}, ["project_id"])},
    {"name": "cadyx_report_dashboard", "description": "Publish honest Russian workflow progress and queue state; this does not mutate the CAD document.", "inputSchema": _schema({"project_id": _STRING, "workflow": {"anyOf": [_OBJECT, {"type": "null"}]}, "tasks": {"type": "array", "items": _OBJECT}}, ["project_id", "workflow", "tasks"])},
    {"name": "cadyx_get_activity", "description": "Read recent real modeling operation states and results.", "inputSchema": _schema({"project_id": _STRING}, ["project_id"])},
    {"name": "cadyx_execute_command", "description": "Execute one Command System operation: checks expected_revision, takes an agent lease immediately before the call, and releases it afterward. Never retries a command whose response was lost.", "inputSchema": _schema({"project_id": _STRING, "expected_revision": _INTEGER, "command": _OBJECT}, ["project_id", "expected_revision", "command"])},
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

    def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            value = self._dispatch(name, args)
            if isinstance(value, tuple) and value and value[0] == "image":
                return {"content": [
                    {"type": "text", "text": value[1]},
                    {"type": "image", "data": base64.b64encode(value[2]).decode("ascii"), "mimeType": value[3]},
                ]}
            return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=2)}], "structuredContent": value}
        except Exception as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}

    def _dispatch(self, name: str, args: dict[str, Any]) -> Any:
        if name == "cadyx_health":
            return self.api.get("/api/v1/health")
        if name == "cadyx_list_workspace":
            return self.api.get("/api/v1/agent/projects")
        if name == "cadyx_get_selection":
            return self.api.get("/api/v1/agent/workspace/selection")
        if name == "cadyx_select_resource":
            selection_type = args.get("type")
            selection = {"type": selection_type}
            key = "project_id" if selection_type == "project" else "folder_name"
            selection[key] = args.get(key)
            self._assert_selection_is_safe(selection_type, args.get(key))
            return self.api.post("/api/v1/agent/workspace/selection", selection)
        if name == "cadyx_create_project":
            self._assert_selection_is_safe("new-project", None)
            return self.api.post("/api/v1/agent/projects", {"name": args.get("name")})
        if name == "cadyx_rename_project":
            return self.api.post(self._project(args["project_id"], "/rename"), {"name": args["name"]})
        if name == "cadyx_inspect_project":
            return self.api.get(self._project(args["project_id"]))
        if name == "cadyx_capabilities":
            return self.api.get("/api/v1/agent/capabilities")
        if name == "cadyx_list_project_files":
            return self.api.get(self._project(args["project_id"], "/files"))
        if name == "cadyx_read_project_file":
            return self._read_file(self._project(args["project_id"], "/files/" + urllib.parse.quote(args["name"], safe="")), args["name"])
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
        if name == "cadyx_get_viewport":
            return self.api.get(self._project(args["project_id"], "/viewport"))
        if name == "cadyx_get_viewport_image":
            raw, meta = self.api.request("GET", self._project(args["project_id"], "/viewport.png"))
            return ("image", "CADYX viewport PNG (server or current browser frame).", raw, meta["content_type"])
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
            return self._execute_command(args["project_id"], args["expected_revision"], args["command"])
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

    def _assert_selection_is_safe(self, target_type: str, target_name: str | None) -> None:
        selection = self.api.get("/api/v1/agent/workspace/selection").get("selection")
        if selection and selection.get("type") == "project":
            current_id = selection["project_id"]
            detail = self.api.get(self._project(current_id))
            activity = self.api.get(self._project(current_id, "/activity"))
            if detail["lock"].get("state") != "FREE" or any(item.get("state") == "running" for item in activity.get("activities", [])):
                raise APIError(409, {"error": "PROJECT_BUSY", "message": "The currently selected project is locked or has a running operation; selection is unchanged."})
        updates = self.api.get("/api/v1/updates")
        if updates.get("agent_work_active"):
            raise APIError(409, {"error": "PROJECT_BUSY", "message": "CADYX reports active agent work; selection is unchanged."})
        if target_type == "project":
            detail = self.api.get(self._project(target_name or ""))
            activity = self.api.get(self._project(target_name or "", "/activity"))
            if detail["lock"].get("state") != "FREE" or detail["lock"].get("recovery_required") or any(item.get("state") == "running" for item in activity.get("activities", [])):
                raise APIError(409, {"error": "PROJECT_BUSY", "message": "The target project is locked, requires lease recovery, or has a running operation; selection is unchanged."})

    def _execute_command(self, project_id: str, expected_revision: int, command: dict[str, Any]) -> Any:
        if not isinstance(command, dict) or not isinstance(command.get("type"), str):
            raise ValueError("command must be an object with a string type.")
        detail = self.api.get(self._project(project_id))
        revision = detail["document"]["revision"]
        if revision != expected_revision:
            raise APIError(409, {"error": "REVISION_CONFLICT", "message": f"Expected revision {expected_revision}; current revision is {revision}. Inspect the project before editing."})
        lock = detail["lock"]
        with self._lease_guard:
            resumed = self._resumed_leases.get(project_id)
        if resumed:
            lease_id, resume_revision = resumed
            if expected_revision != resume_revision or lock.get("state") != "AI_LOCKED" or lock.get("lease", {}).get("lease_id") != lease_id:
                raise APIError(409, {"error": "PROJECT_LOCKED", "message": "The validated resumed lease is no longer current; inspect the project and lock."})
            with self._lease_guard:
                self._resumed_leases.pop(project_id, None)
        else:
            if lock.get("recovery_required"):
                raise APIError(409, {"error": "LEASE_RECOVERY_REQUIRED", "message": "The API reports an expired lease. Validate and recover it explicitly before editing."})
            if lock.get("state") != "FREE":
                raise APIError(409, {"error": "PROJECT_LOCKED", "message": f"Project lock is {lock.get('state')}; do not force or recover it."})
            lease_result = self.api.post(self._project(project_id, "/leases"), {"owner_id": self.owner_id})
            lease_id = lease_result["lease"]["lease_id"]
        with self._lease_guard:
            self._active_leases[project_id] = lease_id
        try:
            result = self.api.post(self._project(project_id, "/commands"), {
                "lease_id": lease_id,
                "expected_revision": expected_revision,
                "command": command,
            })
            return {"command_result": result, "revision": result.get("document", {}).get("revision"), "lease_released": True}
        except RuntimeError as exc:
            # A dropped response is ambiguous. Ask Core to stop/fence this lease,
            # then require inspection; never issue the mutation a second time.
            try:
                stopped = self.api.post(self._project(project_id, "/stop"), {"lease_id": lease_id})
                raise RuntimeError(f"{exc}. CADYX safely stopped/validated the command boundary at revision {stopped.get('document', {}).get('revision')}; inspect history and geometry before deciding whether to retry.") from exc
            except APIError as stop_error:
                raise RuntimeError(f"{exc}. Stop did not confirm a safe boundary ({stop_error}); inspect the current revision, history and lease before any retry.") from exc
        finally:
            with self._lease_guard:
                stop_in_progress = project_id in self._stop_requests
            if not stop_in_progress:
                try:
                    self.api.post(self._project(project_id, f"/leases/{urllib.parse.quote(lease_id, safe='')}/release"), {})
                except (APIError, RuntimeError):
                    # A successful stop may already have released this same lease.
                    latest = self.api.get(self._project(project_id))
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
        if lease_id is None:
            raise APIError(409, {"error": "PROJECT_LOCKED", "message": "This MCP session does not own the active agent lease; no stop was attempted."})
        with self._lease_guard:
            self._stop_requests.add(project_id)
        try:
            return self.api.post(self._project(project_id, "/stop"), {"lease_id": lease_id})
        except APIError as exc:
            if isinstance(exc.payload, dict) and exc.payload.get("error") == "PROJECT_LOCKED":
                detail = self.api.get(self._project(project_id))
                activities = self.api.get(self._project(project_id, "/activity")).get("activities", [])
                if detail["lock"].get("state") == "FREE" and not any(item.get("state") == "running" for item in activities):
                    return {"stopped": False, "command_already_finished": True, "revision": detail["document"]["revision"], "lock": detail["lock"]}
            raise
        finally:
            with self._lease_guard:
                self._stop_requests.discard(project_id)

    def _resume_agent_work(self, project_id: str, expected_revision: int) -> Any:
        detail = self.api.get(self._project(project_id))
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
        detail = self.api.get(self._project(project_id))
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
            result = {"protocolVersion": protocol_version, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "cadyx-operator", "version": "1.0.0"}, "instructions": "Use CADYX tools for every project/model operation; never use shell, direct filesystem writes, or browser input to edit CAD. Inspect the current project, capabilities, and exact revision before each command. Mutations require the returned revision and an Agent API lease. Never replay a command after a lost response; inspect history, lock, and geometry. Use cadyx_stop_agent_work to fence active work."}
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
        return _error(request_id, -32603, str(exc)) if request_id is not None else None


def main() -> int:
    """Serve newline-framed JSON-RPC on stdio; stdout is reserved for protocol frames."""
    app = MCPApplication()
    write_lock = threading.Lock()
    pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="cadyx-mcp")

    def send(future: Any, request_id: Any) -> None:
        try:
            value = future.result()
        except Exception as exc:
            value = _error(request_id, -32603, str(exc))
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
