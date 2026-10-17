"""Focused orchestration collaborators behind CadyxCore."""
from __future__ import annotations

import copy
import math
import threading
from collections import OrderedDict
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Iterator, Literal
from uuid import uuid4

from .delivery import geometry_sha256
from .errors import CadyxError, ConflictError, NotFoundError, ValidationError
from .mesh_geometry import mesh_bounds
from .models import Document, OwnerType
from .rendering import render_scene, render_scene_png
from .runtime_cache import RuntimeCachePolicy, remember_bounded_bytes, trim_lru_entries
from .workspace import utc_now

if TYPE_CHECKING:
    from .core import CadyxCore


@dataclass(frozen=True)
class _ProjectAvailability:
    lock: dict[str, Any]
    running_operation: bool
    active_workflow: bool

    @property
    def lock_state(self) -> str | None:
        state = self.lock.get("state")
        return state if isinstance(state, str) else None

    @property
    def is_free(self) -> bool:
        return self.lock_state == "FREE" and self.lock.get("recovery_required") is False and not self.running_operation

    def permits(self, policy: Literal["read","write","file_management","selection","destructive"], *, valid_lease: bool=False, sole_active_workflow_target: bool=False) -> bool:
        if policy == "read": return True
        if policy == "write":
            if valid_lease:
                return self.lock_state in {"AI_LOCKED","HUMAN_LOCKED","SYSTEM_LOCKED"} and not self.running_operation
            return self.is_free
        if policy in {"file_management","destructive"}: return self.is_free
        if policy == "selection": return self.is_free and (not self.active_workflow or sole_active_workflow_target)
        raise ValueError(f"Unknown project availability policy: {policy}")


class _CoreService:
    _local_names: frozenset[str] = frozenset()
    def __init__(self, core: CadyxCore) -> None:
        object.__setattr__(self, "_core", core)
    def __getattr__(self, name: str) -> Any:
        return getattr(self._core, name)
    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_core" or name in self._local_names:
            object.__setattr__(self, name, value)
        else:
            setattr(self._core, name, value)


class ProjectLifecycleService(_CoreService):
    """Own lifecycle locks, availability policy, and workspace selection."""
    _local_names = frozenset({"_workspace_selection_guard","_workspace_selection","_project_lifecycle_guard","_project_lifecycle_locks"})
    def __init__(self, core: CadyxCore) -> None:
        super().__init__(core)
        self._workspace_selection_guard = threading.RLock()
        self._workspace_selection: dict[str,str] | None = None
        self._project_lifecycle_guard = threading.Lock()
        self._project_lifecycle_locks: dict[str,threading.RLock] = {}

    def _project_availability(
        self,
        project_id: str,
        *,
        nonblocking: bool = False,
    ) -> _ProjectAvailability:
        lock = (
            self.leases.snapshot_for_listing(project_id)
            if nonblocking
            else self.leases.snapshot(project_id)
        )
        activities, _ = self.events.activity_state_snapshot(project_id)
        return _ProjectAvailability(
            lock=lock,
            running_operation=any(item.get("state") == "running" for item in activities),
            active_workflow=self.events.agent_dashboard_is_active(project_id),
        )

    def _require_project_policy(
        self,
        project_id: str,
        policy: Literal["read", "write", "file_management", "selection", "destructive"],
        message: str,
        *,
        code: str = "PROJECT_BUSY",
        valid_lease: bool = False,
    ) -> _ProjectAvailability:
        availability = self._project_availability(project_id)
        if not availability.permits(policy, valid_lease=valid_lease):
            raise ConflictError(message, code=code)
        return availability

    @contextmanager
    def _selection_project_locks(
        self,
        current_selection: dict[str, str] | None,
        target_project_id: str | None,
    ) -> Iterator[None]:
        project_ids = {
            selection["project_id"]
            for selection in (current_selection,)
            if selection is not None and selection.get("type") == "project"
        }
        if target_project_id is not None:
            project_ids.add(target_project_id)
        with ExitStack() as stack:
            for project_id in sorted(project_ids):
                stack.enter_context(self._project_lifecycle_lock(project_id))
            yield

    def _assert_workspace_selection_policy(
        self,
        current_selection: dict[str, str] | None,
        target_type: str,
        target_project_id: str | None,
    ) -> None:
        active_project_ids = self.events.agent_work_snapshot()["active_project_ids"]
        sole_active_target = (
            target_type == "project"
            and target_project_id is not None
            and len(active_project_ids) == 1
            and active_project_ids[0] == target_project_id
        )

        current_project_id = (
            current_selection.get("project_id")
            if current_selection is not None and current_selection.get("type") == "project"
            else None
        )
        current_availability = None
        if current_project_id is not None:
            current_availability = self._project_availability(current_project_id, nonblocking=True)
            allow_current_workflow = sole_active_target and current_project_id == target_project_id
            if not current_availability.permits(
                "selection",
                sole_active_workflow_target=allow_current_workflow,
            ):
                raise ConflictError(
                    "The currently selected project is locked, has a running operation, or belongs to active agent work; selection is unchanged.",
                    code="PROJECT_BUSY",
                )

        if active_project_ids and not sole_active_target:
            raise ConflictError(
                "CADYX reports active agent work; selection is unchanged.",
                code="PROJECT_BUSY",
            )

        if target_type == "project" and target_project_id is not None:
            target_availability = (
                current_availability
                if target_project_id == current_project_id and current_availability is not None
                else self._project_availability(target_project_id, nonblocking=True)
            )
            if not target_availability.permits(
                "selection",
                sole_active_workflow_target=sole_active_target,
            ):
                raise ConflictError(
                    "The target project is locked, requires lease recovery, or has a running operation; selection is unchanged.",
                    code="PROJECT_BUSY",
                )

    @contextmanager
    def _project_lifecycle_lock(self, project_id: str) -> Iterator[None]:
        with self._project_lifecycle_guard:
            lock = self._project_lifecycle_locks.setdefault(project_id, threading.RLock())
        with lock:
            yield

    def create_project(self, name: str, *, select: bool = True) -> dict[str, Any]:
        if not select:
            project = self.workspace.create(name)
            summary = self._summary(project)
            lock = self.leases.snapshot(project["id"])
            self.events.publish("project.lock.changed", project["id"], lock)
            return {"project": summary, "lock": lock}

        with self._workspace_selection_guard:
            current_selection = copy.deepcopy(self._workspace_selection)
            with self._selection_project_locks(current_selection, None):
                self._assert_workspace_selection_policy(current_selection, "new-project", None)
                project = self.workspace.create(name)
                summary = self._summary(project)
                lock = self.leases.snapshot(project["id"])
                self._workspace_selection = {"type": "project", "project_id": project["id"]}
                self.events.publish("project.created", project["id"], summary)
            self.events.publish("project.lock.changed", project["id"], lock)
        return {"project": summary, "lock": lock}

    def upload_project_step(self, project_id: str, filename: str, content: bytes) -> dict[str, Any]:
        with self._project_lifecycle_lock(project_id), self._project_guard(project_id):
            self._require_project_policy(
                project_id,
                "file_management",
                "STEP files can only be staged in a free project with no running operation.",
            )
            return self.workspace.publish_project_step(project_id, filename, content)

    def copy_project_files(self, target_project_id: str, source_project_id: str) -> dict[str, Any]:
        if target_project_id == source_project_id:
            raise ValidationError("Source and target projects must be different.")
        with ExitStack() as stack:
            for project_id in sorted((target_project_id, source_project_id)):
                stack.enter_context(self._project_lifecycle_lock(project_id))
                stack.enter_context(self._project_guard(project_id))
            for project_id in (target_project_id, source_project_id):
                self._require_project_policy(
                    project_id,
                    "file_management",
                    "Both projects must be free with no running operation before files can be copied.",
                )
            return self.workspace.copy_project_files(source_project_id, target_project_id)

    def remove_staged_project_step(self, project_id: str, filename: str) -> dict[str, Any]:
        with self._project_lifecycle_lock(project_id), self._project_guard(project_id):
            self._require_project_policy(
                project_id,
                "file_management",
                "STEP references can only be removed from a free project with no running operation.",
            )
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

    def prune_project_files(self, project_id: str, keep_files: list[str]) -> dict[str, Any]:
        with self._project_lifecycle_lock(project_id), self._project_guard(project_id):
            self._require_project_policy(
                project_id,
                "file_management",
                "Project files can only be pruned from a free project with no running operation.",
            )
            project = self.workspace.load(project_id)
            document = Document.from_dict(project["document"])
            if document.objects:
                raise ConflictError("Clear model geometry through the Command System before pruning project files.", code="FILE_IN_USE")
            return self.workspace.prune_project_files(project_id, keep_files)

    def rename_project(self, project_id: str, name: str) -> dict[str, Any]:
        with self._project_lifecycle_lock(project_id):
            self._require_project_policy(
                project_id,
                "destructive",
                "Project must be unlocked before it can be renamed.",
                code="PROJECT_LOCKED",
            )
            project = self.workspace.rename(project_id, name)
            summary = self._summary(project)
            self.events.publish("project.renamed", project_id, summary)
            return {"project": summary, "lock": self.leases.snapshot(project_id)}

    def delete_project(self, project_id: str) -> dict[str, Any]:
        with self._project_lifecycle_lock(project_id):
            with self._project_guard(project_id):
                self._require_project_policy(
                    project_id,
                    "destructive",
                    "Project must be free with no running operation before deletion.",
                )
                self.workspace.mark_project_deleting(project_id)
            cleanup_pending = self.workspace.delete_project(project_id)
            self._invalidate_project_runtime_caches(project_id)
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
                "lock": self._project_availability(project_id, nonblocking=True).lock,
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
            self._require_project_policy(
                project_id,
                "file_management",
                "A project can only be moved when it is free with no running operation.",
            )
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

        target_project_id = selected.get("project_id") if resource_type == "project" else None
        with self._workspace_selection_guard:
            current_selection = copy.deepcopy(self._workspace_selection)
            with self._selection_project_locks(current_selection, target_project_id):
                if target_project_id is not None:
                    self.workspace.load(target_project_id)
                self._assert_workspace_selection_policy(
                    current_selection,
                    resource_type,
                    target_project_id,
                )
                self._workspace_selection = copy.deepcopy(selected)
                self.events.publish("workspace.selection.changed", None, selected)
        return selected

    def inspect_project(self, project_id: str) -> dict[str, Any]:
        with self._project_lifecycle_lock(project_id):
            # project.json is atomically replaced, so read-only viewers can safely
            # inspect the last committed revision while an agent edits its next one.
            project = self.workspace.load(project_id)
            document = Document.from_dict(project["document"])
            self._validate_geometry_once(project_id, document)
            availability = self._project_availability(project_id, nonblocking=True)
            if not availability.permits("read"):
                raise RuntimeError("Read-only project inspection must remain available while the project is busy.")
            return {
                "project": self._summary(project),
                "lock": availability.lock,
                "document": document.to_dict(),
                "history": copy.deepcopy(project["history"]),
            }

class CommandActivityService(_CoreService):
    """Coordinate leases, commands, dashboards, and activity."""

    def agent_dashboard_snapshot(self, project_id: str) -> dict[str, Any]:
        self.workspace.load(project_id)
        return self.events.agent_dashboard_snapshot(project_id)

    def report_agent_dashboard(self, project_id: str, report: dict[str, Any]) -> dict[str, Any]:
        """Publish agent progress and tasks; elapsed time is tracked by the dashboard store."""
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

    def acquire_lease(self, project_id: str, owner_type: str, owner_id: str) -> dict[str, Any]:
        with self._project_lifecycle_lock(project_id), self._project_guard(project_id):
            self.workspace.load(project_id)
            availability = self._project_availability(project_id)
            if (
                not availability.permits("write")
                and availability.lock_state == "FREE"
                and availability.lock.get("recovery_required") is False
            ):
                raise ConflictError("Project has an operation in progress; retry later.", code="PROJECT_BUSY")
            lease = self.leases.acquire(project_id, owner_type, owner_id)
            snapshot = self.leases.snapshot(project_id)
            self.events.publish("project.lock.changed", project_id, snapshot)
            return lease.as_dict()

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
        operation: Callable[..., Any],
        *,
        reports_progress: bool = False,
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
            def report_progress(phase: str, progress: float) -> None:
                if (not isinstance(phase, str) or not phase
                        or isinstance(progress, bool) or not isinstance(progress, (int, float))
                        or not math.isfinite(progress) or not 0 <= progress <= 1):
                    raise ValidationError("Agent operation reported invalid progress.")
                activity["phase"] = phase
                activity["progress_percent"] = round(progress * 100, 1)
                self._publish_activity(project_id, activity)

            result = operation(report_progress) if reports_progress else operation()
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

    def resume_agent(self, project_id: str, owner_id: str, expected_revision: int) -> dict[str, Any]:
        with self._project_guard(project_id):
            detail = self.inspect_project(project_id)
            # Resuming grants a new editing lease, so recheck geometry at this
            # explicit state transition rather than relying on viewer caching.
            self._validate_geometry(Document.from_dict(detail["document"]))
            self._check_revision(Document.from_dict(detail["document"]), expected_revision)
            availability = self._project_availability(project_id)
            if (
                not availability.permits("write")
                and availability.lock_state == "FREE"
                and availability.lock.get("recovery_required") is False
            ):
                raise ConflictError("Project has an operation in progress; retry later.", code="PROJECT_BUSY")
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
            self._require_project_policy(
                project_id,
                "write",
                "Project has an operation in progress; retry later.",
                valid_lease=True,
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
                "action": self.commands.activity_action(command_name),
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
            # v2 SSE receipt: subscribers fetch full command history via the read API.
            # Never broadcast command inputs or geometry-bearing results here.
            receipt = {
                "schema_version": 2,
                "operation_id": operation_id,
                "revision": document.revision,
                "command": command_name,
                "outcome": "completed",
            }
            if isinstance(result.get("feature_id"), str):
                receipt["feature_id"] = result["feature_id"]
            self.events.publish("command.completed", project_id, receipt)
            return {"document": document.to_dict(), "history_entry": history_entry}

class RenderingCacheService(_CoreService):
    """Own revision-bound rendering caches and viewport snapshots."""
    _local_names = frozenset({"_cache_policy","_geometry_cache_guard","_validated_document_keys","_document_validation_locks","_scene_cache","_png_cache","_png_cache_bytes","_viewport_snapshot_guard","_viewport_snapshots","_viewport_snapshot_bytes"})
    def __init__(self, core: CadyxCore, *, cache_policy: RuntimeCachePolicy | None=None) -> None:
        super().__init__(core)
        self._cache_policy = cache_policy if cache_policy is not None else RuntimeCachePolicy()
        self._geometry_cache_guard = threading.RLock()
        self._validated_document_keys: OrderedDict[tuple[str,str],None] = OrderedDict()
        self._document_validation_locks: dict[tuple[str,str],threading.Lock] = {}
        self._scene_cache: OrderedDict[tuple[str,str],dict[str,Any]] = OrderedDict()
        self._png_cache: OrderedDict[tuple[str,str,int,int,int],bytes] = OrderedDict()
        self._png_cache_bytes = 0
        self._viewport_snapshot_guard = threading.RLock()
        self._viewport_snapshots: OrderedDict[str,tuple[int,bytes]] = OrderedDict()
        self._viewport_snapshot_bytes = 0

    def compare_bodies(self, project_id: str, original_id: str | None = None, modified_id: str | None = None) -> dict:
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
        with self._project_lifecycle_lock(project_id):
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
                trim_lru_entries(self._scene_cache, self._cache_policy.scene_entries)
                cached_scene = scene
            else:
                self._scene_cache.move_to_end(key)
        return key, cached_scene

    def _remember_png_locked(
        self,
        cache_key: tuple[str, str, int, int, int],
        encoded: bytes,
    ) -> None:
        self._png_cache_bytes = remember_bounded_bytes(
            self._png_cache,
            cache_key,
            encoded,
            current_bytes=self._png_cache_bytes,
            max_entries=self._cache_policy.png_entries,
            max_bytes=self._cache_policy.png_bytes,
            size_of=len,
        )

    def _render_project_png_server(
        self, project_id: str, *, width: int = 960, height: int = 720, margin: int = 56,
    ) -> bytes:
        with self._project_lifecycle_lock(project_id):
            key, scene = self._scene_for_project(project_id)
            cache_key = (*key, width, height, margin)
            with self._geometry_cache_guard:
                cached_png = self._png_cache.get(cache_key)
                if cached_png is not None:
                    self._png_cache.move_to_end(cache_key)
                    return cached_png
            encoded = render_scene_png(scene, width=width, height=height, margin=margin)
            with self._geometry_cache_guard:
                cached_png = self._png_cache.get(cache_key)
                if cached_png is not None:
                    self._png_cache.move_to_end(cache_key)
                    return cached_png
                self._remember_png_locked(cache_key, encoded)
            return encoded

    def render_project_thumbnail_png(self, project_id: str) -> tuple[bytes, int]:
        """Render a compact 16:9 frame fitted for square project cards."""
        with self._project_lifecycle_lock(project_id):
            key, scene = self._scene_for_project(project_id)
            width, height, margin = 480, 270, 28
            cache_key = (*key, width, height, margin)
            with self._geometry_cache_guard:
                cached_png = self._png_cache.get(cache_key)
                if cached_png is not None:
                    self._png_cache.move_to_end(cache_key)
                    return cached_png, scene["revision"]
            encoded = render_scene_png(scene, width=width, height=height, margin=margin)
            with self._geometry_cache_guard:
                cached_png = self._png_cache.get(cache_key)
                if cached_png is not None:
                    self._png_cache.move_to_end(cache_key)
                    return cached_png, scene["revision"]
                self._remember_png_locked(cache_key, encoded)
            return encoded, scene["revision"]

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

        with self._project_lifecycle_lock(project_id):
            _, scene = self._scene_for_project(project_id)
            if revision != scene["revision"]:
                raise ConflictError(
                    "The browser viewport has not rendered the current project revision yet.",
                    code="VIEWPORT_SNAPSHOT_STALE",
                )
            with self._viewport_snapshot_guard:
                self._viewport_snapshot_bytes = remember_bounded_bytes(
                    self._viewport_snapshots,
                    project_id,
                    (revision, encoded),
                    current_bytes=self._viewport_snapshot_bytes,
                    max_entries=self._cache_policy.viewport_snapshot_entries,
                    max_bytes=self._cache_policy.viewport_snapshot_bytes,
                    size_of=lambda item: len(item[1]),
                )
        return {"revision": revision, "width": width, "height": height}

    def render_project_png_with_source(self, project_id: str) -> tuple[bytes, str, int]:
        with self._project_lifecycle_lock(project_id):
            _, scene = self._scene_for_project(project_id)
            with self._viewport_snapshot_guard:
                snapshot = self._viewport_snapshots.get(project_id)
                if snapshot is not None:
                    if snapshot[0] != scene["revision"]:
                        # A previous browser frame is not a valid image of the
                        # current document. Treat it as unavailable until the
                        # browser publishes the new revision; the server renderer
                        # can still provide a correct current image immediately.
                        self._viewport_snapshots.pop(project_id)
                        self._viewport_snapshot_bytes -= len(snapshot[1])
                    else:
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

    def _remember_geometry_validation(self, project_id: str, document: Document) -> None:
        key = self._document_cache_key(project_id, document)
        with self._geometry_cache_guard:
            self._validated_document_keys[key] = None
            self._validated_document_keys.move_to_end(key)
            trim_lru_entries(
                self._validated_document_keys,
                self._cache_policy.validated_document_entries,
            )

    def _invalidate_project_runtime_caches(self, project_id: str) -> None:
        with self._geometry_cache_guard:
            for key in tuple(self._validated_document_keys):
                if key[0] == project_id:
                    self._validated_document_keys.pop(key, None)
            for key in tuple(self._document_validation_locks):
                if key[0] == project_id:
                    self._document_validation_locks.pop(key, None)
            for key in tuple(self._scene_cache):
                if key[0] == project_id:
                    self._scene_cache.pop(key, None)
            for key in tuple(self._png_cache):
                if key[0] == project_id:
                    encoded = self._png_cache.pop(key)
                    self._png_cache_bytes -= len(encoded)
        with self._viewport_snapshot_guard:
            snapshot = self._viewport_snapshots.pop(project_id, None)
            if snapshot is not None:
                self._viewport_snapshot_bytes -= len(snapshot[1])

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

class EngineeringValidationService(_CoreService):
    """Run revision-bound engineering validation and persistence."""

    def validate_body_dimensions(
        self, project_id: str, body_id: str, expected_revision: Any,
        expected_dimensions: Any, tolerance: Any, *, persist: bool = False,
    ) -> dict[str, Any]:
        project = self.workspace.load(project_id)
        document = Document.from_dict(project["document"])
        self._check_revision(document, expected_revision)
        if type(persist) is not bool:
            raise ValidationError("persist must be a boolean.")
        if not isinstance(body_id, str) or not body_id:
            raise ValidationError("body_id must be non-empty text.")
        if not isinstance(expected_dimensions, dict) or set(expected_dimensions) != {"x", "y", "z"}:
            raise ValidationError("expected_dimensions must contain exactly x, y, and z.")
        def finite_number(value: Any, name: str, *, positive: bool) -> float:
            message = f"{name} must be a finite {'positive' if positive else 'non-negative'} number."
            if type(value) not in (int, float):
                raise ValidationError(message)
            try:
                normalized = float(value)
            except (OverflowError, ValueError):
                raise ValidationError(message) from None
            if not math.isfinite(normalized) or (normalized <= 0 if positive else normalized < 0):
                raise ValidationError(message)
            return normalized

        normalized_tolerance = finite_number(tolerance, "tolerance", positive=False)
        expected = {}
        for axis, value in expected_dimensions.items():
            expected[axis] = finite_number(value, f"expected_dimensions.{axis}", positive=True)
        body = next((item for item in document.objects
                     if item.get("id") == body_id and item.get("type") == "body"), None)
        if body is None:
            raise ValidationError("Dimension validation needs an existing body_id.")
        geometry = body["geometry"]
        if geometry.get("kernel") == "cadyx-mesh-v1":
            bounds = mesh_bounds(geometry)
        else:
            bounds = self.kernel.metrics(geometry)["bounds"]
        actual = {
            axis: float(bounds["max"][index]) - float(bounds["min"][index])
            for index, axis in enumerate(("x", "y", "z"))
        }
        checks = {
            axis: {"expected": expected[axis], "actual": actual[axis],
                   "absolute_error": abs(actual[axis] - expected[axis]),
                   "status": "pass" if abs(actual[axis] - expected[axis]) <= normalized_tolerance else "fail"}
            for axis in ("x", "y", "z")
        }
        result = {
            "body_id": body_id,
            "revision": document.revision,
            "geometry_sha256": geometry_sha256(geometry),
            "method": "axis_aligned_bounding_box",
            "coordinate_frame": "global_xyz",
            "units": "model_units",
            "tolerance": normalized_tolerance,
            "status": "pass" if all(item["status"] == "pass" for item in checks.values()) else "fail",
            "checks": checks,
        }
        return self._maybe_persist_validation(project_id, "body_dimensions", result, persist)

    def validate_body_clearance(
        self, project_id: str, first_body_id: str, second_body_id: str,
        expected_revision: Any, minimum_clearance: Any, tolerance: Any,
        *, progress: Callable[[str, float], None] | None = None, persist: bool = False,
    ) -> dict[str, Any]:
        project = self.workspace.load(project_id)
        document = Document.from_dict(project["document"])
        self._check_revision(document, expected_revision)
        if type(persist) is not bool:
            raise ValidationError("persist must be a boolean.")
        if (not isinstance(first_body_id, str) or not first_body_id
                or not isinstance(second_body_id, str) or not second_body_id
                or first_body_id == second_body_id):
            raise ValidationError("Clearance validation needs two distinct body ids.")

        def nonnegative_number(value: Any, name: str) -> float:
            message = f"{name} must be a finite non-negative number."
            if type(value) not in (int, float):
                raise ValidationError(message)
            try:
                normalized = float(value)
            except (OverflowError, ValueError):
                raise ValidationError(message) from None
            if not math.isfinite(normalized) or normalized < 0:
                raise ValidationError(message)
            return normalized

        required = nonnegative_number(minimum_clearance, "minimum_clearance")
        allowed_tolerance = nonnegative_number(tolerance, "tolerance")
        bodies = {item["id"]: item for item in document.objects if item.get("type") == "body"}
        if first_body_id not in bodies or second_body_id not in bodies:
            raise ValidationError("Clearance validation needs two existing bodies.")
        first_geometry = bodies[first_body_id]["geometry"]
        second_geometry = bodies[second_body_id]["geometry"]
        measured = (self.kernel.minimum_distance(first_geometry, second_geometry)
                    if progress is None else
                    self.kernel.minimum_distance(first_geometry, second_geometry, progress=progress))
        distance = measured.get("minimum_distance")
        deflection = measured.get("deflection")
        inner_solution = measured.get("inner_solution")
        intersection_volume = measured.get("intersection_volume")
        if (type(distance) not in (int, float) or not math.isfinite(distance) or distance < 0
                or type(deflection) not in (int, float) or not math.isfinite(deflection) or deflection <= 0
                or type(inner_solution) is not bool
                or type(intersection_volume) not in (int, float)
                or not math.isfinite(intersection_volume) or intersection_volume < 0
                or type(measured.get("solution_count")) is not int or measured["solution_count"] < 1):
            raise ValidationError("OCCT returned invalid solid-clearance metrics.")
        witness_points = {}
        for name in ("point_on_first", "point_on_second"):
            point = measured.get(name)
            if (not isinstance(point, list) or len(point) != 3
                    or any(type(value) not in (int, float) or not math.isfinite(value) for value in point)):
                raise ValidationError("OCCT returned invalid solid-clearance witness points.")
            witness_points[name] = [float(value) for value in point]
        distance_lower = max(0.0, distance - deflection)
        distance_upper = distance + deflection
        required_after_tolerance = max(0.0, required - allowed_tolerance)
        volume_resolution = deflection ** 3
        if inner_solution or intersection_volume > volume_resolution:
            interference_status = "interfering"
        elif intersection_volume > 0 or distance <= deflection:
            interference_status = "touching_or_indeterminate"
        else:
            interference_status = "separate"
        if interference_status == "interfering" or distance_upper < required_after_tolerance:
            status = "fail"
        elif interference_status == "separate" and distance_lower >= required_after_tolerance:
            status = "pass"
        else:
            status = "untested"
        geometry_hash = geometry_sha256({
            "first": {"body_id": first_body_id, "geometry": first_geometry},
            "second": {"body_id": second_body_id, "geometry": second_geometry},
        })
        result = {
            "body_ids": [first_body_id, second_body_id],
            "revision": document.revision,
            "geometry_sha256": geometry_hash,
            "method": "OCCT_BRepExtrema_DistShapeShape",
            "coordinate_frame": "global_xyz",
            "units": "model_units",
            "minimum_clearance": required,
            "measured_distance": distance,
            "distance_interval": [distance_lower, distance_upper],
            "tolerance": allowed_tolerance,
            "deflection": deflection,
            "inner_solution": inner_solution,
            "interference_method": "OCCT_BRepAlgoAPI_Common",
            "intersection_volume": intersection_volume,
            "interference_status": interference_status,
            **witness_points,
            "status": status,
        }
        return self._maybe_persist_validation(project_id, "body_clearance", result, persist)

    def list_validation_records(self, project_id: str, *, limit: int = 50) -> dict[str, Any]:
        return {"records": self.workspace.list_validation_records(project_id, limit=limit)}

    def validate_body_preservation(
        self, project_id: str, reference_body_id: str, candidate_body_id: str,
        expected_revision: Any, maximum_lost_volume: Any, tolerance: Any,
        *, persist: bool = False,
    ) -> dict[str, Any]:
        project = self.workspace.load(project_id)
        document = Document.from_dict(project["document"])
        self._check_revision(document, expected_revision)
        if type(persist) is not bool:
            raise ValidationError("persist must be a boolean.")
        if (not isinstance(reference_body_id, str) or not reference_body_id
                or not isinstance(candidate_body_id, str) or not candidate_body_id
                or reference_body_id == candidate_body_id):
            raise ValidationError("Preservation validation needs two distinct body ids.")

        def nonnegative_volume(value: Any, name: str) -> float:
            message = f"{name} must be a finite non-negative volume."
            if type(value) not in (int, float):
                raise ValidationError(message)
            try:
                normalized = float(value)
            except (OverflowError, ValueError):
                raise ValidationError(message) from None
            if not math.isfinite(normalized) or normalized < 0:
                raise ValidationError(message)
            return normalized

        maximum_lost = nonnegative_volume(maximum_lost_volume, "maximum_lost_volume")
        allowed_tolerance = nonnegative_volume(tolerance, "tolerance")
        bodies = {item["id"]: item for item in document.objects if item.get("type") == "body"}
        if reference_body_id not in bodies or candidate_body_id not in bodies:
            raise ValidationError("Preservation validation needs two existing bodies.")
        reference = bodies[reference_body_id]["geometry"]
        candidate = bodies[candidate_body_id]["geometry"]
        measured = self.kernel.compare_preserved_volume(reference, candidate)
        deflection = measured.get("deflection")
        if (type(deflection) not in (int, float) or not math.isfinite(deflection)
                or deflection <= 0):
            raise ValidationError("OCCT returned an invalid preservation deflection.")
        metric_names = (
            "reference_volume", "candidate_volume", "preserved_volume", "lost_volume",
            "added_volume", "estimated_volume_resolution",
        )
        metrics = {name: measured.get(name) for name in metric_names}
        if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
               for value in metrics.values()):
            raise ValidationError("OCCT returned invalid preserved-volume metrics.")
        reference_error = abs(metrics["reference_volume"]
                              - metrics["preserved_volume"] - metrics["lost_volume"])
        candidate_error = abs(metrics["candidate_volume"]
                               - metrics["preserved_volume"] - metrics["added_volume"])
        threshold = max(0.0, maximum_lost - allowed_tolerance)
        resolution = metrics["estimated_volume_resolution"]
        lost_lower = max(0.0, metrics["lost_volume"] - resolution)
        lost_upper = metrics["lost_volume"] + resolution
        if max(reference_error, candidate_error) > resolution:
            status = "untested"
        elif lost_upper <= threshold or (threshold == 0 and metrics["lost_volume"] <= resolution):
            status = "pass"
        elif lost_lower > threshold:
            status = "fail"
        else:
            status = "untested"
        geometry_hash = geometry_sha256({
            "reference": {"body_id": reference_body_id, "geometry": reference},
            "candidate": {"body_id": candidate_body_id, "geometry": candidate},
        })
        result = {
            "body_ids": {"reference": reference_body_id, "candidate": candidate_body_id},
            "revision": document.revision,
            "geometry_sha256": geometry_hash,
            "method": "OCCT_BRepAlgoAPI_Common",
            "coordinate_frame": "global_xyz",
            "units": "model_units^3",
            "deflection": deflection,
            "deflection_units": "model_units",
            **metrics,
            "maximum_lost_volume": maximum_lost,
            "tolerance": allowed_tolerance,
            "estimated_lost_volume_range": [lost_lower, lost_upper],
            "volume_balance_error": {"reference": reference_error, "candidate": candidate_error},
            "status": status,
        }
        return self._maybe_persist_validation(project_id, "body_preservation", result, persist)

    def validate_body_thickness(
        self, project_id: str, body_id: str, expected_revision: Any,
        minimum_thickness: Any, tolerance: Any, *,
        progress: Callable[[str, float], None] | None = None, persist: bool = False,
        method: str = "sampled",
    ) -> dict[str, Any]:
        project = self.workspace.load(project_id)
        document = Document.from_dict(project["document"])
        self._check_revision(document, expected_revision)
        if type(persist) is not bool:
            raise ValidationError("persist must be a boolean.")
        if not isinstance(body_id, str) or not body_id:
            raise ValidationError("Thickness validation needs a body id.")

        def nonnegative(value: Any, name: str, *, positive: bool = False) -> float:
            message = f"{name} must be a finite {'positive' if positive else 'non-negative'} number."
            if type(value) not in (int, float):
                raise ValidationError(message)
            try:
                number = float(value)
            except (OverflowError, ValueError):
                raise ValidationError(message) from None
            if not math.isfinite(number) or number < 0 or (positive and number == 0):
                raise ValidationError(message)
            return number

        required = nonnegative(minimum_thickness, "minimum_thickness", positive=True)
        allowed_tolerance = nonnegative(tolerance, "tolerance")
        if not isinstance(method, str) or method not in {"sampled", "global_minimum"}:
            raise ValidationError("Thickness method must be sampled or global_minimum.")
        bodies = {item["id"]: item for item in document.objects if item.get("type") == "body"}
        if body_id not in bodies:
            raise ValidationError("Thickness validation needs an existing body.")
        geometry = bodies[body_id]["geometry"]
        if method == "global_minimum":
            measured_global = self.kernel.certify_global_minimum_thickness(geometry, progress=progress)
            if type(measured_global.get("supported")) is not bool:
                raise ValidationError("OCCT returned an invalid global wall-thickness certification result.")
            result = {
                "body_id": body_id,
                "revision": document.revision,
                "geometry_sha256": geometry_sha256({"body_id": body_id, "geometry": geometry}),
                "method": "OCCT_convex_polyhedron_minimum_width",
                "metric_definition": "minimum_caliper_width",
                "coordinate_frame": "global_xyz",
                "units": "model_units",
                "minimum_thickness": required,
                "tolerance": allowed_tolerance,
                "certified": False,
                "status": "untested",
            }
            if measured_global["supported"]:
                numeric_names = (
                    "minimum_global_thickness", "minimum_bound", "maximum_bound", "numerical_error_bound",
                )
                metrics = {name: measured_global.get(name) for name in numeric_names}
                if (type(measured_global.get("certified")) is not bool
                        or not measured_global["certified"]
                        or any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
                               for value in metrics.values())
                        or metrics["minimum_bound"] > metrics["minimum_global_thickness"]
                        or metrics["minimum_global_thickness"] > metrics["maximum_bound"]
                        or metrics["maximum_bound"] - metrics["minimum_bound"]
                        > 2.0 * metrics["numerical_error_bound"] + 1e-12):
                    raise ValidationError("OCCT returned invalid global wall-thickness bounds.")
                direction = measured_global.get("support_direction")
                direction_magnitude = (math.sqrt(sum(value * value for value in direction))
                                       if isinstance(direction, list) and len(direction) == 3
                                       and all(type(value) in (int, float) and math.isfinite(value)
                                               for value in direction) else math.nan)
                points = {
                    "minimum": measured_global.get("minimum_support_point"),
                    "maximum": measured_global.get("maximum_support_point"),
                }
                if (not isinstance(direction, list) or len(direction) != 3
                        or any(type(value) not in (int, float) or not math.isfinite(value) for value in direction)
                        or not math.isfinite(direction_magnitude)
                        or abs(direction_magnitude - 1.0) > 1e-8
                        or any(not isinstance(point, list) or len(point) != 3
                               or any(type(value) not in (int, float) or not math.isfinite(value) for value in point)
                               for point in points.values())):
                    raise ValidationError("OCCT returned invalid global wall-thickness witnesses.")
                counts = {name: measured_global.get(name) for name in (
                    "face_count", "vertex_count", "edge_direction_count", "direction_count",
                )}
                if any(type(value) is not int or value <= 0 for value in counts.values()):
                    raise ValidationError("OCCT returned invalid global wall-thickness support counts.")
                lower, upper = metrics["minimum_bound"], metrics["maximum_bound"]
                if upper + allowed_tolerance < required:
                    status = "fail"
                elif lower + allowed_tolerance >= required:
                    status = "pass"
                else:
                    status = "untested"
                result.update({
                    **metrics,
                    **counts,
                    "support_direction": direction,
                    "support_points": points,
                    "certified": True,
                    "status": status,
                })
            else:
                reason = measured_global.get("reason")
                if not isinstance(reason, str) or not reason:
                    raise ValidationError("OCCT returned no reason for unsupported global wall-thickness certification.")
                result["reason"] = reason
            return self._maybe_persist_validation(project_id, "body_thickness", result, persist)

        measured = self.kernel.sample_minimum_thickness(
            geometry, minimum_thickness=required, tolerance=allowed_tolerance, progress=progress,
        )
        metric_names = (
            "minimum_sampled_thickness", "median_sampled_thickness", "p10_sampled_thickness",
            "mesh_deflection", "start_offset",
        )
        metrics = {name: measured.get(name) for name in metric_names}
        if (
            any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
                for value in metrics.values())
            or metrics["minimum_sampled_thickness"] <= 0
            or not (metrics["minimum_sampled_thickness"] <= metrics["p10_sampled_thickness"]
                    <= metrics["median_sampled_thickness"])
        ):
            raise ValidationError("OCCT returned invalid wall-thickness sampling metrics.")
        sample_count, hit_count = measured.get("sample_count"), measured.get("hit_count")
        violating_sample_count = measured.get("violating_sample_count")
        face_count, sampled_face_count = measured.get("face_count"), measured.get("sampled_face_count")
        if (type(sample_count) is not int or type(hit_count) is not int
                or type(violating_sample_count) is not int
                or type(face_count) is not int or type(sampled_face_count) is not int
                or sample_count <= 0 or hit_count <= 0 or hit_count > sample_count
                or violating_sample_count < 0 or violating_sample_count > hit_count
                or face_count <= 0 or sampled_face_count <= 0
                or sampled_face_count > min(face_count, sample_count)):
            raise ValidationError("OCCT returned invalid wall-thickness sample counts.")
        for name in ("point", "opposite_point"):
            point = measured.get(name)
            if (not isinstance(point, list) or len(point) != 3
                    or any(type(value) not in (int, float) or not math.isfinite(value) for value in point)):
                raise ValidationError("OCCT returned invalid wall-thickness witness points.")
        # A low sampled chord proves a local thin-wall condition; passing sparse
        # normal samples cannot prove a global minimum, so the other outcome is untested.
        observed_violation = metrics["minimum_sampled_thickness"] + allowed_tolerance < required
        if observed_violation != (violating_sample_count > 0):
            raise ValidationError("OCCT returned inconsistent wall-thickness violation counts.")
        status = "fail" if violating_sample_count else "untested"
        result = {
            "body_id": body_id,
            "revision": document.revision,
            "geometry_sha256": geometry_sha256({"body_id": body_id, "geometry": geometry}),
            "method": "OCCT_inward_normal_ray_sample",
            "coordinate_frame": "global_xyz",
            "units": "model_units",
            **metrics,
            "sample_count": sample_count,
            "face_count": face_count,
            "sampled_face_count": sampled_face_count,
            "hit_count": hit_count,
            "violating_sample_count": violating_sample_count,
            "minimum_thickness": required,
            "tolerance": allowed_tolerance,
            "witness_points": {"surface": measured["point"], "opposite": measured["opposite_point"]},
            "status": status,
        }
        return self._maybe_persist_validation(project_id, "body_thickness", result, persist)

    def _maybe_persist_validation(
        self, project_id: str, kind: str, result: dict[str, Any], persist: bool,
    ) -> dict[str, Any]:
        if type(persist) is not bool:
            raise ValidationError("persist must be a boolean.")
        if not persist:
            return result
        record = self.workspace.record_validation(project_id, kind, result)
        return {**result, "record": record}

