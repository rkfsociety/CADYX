"""Persistent, versioned registry for validated agent-level capabilities.

Runtime capabilities are declarative compositions of existing Command System
commands. They cannot contain Python, shell commands, or filesystem operations.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import ValidationError

_CAPABILITY_ID = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class CapabilityRegistry:
    """Store validated capability definitions and reversible prior versions."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        if self.database_path.is_symlink():
            raise ValidationError("Capability registry database must not be a symbolic link.")

    def list(self, *, ready_only: bool = False) -> list[dict[str, Any]]:
        if not self.database_path.exists():
            return []
        try:
            with closing(self._connect()) as connection, connection:
                self._ensure_schema(connection)
                query = "SELECT definition_json, status, test_json, updated_at FROM capability"
                if ready_only:
                    query += " WHERE status = 'ready'"
                query += " ORDER BY capability_id"
                rows = connection.execute(query).fetchall()
            return [
                {
                    **json.loads(row[0]),
                    "status": row[1],
                    "test_result": json.loads(row[2]) if row[2] else None,
                    "updated_at": row[3],
                }
                for row in rows
            ]
        except (sqlite3.Error, json.JSONDecodeError) as exc:
            raise ValidationError(f"Capability registry could not be read: {exc}.") from exc

    def get_ready(self, capability_id: str, *, command_contract_hash: str) -> dict[str, Any] | None:
        for item in self.list(ready_only=True):
            if item["id"] == capability_id and item["compatibility"]["command_contract_hash"] == command_contract_hash:
                return item
        return None

    def promote(
        self,
        definition: dict[str, Any],
        *,
        test_result: dict[str, Any],
    ) -> dict[str, Any]:
        capability_id = definition.get("id")
        if not isinstance(capability_id, str) or not _CAPABILITY_ID.fullmatch(capability_id):
            raise ValidationError("Capability id must be a lowercase stable identifier.")
        if not isinstance(test_result, dict) or test_result.get("status") != "passed":
            raise ValidationError("A capability can be promoted only after its tests pass.")
        try:
            definition_json = self._json(definition)
            test_json = self._json(test_result)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Capability definition and test result must be finite JSON.") from exc

        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        try:
            with closing(self._connect()) as connection, connection:
                self._ensure_schema(connection)
                previous = connection.execute(
                    "SELECT definition_json, status, test_json, updated_at FROM capability WHERE capability_id = ?",
                    (capability_id,),
                ).fetchone()
                if previous is not None:
                    connection.execute(
                        "INSERT INTO capability_checkpoint (capability_id, definition_json, status, test_json, updated_at, checkpointed_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (capability_id, *previous, now),
                    )
                connection.execute(
                    "INSERT INTO capability (capability_id, definition_json, status, test_json, updated_at) VALUES (?, ?, 'ready', ?, ?) "
                    "ON CONFLICT(capability_id) DO UPDATE SET definition_json=excluded.definition_json, status='ready', test_json=excluded.test_json, updated_at=excluded.updated_at",
                    (capability_id, definition_json, test_json, now),
                )
        except sqlite3.Error as exc:
            raise ValidationError(f"Capability could not be registered: {exc}.") from exc
        return {**definition, "status": "ready", "test_result": test_result, "updated_at": now}

    def rollback(self, capability_id: str) -> dict[str, Any]:
        try:
            with closing(self._connect()) as connection, connection:
                self._ensure_schema(connection)
                checkpoint = connection.execute(
                    "SELECT definition_json, status, test_json, updated_at FROM capability_checkpoint "
                    "WHERE capability_id = ? ORDER BY checkpoint_id DESC LIMIT 1",
                    (capability_id,),
                ).fetchone()
                if checkpoint is None:
                    connection.execute("DELETE FROM capability WHERE capability_id = ?", (capability_id,))
                    return {"id": capability_id, "status": "removed", "restored": False}
                connection.execute(
                    "INSERT INTO capability (capability_id, definition_json, status, test_json, updated_at) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(capability_id) DO UPDATE SET definition_json=excluded.definition_json, status=excluded.status, test_json=excluded.test_json, updated_at=excluded.updated_at",
                    (capability_id, *checkpoint),
                )
                connection.execute(
                    "DELETE FROM capability_checkpoint WHERE checkpoint_id = (SELECT MAX(checkpoint_id) FROM capability_checkpoint WHERE capability_id = ?)",
                    (capability_id,),
                )
            return {"id": capability_id, "status": checkpoint[1], "restored": True}
        except sqlite3.Error as exc:
            raise ValidationError(f"Capability rollback failed: {exc}.") from exc

    def _connect(self) -> sqlite3.Connection:
        if self.database_path.is_symlink():
            raise ValidationError("Capability registry database must not be a symbolic link.")
        return sqlite3.connect(self.database_path, timeout=5)

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS capability (capability_id TEXT PRIMARY KEY, definition_json TEXT NOT NULL, status TEXT NOT NULL, test_json TEXT, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS capability_checkpoint (checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT, capability_id TEXT NOT NULL, definition_json TEXT NOT NULL, status TEXT NOT NULL, test_json TEXT, updated_at TEXT NOT NULL, checkpointed_at TEXT NOT NULL)"
        )
