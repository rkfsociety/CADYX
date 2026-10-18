"""Declarative HTTP route matching and focused CADYX transport dispatch."""

from __future__ import annotations

import math
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import SplitResult, parse_qs, unquote

from .api_contracts import validate_request_fields
from .errors import ValidationError
from .models import OwnerType

if TYPE_CHECKING:
    from .core import CadyxCore


@dataclass(frozen=True)
class RouteSpec:
    """One method/path template mapped to a focused transport domain."""

    method: str
    template: str
    name: str
    domain: str
    body_kind: str = "none"


@dataclass(frozen=True)
class RouteMatch:
    """Resolved route plus raw path parameters."""

    spec: RouteSpec
    params: dict[str, str]


_ROUTE_SPECS = (
    RouteSpec("GET", "/api/v1/health", "health", "system"),
    RouteSpec("GET", "/api/v1/events", "events", "system"),
    RouteSpec("GET", "/api/v1/updates", "updates", "system"),
    RouteSpec("GET", "/api/v1/agent/capabilities", "capabilities", "system"),
    RouteSpec("GET", "/api/v1/agent/workspace/selection", "workspace_selection_get", "workspace"),
    RouteSpec("POST", "/api/v1/agent/workspace/selection", "workspace_selection_post", "workspace", "json"),
    RouteSpec("POST", "/api/v1/project-groups", "project_group_create", "workspace", "json"),
    RouteSpec("GET", "/api/v1/workspace/folders/{folder}/files", "folder_files", "files"),
    RouteSpec("GET", "/api/v1/workspace/folders/{folder}/files/{filename}", "folder_file", "files"),
    RouteSpec("GET", "/api/v1/scanner/live-frame", "scanner_live_frame", "scanner"),
    RouteSpec("GET", "/api/v1/scanner/calibration", "scanner_calibration", "scanner"),
    RouteSpec("GET", "/api/v1/projects", "projects", "project"),
    RouteSpec("POST", "/api/v1/projects", "project_create", "project", "json"),
    RouteSpec("GET", "/api/v1/projects/{project_id}", "project_get", "project"),
    RouteSpec("DELETE", "/api/v1/projects/{project_id}", "project_delete", "project"),
    RouteSpec("GET", "/api/v1/projects/{project_id}/dashboard", "project_dashboard", "project"),
    RouteSpec("GET", "/api/v1/projects/{project_id}/activity", "project_activity", "project"),
    RouteSpec("GET", "/api/v1/projects/{project_id}/comparison", "project_comparison", "project"),
    RouteSpec("GET", "/api/v1/projects/{project_id}/viewport", "project_viewport", "viewport"),
    RouteSpec("GET", "/api/v1/projects/{project_id}/viewport.png", "project_viewport_png", "viewport"),
    RouteSpec("POST", "/api/v1/projects/{project_id}/viewport/snapshot", "project_viewport_snapshot", "viewport", "png"),
    RouteSpec("GET", "/api/v1/projects/{project_id}/files", "project_files", "files"),
    RouteSpec("GET", "/api/v1/projects/{project_id}/files/{filename}", "project_file", "files"),
    RouteSpec("POST", "/api/v1/projects/{project_id}/group", "project_group_assign", "project", "json"),
    RouteSpec("POST", "/api/v1/projects/{project_id}/leases", "project_lease", "project", "json"),
    RouteSpec("POST", "/api/v1/projects/{project_id}/commands", "project_command", "project", "json"),
    RouteSpec("POST", "/api/v1/projects/{project_id}/leases/recover", "project_lease_recover", "project", "json"),
    RouteSpec("POST", "/api/v1/projects/{project_id}/leases/{lease_id}/heartbeat", "project_lease_heartbeat", "project", "json"),
    RouteSpec("POST", "/api/v1/projects/{project_id}/leases/{lease_id}/release", "project_lease_release", "project", "json"),
    RouteSpec("GET", "/api/v1/agent/projects", "agent_projects", "agent"),
    RouteSpec("POST", "/api/v1/agent/projects", "agent_project_create", "agent", "json"),
    RouteSpec("GET", "/api/v1/agent/projects/{project_id}", "agent_project_get", "agent"),
    RouteSpec("GET", "/api/v1/agent/projects/{project_id}/inspection", "agent_inspection", "agent"),
    RouteSpec("GET", "/api/v1/agent/projects/{project_id}/dashboard", "agent_dashboard_get", "agent"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/dashboard", "agent_dashboard_post", "agent", "json"),
    RouteSpec("GET", "/api/v1/agent/projects/{project_id}/activity", "agent_activity", "agent"),
    RouteSpec("GET", "/api/v1/agent/projects/{project_id}/comparison", "agent_comparison", "agent"),
    RouteSpec("GET", "/api/v1/agent/projects/{project_id}/topology", "agent_topology", "agent"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/topology/query", "agent_topology_query", "agent", "json"),
    RouteSpec("GET", "/api/v1/agent/projects/{project_id}/validations", "agent_validations", "validation"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/validations/dimensions", "agent_validate_dimensions", "validation", "json"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/validations/planetary-kinematics", "agent_validate_planetary_kinematics", "validation", "json"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/validations/clearance", "agent_validate_clearance", "validation", "json"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/validations/preservation", "agent_validate_preservation", "validation", "json"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/validations/thickness", "agent_validate_thickness", "validation", "json"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/validations/specification", "agent_validate_specification", "validation", "json"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/validations/project-check", "agent_validate_project_check", "validation", "json"),
    RouteSpec("GET", "/api/v1/agent/projects/{project_id}/viewport", "agent_viewport", "viewport"),
    RouteSpec("GET", "/api/v1/agent/projects/{project_id}/viewport.png", "agent_viewport_png", "viewport"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/viewport/snapshot", "agent_viewport_snapshot", "viewport", "png"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/viewport/camera", "agent_viewport_camera", "viewport", "json"),
    RouteSpec("GET", "/api/v1/agent/projects/{project_id}/files", "agent_project_files", "files"),
    RouteSpec("GET", "/api/v1/agent/projects/{project_id}/files/{filename}", "agent_project_file", "files"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/files/{filename}", "agent_project_file_upload", "files", "step"),
    RouteSpec("DELETE", "/api/v1/agent/projects/{project_id}/files/{filename}", "agent_project_file_delete", "files"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/rename", "agent_rename", "agent", "json"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/copy-files", "agent_copy_files", "agent", "json"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/prune-files", "agent_prune_files", "agent", "json"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/stop", "agent_stop", "agent", "json"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/resume", "agent_resume", "agent", "json"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/leases", "agent_lease", "agent", "json"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/commands", "agent_command", "agent", "json"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/commands/preview", "agent_commands_preview", "agent", "json"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/commands/commit", "agent_commands_commit", "agent", "json"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/capabilities", "agent_capability_develop", "agent", "json"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/capabilities/check", "agent_capability_check", "agent", "json"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/capabilities/{capability_id}/execute", "agent_capability_execute", "agent", "json"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/capabilities/{capability_id}/rollback", "agent_capability_rollback", "agent", "json"),
    RouteSpec("GET", "/api/v1/agent/projects/{project_id}/source-extensions", "agent_source_extensions", "agent"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/source-extensions", "agent_source_extension_develop", "agent", "json"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/source-extensions/{extension_id}/rollback", "agent_source_extension_rollback", "agent", "json"),
    RouteSpec("POST", "/api/v1/agent/source-files/read", "agent_source_file_read", "agent", "json"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/leases/recover", "agent_lease_recover", "agent", "json"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/leases/{lease_id}/heartbeat", "agent_lease_heartbeat", "agent", "json"),
    RouteSpec("POST", "/api/v1/agent/projects/{project_id}/leases/{lease_id}/release", "agent_lease_release", "agent", "json"),
    RouteSpec("GET", "/", "ui_index", "static"),
    RouteSpec("GET", "/scanner", "ui_scanner", "static"),
    RouteSpec("GET", "/favicon.svg", "ui_favicon", "static"),
    RouteSpec("GET", "/assets/app.js", "asset_app", "static"),
    RouteSpec("GET", "/assets/viewport.js", "asset_viewport", "static"),
    RouteSpec("GET", "/assets/trace-draft.js", "asset_trace_draft", "static"),
    RouteSpec("GET", "/assets/scanner-camera.js", "asset_scanner_camera", "static"),
    RouteSpec("GET", "/assets/ply-parser.js", "asset_ply_parser", "static"),
    RouteSpec("GET", "/assets/ply-preview.js", "asset_ply_preview", "static"),
    RouteSpec("GET", "/assets/style.css", "asset_style", "static"),
)


def route_specs() -> tuple[RouteSpec, ...]:
    """Return the immutable HTTP route inventory."""
    return _ROUTE_SPECS


def _template_segments(template: str) -> tuple[str, ...]:
    if template == "/":
        return ()
    return tuple(template.strip("/").split("/"))


def _path_segments(path: str) -> tuple[str, ...]:
    if path == "/":
        return ()
    return tuple(path.strip("/").split("/"))


def match_route(method: str, path: str) -> RouteMatch | None:
    """Resolve one HTTP method/path without socket or handler state."""
    normalized_method = method.upper()
    actual = _path_segments(path)
    for spec in _ROUTE_SPECS:
        if spec.method != normalized_method:
            continue
        expected = _template_segments(spec.template)
        if len(expected) != len(actual):
            continue
        params: dict[str, str] = {}
        matched = True
        for expected_segment, actual_segment in zip(expected, actual, strict=True):
            if expected_segment.startswith("{") and expected_segment.endswith("}"):
                if not actual_segment:
                    matched = False
                    break
                params[expected_segment[1:-1]] = actual_segment
            elif expected_segment != actual_segment:
                matched = False
                break
        if matched:
            return RouteMatch(spec=spec, params=params)
    return None


def dispatch_route(handler: Any, core: CadyxCore, route: RouteMatch, parsed: SplitResult) -> None:
    """Dispatch a matched route to its focused transport domain."""
    dispatchers: dict[str, Callable[[Any, CadyxCore, RouteMatch, SplitResult], None]] = {
        "system": _dispatch_system,
        "workspace": _dispatch_workspace,
        "scanner": _dispatch_scanner,
        "files": _dispatch_files,
        "project": _dispatch_project,
        "agent": _dispatch_agent,
        "validation": _dispatch_validation,
        "viewport": _dispatch_viewport,
        "static": _dispatch_static,
    }
    dispatchers[route.spec.domain](handler, core, route, parsed)


def _require_json(handler: Any) -> dict[str, Any]:
    body = handler._read_json()
    if body is None:
        raise ValidationError("A JSON request body is required.")
    return body


def _dispatch_system(handler: Any, core: CadyxCore, route: RouteMatch, parsed: SplitResult) -> None:
    name = route.spec.name
    if name == "health":
        handler._json(200, {"status": "ok", "api_version": 1})
    elif name == "events":
        handler._stream_events(parse_qs(parsed.query).get("project_id", [None])[0])
    elif name == "capabilities":
        handler._json(200, core.agent_capabilities())
    elif name == "updates":
        query = parse_qs(parsed.query)
        project_id = query.get("project_id", [None])[0]
        folder_name = query.get("folder", [None])[0]
        agent_work = core.events.agent_work_snapshot()
        snapshot: dict[str, Any] = {
            "ui_version": handler._ui_asset_version(),
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
        handler._json(200, snapshot)
    else:
        raise RuntimeError(f"Unhandled system route: {name}")


def _dispatch_workspace(handler: Any, core: CadyxCore, route: RouteMatch, parsed: SplitResult) -> None:
    del parsed
    name = route.spec.name
    if name == "workspace_selection_get":
        handler._json(200, {"selection": core.workspace_selection_snapshot()})
    elif name == "workspace_selection_post":
        handler._json(200, {"selection": core.select_workspace_resource(_require_json(handler))})
    elif name == "project_group_create":
        body = _require_json(handler)
        if set(body) != {"name"}:
            raise ValidationError("Project group creation requires only a name field.")
        handler._json(201, {"group": core.create_project_group(body["name"])})
    else:
        raise RuntimeError(f"Unhandled workspace route: {name}")


def _dispatch_scanner(handler: Any, core: CadyxCore, route: RouteMatch, parsed: SplitResult) -> None:
    del parsed
    if route.spec.name == "scanner_live_frame":
        frame_path = core.workspace.root / ".revopoint-live.raw"
        if not frame_path.is_file() or frame_path.stat().st_size != 640 * 400 * 2:
            handler._json(404, {"error": "NO_LIVE_FRAME", "message": "Live scanner frame is not available."})
            return
        payload = frame_path.read_bytes()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/octet-stream")
        handler.send_header("Content-Length", str(len(payload)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-CADYX-Frame-Width", "640")
        handler.send_header("X-CADYX-Frame-Height", "400")
        handler.send_header("X-CADYX-Frame-Mtime", str(frame_path.stat().st_mtime_ns))
        handler.end_headers()
        handler.wfile.write(payload)
        return
    calibration_path = Path(__file__).resolve().parent.parent / "tools" / "revopoint" / "calibration" / "Q.bin"
    calibration = calibration_path.read_bytes()
    if len(calibration) != 64:
        raise RuntimeError("Revopoint Q calibration must contain 64 bytes.")
    matrix = struct.unpack("<16f", calibration)
    if not all(math.isfinite(value) for value in matrix):
        raise RuntimeError("Revopoint Q calibration contains a non-finite value.")
    handler._json(200, {"width": 640, "height": 400, "matrix": matrix,
                        "disparity_scale": 256,
                        "disparity_scale_status": "provisional_unverified"})


def _dispatch_files(handler: Any, core: CadyxCore, route: RouteMatch, parsed: SplitResult) -> None:
    del parsed
    name = route.spec.name
    project_id = route.params.get("project_id")
    filename = unquote(route.params["filename"]) if "filename" in route.params else None
    if name == "folder_files":
        handler._json(200, {"files": core.workspace.list_folder_files(unquote(route.params["folder"]))})
    elif name == "folder_file":
        handler._binary_file(core.workspace.resolve_folder_file(unquote(route.params["folder"]), filename))
    elif name in {"project_files", "agent_project_files"}:
        handler._json(200, {"files": core.workspace.list_project_files(project_id)})
    elif name in {"project_file", "agent_project_file"}:
        handler._binary_file(core.workspace.resolve_project_file(project_id, filename))
    elif name == "agent_project_file_upload":
        result = core.upload_project_step(project_id, filename, handler._read_step_upload())
        handler._json(201, {"project_id": project_id, "file": result})
    elif name == "agent_project_file_delete":
        handler._json(200, core.remove_staged_project_step(project_id, filename))
    else:
        raise RuntimeError(f"Unhandled file route: {name}")


def _project_activity(handler: Any, core: CadyxCore, project_id: str) -> None:
    activities, revision = core.events.activity_state_snapshot(project_id)
    if not activities:
        core.workspace.load(project_id)
    handler._json(200, {"activities": activities, "revision": revision})


def _create_project(handler: Any, core: CadyxCore, *, agent_api: bool) -> None:
    body = _require_json(handler)
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
    handler._json(201, core.create_project(body["name"], select=select))


def _dispatch_project(handler: Any, core: CadyxCore, route: RouteMatch, parsed: SplitResult) -> None:
    name = route.spec.name
    project_id = route.params.get("project_id")
    if name == "projects":
        handler._json(200, core.list_workspace())
    elif name == "project_create":
        _create_project(handler, core, agent_api=False)
    elif name == "project_get":
        handler._json(200, core.inspect_project(project_id))
    elif name == "project_delete":
        handler._json(200, core.delete_project(project_id))
    elif name == "project_dashboard":
        handler._json(200, core.agent_dashboard_snapshot(project_id))
    elif name == "project_activity":
        _project_activity(handler, core, project_id)
    elif name == "project_comparison":
        query = parse_qs(parsed.query)
        handler._json(200, core.compare_bodies(project_id,
            query.get("original_body_id", [None])[0],
            query.get("modified_body_id", [None])[0]))
    elif name == "project_group_assign":
        body = _require_json(handler)
        if set(body) != {"group_id"}:
            raise ValidationError("Project group assignment requires only group_id.")
        handler._json(200, core.assign_project_group(project_id, body["group_id"]))
    elif name in {"project_lease", "project_command", "project_lease_recover", "project_lease_heartbeat", "project_lease_release"}:
        _dispatch_project_control(handler, core, route, agent_api=False)
    else:
        raise RuntimeError(f"Unhandled project route: {name}")


def _dispatch_agent(handler: Any, core: CadyxCore, route: RouteMatch, parsed: SplitResult) -> None:
    name = route.spec.name
    project_id = route.params.get("project_id")
    if name == "agent_projects":
        handler._json(200, core.list_workspace())
    elif name == "agent_project_create":
        _create_project(handler, core, agent_api=True)
    elif name == "agent_project_get":
        handler._json(200, core.observe_agent_operation(project_id, "Изучает модель", lambda: core.inspect_project(project_id)))
    elif name == "agent_inspection":
        query = parse_qs(parsed.query, keep_blank_values=True)
        allowed = {"section", "expected_revision", "kind", "body_id", "shape",
                   "since_revision", "offset", "limit", "include_metrics"}
        if set(query) - allowed or any(len(values) != 1 for values in query.values()):
            raise ValidationError("Unknown or repeated compact inspection query parameter.")
        options: dict[str, Any] = {}
        for key in ("section", "kind", "body_id", "shape"):
            if key in query:
                options[key] = query[key][0]
        for key in ("expected_revision", "since_revision", "offset", "limit"):
            if key in query:
                value = query[key][0]
                if not value.isascii() or not value.isdecimal():
                    raise ValidationError(f"{key} must be a nonnegative decimal integer.")
                options[key] = int(value)
        if "include_metrics" in query:
            value = query["include_metrics"][0]
            if value not in ("0", "1"):
                raise ValidationError("include_metrics must be 0 or 1.")
            options["include_metrics"] = value == "1"
        handler._json(200, core.inspect_compact(project_id, **options))
    elif name == "agent_capability_develop":
        body = _require_json(handler)
        if set(body) != {"definition"} or not isinstance(body["definition"], dict):
            raise ValidationError("Capability development requires only a definition object.")
        handler._json(201, core.develop_agent_capability(project_id, body["definition"]))
    elif name == "agent_capability_check":
        body = _require_json(handler)
        if set(body) != {"command_type"} or not isinstance(body["command_type"], str):
            raise ValidationError("Capability check requires only a command_type string.")
        handler._json(200, core.check_agent_capability(project_id, body["command_type"]))
    elif name == "agent_capability_execute":
        body = _require_json(handler)
        if set(body) != {"arguments", "expected_revision", "owner_id"}:
            raise ValidationError("Capability execution requires arguments, expected_revision, and owner_id.")
        if not isinstance(body["arguments"], dict):
            raise ValidationError("Capability arguments must be an object.")
        handler._json(200, core.execute_agent_capability(
            project_id, route.params["capability_id"], body["arguments"],
            owner_id=body["owner_id"], expected_revision=body["expected_revision"],
        ))
    elif name == "agent_capability_rollback":
        body = _require_json(handler)
        if body:
            raise ValidationError("Capability rollback accepts an empty object only.")
        handler._json(200, core.rollback_agent_capability(project_id, route.params["capability_id"]))
    elif name == "agent_source_file_read":
        body = _require_json(handler)
        if set(body) != {"path"} or not isinstance(body["path"], str):
            raise ValidationError("Source-file read requires only a path string.")
        handler._json(200, core.read_agent_source_file(body["path"]))
    elif name == "agent_source_extensions":
        handler._json(200, core.observe_agent_operation(
            project_id, "Изучает проверенные исходные возможности", core.list_agent_source_extensions,
        ))
    elif name == "agent_source_extension_develop":
        body = _require_json(handler)
        if set(body) != {"proposal"} or not isinstance(body["proposal"], dict):
            raise ValidationError("Source extension development requires only a proposal object.")
        result = core.develop_agent_source_extension(project_id, body["proposal"])
        if result.get("restart_required"):
            script = Path(__file__).resolve().parent.parent / "scripts" / "restart-after-source-extension.ps1"
            try:
                subprocess.Popen(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                    cwd=script.parent.parent, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, close_fds=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                result["restart_scheduled"] = True
            except OSError:
                result["restart_scheduled"] = False
        handler._json(201 if result.get("status") == "ready" else 200, result)
    elif name == "agent_source_extension_rollback":
        body = _require_json(handler)
        if body:
            raise ValidationError("Source extension rollback accepts an empty object only.")
        handler._json(200, core.rollback_agent_source_extension(project_id, route.params["extension_id"]))
    elif name == "agent_dashboard_get":
        handler._json(200, core.agent_dashboard_snapshot(project_id))
    elif name == "agent_dashboard_post":
        handler._json(200, core.report_agent_dashboard(project_id, _require_json(handler)))
    elif name == "agent_activity":
        _project_activity(handler, core, project_id)
    elif name == "agent_comparison":
        query = parse_qs(parsed.query)
        handler._json(200, core.compare_bodies(project_id, query.get("original_body_id", [None])[0], query.get("modified_body_id", [None])[0]))
    elif name == "agent_topology":
        body_id = parse_qs(parsed.query).get("body_id", [None])[0]
        handler._json(200, core.observe_agent_operation(project_id, "Изучает рёбра модели", lambda: core.inspect_body_topology(project_id, body_id)))
    elif name == "agent_topology_query":
        body = _require_json(handler)
        if set(body) != {"body_id", "expected_revision", "query"}:
            raise ValidationError("Topology query requires only body_id, expected_revision, and query.")
        handler._json(200, core.query_body_topology(
            project_id, body["body_id"], body["expected_revision"], body["query"],
        ))
    elif name == "agent_commands_preview":
        body = _require_json(handler)
        if set(body) != {"expected_revision", "commands"}:
            raise ValidationError("Command preview requires expected_revision and commands.")
        handler._json(200, core.preview_commands(project_id, body["expected_revision"], body["commands"]))
    elif name == "agent_commands_commit":
        body = _require_json(handler)
        if set(body) != {"expected_revision", "proposal_id", "lease_id"}:
            raise ValidationError("Command commit requires expected_revision, proposal_id, and lease_id.")
        handler._json(200, core.commit_command_proposal(
            project_id, body["lease_id"], body["expected_revision"], body["proposal_id"],
            expected_owner_type=OwnerType.AGENT,
        ))
    elif name == "agent_rename":
        body = _require_json(handler)
        if set(body) != {"name"}:
            raise ValidationError("Project rename requires only a name field.")
        handler._json(200, core.rename_project(project_id, body["name"]))
    elif name == "agent_copy_files":
        body = _require_json(handler)
        if set(body) != {"source_project_id"}:
            raise ValidationError("Project file copy requires only source_project_id.")
        handler._json(201, core.copy_project_files(project_id, body["source_project_id"]))
    elif name == "agent_prune_files":
        body = _require_json(handler)
        if set(body) != {"keep_files"} or not isinstance(body["keep_files"], list):
            raise ValidationError("Project file pruning requires only a keep_files array.")
        handler._json(200, core.prune_project_files(project_id, body["keep_files"]))
    elif name in {"agent_stop", "agent_resume"}:
        body = _require_json(handler)
        result = (core.stop_agent(project_id, body.get("lease_id")) if name == "agent_stop" else core.resume_agent(project_id, body.get("owner_id"), body.get("expected_revision")))
        handler._json(200, result)
    elif name in {"agent_lease", "agent_command", "agent_lease_recover", "agent_lease_heartbeat", "agent_lease_release"}:
        _dispatch_project_control(handler, core, route, agent_api=True)
    else:
        raise RuntimeError(f"Unhandled agent route: {name}")


def _dispatch_project_control(handler: Any, core: CadyxCore, route: RouteMatch, *, agent_api: bool) -> None:
    name = route.spec.name.removeprefix("agent_").removeprefix("project_")
    project_id = route.params["project_id"]
    if name == "lease":
        body = _require_json(handler)
        owner_type = "agent" if agent_api else body.get("owner_type")
        handler._json(201, {"lease": core.acquire_lease(project_id, owner_type, body.get("owner_id"))})
    elif name == "command":
        body = _require_json(handler)
        expected = OwnerType.AGENT if agent_api else OwnerType.HUMAN
        handler._json(200, core.execute_command(project_id, body.get("lease_id"), body.get("command"), expected_owner_type=expected, expected_revision=body.get("expected_revision")))
    elif name == "lease_recover":
        handler._read_json()
        handler._json(200, core.recover_expired_lease(project_id))
    elif name in {"lease_heartbeat", "lease_release"}:
        handler._read_json()
        lease_id = route.params["lease_id"]
        if name == "lease_heartbeat":
            handler._json(200, {"lease": core.heartbeat(project_id, lease_id)})
        else:
            handler._json(200, core.release_lease(project_id, lease_id))
    else:
        raise RuntimeError(f"Unhandled project control route: {route.spec.name}")


def _dispatch_validation(handler: Any, core: CadyxCore, route: RouteMatch, parsed: SplitResult) -> None:
    name = route.spec.name
    project_id = route.params["project_id"]
    if name == "agent_validations":
        query = parse_qs(parsed.query)
        try:
            limit = int(query.get("limit", ["50"])[0])
        except ValueError as exc:
            raise ValidationError("Validation record limit must be an integer from 1 to 200.") from exc
        handler._json(200, core.list_validation_records(project_id, limit=limit))
        return
    body = handler._read_json()
    operation = name.removeprefix("agent_validate_")
    if operation == "project_check":
        validate_request_fields(operation, body)
        assert body is not None
        result = core.observe_agent_operation(
            project_id, "Проверяет проект",
            lambda: core.validate_body_project_check(
                project_id, body["expected_revision"],
                specification=body.get("specification"), strict=body.get("strict", False),
                export_body_id=body.get("export_body_id"),
                require_assembly=body.get("require_assembly", False),
                require_drawings=body.get("require_drawings", False),
                deterministic_rebuild=body.get("deterministic_rebuild", False),
                max_bodies=body.get("max_bodies", 128),
                time_budget_seconds=body.get("time_budget_seconds", 30.0),
                persist=body.get("persist", False),
            ),
        )
        handler._json(200, result)
        return
    if operation == "specification":
        validate_request_fields(operation, body)
        assert body is not None
        result = core.observe_agent_operation(
            project_id, "Проверяет спецификацию модели",
            lambda: core.validate_body_specification(
                project_id, body["expected_revision"], body["specification"],
                persist=body.get("persist", False),
            ),
        )
        handler._json(200, result)
        return
    if operation == "planetary_kinematics":
        validate_request_fields(operation, body)
        assert body is not None
        result = core.observe_agent_operation(
            project_id, "Проверяет кинематику планетарной передачи",
            lambda: core.validate_body_planetary_kinematics(
                project_id,
                expected_revision=body["expected_revision"],
                sun_sketch_id=body["sun_sketch_id"],
                planet_sketch_ids=body["planet_sketch_ids"],
                ring_sketch_id=body["ring_sketch_id"],
                sun_speed_radians_per_second=body["sun_speed_radians_per_second"],
                ring_speed_radians_per_second=body["ring_speed_radians_per_second"],
                carrier_body_id=body.get("carrier_body_id"),
                persist=body.get("persist", False),
            ),
        )
        duration_ms = body.get("animate_duration_ms", 0)
        if type(duration_ms) is not int or not 0 <= duration_ms <= 60000:
            raise ValidationError("animate_duration_ms must be an integer from 0 to 60000.")
        if duration_ms and result.get("status") == "pass":
            core.events.publish("viewport.body-motion", project_id, {
                "revision": result["revision"],
                "duration_ms": duration_ms,
                "tracks": result["motion_tracks"],
            })
            result["animation"] = {"status": "started", "duration_ms": duration_ms}
        handler._json(200, result)
        return
    validate_request_fields(operation, body)
    assert body is not None
    if operation == "dimensions":
        result = core.observe_agent_operation(project_id, "Проверяет габариты тела", lambda: core.validate_body_dimensions(project_id, body["body_id"], body["expected_revision"], body["expected_dimensions"], body["tolerance"], persist=body.get("persist", False)))
    elif operation == "clearance":
        result = core.observe_agent_operation(project_id, "Измеряет зазор между телами", lambda progress: core.validate_body_clearance(project_id, body["first_body_id"], body["second_body_id"], body["expected_revision"], body["minimum_clearance"], body["tolerance"], progress=progress, persist=body.get("persist", False)), reports_progress=True)
    elif operation == "preservation":
        result = core.observe_agent_operation(project_id, "Проверяет сохранённый объём тела", lambda: core.validate_body_preservation(project_id, body["reference_body_id"], body["candidate_body_id"], body["expected_revision"], body["maximum_lost_volume"], body["tolerance"], persist=body.get("persist", False)))
    else:
        result = core.observe_agent_operation(project_id, "Измеряет минимальную толщину стенки", lambda progress: core.validate_body_thickness(project_id, body["body_id"], body["expected_revision"], body["minimum_thickness"], body["tolerance"], progress=progress, persist=body.get("persist", False), method=body.get("method", "sampled")), reports_progress=True)
    handler._json(200, result)


def _dispatch_viewport(handler: Any, core: CadyxCore, route: RouteMatch, parsed: SplitResult) -> None:
    name = route.spec.name
    project_id = route.params["project_id"]
    if name == "project_viewport":
        handler._json(200, core.render_project(project_id))
    elif name == "agent_viewport":
        handler._json(200, core.observe_agent_operation(project_id, "Получает 3D-сцену", lambda: core.render_project(project_id)))
    elif name == "project_viewport_png":
        if parse_qs(parsed.query).get("thumbnail") == ["1"]:
            encoded, revision = core.render_project_thumbnail_png(project_id)
            source = "server-thumbnail"
        else:
            encoded, source, revision = core.render_project_png_with_source(project_id)
        handler._png(200, encoded, extra_headers=(("X-CADYX-Viewport-Source", source), ("X-CADYX-Document-Revision", str(revision))))
    elif name == "agent_viewport_png":
        encoded, source, revision = core.observe_agent_operation(project_id, "Получает изображение модели", lambda: core.render_project_png_with_source(project_id))
        handler._png(200, encoded, extra_headers=(("X-CADYX-Viewport-Source", source), ("X-CADYX-Document-Revision", str(revision))))
    elif name in {"project_viewport_snapshot", "agent_viewport_snapshot"}:
        revision_text = handler.headers.get("X-CADYX-Document-Revision")
        try:
            revision = int(revision_text) if revision_text is not None else -1
        except ValueError as exc:
            raise ValidationError("X-CADYX-Document-Revision must be an integer.") from exc
        handler._json(200, core.publish_viewport_snapshot(project_id, revision, handler._read_viewport_png()))
    elif name == "agent_viewport_camera":
        body = _require_json(handler)
        allowed = {"yaw_delta", "pitch_delta", "zoom_factor", "duration_ms"}
        if not body.keys() <= allowed or "yaw_delta" not in body:
            raise ValidationError("Camera requests need yaw_delta and only supported camera fields.")
        result = core.rotate_view(project_id, yaw_delta=body["yaw_delta"], pitch_delta=body.get("pitch_delta", 0.0), zoom_factor=body.get("zoom_factor", 1.0), duration_ms=body.get("duration_ms", 1200))
        handler._json(200, {"camera": result})
    else:
        raise RuntimeError(f"Unhandled viewport route: {name}")


def _dispatch_static(handler: Any, core: CadyxCore, route: RouteMatch, parsed: SplitResult) -> None:
    del core, parsed
    assets = {
        "ui_index": ("index.html", "text/html; charset=utf-8"),
        "ui_scanner": ("scanner.html", "text/html; charset=utf-8"),
        "ui_favicon": ("favicon.svg", "image/svg+xml"),
        "asset_app": ("app.js", "text/javascript; charset=utf-8"),
        "asset_viewport": ("viewport.js", "text/javascript; charset=utf-8"),
        "asset_trace_draft": ("trace-draft.js", "text/javascript; charset=utf-8"),
        "asset_scanner_camera": ("scanner-camera.js", "text/javascript; charset=utf-8"),
        "asset_ply_parser": ("ply-parser.js", "text/javascript; charset=utf-8"),
        "asset_ply_preview": ("ply-preview.js", "text/javascript; charset=utf-8"),
        "asset_style": ("style.css", "text/css; charset=utf-8"),
    }
    filename, content_type = assets[route.spec.name]
    handler._static(filename, content_type)
