"""Durable local multi-process edit leases with stale-lease recovery instead of silent release."""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterator
from uuid import UUID, uuid4

from .errors import ConflictError, LeaseRequiredError, NotFoundError, ValidationError
from .models import EditLease, OwnerType, lock_state_for


def _sqlite_contention(exc: sqlite3.OperationalError) -> bool:
    """Recognize base and extended SQLite BUSY/LOCKED result codes."""
    code = getattr(exc, "sqlite_errorcode", None)
    return isinstance(code, int) and (code & 0xFF) in {
        sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED,
    }


def _project_database_uri(path: Path) -> str:
    """Do not recreate a database deleted between the existence check and open."""
    mode = "rw" if path.exists() else "rwc"
    return f"{path.absolute().as_uri()}?mode={mode}"


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
    def guard(self, project_id: str, *, allow_deleting: bool = False) -> Iterator[sqlite3.Connection]:
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
        project_directory = self._project_directory(project_id)
        if (project_directory / ".cadyx-deleting").exists():
            raise NotFoundError("Project is being deleted.")
        path = project_directory / ".cadyx-leases.sqlite3"
        if not path.parent.is_dir() or not (path.parent / "project.json").is_file():
            raise NotFoundError("Project was not found.")
        if path.is_symlink():
            raise ValidationError("Lease database must not be a symbolic link.")
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                _project_database_uri(path), uri=True, timeout=5, isolation_level=None,
            )
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""CREATE TABLE IF NOT EXISTS lease (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                owner_type TEXT NOT NULL, owner_id TEXT NOT NULL,
                acquired_at REAL NOT NULL, heartbeat_at REAL NOT NULL,
                lease_id TEXT NOT NULL, timeout_seconds REAL NOT NULL
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS mutation_idempotency (
                request_id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                command_hash TEXT NOT NULL,
                expected_revision INTEGER,
                outcome TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )""")
            if (project_directory / ".cadyx-deleting").exists():
                raise NotFoundError("Project is being deleted.")
            connections[project_id] = connection
            yield connection
            # Deletion may start while the caller holds this transaction.
            # Do not publish lease changes after the project was fenced.
            if not allow_deleting and (project_directory / ".cadyx-deleting").exists():
                raise NotFoundError("Project is being deleted.")
            connection.execute("COMMIT")
        except BaseException as exc:
            if connection is not None and connection.in_transaction:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    # Closing the connection still rolls back; preserve the original failure.
                    pass
            if isinstance(exc, sqlite3.OperationalError) and _sqlite_contention(exc):
                raise ConflictError(
                    "Project has an operation in progress; retry later.", code="PROJECT_BUSY",
                ) from exc
            raise
        finally:
            connections.pop(project_id, None)
            if connection is not None:
                connection.close()

    def _read(self, project_id: str) -> EditLease | None:
        row = self._local.connections[project_id].execute(
            "SELECT owner_type, owner_id, acquired_at, heartbeat_at, lease_id FROM lease"
        ).fetchone()
        return EditLease(project_id, OwnerType(row[0]), *row[1:]) if row else None

    @contextmanager
    def agent_control(self, project_id: str) -> Iterator[sqlite3.Connection]:
        """Independent durable signals: never wait for the document transaction."""
        project_directory = self._project_directory(project_id)
        if (project_directory / ".cadyx-deleting").exists():
            raise NotFoundError("Project is being deleted.")
        path = project_directory / ".cadyx-agent.sqlite3"
        if not path.parent.is_dir() or not (path.parent / "project.json").is_file():
            raise NotFoundError("Project was not found.")
        if path.is_symlink():
            raise ValidationError("Agent control database must not be a symbolic link.")
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(_project_database_uri(path), uri=True, timeout=5)
            with connection:
                connection.execute("""CREATE TABLE IF NOT EXISTS control (
                    lease_id TEXT PRIMARY KEY, stopped INTEGER NOT NULL,
                    revision_required INTEGER NOT NULL
                )""")
                if (project_directory / ".cadyx-deleting").exists():
                    raise NotFoundError("Project is being deleted.")
                yield connection
                if (project_directory / ".cadyx-deleting").exists():
                    raise NotFoundError("Project is being deleted.")
        except sqlite3.OperationalError as exc:
            if _sqlite_contention(exc):
                raise ConflictError("Agent control is busy; retry later.", code="PROJECT_BUSY") from exc
            raise
        finally:
            if connection is not None:
                connection.close()

    def request_agent_stop(self, project_id: str, lease_id: str) -> None:
        try:
            UUID(lease_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValidationError("lease_id must be a UUID.") from exc
        with self.agent_control(project_id) as connection:
            connection.execute("""INSERT INTO control VALUES (?, 1, 0)
                ON CONFLICT(lease_id) DO UPDATE SET stopped = 1""", (lease_id,))

    def check_agent_command(self, project_id: str, lease_id: str) -> bool:
        with self.agent_control(project_id) as connection:
            row = connection.execute(
                "SELECT stopped, revision_required FROM control WHERE lease_id = ?", (lease_id,),
            ).fetchone()
        if row and row[0]:
            raise ConflictError("Agent work is stopping; no new commands are allowed.", code="AGENT_STOPPING")
        return bool(row and row[1])

    def mark_resumed(self, project_id: str, lease_id: str) -> None:
        with self.agent_control(project_id) as connection:
            connection.execute("DELETE FROM control WHERE lease_id != ?", (lease_id,))
            connection.execute("INSERT INTO control VALUES (?, 0, 1)", (lease_id,))

    def release_stopped_agent(self, project_id: str, lease_id: str) -> EditLease:
        with self.guard(project_id):
            lease = self._read(project_id)
            if lease is None or lease.lease_id != lease_id or lease.owner_type is not OwnerType.AGENT:
                raise ConflictError("The supplied lease is not the current agent lease.", code="PROJECT_LOCKED")
            self._local.connections[project_id].execute("DELETE FROM lease")
            return lease

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

    def snapshot_for_listing(self, project_id: str) -> dict[str, object]:
        """Read a display-only lease snapshot without the document lease transaction.

        SQLite needs a writable connection when a crashed writer leaves a hot rollback
        journal. This SELECT does not modify lease content; opening the database lets
        SQLite recover its journal and return the last committed snapshot."""
        project_directory = self._project_directory(project_id)
        if (project_directory / ".cadyx-deleting").exists():
            raise NotFoundError("Project is being deleted.")
        path = project_directory / ".cadyx-leases.sqlite3"
        if not path.parent.is_dir() or not (path.parent / "project.json").is_file():
            raise NotFoundError("Project was not found.")
        if path.is_symlink():
            raise ValidationError("Lease database must not be a symbolic link.")
        if not path.exists():
            if (project_directory / ".cadyx-deleting").exists():
                raise NotFoundError("Project is being deleted.")
            return {"state": "FREE", "lease": None, "recovery_required": False}

        connection: sqlite3.Connection | None = None
        try:
            # Listings must not recreate a lease database if deletion removes it
            # after the existence check but before SQLite opens the file.
            connection = sqlite3.connect(
                f"{path.absolute().as_uri()}?mode=rw",
                uri=True, timeout=0, isolation_level=None,
            )
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'lease'"
            ).fetchone()
            if table is None:
                row = None
            else:
                row = connection.execute(
                    "SELECT owner_type, owner_id, acquired_at, heartbeat_at, lease_id, timeout_seconds "
                    "FROM lease WHERE singleton = 1"
                ).fetchone()
        except sqlite3.OperationalError as exc:
            if _sqlite_contention(exc):
                return {"state": "BUSY", "lease": None, "recovery_required": False}
            code = getattr(exc, "sqlite_errorcode", None)
            if (
                isinstance(code, int)
                and (code & 0xFF) == sqlite3.SQLITE_CANTOPEN
                and not path.exists()
            ):
                if (project_directory / ".cadyx-deleting").exists():
                    raise NotFoundError("Project is being deleted.") from exc
                # The missing database may be a deletion race, not a free lease.
                return {"state": "BUSY", "lease": None, "recovery_required": False}
            raise
        finally:
            if connection is not None:
                connection.close()

        if (project_directory / ".cadyx-deleting").exists():
            raise NotFoundError("Project is being deleted.")
        if row is None:
            return {"state": "FREE", "lease": None, "recovery_required": False}
        lease = EditLease(project_id, OwnerType(row[0]), *row[1:5])
        expired = self._clock() - lease.heartbeat_at > row[5]
        return {
            "state": lock_state_for(lease).value,
            "lease": lease.as_dict(expired=expired),
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
            # Wall-clock corrections must not move a durable lease heartbeat
            # backwards: a later forward adjustment could otherwise expire it.
            updated = replace(lease, heartbeat_at=max(lease.heartbeat_at, self._clock()))
            self._local.connections[project_id].execute(
                "UPDATE lease SET heartbeat_at = ? WHERE singleton = 1", (updated.heartbeat_at,)
            )
            return updated

    def refresh_after_command(self, project_id: str, lease_id: str) -> None:
        """Renew a previously admitted command without rechecking its expiry.

        The command owns the project transaction from lease validation through
        completion, so an independent heartbeat cannot run during a long edit.
        Only the same lease may be refreshed, and callers must hold its guard.
        """
        connection = self._local.connections[project_id]
        updated = connection.execute(
            "UPDATE lease SET heartbeat_at = MAX(heartbeat_at, ?) "
            "WHERE singleton = 1 AND lease_id = ?",
            (self._clock(), lease_id),
        )
        if updated.rowcount != 1:
            raise ConflictError("The supplied lease does not own this project.", code="PROJECT_LOCKED")

    def extend_timeout(self, project_id: str, lease_id: str, minimum_seconds: float) -> EditLease:
        if not math.isfinite(minimum_seconds) or minimum_seconds <= 0:
            raise ValidationError("Lease timeout extension must be finite and positive.")
        with self.guard(project_id):
            lease = self.require(project_id, lease_id)
            row = self._local.connections[project_id].execute(
                "SELECT timeout_seconds FROM lease WHERE singleton = 1"
            ).fetchone()
            if row is None:
                raise LeaseRequiredError("Project has no active edit lease.")
            if row[0] < minimum_seconds:
                self._local.connections[project_id].execute(
                    "UPDATE lease SET timeout_seconds = ? WHERE singleton = 1", (minimum_seconds,)
                )
            return lease

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

    def _query_idempotency(self, connection: sqlite3.Connection, request_id: str) -> dict[str, Any] | None:
        try:
            row = connection.execute(
                """SELECT request_id, operation_id, revision, command_hash,
                          expected_revision, outcome, result_json, created_at
                   FROM mutation_idempotency WHERE request_id = ?""",
                (request_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        if row is None:
            return None
        return {
            "request_id": row[0],
            "operation_id": row[1],
            "revision": row[2],
            "command_hash": row[3],
            "expected_revision": row[4],
            "outcome": row[5],
            "result": json.loads(row[6]),
            "created_at": row[7],
        }

    def get_idempotency_record(self, project_id: str, request_id: str) -> dict[str, Any] | None:
        connections = getattr(self._local, "connections", None)
        conn = connections.get(project_id) if connections else None
        if conn is not None:
            return self._query_idempotency(conn, request_id)
        project_directory = self._project_directory(project_id)
        path = project_directory / ".cadyx-leases.sqlite3"
        if not path.parent.is_dir() or not (path.parent / "project.json").is_file() or not path.is_file():
            return None
        if path.is_symlink():
            raise ValidationError("Lease database must not be a symbolic link.")
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(_project_database_uri(path), uri=True, timeout=5)
            return self._query_idempotency(connection, request_id)
        except sqlite3.OperationalError as exc:
            if _sqlite_contention(exc):
                raise ConflictError("Project is busy; retry later.", code="PROJECT_BUSY") from exc
            return None
        finally:
            if connection is not None:
                connection.close()

    def record_idempotency(
        self,
        project_id: str,
        request_id: str,
        operation_id: str,
        revision: int,
        command_hash: str,
        expected_revision: int | None,
        outcome: str,
        result: dict[str, Any],
        *,
        max_retained: int = 1000,
        max_age_seconds: float = 86400 * 7,
    ) -> None:
        connections = getattr(self._local, "connections", None)
        conn = connections.get(project_id) if connections else None
        if conn is None:
            raise RuntimeError("Cannot record idempotency without an active project transaction.")
        now = time.time()
        conn.execute(
            """INSERT INTO mutation_idempotency
               (request_id, operation_id, revision, command_hash, expected_revision, outcome, result_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                request_id,
                operation_id,
                revision,
                command_hash,
                expected_revision,
                outcome,
                json.dumps(result, separators=(",", ":")),
                now,
            ),
        )
        cutoff = now - max_age_seconds
        conn.execute("DELETE FROM mutation_idempotency WHERE created_at < ?", (cutoff,))
        conn.execute(
            """DELETE FROM mutation_idempotency WHERE request_id NOT IN (
                   SELECT request_id FROM mutation_idempotency ORDER BY created_at DESC LIMIT ?
               )""",
            (max_retained,),
        )
