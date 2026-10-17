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
        self._subscribers: dict[int, tuple[queue.Queue[ProjectEvent], str | None]] = {}
        self._next_subscriber_id = 1
        self._activities: dict[str, list[dict[str, Any]]] = {}
        self._activity_revisions: dict[str, int] = {}
        self._agent_dashboards: dict[str, dict[str, Any]] = {}
        self._dashboard_state_path = Path(dashboard_state_path) if dashboard_state_path is not None else None
        self._agent_work_revision = 0
        self._load_agent_dashboards()

    def _load_agent_dashboards(self) -> None:
        path = self._dashboard_state_path
        if path is None or not path.exists():
            return
        try:
            with path.open("r", encoding="utf-8") as stream:
                state = json.load(stream)
            if not isinstance(state, dict):
                raise ValueError("invalid dashboard state structure")
            dashboards = state["dashboards"]
            revision = state["agent_work_revision"]
            if (
                state.get("version") != 1
                or not isinstance(dashboards, dict)
                or any(not isinstance(project_id, str) or not self._dashboard_state_is_valid(value)
                       for project_id, value in dashboards.items())
                or type(revision) is not int
                or revision < 0
            ):
                raise ValueError("invalid dashboard state structure")
            self._agent_dashboards = copy.deepcopy(dashboards)
            self._agent_work_revision = revision
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"CADYX agent dashboard state could not be loaded from {path}: {exc}") from exc

    @staticmethod
    def _dashboard_state_is_valid(dashboard: Any) -> bool:
        required = {"workflow", "tasks", "revision", "updated_at"}
        optional = {"started_at", "elapsed_seconds"}
        if (
            not isinstance(dashboard, dict)
            or not required.issubset(dashboard)
            or set(dashboard) - required - optional
            or type(dashboard.get("revision")) is not int
            or dashboard["revision"] < 1
            or not isinstance(dashboard.get("updated_at"), str)
            or not isinstance(dashboard.get("tasks"), list)
        ):
            return False
        started_at = dashboard.get("started_at")
        elapsed_seconds = dashboard.get("elapsed_seconds")
        if started_at is not None:
            if not isinstance(started_at, str):
                return False
            try:
                datetime.fromisoformat(started_at)
            except ValueError:
                return False
        if elapsed_seconds is not None and (type(elapsed_seconds) is not int or elapsed_seconds < 0):
            return False
        if elapsed_seconds is not None and started_at is None:
            return False
        workflow = dashboard["workflow"]
        if workflow is not None:
            if (
                not isinstance(workflow, dict)
                or set(workflow) != {"title", "progress_percent", "eta_seconds", "phases"}
                or not isinstance(workflow["title"], str)
                or isinstance(workflow["progress_percent"], bool)
                or not isinstance(workflow["progress_percent"], (int, float))
                or not math.isfinite(workflow["progress_percent"])
                or not 0 <= workflow["progress_percent"] <= 100
                or (workflow["eta_seconds"] is not None and type(workflow["eta_seconds"]) is not int)
                or not isinstance(workflow["phases"], list)
            ):
                return False
            if any(
                not isinstance(phase, dict)
                or set(phase) != {"id", "title", "state"}
                or phase.get("state") not in {"pending", "in_progress", "completed", "failed"}
                for phase in workflow["phases"]
            ):
                return False
        return all(
            isinstance(task, dict)
            and set(task) == {"id", "title", "state"}
            and task.get("state") in {"pending", "running", "completed", "failed"}
            for task in dashboard["tasks"]
        )

    def _persist_agent_dashboards(self, dashboards: dict[str, dict[str, Any]], revision: int) -> None:
        path = self._dashboard_state_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: str | None = None
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
                    {"version": 1, "agent_work_revision": revision, "dashboards": dashboards},
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
    def _dashboard_is_active(dashboard: dict[str, Any]) -> bool:
        workflow = dashboard.get("workflow")
        if workflow is not None:
            phases = workflow.get("phases", [])
            phase_states = [phase.get("state") for phase in phases]
            if any(state in {"pending", "in_progress"} for state in phase_states):
                return True
            # Failed is a terminal state, not active work. Requiring failed
            # workflows to report 100% would leave workspace selection locked
            # forever after an interrupted/abandoned task.
            if phase_states and all(state == "completed" for state in phase_states):
                if float(workflow.get("progress_percent", 0)) < 100:
                    return True
            elif not phase_states:
                return True
        return any(task.get("state") in {"pending", "running"} for task in dashboard.get("tasks", []))

    @staticmethod
    def _dashboard_has_started(dashboard: dict[str, Any]) -> bool:
        workflow = dashboard.get("workflow")
        return bool(
            workflow is not None
            and any(phase.get("state") == "in_progress" for phase in workflow.get("phases", []))
        ) or any(task.get("state") == "running" for task in dashboard.get("tasks", []))

    @staticmethod
    def _dashboard_is_terminal(dashboard: dict[str, Any]) -> bool:
        workflow = dashboard.get("workflow")
        tasks = dashboard.get("tasks", [])
        states = [phase.get("state") for phase in (workflow or {}).get("phases", [])]
        states.extend(task.get("state") for task in tasks)
        if "failed" in states:
            return True
        if workflow is None:
            return not any(state in {"pending", "running"} for state in states)
        return (
            workflow.get("progress_percent") == 100
            and all(state == "completed" for state in states)
        )

    def _agent_work_active_locked(self) -> bool:
        return bool(self._agent_work_project_ids_locked())

    def _agent_work_project_ids_locked(self) -> list[str]:
        return sorted(
            project_id for project_id, dashboard in self._agent_dashboards.items()
            if self._dashboard_is_active(dashboard)
        )

    def agent_dashboard_is_active(self, project_id: str) -> bool:
        with self._lock:
            dashboard = self._agent_dashboards.get(project_id)
            return dashboard is not None and self._dashboard_is_active(dashboard)

    def publish(self, event: str, project_id: str | None, data: dict[str, Any]) -> ProjectEvent:
        with self._lock:
            if event == "agent.activity" and project_id is not None:
                revision = self._activity_revisions.get(project_id, 0) + 1
                self._activity_revisions[project_id] = revision
                data = {**data, "revision": revision}
            item = ProjectEvent(
                id=self._next_id,
                event=event,
                project_id=project_id,
                data=data,
                occurred_at=datetime.now(timezone.utc).isoformat(),
            )
            self._next_id += 1
            if event == "agent.activity" and project_id is not None:
                activities = self._activities.setdefault(project_id, [])
                operation_id = data.get("operation_id")
                existing = next((index for index, activity in enumerate(activities)
                                 if activity.get("operation_id") == operation_id), None)
                snapshot = copy.deepcopy(data)
                if existing is None:
                    activities.append(snapshot)
                else:
                    activities[existing] = snapshot
                del activities[:-128]
            for subscriber, filter_project in self._subscribers.values():
                if filter_project is not None and filter_project != project_id:
                    continue
                if subscriber.full():
                    try:
                        subscriber.get_nowait()
                    except queue.Empty:
                        pass
                subscriber.put_nowait(item)
            return item

    def activity_snapshot(self, project_id: str) -> list[dict[str, Any]]:
        return self.activity_state_snapshot(project_id)[0]

    def activity_state_snapshot(self, project_id: str) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            return (
                copy.deepcopy(self._activities.get(project_id, [])),
                self._activity_revisions.get(project_id, 0),
            )

    def activity_revision(self, project_id: str) -> int:
        with self._lock:
            return self._activity_revisions.get(project_id, 0)

    def publish_agent_dashboard(self, project_id: str, data: dict[str, Any]) -> dict[str, Any]:
        # Disk durability is intentionally serialized separately from the
        # broker lock. Dashboard fsync may be slow, but ordinary SSE events,
        # snapshots, and subscriber management should remain responsive.
        with self._dashboard_persist_lock:
            with self._lock:
                previous = self._agent_dashboards.get(project_id)
                revision = previous["revision"] + 1 if previous else 1
                now = datetime.now(timezone.utc)
                started_at = previous.get("started_at") if previous else None
                elapsed_seconds = previous.get("elapsed_seconds") if previous else None
                has_started = self._dashboard_has_started(data)
                has_unfinished_work = self._dashboard_is_active(data)
                terminal = self._dashboard_is_terminal(data)
                if has_started and (started_at is None or elapsed_seconds is not None):
                    started_at = now.isoformat()
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
                dashboard = {
                    **copy.deepcopy(data),
                    "started_at": started_at,
                    "elapsed_seconds": elapsed_seconds,
                    "revision": revision,
                    "updated_at": now.isoformat(),
                }
                dashboards = {**self._agent_dashboards, project_id: dashboard}
                work_revision = self._agent_work_revision + 1

            self._persist_agent_dashboards(dashboards, work_revision)

            with self._lock:
                self._agent_dashboards = dashboards
                self._agent_work_revision = work_revision
                item = ProjectEvent(
                    id=self._next_id,
                    event="agent.dashboard",
                    project_id=project_id,
                    data=copy.deepcopy(dashboard),
                    occurred_at=dashboard["updated_at"],
                )
                self._next_id += 1
                for subscriber, filter_project in self._subscribers.values():
                    if filter_project is not None and filter_project != project_id:
                        continue
                    if subscriber.full():
                        try:
                            subscriber.get_nowait()
                        except queue.Empty:
                            pass
                    subscriber.put_nowait(item)
                active_project_ids = self._agent_work_project_ids_locked()
                work_item = ProjectEvent(
                    id=self._next_id,
                    event="workspace.agent-work",
                    project_id=None,
                    data={"active": bool(active_project_ids), "revision": work_revision,
                          "project_ids": active_project_ids},
                    occurred_at=dashboard["updated_at"],
                )
                self._next_id += 1
                for subscriber, filter_project in self._subscribers.values():
                    if filter_project is not None:
                        continue
                    if subscriber.full():
                        try:
                            subscriber.get_nowait()
                        except queue.Empty:
                            pass
                    subscriber.put_nowait(work_item)
                return copy.deepcopy(dashboard)

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
                return {"workflow": None, "tasks": [], "started_at": None,
                        "elapsed_seconds": None, "revision": 0, "updated_at": None}
            return copy.deepcopy(dashboard)

    def agent_dashboard_revision(self, project_id: str) -> int:
        with self._lock:
            dashboard = self._agent_dashboards.get(project_id)
            return dashboard["revision"] if dashboard else 0

    def subscribe(self, project_id: str | None = None) -> tuple[int, queue.Queue[ProjectEvent]]:
        subscriber: queue.Queue[ProjectEvent] = queue.Queue(maxsize=self._capacity)
        with self._lock:
            subscriber_id = self._next_subscriber_id
            self._next_subscriber_id += 1
            self._subscribers[subscriber_id] = (subscriber, project_id)
        return subscriber_id, subscriber

    def unsubscribe(self, subscriber_id: int) -> None:
        with self._lock:
            self._subscribers.pop(subscriber_id, None)
