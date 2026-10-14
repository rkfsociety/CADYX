"""Bounded in-memory event fan-out used by the local SSE endpoint."""

from __future__ import annotations

import copy
import queue
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
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
    def __init__(self, *, subscriber_capacity: int = 128) -> None:
        self._capacity = subscriber_capacity
        self._lock = threading.Lock()
        self._next_id = 1
        self._subscribers: dict[int, tuple[queue.Queue[ProjectEvent], str | None]] = {}
        self._next_subscriber_id = 1
        self._activities: dict[str, list[dict[str, Any]]] = {}

    def publish(self, event: str, project_id: str | None, data: dict[str, Any]) -> ProjectEvent:
        with self._lock:
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
        with self._lock:
            return copy.deepcopy(self._activities.get(project_id, []))

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
