"""Durable local multi-process edit leases with stale-lease recovery instead of silent release."""

from __future__ import annotations

import math
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterator
from uuid import uuid4

from .errors import ConflictError, LeaseRequiredError, NotFoundError, ValidationError
from .models import EditLease, OwnerType, lock_state_for


class EditLeaseManager:
    def __init__(
        self, project_directory: Callable[[str], Path], *,
        timeout_seconds: float = 60.0, clock: Callable[[], float] = time.time,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds
        self._clock = clock
        self._project_directory = project_directory
        self._local = threading.local()

    @contextmanager
    def guard(self, project_id: str) -> Iterator[sqlite3.Connection]:
        """Hold the same transaction through lease validation and the complete command.

        Nested calls on this thread reuse it. SQLite releases the OS lock on a
        process crash, while the last committed lease remains for recovery.
        """
        connections = getattr(self._local, "connections", None)
        if connections is None:
            connections = self._local.connections = {}
        if project_id in connections:
            yield connections[project_id]
            return
        path = self._project_directory(project_id) / ".cadyx-leases.sqlite3"
        if not path.parent.is_dir() or not (path.parent / "project.json").is_file():
            raise NotFoundError("Project was not found.")
        if path.is_symlink():
            raise ValidationError("Lease database must not be a symbolic link.")
        connection = sqlite3.connect(path, timeout=5, isolation_level=None)
        try:
            try:
                connection.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                if exc.sqlite_errorcode == sqlite3.SQLITE_BUSY:
                    raise ConflictError(
                        "Project has an operation in progress; retry later.", code="PROJECT_BUSY",
                    ) from exc
                raise
            connection.execute("""CREATE TABLE IF NOT EXISTS lease (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                owner_type TEXT NOT NULL, owner_id TEXT NOT NULL,
                acquired_at REAL NOT NULL, heartbeat_at REAL NOT NULL,
                lease_id TEXT NOT NULL, timeout_seconds REAL NOT NULL
            )""")
            connections[project_id] = connection
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connections.pop(project_id, None)
            connection.close()

    def _read(self, project_id: str) -> EditLease | None:
        row = self._local.connections[project_id].execute(
            "SELECT owner_type, owner_id, acquired_at, heartbeat_at, lease_id FROM lease"
        ).fetchone()
        return EditLease(project_id, OwnerType(row[0]), *row[1:]) if row else None

    def _expired(self, lease: EditLease) -> bool:
        timeout = self._local.connections[lease.project_id].execute(
            "SELECT timeout_seconds FROM lease"
        ).fetchone()[0]
        return self._clock() - lease.heartbeat_at > timeout

    def snapshot(self, project_id: str) -> dict[str, object]:
        with self.guard(project_id):
            lease = self._read(project_id)
            expired = lease is not None and self._expired(lease)
            return {
                "state": lock_state_for(lease).value,
                "lease": lease.as_dict(expired=expired) if lease else None,
                "recovery_required": bool(expired),
            }

    def get(self, project_id: str) -> EditLease | None:
        with self.guard(project_id):
            return self._read(project_id)

    def acquire(self, project_id: str, owner_type: str, owner_id: str) -> EditLease:
        try:
            owner = OwnerType(owner_type)
        except (TypeError, ValueError) as exc:
            raise ValidationError("owner_type must be agent, human, or system.") from exc
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 160:
            raise ValidationError("owner_id must be a non-empty string of at most 160 characters.")

        with self.guard(project_id):
            current = self._read(project_id)
            if current is not None:
                if self._expired(current):
                    raise ConflictError(
                        "The previous edit lease expired; validate the saved project before recovery.",
                        code="LEASE_RECOVERY_REQUIRED",
                    )
                raise ConflictError(
                    f"Project is already locked by {current.owner_type.value} '{current.owner_id}'.",
                    code="PROJECT_LOCKED",
                )
            now = self._clock()
            lease = EditLease(project_id, owner, owner_id.strip(), now, now, str(uuid4()))
            self._local.connections[project_id].execute(
                "INSERT INTO lease VALUES (1, ?, ?, ?, ?, ?, ?)",
                (lease.owner_type.value, lease.owner_id, lease.acquired_at,
                 lease.heartbeat_at, lease.lease_id, self.timeout_seconds),
            )
            return lease

    def require(self, project_id: str, lease_id: str | None) -> EditLease:
        if not isinstance(lease_id, str) or not lease_id:
            raise LeaseRequiredError("A current edit lease is required.")
        with self.guard(project_id):
            lease = self._read(project_id)
            if lease is None:
                raise LeaseRequiredError("Project has no active edit lease.")
            if lease.lease_id != lease_id:
                raise ConflictError("The supplied lease does not own this project.", code="PROJECT_LOCKED")
            if self._expired(lease):
                raise ConflictError(
                    "Edit lease expired; validate the saved project before recovery.",
                    code="LEASE_EXPIRED",
                )
            return lease

    def heartbeat(self, project_id: str, lease_id: str) -> EditLease:
        with self.guard(project_id):
            lease = self.require(project_id, lease_id)
            updated = replace(lease, heartbeat_at=self._clock())
            self._local.connections[project_id].execute(
                "UPDATE lease SET heartbeat_at = ? WHERE singleton = 1", (updated.heartbeat_at,)
            )
            return updated

    def release(self, project_id: str, lease_id: str) -> EditLease:
        with self.guard(project_id):
            lease = self.require(project_id, lease_id)
            self._local.connections[project_id].execute("DELETE FROM lease")
            return lease

    def release_expired(self, project_id: str) -> EditLease:
        with self.guard(project_id):
            lease = self._read(project_id)
            if lease is None or not self._expired(lease):
                raise ConflictError("Project has no expired lease to recover.", code="LEASE_NOT_EXPIRED")
            self._local.connections[project_id].execute("DELETE FROM lease")
            return lease
