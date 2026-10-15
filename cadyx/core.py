"""Application Core shared by agent tools and the web interface."""

from __future__ import annotations

import copy
import math
import threading
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator
from uuid import uuid4

from .commands import CommandSystem
from .errors import CadyxError, ConflictError, NotFoundError, ValidationError
from .events import EventBroker
from .kernel import GeometryKernel, OCCTKernelAdapter
from .lease import EditLeaseManager
from .models import Document, OwnerType
from .rendering import render_scene, render_scene_png
from .scan import ScanProcessor
from .workspace import ProjectWorkspace, utc_now
from .sketches import closed_profile, open_path


class CadyxCore:
    def __init__(
        self,
        projects_dir: str,
        *,
        kernel: GeometryKernel | None = None,
        scan_processor: ScanProcessor | None = None,
        lease_timeout_seconds: float = 60.0,
        lease_clock: Callable[[], float] | None = None,
    ) -> None:
        self.workspace = ProjectWorkspace(projects_dir)
        self.kernel = kernel if kernel is not None else OCCTKernelAdapter()
        self.commands = CommandSystem(
            self.kernel,
            resolve_scan=self.workspace.resolve_folder_file,
            resolve_project_file=self.workspace.resolve_project_file,
            scan_processor=scan_processor,
            publish_scan_output=self.workspace.publish_scan_output,
            publish_cad_delivery=self.workspace.publish_cad_delivery,
        )
        if lease_clock is None:
            self.leases = EditLeaseManager(self.workspace._project_dir, timeout_seconds=lease_timeout_seconds)
        else:
            self.leases = EditLeaseManager(self.workspace._project_dir, timeout_seconds=lease_timeout_seconds, clock=lease_clock)
        self.events = EventBroker(dashboard_state_path=self.workspace.root / ".cadyx-agent-dashboards.json")
        self._workspace_selection_guard = threading.RLock()
        self._workspace_selection: dict[str, str] | None = None
        self._project_lifecycle_guard = threading.Lock()
        self._project_lifecycle_locks: dict[str, threading.RLock] = {}
        self._geometry_cache_guard = threading.RLock()
        self._validated_document_keys: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._document_validation_locks: dict[tuple[str, str], threading.Lock] = {}
        self._scene_cache: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
        self._png_cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()
        self._viewport_snapshot_guard = threading.RLock()
        self._viewport_snapshots: OrderedDict[str, tuple[int, bytes]] = OrderedDict()
        self._viewport_snapshot_bytes = 0

    @contextmanager
    def _project_guard(self, project_id: str) -> Iterator[None]:
        with self.leases.guard(project_id):
            yield

    @contextmanager
    def _project_lifecycle_lock(self, project_id: str) -> Iterator[None]:
        with self._project_lifecycle_guard:
            lock = self._project_lifecycle_locks.setdefault(project_id, threading.RLock())
        with lock:
            yield

    def create_project(self, name: str, *, select: bool = True) -> dict[str, Any]:
        project = self.workspace.create(name)
        summary = self._summary(project)
        lock = self.leases.snapshot(project["id"])
        if select:
            with self._workspace_selection_guard:
                self._workspace_selection = {"type": "project", "project_id": project["id"]}
                self.events.publish("project.created", project["id"], summary)
        self.events.publish("project.lock.changed", project["id"], lock)
        return {"project": summary, "lock": lock}

    def upload_project_step(self, project_id: str, filename: str, content: bytes) -> dict[str, Any]:
        with self._project_lifecycle_lock(project_id), self._project_guard(project_id):
            lock = self.leases.snapshot(project_id)
            activities, _ = self.events.activity_state_snapshot(project_id)
            if lock.get("state") != "FREE" or lock.get("recovery_required") or any(
                item.get("state") == "running" for item in activities
            ):
                raise ConflictError("STEP files can only be staged in a free project with no running operation.", code="PROJECT_BUSY")
            return self.workspace.publish_project_step(project_id, filename, content)

    def remove_staged_project_step(self, project_id: str, filename: str) -> dict[str, Any]:
        with self._project_lifecycle_lock(project_id), self._project_guard(project_id):
            lock = self.leases.snapshot(project_id)
            activities, _ = self.events.activity_state_snapshot(project_id)
            if lock.get("state") != "FREE" or lock.get("recovery_required") or any(
                item.get("state") == "running" for item in activities
            ):
                raise ConflictError("STEP references can only be removed from a free project with no running operation.", code="PROJECT_BUSY")
            project = self.workspace.load(project_id)
            document = Document.from_dict(project["document"])
            still_used = any(
                item.get("type") == "feature"
                and item.get("operation") == "import_step"
                and item.get("source", {}).get("file") == filename
                for item in document.objects
            )
            if still_used:
                raise ConflictError("The STEP file is still used by an imported body and cannot be removed.", code="FILE_IN_USE")
            return self.workspace.remove_project_step(project_id, filename)

    def rename_project(self, project_id: str, name: str) -> dict[str, Any]:
        with self._project_lifecycle_lock(project_id):
            lock = self.leases.snapshot(project_id)
            if lock.get("state") != "FREE":
                raise ConflictError("Project must be unlocked before it can be renamed.", code="PROJECT_LOCKED")
            project = self.workspace.rename(project_id, name)
            summary = self._summary(project)
            self.events.publish("project.renamed", project_id, summary)
            return {"project": summary, "lock": self.leases.snapshot(project_id)}

    def delete_project(self, project_id: str) -> dict[str, Any]:
        with self._project_lifecycle_lock(project_id):
            with self._project_guard(project_id):
                lock = self.leases.snapshot(project_id)
                activities, _ = self.events.activity_state_snapshot(project_id)
                if lock.get("state") != "FREE" or lock.get("recovery_required") or any(
                    item.get("state") == "running" for item in activities
                ):
                    raise ConflictError("Project must be free with no running operation before deletion.", code="PROJECT_BUSY")
                self.workspace.mark_project_deleting(project_id)
            cleanup_pending = self.workspace.delete_project(project_id)
        with self._workspace_selection_guard:
            if self._workspace_selection == {"type": "project", "project_id": project_id}:
                self._workspace_selection = None
                self.events.publish("workspace.selection.changed", None, {})
        self.events.publish("project.deleted", project_id, {
            "project_id": project_id, "cleanup_pending": cleanup_pending,
        })
        return {"project_id": project_id, "cleanup_pending": cleanup_pending}

    def list_projects(self) -> list[dict[str, Any]]:
        result = []
        group_by_project = self.workspace.project_group_assignments()
        for project in self.workspace.list():
            project_id = project["id"]
            result.append({
                "project": self._summary(project),
                "lock": self.leases.snapshot_for_listing(project_id),
                "group_id": group_by_project.get(project_id),
            })
        return result

    def list_workspace(self) -> dict[str, Any]:
        return {
            "projects": self.list_projects(),
            "folders": self.workspace.list_folder_projects(),
            "groups": self.workspace.list_project_groups(),
        }

    def create_project_group(self, name: str) -> dict[str, str]:
        return self.workspace.create_project_group(name)

    def assign_project_group(self, project_id: str, group_id: str | None) -> dict[str, Any]:
        with self._project_lifecycle_lock(project_id), self._project_guard(project_id):
            lock = self.leases.snapshot(project_id)
            activities, _ = self.events.activity_state_snapshot(project_id)
            if lock.get("state") != "FREE" or lock.get("recovery_required") or any(
                item.get("state") == "running" for item in activities
            ):
                raise ConflictError("A project can only be moved when it is free with no running operation.", code="PROJECT_BUSY")
            self.workspace.assign_project_group(project_id, group_id)
        return {"project_id": project_id, "group_id": group_id}

    def workspace_selection_snapshot(self) -> dict[str, str] | None:
        with self._workspace_selection_guard:
            return copy.deepcopy(self._workspace_selection)

    def select_workspace_resource(self, selection: dict[str, Any]) -> dict[str, str]:
        if not isinstance(selection, dict) or not isinstance(selection.get("type"), str):
            raise ValidationError("A workspace selection needs a resource type.")

        resource_type = selection["type"]
        if resource_type == "project":
            if set(selection) != {"type", "project_id"}:
                raise ValidationError("Project selection needs only type and project_id.")
            project_id = selection["project_id"]
            if not isinstance(project_id, str) or not project_id.strip():
                raise ValidationError("project_id must be non-empty text.")
            self.workspace.load(project_id)
            selected = {"type": "project", "project_id": project_id}
        elif resource_type == "folder":
            if set(selection) != {"type", "folder_name"}:
                raise ValidationError("Folder selection needs only type and folder_name.")
            folder_name = selection["folder_name"]
            if not isinstance(folder_name, str) or not folder_name.strip():
                raise ValidationError("folder_name must be non-empty text.")
            if not self.workspace.is_workspace_folder(folder_name):
                raise NotFoundError("Workspace folder was not found.")
            selected = {"type": "folder", "folder_name": folder_name}
        else:
            raise ValidationError("Workspace selection type must be project or folder.")

        with self._workspace_selection_guard:
            self._workspace_selection = copy.deepcopy(selected)
            self.events.publish("workspace.selection.changed", None, selected)
        return selected

    def agent_dashboard_snapshot(self, project_id: str) -> dict[str, Any]:
        self.workspace.load(project_id)
        return self.events.agent_dashboard_snapshot(project_id)

    def report_agent_dashboard(self, project_id: str, report: dict[str, Any]) -> dict[str, Any]:
        """Publish an agent-reported workflow estimate and task queue."""
        self.workspace.load(project_id)
        if not isinstance(report, dict) or set(report) != {"workflow", "tasks"}:
            raise ValidationError("Dashboard reports must contain workflow and tasks only.")

        workflow = report["workflow"]
        normalized_workflow = None
        if workflow is not None:
            if not isinstance(workflow, dict) or set(workflow) != {
                "title", "progress_percent", "eta_seconds", "phases",
            }:
                raise ValidationError("workflow must contain title, progress_percent, eta_seconds and phases.")
            title = workflow["title"]
            progress = workflow["progress_percent"]
            eta = workflow["eta_seconds"]
            phases = workflow["phases"]
            if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120:
                raise ValidationError("workflow.title must be non-empty text up to 120 characters.")
            if isinstance(progress, bool) or not isinstance(progress, (int, float)):
                raise ValidationError("workflow.progress_percent must be a finite number from 0 to 100.")
            try:
                progress = float(progress)
            except (OverflowError, ValueError) as exc:
                raise ValidationError("workflow.progress_percent must be a finite number from 0 to 100.") from exc
            if not math.isfinite(progress) or not 0 <= progress <= 100:
                raise ValidationError("workflow.progress_percent must be a finite number from 0 to 100.")
            if eta is not None and (type(eta) is not int or not 0 <= eta <= 31_536_000):
                raise ValidationError("workflow.eta_seconds must be null or an integer from 0 to 31536000.")
            if not isinstance(phases, list) or not phases or len(phases) > 24:
                raise ValidationError("workflow.phases must contain between 1 and 24 phases.")
            normalized_phases = []
            phase_ids = set()
            for phase in phases:
                if not isinstance(phase, dict) or set(phase) != {"id", "title", "state"}:
                    raise ValidationError("Each workflow phase must contain id, title and state.")
                phase_id, phase_title, phase_state = phase["id"], phase["title"], phase["state"]
                if not isinstance(phase_id, str) or not phase_id.strip() or len(phase_id.strip()) > 80:
                    raise ValidationError("Workflow phase ids must be non-empty text up to 80 characters.")
                phase_id = phase_id.strip()
                if phase_id in phase_ids:
                    raise ValidationError("Workflow phase ids must be unique.")
                phase_ids.add(phase_id)
                if not isinstance(phase_title, str) or not phase_title.strip() or len(phase_title.strip()) > 120:
                    raise ValidationError("Workflow phase titles must be non-empty text up to 120 characters.")
                if not isinstance(phase_state, str) or phase_state not in {"pending", "in_progress", "completed", "failed"}:
                    raise ValidationError("Workflow phase state is not supported.")
                normalized_phases.append({"id": phase_id, "title": phase_title.strip(), "state": phase_state})
            normalized_workflow = {
                "title": title.strip(),
                "progress_percent": float(progress),
                "eta_seconds": eta,
                "phases": normalized_phases,
            }

        tasks = report["tasks"]
        if not isinstance(tasks, list) or len(tasks) > 100:
            raise ValidationError("tasks must be an array of at most 100 items.")
        normalized_tasks = []
        task_ids = set()
        for task in tasks:
            if not isinstance(task, dict) or set(task) != {"id", "title", "state"}:
                raise ValidationError("Each task must contain id, title and state.")
            task_id, task_title, task_state = task["id"], task["title"], task["state"]
            if not isinstance(task_id, str) or not task_id.strip() or len(task_id.strip()) > 80:
                raise ValidationError("Task ids must be non-empty text up to 80 characters.")
            task_id = task_id.strip()
            if task_id in task_ids:
                raise ValidationError("Task ids must be unique.")
            task_ids.add(task_id)
            if not isinstance(task_title, str) or not task_title.strip() or len(task_title.strip()) > 180:
                raise ValidationError("Task titles must be non-empty text up to 180 characters.")
            if not isinstance(task_state, str) or task_state not in {"running", "pending", "completed", "failed"}:
                raise ValidationError("Task state is not supported.")
            normalized_tasks.append({"id": task_id, "title": task_title.strip(), "state": task_state})

        if normalized_workflow and normalized_workflow["progress_percent"] == 100 and (
            any(phase["state"] != "completed" for phase in normalized_workflow["phases"])
            or any(task["state"] != "completed" for task in normalized_tasks)
        ):
            raise ValidationError("100% progress requires every workflow phase and queued task to be completed.")
        if normalized_workflow is None and self.events.agent_dashboard_is_active(project_id):
            raise ValidationError("An active workflow cannot be cleared before it reaches verified 100% progress.")

        return self.events.publish_agent_dashboard(project_id, {
            "workflow": normalized_workflow,
            "tasks": normalized_tasks,
        })

    def inspect_project(self, project_id: str) -> dict[str, Any]:
        # project.json is atomically replaced, so read-only viewers can safely
        # inspect the last committed revision while an agent edits its next one.
        project = self.workspace.load(project_id)
        document = Document.from_dict(project["document"])
        self._validate_geometry_once(project_id, document)
        return {
            "project": self._summary(project),
            "lock": self.leases.snapshot_for_listing(project_id),
            "document": document.to_dict(),
            "history": copy.deepcopy(project["history"]),
        }

    def acquire_lease(self, project_id: str, owner_type: str, owner_id: str) -> dict[str, Any]:
        with self._project_lifecycle_lock(project_id), self._project_guard(project_id):
            self.workspace.load(project_id)
            lease = self.leases.acquire(project_id, owner_type, owner_id)
            snapshot = self.leases.snapshot(project_id)
            self.events.publish("project.lock.changed", project_id, snapshot)
            return lease.as_dict()

    def compare_bodies(self, project_id: str, original_id=None, modified_id=None) -> dict:
        explicit_selection = original_id is not None or modified_id is not None
        document = Document.from_dict(self.workspace.load(project_id)["document"])
        bodies = {item["id"]: item for item in document.objects if item.get("type") == "body"}
        if len(bodies) < 2:
            return {"revision": document.revision, "available": False}
        original_id = original_id or next(iter(bodies))
        modified_id = modified_id or next(reversed(bodies))
        if original_id not in bodies or modified_id not in bodies or original_id == modified_id:
            raise ValidationError("Comparison requires two different existing bodies.")
        features = {item["body_id"]: item for item in document.objects
                    if item.get("type") == "feature" and item.get("source_body_id")}
        chain, current, visited = [], modified_id, set()
        while current != original_id:
            if current in visited or current not in features:
                if not explicit_selection:
                    return {"revision": document.revision, "available": False}
                raise ValidationError("Comparison requires a source-body ancestry chain.")
            visited.add(current)
            feature = features[current]
            chain.append(feature)
            current = feature["source_body_id"]
        original = self.kernel.tessellate(bodies[original_id]["geometry"])
        positions = list(original["positions"])
        for feature in reversed(chain):
            if feature["operation"] != "transform_imported_brep":
                continue
            parameters = feature["parameters"]
            axis = parameters["rotation_axis"]
            length = math.hypot(*axis)
            a, b, c = [value / length for value in axis]
            angle = math.radians(parameters["angle_degrees"])
            cosine, sine = math.cos(angle), math.sin(angle)
            translation = parameters["translation"]
            for offset in range(0, len(positions), 3):
                x, y, z = positions[offset:offset + 3]
                dot = a*x + b*y + c*z
                cross = [b*z-c*y, c*x-a*z, a*y-b*x]
                positions[offset:offset + 3] = [
                    value*cosine + cross[index]*sine + axis_value*dot*(1-cosine) + translation[index]
                    for index, (value, axis_value) in enumerate(zip((x,y,z), (a,b,c)))
                ]
        modified = self.kernel.tessellate(bodies[modified_id]["geometry"])
        original_volume = bodies[original_id]["geometry"]["volume"]
        modified_volume = bodies[modified_id]["geometry"]["volume"]
        return {"available": True, "revision": document.revision,
                "original_body_id": original_id, "modified_body_id": modified_id,
                "original_volume": original_volume, "modified_volume": modified_volume,
                "volume_delta": modified_volume-original_volume,
                "mode": "aligned_overlay",
                "original": {**original, "positions": positions}, "modified": modified}

    def render_project(self, project_id: str) -> dict[str, Any]:
        _, scene = self._scene_for_project(project_id)
        return self._clone_scene(scene)

    def _scene_for_project(self, project_id: str) -> tuple[tuple[str, str], dict[str, Any]]:
        # Check the stat-only revision token before loading and validating the full document.
        key = (project_id, self.workspace.project_revision_token(project_id))
        with self._geometry_cache_guard:
            cached_scene = self._scene_cache.get(key)
            if cached_scene is not None:
                self._scene_cache.move_to_end(key)
                return key, cached_scene
        project = self.workspace.load(project_id)
        document = Document.from_dict(project["document"])
        self._validate_geometry_once(project_id, document)
        scene = render_scene(document, self.kernel)
        with self._geometry_cache_guard:
            cached_scene = self._scene_cache.get(key)
            if cached_scene is None:
                self._scene_cache[key] = scene
                self._scene_cache.move_to_end(key)
                while len(self._scene_cache) > 8:
                    self._scene_cache.popitem(last=False)
                cached_scene = scene
            else:
                self._scene_cache.move_to_end(key)
        return key, cached_scene

    def _render_project_png_server(self, project_id: str) -> bytes:
        key, scene = self._scene_for_project(project_id)
        with self._geometry_cache_guard:
            cached_png = self._png_cache.get(key)
            if cached_png is not None:
                self._png_cache.move_to_end(key)
                return cached_png
        encoded = render_scene_png(scene)
        with self._geometry_cache_guard:
            cached_png = self._png_cache.get(key)
            if cached_png is not None:
                self._png_cache.move_to_end(key)
                return cached_png
            self._png_cache[key] = encoded
            self._png_cache.move_to_end(key)
            while len(self._png_cache) > 8:
                self._png_cache.popitem(last=False)
        return encoded

    def publish_viewport_snapshot(self, project_id: str, revision: int, encoded: bytes) -> dict[str, int]:
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ValidationError("Viewport snapshot revision must be a non-negative integer.")
        if not isinstance(encoded, bytes) or len(encoded) < 24 or len(encoded) > 16 * 1024 * 1024:
            raise ValidationError("Viewport snapshot must be a PNG no larger than 16 MiB.")
        if encoded[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValidationError("Viewport snapshot must use PNG encoding.")
        width = int.from_bytes(encoded[16:20], "big")
        height = int.from_bytes(encoded[20:24], "big")
        if not width or not height or width > 4096 or height > 4096 or width * height > 16_777_216:
            raise ValidationError("Viewport snapshot dimensions exceed the supported limit.")

        _, scene = self._scene_for_project(project_id)
        if revision != scene["revision"]:
            raise ConflictError(
                "The browser viewport has not rendered the current project revision yet.",
                code="VIEWPORT_SNAPSHOT_STALE",
            )
        with self._viewport_snapshot_guard:
            previous = self._viewport_snapshots.pop(project_id, None)
            if previous is not None:
                self._viewport_snapshot_bytes -= len(previous[1])
            self._viewport_snapshots[project_id] = (revision, encoded)
            self._viewport_snapshot_bytes += len(encoded)
            while len(self._viewport_snapshots) > 8 or self._viewport_snapshot_bytes > 32 * 1024 * 1024:
                _, evicted = self._viewport_snapshots.popitem(last=False)
                self._viewport_snapshot_bytes -= len(evicted[1])
        return {"revision": revision, "width": width, "height": height}

    def render_project_png_with_source(self, project_id: str) -> tuple[bytes, str, int]:
        _, scene = self._scene_for_project(project_id)
        with self._viewport_snapshot_guard:
            snapshot = self._viewport_snapshots.get(project_id)
            if snapshot is not None:
                if snapshot[0] != scene["revision"]:
                    raise ConflictError(
                        "Waiting for the browser viewport to render the current project revision.",
                        code="VIEWPORT_SNAPSHOT_STALE",
                    )
                self._viewport_snapshots.move_to_end(project_id)
                return snapshot[1], "browser", scene["revision"]
        return self._render_project_png_server(project_id), "server-fallback", scene["revision"]

    def render_project_png(self, project_id: str) -> bytes:
        return self.render_project_png_with_source(project_id)[0]

    def inspect_body_topology(self, project_id: str, body_id: str) -> dict[str, Any]:
        project = self.workspace.load(project_id)
        document = Document.from_dict(project["document"])
        body = next((item for item in document.objects
                     if item.get("id") == body_id and item.get("type") == "body"), None)
        if body is None:
            raise ValidationError("Topology inspection needs an existing body_id.")
        topology = self.kernel.topology(body["geometry"])
        return {"body_id": body_id, **topology}

    def rotate_view(
        self, project_id: str, *, yaw_delta: float, pitch_delta: float = 0.0,
        zoom_factor: float = 1.0, duration_ms: int = 1200,
    ) -> dict[str, Any]:
        """Publish an ephemeral camera orbit for the selected read-only viewport."""
        activity = {
            "operation_id": str(uuid4()), "kind": "camera",
            "actor_type": OwnerType.AGENT.value, "action": "Поворачивает камеру",
            "command": None, "source": None, "state": "running",
            "phase": "starting", "steps": [], "result": None, "error": None,
        }
        self._publish_activity(project_id, activity)
        try:
            self.workspace.load(project_id)
            for name, value, limit in (("yaw_delta", yaw_delta, math.tau),
                                       ("pitch_delta", pitch_delta, 0.5),
                                       ("zoom_factor", zoom_factor, 2.0)):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValidationError(f"{name} must be a finite number.")
                try:
                    value = float(value)
                except OverflowError as exc:
                    raise ValidationError(f"{name} is outside the supported range.") from exc
                if not math.isfinite(value) or abs(value) > limit:
                    raise ValidationError(f"{name} is outside the supported range.")
            if zoom_factor < 0.5:
                raise ValidationError("zoom_factor must be at least 0.5.")
            if type(duration_ms) is not int or not 0 <= duration_ms <= 5000:
                raise ValidationError("duration_ms must be an integer from 0 to 5000.")
            event = {
                "yaw_delta": float(yaw_delta), "pitch_delta": float(pitch_delta),
                "zoom_factor": float(zoom_factor), "duration_ms": duration_ms,
            }
            activity["phase"] = "animate_camera"
            self._publish_activity(project_id, activity)
            self.events.publish("viewport.camera", project_id, event)
            activity.update({"state": "completed", "phase": "completed", "result": event})
            self._publish_activity(project_id, activity)
            return event
        except Exception as exc:
            activity.update({"state": "failed", "error": str(exc)})
            self._publish_activity(project_id, activity)
            raise

    def observe_agent_operation(
        self,
        project_id: str,
        action: str,
        operation: Callable[[], Any],
    ) -> Any:
        """Expose an agent's read-only model inspection as a visual activity."""
        self.workspace.load(project_id)
        activity = {
            "operation_id": str(uuid4()),
            "kind": "observation",
            "actor_type": OwnerType.AGENT.value,
            "action": action,
            "command": None,
            "source": None,
            "state": "running",
            "phase": "starting",
            "steps": [],
            "result": None,
            "error": None,
        }
        self._publish_activity(project_id, activity)
        activity["phase"] = "observe"
        self._publish_activity(project_id, activity)
        try:
            result = operation()
        except Exception as exc:
            activity.update({"state": "failed", "error": str(exc)})
            self._publish_activity(project_id, activity)
            raise
        activity.update({"state": "completed", "phase": "completed"})
        self._publish_activity(project_id, activity)
        return result

    def heartbeat(self, project_id: str, lease_id: str) -> dict[str, Any]:
        with self._project_guard(project_id):
            self.workspace.load(project_id)
            lease = self.leases.heartbeat(project_id, lease_id)
            return lease.as_dict()

    def release_lease(self, project_id: str, lease_id: str) -> dict[str, Any]:
        with self._project_guard(project_id):
            lease = self.leases.release(project_id, lease_id)
            snapshot = self.leases.snapshot(project_id)
            self.events.publish("project.lock.changed", project_id, snapshot)
            return {"released": True, "lease": lease.as_dict(), "lock": snapshot}

    def recover_expired_lease(self, project_id: str) -> dict[str, Any]:
        """Release only after reloading and validating the last atomic project save."""
        with self._project_guard(project_id):
            project = self.workspace.load(project_id)
            document = Document.from_dict(project["document"])
            self._validate_geometry(document)
            lease = self.leases.release_expired(project_id)
            snapshot = self.leases.snapshot(project_id)
            if lease.owner_type is not OwnerType.AGENT:
                self.events.publish(
                    "project.lock.changed",
                    project_id,
                    {**snapshot, "recovered_owner_type": lease.owner_type.value, "recovered_owner_id": lease.owner_id},
                )
            return {"recovered": True, "lock": snapshot}

    def stop_agent(self, project_id: str, lease_id: str) -> dict[str, Any]:
        # Commit the fence before waiting for any in-flight command's guard.
        self.leases.request_agent_stop(project_id, lease_id)
        with self._project_guard(project_id):
            lease = self.leases.get(project_id)
            if lease is None or lease.lease_id != lease_id or lease.owner_type is not OwnerType.AGENT:
                raise ConflictError("The supplied lease is not the current agent lease.", code="PROJECT_LOCKED")
            detail = self.inspect_project(project_id)
            # Agent stop is a safety boundary: validate the saved snapshot here
            # even when the read-only inspection cache has a matching revision.
            self._validate_geometry(Document.from_dict(detail["document"]))
            self.leases.release_stopped_agent(project_id, lease_id)
            detail["lock"] = self.leases.snapshot(project_id)
            self.events.publish("project.lock.changed", project_id, detail["lock"])
            return {"stopped": True, **detail}

    @staticmethod
    def _check_revision(document: Document, expected_revision: Any) -> None:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValidationError("expected_revision must be a non-negative integer.")
        if document.revision != expected_revision:
            raise ConflictError("Saved revision changed; inspect the project before continuing.", code="REVISION_CONFLICT")

    def resume_agent(self, project_id: str, owner_id: str, expected_revision: int) -> dict[str, Any]:
        with self._project_guard(project_id):
            detail = self.inspect_project(project_id)
            # Resuming grants a new editing lease, so recheck geometry at this
            # explicit state transition rather than relying on viewer caching.
            self._validate_geometry(Document.from_dict(detail["document"]))
            self._check_revision(Document.from_dict(detail["document"]), expected_revision)
            lease = self.leases.acquire(project_id, "agent", owner_id)
            self.leases.mark_resumed(project_id, lease.lease_id)
            detail["lock"] = self.leases.snapshot(project_id)
            self.events.publish("project.lock.changed", project_id, detail["lock"])
            return {"resumed": True, "lease": lease.as_dict(), **detail}

    def execute_command(
        self,
        project_id: str,
        lease_id: str | None,
        command: dict[str, Any],
        *,
        expected_owner_type: OwnerType | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        with self._project_guard(project_id):
            command_name = command.get("type") if isinstance(command, dict) else None
            lease = self.leases.require(project_id, lease_id)
            if expected_owner_type is not None and lease.owner_type is not expected_owner_type:
                raise ConflictError(
                    f"This API requires a {expected_owner_type.value} edit lease.",
                    code="PROJECT_LOCKED",
                )
            project = self.workspace.load(project_id)
            if command_name == "export_brep" and expected_revision is None:
                raise ValidationError("export_brep requires expected_revision to bind files to a saved document revision.")
            if lease.owner_type is OwnerType.AGENT:
                revision_required = self.leases.check_agent_command(project_id, lease.lease_id)
                if revision_required or expected_revision is not None:
                    self._check_revision(Document.from_dict(project["document"]), expected_revision)
            elif expected_revision is not None:
                self._check_revision(Document.from_dict(project["document"]), expected_revision)
            if command_name in {"resize_imported_thread", "export_brep"}:
                # OCCT operations on complex imported NURBS can outlast the
                # normal lease window; the project transaction prevents
                # concurrent heartbeats, so grant them a wider expiry.
                self.leases.extend_timeout(project_id, lease.lease_id, 300.0)
            operation_id = str(uuid4())
            source = self._scan_source(command, project["document"])
            activity = {
                "operation_id": operation_id,
                "actor_type": lease.owner_type.value,
                "actor_id": lease.owner_id,
                "action": self._activity_action(command_name),
                "command": command_name,
                "source": source,
                "state": "running",
                "phase": "starting",
                "steps": [],
                "result": None,
                "error": None,
            }
            self._publish_activity(project_id, activity)
            self.events.publish("command.started", project_id, {
                "command": command_name,
                "actor_id": lease.owner_id,
                "actor_type": lease.owner_type.value,
                "operation_id": operation_id,
            })

            def report_progress(update: dict[str, Any]) -> None:
                activity.update(update)
                self._publish_activity(project_id, activity)

            try:
                document = Document.from_dict(project["document"])
                result = self.commands.execute(document, command, progress=report_progress, project_id=project_id)
                activity["result"] = copy.deepcopy(result)
                activity["phase"] = "validate_document"
                self._publish_activity(project_id, activity)
                document.revision += 1
                document.validate()
                self._validate_geometry(document)
                history_entry = {
                    "actor_type": lease.owner_type.value,
                    "actor_id": lease.owner_id,
                    "command": copy.deepcopy(command),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "result": copy.deepcopy(result),
                    "revision": document.revision,
                }
                next_project = copy.deepcopy(project)
                next_project["document"] = document.to_dict()
                next_project["history"].append(history_entry)
                next_project["modified_at"] = utc_now()
                activity["phase"] = "save_document"
                self._publish_activity(project_id, activity)
                self.workspace.save(next_project)
                staged_source = result.get("source_file_to_remove")
                if staged_source:
                    try:
                        self.workspace.remove_project_step(project_id, staged_source)
                        result["source_file_removed"] = True
                    except CadyxError as cleanup_error:
                        result["source_file_removed"] = False
                        result["source_file_cleanup_error"] = str(cleanup_error)
                self._remember_geometry_validation(project_id, document)
            except Exception as exc:
                activity.update({"state": "failed", "error": str(exc)})
                self._publish_activity(project_id, activity)
                self.events.publish(
                    "command.failed",
                    project_id,
                    {"command": command_name, "actor_id": lease.owner_id, "message": str(exc)},
                )
                raise

            activity.update({"state": "completed", "phase": "completed", "result": copy.deepcopy(result)})
            self._publish_activity(project_id, activity)
            self.events.publish("document.changed", project_id, {"revision": document.revision})
            self.events.publish("command.completed", project_id, history_entry)
            return {"document": document.to_dict(), "history_entry": history_entry}

    def _publish_activity(self, project_id: str, activity: dict[str, Any]) -> None:
        steps = activity.setdefault("steps", [])
        phase = activity.get("phase")
        state = activity.get("state")
        if state == "completed":
            for step in steps:
                step["state"] = "completed"
        elif isinstance(phase, str) and phase != "completed":
            if steps and steps[-1]["phase"] == phase:
                steps[-1]["state"] = "failed" if state == "failed" else "active"
            else:
                if steps:
                    steps[-1]["state"] = "completed"
                steps.append({"phase": phase, "state": "failed" if state == "failed" else "active"})
        elif state == "failed" and steps:
            steps[-1]["state"] = "failed"
        activity["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.events.publish("agent.activity", project_id, copy.deepcopy(activity))

    @staticmethod
    def _activity_action(command_name: Any) -> str:
        return {
            "create_box": "Строит модель",
            "create_sketch": "Создаёт эскиз",
            "add_sketch_element": "Рисует элемент эскиза",
            "add_sketch_polyline": "Рисует ломаную",
            "add_sketch_involute_gear": "Строит эвольвентный профиль",
            "add_sketch_rectangle": "Рисует прямоугольник",
            "add_sketch_rectangle_two_corners": "Рисует прямоугольник по двум углам",
            "add_sketch_rectangle_center": "Рисует прямоугольник от центра",
            "add_sketch_rectangle_three_points": "Рисует прямоугольник по трём точкам",
            "add_sketch_rotated_rectangle": "Рисует повёрнутый прямоугольник",
            "add_sketch_parallelogram": "Рисует параллелограмм",
            "add_sketch_regular_polygon": "Рисует правильный многоугольник",
            "add_sketch_circle": "Рисует окружность",
            "add_sketch_circle_diameter": "Рисует окружность по диаметру",
            "add_sketch_circle_two_points": "Рисует окружность по двум точкам",
            "add_sketch_circle_three_points": "Рисует окружность по трём точкам",
            "add_sketch_circle_tangent_to_line": "Строит окружность касательно к линии",
            "add_sketch_circle_tangent_to_two_lines": "Строит окружность по двум касательным",
            "add_sketch_circle_tangent_to_three_lines": "Строит окружность по трём касательным",
            "add_sketch_arc_center": "Строит дугу по центру",
            "add_sketch_arc_three_points": "Строит дугу по трём точкам",
            "add_sketch_arc_start_center_end": "Строит дугу по началу, центру и концу",
            "add_sketch_arc_radius": "Строит дугу по началу, концу и радиусу",
            "add_sketch_arc_tangent": "Строит касательную дугу",
            "add_sketch_rounded_rectangle": "Строит скруглённый прямоугольник",
            "mirror_sketch_elements": "Зеркально копирует геометрию эскиза",
            "pattern_sketch_elements": "Создаёт массив геометрии эскиза",
            "add_sketch_constraint": "Проверяет ограничение эскиза",
            "update_sketch_elements": "Меняет геометрию эскиза",
            "extrude_sketch": "Выдавливает профиль",
            "revolve_sketch": "Вращает профиль",
            "sweep_sketch": "Перемещает профиль по траектории",
            "fillet_edges": "Скругляет рёбра",
            "replace_sketch_profile": "Перерисовывает профиль",
            "replace_sketch_path": "Рисует траекторию",
            "fuse_bodies": "Объединяет тела",
            "cut_bodies": "Вырезает материал",
            "remove_body": "Удаляет тело из модели",
            "set_parameter": "Меняет параметры модели",
            "create_metric_thread": "Строит метрическую резьбу",
            "resize_imported_thread": "Увеличивает диаметр резьбы",
            "transform_imported_brep": "Выравнивает импортированную модель",
            "create_sketch_from_section": "Извлекает сечение STEP в эскиз",
            "loft_sketches": "Строит loft по эскизам сечений",
            "import_step": "Импортирует точную STEP-геометрию",
            "export_brep": "Проверяет и экспортирует модель САПР",
            "import_scan": "Обрабатывает скан",
            "set_scan_parameter": "Пересобирает скан",
            "remove_sketch_object": "Удаляет объект эскиза",
        }.get(command_name, "Выполняет операцию")

    @staticmethod
    def _scan_source(command: Any, saved_document: Any) -> dict[str, str] | None:
        if not isinstance(command, dict):
            return None
        if command.get("type") == "import_scan":
            folder, filename = command.get("folder"), command.get("file")
        elif command.get("type") == "set_scan_parameter":
            feature_id = command.get("feature_id")
            objects = saved_document.get("objects", []) if isinstance(saved_document, dict) else []
            feature = next((item for item in objects if isinstance(item, dict) and
                            item.get("id") == feature_id and item.get("operation") == "reconstruct_scan"), None)
            source = feature.get("source") if feature else None
            folder = source.get("folder") if isinstance(source, dict) else None
            filename = source.get("file") if isinstance(source, dict) else None
        else:
            return None
        if isinstance(folder, str) and isinstance(filename, str):
            return {"folder": folder, "file": filename}
        return None

    def _validate_geometry(self, document: Document) -> None:
        by_id = {item["id"]: item for item in document.objects}
        consumed_bodies = {
            source_id
            for item in document.objects
            if item.get("type") == "feature"
            for source_id in ((item.get("base_body_id"), item.get("tool_body_id"))
                              if item.get("operation") in {"fuse_bodies", "cut_bodies"}
                              else (item.get("source_body_id"),)
                              if item.get("operation") in {
                                  "fillet_edges", "resize_imported_thread", "transform_imported_brep",
                              } else ())
        }
        for item in document.objects:
            geometry = item.get("geometry")
            # Feature records duplicate their body's geometry, and consumed
            # source bodies are embedded in the current body's geometry graph.
            # Validate each live B-rep once instead of rebuilding every prefix
            # of a long revolve/fuse/fillet chain on every read-only refresh.
            if geometry is not None and item.get("type") == "body" and item["id"] not in consumed_bodies:
                self.kernel.validate(geometry)
            if item.get("type") != "feature":
                continue
            if item.get("operation") in {"fuse_bodies", "cut_bodies"}:
                feature_geometry = item.get("geometry")
                body = by_id.get(item.get("body_id"))
                base = by_id.get(item.get("base_body_id"))
                tool = by_id.get(item.get("tool_body_id"))
                if any(value is None or value.get("type") != "body" for value in (body, base, tool)):
                    raise ValidationError("Boolean feature refers to a missing body.")
                if feature_geometry != body.get("geometry"):
                    raise ValidationError("Boolean feature geometry must match its body.")
                expected_kind = {"fused_profiles", "fused_solids"} if item.get("operation") == "fuse_bodies" else {"cut_solids"}
                if (not isinstance(feature_geometry, dict)
                        or feature_geometry.get("kind") not in expected_kind
                        or feature_geometry.get("base") != base.get("geometry")
                        or feature_geometry.get("tool") != tool.get("geometry")):
                    raise ValidationError("Boolean feature geometry is out of date with its source bodies.")
                continue
            if item.get("operation") == "fillet_edges":
                body = by_id.get(item.get("body_id"))
                source = by_id.get(item.get("source_body_id"))
                feature_geometry = item.get("geometry")
                if (body is None or source is None or feature_geometry != body.get("geometry")
                        or not isinstance(feature_geometry, dict)
                        or feature_geometry.get("kind") != "filleted_solid"
                        or feature_geometry.get("base") != source.get("geometry")
                        or feature_geometry.get("edge_indices") != item.get("edge_indices")
                        or feature_geometry.get("radius") != item.get("parameters", {}).get("radius")):
                    raise ValidationError("Fillet geometry is out of date with its source body and parameters.")
                continue
            if item.get("operation") in {"resize_imported_thread", "transform_imported_brep"}:
                body = by_id.get(item.get("body_id"))
                source = by_id.get(item.get("source_body_id"))
                feature_geometry = item.get("geometry")
                parameters = item.get("parameters")
                operation = item.get("operation")
                expected_parameters = (
                    {"x_start", "x_end", "axis_origin_y", "axis_origin_z", "major_diameter", "diameter_delta"}
                    if operation == "resize_imported_thread"
                    else {"rotation_axis", "angle_degrees", "translation"}
                )
                if (body is None or body.get("type") != "body"
                        or source is None or source.get("type") != "body"
                        or feature_geometry != body.get("geometry")
                        or not isinstance(feature_geometry, dict)
                        or feature_geometry.get("kind") != "imported_brep"
                        or feature_geometry.get("brep_valid") is not True
                        or not isinstance(parameters, dict) or set(parameters) != expected_parameters):
                    raise ValidationError("Imported B-rep feature is out of date or invalid.")
                if operation == "resize_imported_thread":
                    dimensions = [parameters[key] for key in (
                        "x_start", "x_end", "axis_origin_y", "axis_origin_z",
                        "major_diameter", "diameter_delta",
                    )]
                    if (any(isinstance(value, bool) or not isinstance(value, (int, float))
                            or not math.isfinite(value) for value in dimensions)
                            or parameters["x_end"] <= parameters["x_start"]
                            or parameters["major_diameter"] <= 0 or parameters["diameter_delta"] <= 0):
                        raise ValidationError("Imported thread feature parameters are invalid.")
                else:
                    axis, angle, translation = (
                        parameters.get("rotation_axis"), parameters.get("angle_degrees"),
                        parameters.get("translation"),
                    )
                    if (not isinstance(axis, list) or len(axis) != 3
                            or not isinstance(translation, list) or len(translation) != 3
                            or any(isinstance(value, bool) or not isinstance(value, (int, float))
                                   or not math.isfinite(value) for value in [*axis, *translation])
                            or isinstance(angle, bool) or not isinstance(angle, (int, float))
                            or not math.isfinite(angle) or abs(angle) > 360
                            or math.hypot(*axis) <= 1e-12):
                        raise ValidationError("Imported B-rep transform parameters are invalid.")
                continue
            if item.get("operation") == "create_ruled_shell":
                body = by_id.get(item.get("body_id"))
                feature_geometry = item.get("geometry")
                parameters = item.get("parameters")
                if (body is None or body.get("type") != "body"
                        or feature_geometry != body.get("geometry")
                        or not isinstance(parameters, dict)
                        or set(parameters) != {"patches", "sewing_tolerance"}):
                    raise ValidationError("Ruled shell feature geometry must match its body and patch parameters.")
                expected = self.kernel.create_ruled_shell(
                    parameters["patches"], sewing_tolerance=parameters["sewing_tolerance"],
                    compute_volume=False,
                )
                expected.pop("volume", None)
                comparable_geometry = {key: value for key, value in feature_geometry.items() if key != "volume"}
                if expected != comparable_geometry:
                    raise ValidationError("Ruled shell geometry is out of date with its saved rail patches.")
                continue
            if item.get("operation") == "loft_sketches":
                body = by_id.get(item.get("body_id"))
                feature_geometry = item.get("geometry")
                source_sketch_ids = item.get("source_sketch_ids")
                parameters = item.get("parameters")
                if (body is None or body.get("type") != "body"
                        or feature_geometry != body.get("geometry")
                        or not isinstance(source_sketch_ids, list) or not isinstance(parameters, dict)
                        or parameters.get("section_sketch_ids") != source_sketch_ids):
                    raise ValidationError("Loft feature geometry must match its body and ordered source sketches.")
                groups = parameters.get("section_sketch_groups", [[value] for value in source_sketch_ids])
                if (not isinstance(groups, list) or not groups
                        or [value for group in groups for value in group] != source_sketch_ids):
                    raise ValidationError("Loft feature contour groups do not match their source sketches.")
                sections, offsets = [], []
                for group in groups:
                    group_profiles, group_offsets = [], []
                    for sketch_id in group:
                        sketch = by_id.get(sketch_id)
                        if (sketch is None or sketch.get("type") != "sketch" or sketch.get("plane") != "XZ"
                                or sketch.get("section_normal_axis") != "Y"):
                            raise ValidationError("Loft feature source sketches must be Y-normal XZ sections.")
                        _, profile = closed_profile(document.objects, sketch_id)
                        group_profiles.append(profile)
                        group_offsets.append(sketch.get("section_offset"))
                    if not group_offsets or any(value != group_offsets[0] for value in group_offsets):
                        raise ValidationError("Loft feature contour group has inconsistent section offsets.")
                    sections.append(group_profiles)
                    offsets.append(group_offsets[0])
                if any(len(group) != 1 for group in sections):
                    expected = self.kernel.create_multicontour_loft(
                        sections, section_offsets=offsets, compute_volume=False,
                    )
                else:
                    expected = self.kernel.create_lofted_profiles(
                        [group[0] for group in sections], section_offsets=offsets,
                        alignment=feature_geometry.get("profile_alignment", "independent-start-v1"),
                    )
                    if "profile_alignment" not in feature_geometry:
                        expected.pop("profile_alignment", None)
                if expected.get("kind") == "multicontour_loft":
                    expected.pop("volume", None)
                    comparable_geometry = copy.deepcopy(feature_geometry)
                    comparable_geometry.pop("volume", None)
                    for branch in expected["branches"] + comparable_geometry.get("branches", []):
                        branch.pop("volume", None)
                else:
                    comparable_geometry = feature_geometry
                if expected != comparable_geometry:
                    raise ValidationError("Loft feature geometry is out of date with its section sketches.")
                continue
            if item.get("operation") == "import_step":
                body = by_id.get(item.get("body_id"))
                geometry = body.get("geometry") if body is not None and body.get("type") == "body" else None
                source_metrics = item.get("source_metrics")
                if (not isinstance(geometry, dict) or geometry.get("kind") != "imported_brep"
                        or not isinstance(source_metrics, dict)
                        or set(source_metrics) != {
                            "volume", "surface_area", "bounds", "topology", "brep_valid", "repaired",
                        }):
                    raise ValidationError("STEP import feature is missing its saved source geometry metrics.")
                actual_metrics = self.kernel.metrics(geometry)
                bounds_match = all(
                    math.isclose(actual_metrics["bounds"][side][index], source_metrics["bounds"][side][index],
                                 rel_tol=1e-12, abs_tol=1e-12)
                    for side in ("min", "max") for index in range(3)
                )
                if (not bounds_match
                        or actual_metrics.get("topology") != source_metrics.get("topology")
                        or actual_metrics.get("brep_valid") != source_metrics.get("brep_valid")
                        or not math.isclose(actual_metrics.get("volume", float("nan")),
                                            source_metrics.get("volume", float("nan")),
                                            rel_tol=1e-12, abs_tol=1e-12)
                        or not math.isclose(actual_metrics.get("surface_area", float("nan")),
                                            source_metrics.get("surface_area", float("nan")),
                                            rel_tol=1e-12, abs_tol=1e-12)):
                    raise ValidationError("STEP import B-rep does not match the geometry read from the source file.")
                continue
            if item.get("operation") == "metric_thread":
                body = by_id.get(item.get("body_id"))
                geometry = item.get("geometry")
                parameters = item.get("parameters")
                if (body is None or body.get("type") != "body" or geometry != body.get("geometry")
                        or not isinstance(geometry, dict) or geometry.get("kind") != "metric_thread"
                        or not isinstance(parameters, dict)
                        or set(parameters) != {"major_diameter", "pitch", "length", "handedness"}
                        or any(geometry.get(name) != parameters.get(name)
                               for name in ("major_diameter", "pitch", "length", "handedness"))):
                    raise ValidationError("Metric thread geometry is out of date with its feature parameters.")
                continue
            if item.get("operation") == "sweep_sketch":
                body = by_id.get(item.get("body_id"))
                feature_geometry = item.get("geometry")
                source_id = item.get("source_sketch_id")
                if body is None or feature_geometry != body.get("geometry"):
                    raise ValidationError("Sweep feature geometry must match its body.")
                plane, path = open_path(document.objects, source_id)
                if (feature_geometry.get("kind") != "swept_pipe" or feature_geometry.get("plane") != plane
                        or feature_geometry.get("path") != path
                        or feature_geometry.get("radius") != item.get("parameters", {}).get("radius")):
                    raise ValidationError("Sweep feature geometry is out of date with its source sketch.")
                continue
            if item.get("operation") not in {"extrude_sketch", "revolve_sketch"}:
                continue
            feature_geometry = item.get("geometry")
            body = by_id.get(item.get("body_id"))
            source_id = item.get("source_sketch_id")
            if body is None or feature_geometry != body.get("geometry"):
                raise ValidationError("Profile feature geometry must match its body.")
            plane, profile = closed_profile(document.objects, source_id)
            operation = item["operation"]
            parameters = item.get("parameters")
            if not isinstance(feature_geometry, dict):
                raise ValidationError("Profile feature geometry is missing.")
            expected_kind = "extruded_profile" if operation == "extrude_sketch" else "revolved_profile"
            if not isinstance(parameters, dict):
                raise ValidationError("Profile feature parameters are invalid.")
            if feature_geometry.get("kind") != expected_kind or feature_geometry.get("plane") != plane or feature_geometry.get("profile") != profile:
                raise ValidationError("Profile feature geometry is out of date with its source sketch.")
            parameter_name = "depth" if operation == "extrude_sketch" else "angle_degrees"
            if feature_geometry.get(parameter_name) != parameters.get(parameter_name):
                raise ValidationError("Profile feature geometry is out of date with its parameters.")
            if feature_geometry.get("centered", False) != parameters.get("centered", False):
                raise ValidationError("Profile feature centered-extrusion state is out of date.")

    @staticmethod
    def _clone_scene(scene: dict[str, Any]) -> dict[str, Any]:
        clone = scene.copy()
        bounds = scene.get("bounds")
        clone["bounds"] = None if bounds is None else {
            "min": list(bounds["min"]),
            "max": list(bounds["max"]),
        }
        objects = []
        for item in scene.get("objects", []):
            copied = item.copy()
            for field in ("positions", "triangles", "lines", "points"):
                copied[field] = list(item[field])
            objects.append(copied)
        clone["objects"] = objects
        return clone

    @staticmethod
    def _document_cache_key(project_id: str, document: Document | dict[str, Any]) -> tuple[str, str]:
        # The revision changes atomically with every accepted document edit.
        # Use it instead of serializing and hashing large scan meshes on reads.
        revision = document.get("revision", 0) if isinstance(document, dict) else document.revision
        return project_id, f"{type(revision).__name__}:{revision!r}"

    def _remember_geometry_validation(self, project_id: str, document: Document) -> None:
        key = self._document_cache_key(project_id, document)
        with self._geometry_cache_guard:
            self._validated_document_keys[key] = None
            self._validated_document_keys.move_to_end(key)
            while len(self._validated_document_keys) > 32:
                self._validated_document_keys.popitem(last=False)

    def _validate_geometry_once(self, project_id: str, document: Document) -> None:
        key = self._document_cache_key(project_id, document)
        with self._geometry_cache_guard:
            if key in self._validated_document_keys:
                self._validated_document_keys.move_to_end(key)
                return
            validation_lock = self._document_validation_locks.setdefault(key, threading.Lock())
        try:
            with validation_lock:
                with self._geometry_cache_guard:
                    if key in self._validated_document_keys:
                        self._validated_document_keys.move_to_end(key)
                        return
                self._validate_geometry(document)
                self._remember_geometry_validation(project_id, document)
        finally:
            with self._geometry_cache_guard:
                if self._document_validation_locks.get(key) is validation_lock:
                    self._document_validation_locks.pop(key, None)

    @staticmethod
    def _summary(project: dict[str, Any]) -> dict[str, Any]:
        return {
            key: project[key]
            for key in ("id", "format", "version", "name", "created_at", "modified_at")
        }
