"""Application Core shared by agent tools and the web interface."""

from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

from .commands import CommandSystem
from .errors import ConflictError, ValidationError
from .events import EventBroker
from .kernel import GeometryKernel, OCPKernelAdapter
from .lease import EditLeaseManager
from .models import Document, OwnerType
from .workspace import ProjectWorkspace, utc_now


class CadyxCore:
    def __init__(
        self,
        projects_dir: str,
        *,
        kernel: GeometryKernel | None = None,
        lease_timeout_seconds: float = 60.0,
        lease_clock: Callable[[], float] | None = None,
    ) -> None:
        self.workspace = ProjectWorkspace(projects_dir)
        self.kernel = kernel if kernel is not None else OCPKernelAdapter()
        self.commands = CommandSystem(self.kernel)
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
            self.events.publish("command.started", project_id, {"command": command_name, "actor_id": lease.owner_id})
            try:
                document = Document.from_dict(project["document"])
                result = self.commands.execute(document, command)
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
                self.workspace.save(next_project)
            except Exception as exc:
                self.events.publish(
                    "command.failed",
                    project_id,
                    {"command": command_name, "actor_id": lease.owner_id, "message": str(exc)},
                )
                raise

            self.events.publish("document.changed", project_id, {"revision": document.revision})
            self.events.publish("command.completed", project_id, history_entry)
            return {"document": document.to_dict(), "history_entry": history_entry}

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
