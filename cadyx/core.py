"""Application Core shared by agent tools and the web interface."""

from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator
from uuid import uuid4

from .commands import CommandSystem
from .errors import ConflictError, ValidationError
from .events import EventBroker
from .kernel import GeometryKernel, OCCTKernelAdapter
from .lease import EditLeaseManager
from .models import Document, OwnerType
from .rendering import render_scene, render_scene_png
from .scan import ScanProcessor
from .workspace import ProjectWorkspace, utc_now


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
            scan_processor=scan_processor,
            publish_scan_output=self.workspace.publish_scan_output,
        )
        if lease_clock is None:
            self.leases = EditLeaseManager(self.workspace._project_dir, timeout_seconds=lease_timeout_seconds)
        else:
            self.leases = EditLeaseManager(self.workspace._project_dir, timeout_seconds=lease_timeout_seconds, clock=lease_clock)
        self.events = EventBroker()

    @contextmanager
    def _project_guard(self, project_id: str) -> Iterator[None]:
        with self.leases.guard(project_id):
            yield

    def create_project(self, name: str) -> dict[str, Any]:
        project = self.workspace.create(name)
        summary = self._summary(project)
        lock = self.leases.snapshot(project["id"])
        self.events.publish("project.created", project["id"], summary)
        self.events.publish("project.lock.changed", project["id"], lock)
        return {"project": summary, "lock": lock}

    def list_projects(self) -> list[dict[str, Any]]:
        result = []
        for project in self.workspace.list():
            project_id = project["id"]
            with self._project_guard(project_id):
                result.append({
                    "project": self._summary(self.workspace.load(project_id)),
                    "lock": self.leases.snapshot(project_id),
                })
        return result

    def list_workspace(self) -> dict[str, Any]:
        return {"projects": self.list_projects(), "folders": self.workspace.list_folder_projects()}

    def inspect_project(self, project_id: str) -> dict[str, Any]:
        with self._project_guard(project_id):
            project = self.workspace.load(project_id)
            document = Document.from_dict(project["document"])
            self._validate_geometry(document)
            return {
                "project": self._summary(project),
                "lock": self.leases.snapshot(project_id),
                "document": document.to_dict(),
                "history": copy.deepcopy(project["history"]),
            }

    def acquire_lease(self, project_id: str, owner_type: str, owner_id: str) -> dict[str, Any]:
        with self._project_guard(project_id):
            self.workspace.load(project_id)
            lease = self.leases.acquire(project_id, owner_type, owner_id)
            snapshot = self.leases.snapshot(project_id)
            self.events.publish("project.lock.changed", project_id, snapshot)
            return lease.as_dict()

    def render_project(self, project_id: str) -> dict[str, Any]:
        with self._project_guard(project_id):
            project = self.workspace.load(project_id)
            document = Document.from_dict(project["document"])
            self._validate_geometry(document)
            return render_scene(document, self.kernel)

    def render_project_png(self, project_id: str) -> bytes:
        with self._project_guard(project_id):
            project = self.workspace.load(project_id)
            document = Document.from_dict(project["document"])
            self._validate_geometry(document)
            return render_scene_png(render_scene(document, self.kernel))

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
            lease = self.leases.require(project_id, lease_id)
            if expected_owner_type is not None and lease.owner_type is not expected_owner_type:
                raise ConflictError(
                    f"This API requires a {expected_owner_type.value} edit lease.",
                    code="PROJECT_LOCKED",
                )
            project = self.workspace.load(project_id)
            if lease.owner_type is OwnerType.AGENT:
                revision_required = self.leases.check_agent_command(project_id, lease.lease_id)
                if revision_required or expected_revision is not None:
                    self._check_revision(Document.from_dict(project["document"]), expected_revision)
            elif expected_revision is not None:
                self._check_revision(Document.from_dict(project["document"]), expected_revision)
            command_name = command.get("type") if isinstance(command, dict) else None
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
                result = self.commands.execute(document, command, progress=report_progress)
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
            "set_parameter": "Меняет параметры модели",
            "import_scan": "Обрабатывает скан",
            "set_scan_parameter": "Пересобирает скан",
            "create_sketch": "Создаёт эскиз",
            "add_sketch_element": "Обновляет эскиз",
            "add_sketch_constraint": "Задаёт ограничение эскиза",
            "update_sketch_elements": "Обновляет геометрию эскиза",
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
        for item in document.objects:
            geometry = item.get("geometry")
            if geometry is not None:
                self.kernel.validate(geometry)

    @staticmethod
    def _summary(project: dict[str, Any]) -> dict[str, Any]:
        return {
            key: project[key]
            for key in ("id", "format", "version", "name", "created_at", "modified_at")
        }
