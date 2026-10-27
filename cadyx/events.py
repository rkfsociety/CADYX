"""Bounded in-memory event fan-out used by the local SSE endpoint."""

from __future__ import annotations

import copy
import json
import math
import queue
import uuid
from collections import deque
import sqlite3
import threading
from contextlib import closing
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
        replay_capacity: int = 256,
        replay_max_bytes: int = 1024 * 1024,
        dashboard_state_path: str | Path | None = None,
        dashboard_database_path: str | Path | None = None,
    ) -> None:
        if type(subscriber_capacity) is not int or subscriber_capacity < 1:
            raise ValueError("subscriber_capacity must be positive")
        if type(replay_capacity) is not int or replay_capacity < 1:
            raise ValueError("replay_capacity must be positive")
        if type(replay_max_bytes) is not int or replay_max_bytes < 1:
            raise ValueError("replay_max_bytes must be positive")
        self._capacity = subscriber_capacity
        self._replay_capacity = replay_capacity
        self._replay_max_bytes = replay_max_bytes
        self._replay: deque[tuple[int, str | None, bytes]] = deque()
        self._replay_bytes = 0
        self._replay_floor = 0
        self._stream_epoch = uuid.uuid4().hex
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
        self._dashboard_database_path = (
            Path(dashboard_database_path)
            if dashboard_database_path is not None
            else self._dashboard_state_path.with_suffix(".sqlite3")
            if self._dashboard_state_path is not None
            else None
        )
        self._agent_work_revision = 0
        self._load_agent_dashboards()

    def _load_agent_dashboards(self) -> None:
        path = self._dashboard_database_path
        if path is None or not path.exists():
            if path is None:
                return
            self._migrate_legacy_dashboard_state()
            return
        try:
            with closing(self._connect_dashboard_database()) as connection, connection:
                self._create_dashboard_schema(connection)
                dashboards = {
                    project_id: _DashboardRecord.from_mapping(json.loads(payload))
                    for project_id, payload in connection.execute(
                        "SELECT project_id, payload FROM dashboards"
                    )
                }
                row = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'agent_work_revision'"
                ).fetchone()
                revision = row[0] if row is not None else 0
                if type(revision) is not int or revision < 0:
                    raise ValueError("invalid dashboard state structure")
            self._agent_dashboards = dashboards
            self._agent_work_revision = revision
        except (OSError, sqlite3.Error, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"CADYX agent dashboard state could not be loaded from {path}: {exc}"
            ) from exc

    def _connect_dashboard_database(self) -> sqlite3.Connection:
        path = self._dashboard_database_path
        if path is None:
            raise RuntimeError("dashboard database path is not configured")
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=30.0)
        connection.execute("PRAGMA auto_vacuum = INCREMENTAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @staticmethod
    def _create_dashboard_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS dashboards ("
            "project_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS metadata ("
            "key TEXT PRIMARY KEY, value INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT OR IGNORE INTO metadata (key, value) VALUES ('agent_work_revision', 0)"
        )

    def _migrate_legacy_dashboard_state(self) -> None:
        legacy_path = self._dashboard_state_path
        database_path = self._dashboard_database_path
        if legacy_path is None or database_path is None or not legacy_path.exists():
            return
        try:
            with legacy_path.open("r", encoding="utf-8") as stream:
                state = _DashboardStoreState.from_mapping(json.load(stream))
            with closing(self._connect_dashboard_database()) as connection, connection:
                self._create_dashboard_schema(connection)
                connection.executemany(
                    "INSERT INTO dashboards (project_id, payload) VALUES (?, ?)",
                    [
                        (project_id, json.dumps(dashboard.as_dict(), ensure_ascii=False,
                                                allow_nan=False, separators=(",", ":")))
                        for project_id, dashboard in state.dashboards.items()
                    ],
                )
                connection.execute(
                    "UPDATE metadata SET value = ? WHERE key = 'agent_work_revision'",
                    (state.agent_work_revision,),
                )
            self._agent_dashboards = state.dashboards
            self._agent_work_revision = state.agent_work_revision
        except (OSError, sqlite3.Error, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"CADYX agent dashboard state could not be loaded from {legacy_path}: {exc}"
            ) from exc

    @staticmethod
    def _dashboard_state_is_valid(dashboard: Any) -> bool:
        try:
            _DashboardRecord.from_mapping(dashboard)
        except (TypeError, ValueError):
            return False
        return True

    def _persist_agent_dashboard(
        self,
        project_id: str,
        dashboard: _DashboardRecord,
        revision: int,
    ) -> None:
        path = self._dashboard_database_path
        if path is None:
            return
        try:
            with closing(self._connect_dashboard_database()) as connection:
                with connection:
                    self._create_dashboard_schema(connection)
                    connection.execute(
                        "INSERT INTO dashboards (project_id, payload) VALUES (?, ?) "
                        "ON CONFLICT(project_id) DO UPDATE SET payload = excluded.payload",
                        (project_id, json.dumps(dashboard.as_dict(), ensure_ascii=False,
                                                allow_nan=False, separators=(",", ":"))),
                    )
                    connection.execute(
                        "UPDATE metadata SET value = ? WHERE key = 'agent_work_revision'",
                        (revision,),
                    )
                connection.execute("PRAGMA incremental_vacuum(64)")
        except (OSError, sqlite3.Error):
            raise

    def _persist_agent_dashboard_prune(self, project_ids: list[str], revision: int) -> None:
        path = self._dashboard_database_path
        if path is None:
            return
        try:
            with closing(self._connect_dashboard_database()) as connection, connection:
                self._create_dashboard_schema(connection)
                connection.executemany(
                    "DELETE FROM dashboards WHERE project_id = ?",
                    [(project_id,) for project_id in project_ids],
                )
                connection.execute(
                    "UPDATE metadata SET value = ? WHERE key = 'agent_work_revision'",
                    (revision,),
                )
        except (OSError, sqlite3.Error):
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
        # Do not consume an event ID until the payload is serializable.
        # A rejected event must not create a gap in SSE cursors.
        encoded = json.dumps(
            item.as_dict(), ensure_ascii=False, allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self._next_id += 1
        size = len(encoded)
        if size > self._replay_max_bytes:
            # This event cannot be replayed; older cursors must resynchronize.
            self._replay.clear()
            self._replay_bytes = 0
            self._replay_floor = item.id
        else:
            while self._replay and (
                len(self._replay) >= self._replay_capacity
                or self._replay_bytes + size > self._replay_max_bytes
            ):
                removed_id, _, removed_payload = self._replay.popleft()
                self._replay_bytes -= len(removed_payload)
                self._replay_floor = removed_id
            self._replay.append((item.id, project_id, encoded))
            self._replay_bytes += size
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
                data = {**data, "revision": revision}
                record = _ActivityRecord.capture(data)
                activities = self._activities.get(project_id, [])
                existing = next(
                    (
                        index
                        for index, activity in enumerate(activities)
                        if activity.operation_id == record.operation_id
                    ),
                    None,
                )
                # Commit the activity snapshot only after the event was
                # successfully encoded and accepted for delivery.
                published = self._emit_locked(event, project_id, data)
                self._activity_revisions[project_id] = revision
                if existing is None:
                    activities = self._activities.setdefault(project_id, [])
                    activities.append(record)
                else:
                    activities[existing] = record
                del activities[:-128]
                return published
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

            self._persist_agent_dashboard(project_id, dashboard, work_revision)

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

    def prune_agent_dashboards(self, valid_project_ids: set[str]) -> list[str]:
        """Remove persisted workflow reports for projects absent from the workspace."""
        with self._dashboard_persist_lock:
            with self._lock:
                removed = sorted(set(self._agent_dashboards) - valid_project_ids)
                if not removed:
                    return []
                dashboards = {
                    project_id: dashboard
                    for project_id, dashboard in self._agent_dashboards.items()
                    if project_id in valid_project_ids
                }
                work_revision = self._agent_work_revision + 1

            self._persist_agent_dashboard_prune(removed, work_revision)

            with self._lock:
                self._agent_dashboards = dashboards
                self._agent_work_revision = work_revision
                active_project_ids = self._agent_work_project_ids_locked()
                self._emit_locked(
                    "workspace.agent-work",
                    None,
                    {
                        "active": bool(active_project_ids),
                        "revision": work_revision,
                        "project_ids": active_project_ids,
                    },
                )
            return removed

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

    def event_cursor(self, event_id: int) -> str:
        """Opaque SSE cursor; a restarted server has a different epoch."""
        return f"{self._stream_epoch}:{event_id}"

    def subscribe(
        self,
        project_id: str | None = None,
        *,
        last_event_id: str | None = None,
    ) -> tuple[int, queue.Queue[ProjectEvent]]:
        subscriber_queue: queue.Queue[ProjectEvent] = queue.Queue(maxsize=self._capacity)
        with self._lock:
            # Replay and subscriber enrollment are atomic with publication.
            if last_event_id is not None:
                reason: str | None = None
                cursor_id = -1
                if not isinstance(last_event_id, str) or len(last_event_id) > 80:
                    reason = "invalid_cursor"
                else:
                    epoch, separator, number = last_event_id.partition(":")
                    if not separator or not number.isascii() or not number.isdecimal():
                        reason = "invalid_cursor"
                    elif epoch != self._stream_epoch:
                        reason = "stream_changed"
                    else:
                        cursor_id = int(number)
                        if cursor_id > self._next_id - 1:
                            reason = "invalid_cursor"
                        elif self._replay_floor > 0 and cursor_id <= self._replay_floor:
                            reason = "history_expired"
                if reason is None:
                    replay = [
                        json.loads(payload)
                        for event_id, event_project_id, payload in self._replay
                        if event_id > cursor_id
                        and (project_id is None or event_project_id == project_id)
                    ]
                    if len(replay) > self._capacity:
                        reason = "subscriber_capacity"
                    else:
                        for row in replay:
                            subscriber_queue.put_nowait(ProjectEvent(
                                id=row["id"],
                                event=row["event"],
                                project_id=row["project_id"],
                                data=row["data"],
                                occurred_at=row["occurred_at"],
                            ))
                if reason is not None:
                    latest_id = self._next_id - 1
                    subscriber_queue.put_nowait(ProjectEvent(
                        id=latest_id,
                        event="stream.gap",
                        project_id=project_id,
                        data={"reason": reason, "latest_event_id": latest_id},
                        occurred_at=datetime.now(timezone.utc).isoformat(),
                    ))
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
