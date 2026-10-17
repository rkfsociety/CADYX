"""Bounded in-memory event fan-out used by the local SSE endpoint."""

from __future__ import annotations

import copy
import json
import math
import os
import queue
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ProjectEvent:
    id: int
    event: str
    project_id: str | None
    data: dict[str, Any]
    occurred_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "event": self.event,
            "project_id": self.project_id,
            "data": self.data,
            "occurred_at": self.occurred_at,
        }


@dataclass(frozen=True, slots=True)
class _ActivityRecord:
    operation_id: Any
    payload: dict[str, Any]

    @classmethod
    def capture(cls, payload: dict[str, Any]) -> _ActivityRecord:
        copied = copy.deepcopy(payload)
        return cls(operation_id=copied.get("operation_id"), payload=copied)

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.payload)


@dataclass(frozen=True, slots=True)
class _DashboardRecord:
    """Validated persisted/API dashboard value with lossless v1 serialization."""

    payload: dict[str, Any]

    @classmethod
    def from_mapping(cls, value: Any) -> _DashboardRecord:
        required = {"workflow", "tasks", "revision", "updated_at"}
        optional = {"started_at", "elapsed_seconds"}
        if (
            not isinstance(value, dict)
            or not required.issubset(value)
            or set(value) - required - optional
            or type(value.get("revision")) is not int
            or value["revision"] < 1
            or not isinstance(value.get("updated_at"), str)
            or not isinstance(value.get("tasks"), list)
        ):
            raise ValueError("invalid dashboard state structure")

        started_at = value.get("started_at")
        elapsed_seconds = value.get("elapsed_seconds")
        if started_at is not None:
            if not isinstance(started_at, str):
                raise ValueError("invalid dashboard state structure")
            try:
                datetime.fromisoformat(started_at)
            except ValueError as exc:
                raise ValueError("invalid dashboard state structure") from exc
        if elapsed_seconds is not None and (
            type(elapsed_seconds) is not int or elapsed_seconds < 0
        ):
            raise ValueError("invalid dashboard state structure")
        if elapsed_seconds is not None and started_at is None:
            raise ValueError("invalid dashboard state structure")

        workflow = value["workflow"]
        if workflow is not None:
            if (
                not isinstance(workflow, dict)
                or set(workflow) != {"title", "progress_percent", "eta_seconds", "phases"}
                or not isinstance(workflow["title"], str)
                or isinstance(workflow["progress_percent"], bool)
                or not isinstance(workflow["progress_percent"], (int, float))
                or not math.isfinite(workflow["progress_percent"])
                or not 0 <= workflow["progress_percent"] <= 100
                or (
                    workflow["eta_seconds"] is not None
                    and type(workflow["eta_seconds"]) is not int
                )
                or not isinstance(workflow["phases"], list)
            ):
                raise ValueError("invalid dashboard state structure")
            if any(
                not isinstance(phase, dict)
                or set(phase) != {"id", "title", "state"}
                or phase.get("state")
                not in {"pending", "in_progress", "completed", "failed"}
                for phase in workflow["phases"]
            ):
                raise ValueError("invalid dashboard state structure")

        if not all(
            isinstance(task, dict)
            and set(task) == {"id", "title", "state"}
            and task.get("state") in {"pending", "running", "completed", "failed"}
            for task in value["tasks"]
        ):
            raise ValueError("invalid dashboard state structure")
        return cls(copy.deepcopy(value))

    @classmethod
    def from_report(
        cls,
        report: dict[str, Any],
        *,
        revision: int,
        updated_at: str,
        started_at: str | None,
        elapsed_seconds: int | None,
    ) -> _DashboardRecord:
        return cls.from_mapping(
            {
                **copy.deepcopy(report),
                "started_at": started_at,
                "elapsed_seconds": elapsed_seconds,
                "revision": revision,
                "updated_at": updated_at,
            }
        )

    @property
    def revision(self) -> int:
        return self.payload["revision"]

    @property
    def updated_at(self) -> str:
        return self.payload["updated_at"]

    @property
    def started_at(self) -> str | None:
        value = self.payload.get("started_at")
        return value if isinstance(value, str) else None

    @property
    def elapsed_seconds(self) -> int | None:
        value = self.payload.get("elapsed_seconds")
        return value if type(value) is int else None

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.payload)

    def is_active(self) -> bool:
        workflow = self.payload.get("workflow")
        if workflow is not None:
            phases = workflow.get("phases", [])
            phase_states = [phase.get("state") for phase in phases]
            if any(state in {"pending", "in_progress"} for state in phase_states):
                return True
            # Failed is terminal. Requiring failed workflows to report 100%
            # would leave workspace selection locked forever.
            if phase_states and all(state == "completed" for state in phase_states):
                if float(workflow.get("progress_percent", 0)) < 100:
                    return True
            elif not phase_states:
                return True
        return any(
            task.get("state") in {"pending", "running"}
            for task in self.payload.get("tasks", [])
        )

    def has_started(self) -> bool:
        workflow = self.payload.get("workflow")
        return bool(
            workflow is not None
            and any(
                phase.get("state") == "in_progress"
                for phase in workflow.get("phases", [])
            )
        ) or any(
            task.get("state") == "running" for task in self.payload.get("tasks", [])
        )

    def is_terminal(self) -> bool:
        workflow = self.payload.get("workflow")
        tasks = self.payload.get("tasks", [])
        states = [phase.get("state") for phase in (workflow or {}).get("phases", [])]
        states.extend(task.get("state") for task in tasks)
        if "failed" in states:
            return True
        if workflow is None:
            return not any(state in {"pending", "running"} for state in states)
        return workflow.get("progress_percent") == 100 and all(
            state == "completed" for state in states
        )


@dataclass(frozen=True, slots=True)
class _DashboardStoreState:
    dashboards: dict[str, _DashboardRecord]
    agent_work_revision: int

    @classmethod
    def from_mapping(cls, value: Any) -> _DashboardStoreState:
        if not isinstance(value, dict) or value.get("version") != 1:
            raise ValueError("invalid dashboard state structure")
        dashboards = value.get("dashboards")
        revision = value.get("agent_work_revision")
        if not isinstance(dashboards, dict) or type(revision) is not int or revision < 0:
            raise ValueError("invalid dashboard state structure")
        parsed: dict[str, _DashboardRecord] = {}
        for project_id, dashboard in dashboards.items():
            if not isinstance(project_id, str):
                raise ValueError("invalid dashboard state structure")
            parsed[project_id] = _DashboardRecord.from_mapping(dashboard)
        return cls(parsed, revision)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "agent_work_revision": self.agent_work_revision,
            "dashboards": {
                project_id: dashboard.as_dict()
                for project_id, dashboard in self.dashboards.items()
            },
        }


@dataclass(frozen=True, slots=True)
class _Subscriber:
    queue: queue.Queue[ProjectEvent]
    project_id: str | None

    def accepts(self, item: ProjectEvent) -> bool:
        return self.project_id is None or self.project_id == item.project_id


class EventBroker:
    def __init__(
        self,
        *,
        subscriber_capacity: int = 128,
        dashboard_state_path: str | Path | None = None,
    ) -> None:
        self._capacity = subscriber_capacity
        self._lock = threading.Lock()
        self._dashboard_persist_lock = threading.Lock()
        self._next_id = 1
        self._subscribers: dict[int, _Subscriber] = {}
        self._next_subscriber_id = 1
        self._activities: dict[str, list[_ActivityRecord]] = {}
        self._activity_revisions: dict[str, int] = {}
        self._agent_dashboards: dict[str, _DashboardRecord] = {}
        self._dashboard_state_path = (
            Path(dashboard_state_path) if dashboard_state_path is not None else None
        )
        self._agent_work_revision = 0
        self._load_agent_dashboards()

    def _load_agent_dashboards(self) -> None:
        path = self._dashboard_state_path
        if path is None or not path.exists():
            return
        try:
            with path.open("r", encoding="utf-8") as stream:
                state = _DashboardStoreState.from_mapping(json.load(stream))
            self._agent_dashboards = state.dashboards
            self._agent_work_revision = state.agent_work_revision
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"CADYX agent dashboard state could not be loaded from {path}: {exc}"
            ) from exc

    @staticmethod
    def _dashboard_state_is_valid(dashboard: Any) -> bool:
        try:
            _DashboardRecord.from_mapping(dashboard)
        except (TypeError, ValueError):
            return False
        return True

    def _persist_agent_dashboards(
        self,
        dashboards: dict[str, _DashboardRecord],
        revision: int,
    ) -> None:
        path = self._dashboard_state_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: str | None = None
        state = _DashboardStoreState(dashboards, revision).as_dict()
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f"{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = stream.name
                json.dump(
                    state,
                    stream,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
        except OSError:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass
            raise

    @staticmethod
    def _offer_drop_oldest(
        subscriber: queue.Queue[ProjectEvent],
        item: ProjectEvent,
    ) -> None:
        if subscriber.full():
            try:
                subscriber.get_nowait()
            except queue.Empty:
                pass
        subscriber.put_nowait(item)

    def _fan_out_locked(self, item: ProjectEvent) -> None:
        for subscriber in self._subscribers.values():
            if not subscriber.accepts(item):
                continue
            self._offer_drop_oldest(subscriber.queue, item)

    def _emit_locked(
        self,
        event: str,
        project_id: str | None,
        data: dict[str, Any],
        *,
        occurred_at: str | None = None,
    ) -> ProjectEvent:
        item = ProjectEvent(
            id=self._next_id,
            event=event,
            project_id=project_id,
            data=data,
            occurred_at=occurred_at or datetime.now(timezone.utc).isoformat(),
        )
        self._next_id += 1
        self._fan_out_locked(item)
        return item

    def _agent_work_project_ids_locked(self) -> list[str]:
        return sorted(
            project_id
            for project_id, dashboard in self._agent_dashboards.items()
            if dashboard.is_active()
        )

    def agent_dashboard_is_active(self, project_id: str) -> bool:
        with self._lock:
            dashboard = self._agent_dashboards.get(project_id)
            return dashboard is not None and dashboard.is_active()

    def publish(
        self,
        event: str,
        project_id: str | None,
        data: dict[str, Any],
    ) -> ProjectEvent:
        with self._lock:
            if event == "agent.activity" and project_id is not None:
                revision = self._activity_revisions.get(project_id, 0) + 1
                self._activity_revisions[project_id] = revision
                data = {**data, "revision": revision}
                activities = self._activities.setdefault(project_id, [])
                record = _ActivityRecord.capture(data)
                existing = next(
                    (
                        index
                        for index, activity in enumerate(activities)
                        if activity.operation_id == record.operation_id
                    ),
                    None,
                )
                if existing is None:
                    activities.append(record)
                else:
                    activities[existing] = record
                del activities[:-128]
            return self._emit_locked(event, project_id, data)

    def activity_snapshot(self, project_id: str) -> list[dict[str, Any]]:
        return self.activity_state_snapshot(project_id)[0]

    def activity_state_snapshot(self, project_id: str) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            return (
                [record.as_dict() for record in self._activities.get(project_id, [])],
                self._activity_revisions.get(project_id, 0),
            )

    def activity_revision(self, project_id: str) -> int:
        with self._lock:
            return self._activity_revisions.get(project_id, 0)

    def publish_agent_dashboard(
        self,
        project_id: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        # Disk durability is intentionally serialized separately from the
        # broker lock. Dashboard fsync may be slow, but ordinary SSE events,
        # snapshots, and subscriber management should remain responsive.
        with self._dashboard_persist_lock:
            with self._lock:
                previous = self._agent_dashboards.get(project_id)
                revision = previous.revision + 1 if previous else 1
                now = datetime.now(timezone.utc)
                now_iso = now.isoformat()
                started_at = previous.started_at if previous else None
                elapsed_seconds = previous.elapsed_seconds if previous else None
                report = _DashboardRecord.from_report(
                    data,
                    revision=revision,
                    updated_at=now_iso,
                    started_at=None,
                    elapsed_seconds=None,
                )
                has_started = report.has_started()
                has_unfinished_work = report.is_active()
                terminal = report.is_terminal()
                if has_started and (started_at is None or elapsed_seconds is not None):
                    started_at = now_iso
                    elapsed_seconds = None
                elif has_unfinished_work and elapsed_seconds is not None:
                    # A newly queued workflow clears the previous run's frozen duration.
                    started_at = None
                    elapsed_seconds = None
                elif terminal and started_at is not None and elapsed_seconds is None:
                    try:
                        started = datetime.fromisoformat(started_at)
                    except ValueError:
                        started = now
                    elapsed_seconds = max(0, int((now - started).total_seconds()))
                dashboard = _DashboardRecord.from_report(
                    data,
                    revision=revision,
                    updated_at=now_iso,
                    started_at=started_at,
                    elapsed_seconds=elapsed_seconds,
                )
                dashboards = dict(self._agent_dashboards)
                dashboards[project_id] = dashboard
                work_revision = self._agent_work_revision + 1

            self._persist_agent_dashboards(dashboards, work_revision)

            with self._lock:
                self._agent_dashboards = dashboards
                self._agent_work_revision = work_revision
                dashboard_payload = dashboard.as_dict()
                self._emit_locked(
                    "agent.dashboard",
                    project_id,
                    dashboard_payload,
                    occurred_at=dashboard.updated_at,
                )
                active_project_ids = self._agent_work_project_ids_locked()
                self._emit_locked(
                    "workspace.agent-work",
                    None,
                    {
                        "active": bool(active_project_ids),
                        "revision": work_revision,
                        "project_ids": active_project_ids,
                    },
                    occurred_at=dashboard.updated_at,
                )
                return dashboard.as_dict()

    def agent_work_snapshot(self) -> dict[str, Any]:
        with self._lock:
            active_projects = self._agent_work_project_ids_locked()
            return {
                "active": bool(active_projects),
                "active_project_ids": active_projects,
                "revision": self._agent_work_revision,
            }

    def agent_dashboard_snapshot(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            dashboard = self._agent_dashboards.get(project_id)
            if dashboard is None:
                return {
                    "workflow": None,
                    "tasks": [],
                    "started_at": None,
                    "elapsed_seconds": None,
                    "revision": 0,
                    "updated_at": None,
                }
            return dashboard.as_dict()

    def agent_dashboard_revision(self, project_id: str) -> int:
        with self._lock:
            dashboard = self._agent_dashboards.get(project_id)
            return dashboard.revision if dashboard else 0

    def subscribe(
        self,
        project_id: str | None = None,
    ) -> tuple[int, queue.Queue[ProjectEvent]]:
        subscriber_queue: queue.Queue[ProjectEvent] = queue.Queue(maxsize=self._capacity)
        with self._lock:
            subscriber_id = self._next_subscriber_id
            self._next_subscriber_id += 1
            self._subscribers[subscriber_id] = _Subscriber(
                queue=subscriber_queue,
                project_id=project_id,
            )
        return subscriber_id, subscriber_queue

    def unsubscribe(self, subscriber_id: int) -> None:
        with self._lock:
            self._subscribers.pop(subscriber_id, None)
