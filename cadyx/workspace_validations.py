"""Per-project engineering validation record persistence."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .errors import ValidationError

_SUPPORTED_VALIDATION_KINDS = {
    "body_dimensions",
    "body_clearance",
    "body_preservation",
    "body_thickness",
}


class ValidationRecordStore:
    """Own the per-project validation SQLite schema and connection lifecycle."""

    @staticmethod
    def database_path(project_dir: Path) -> Path:
        return project_dir / ".cadyx-validations.sqlite3"

    def record(
        self,
        project_dir: Path,
        kind: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(kind, str) or not kind:
            raise ValidationError("Validation record kind is invalid.")
        if kind not in _SUPPORTED_VALIDATION_KINDS:
            raise ValidationError("Validation record kind is unsupported.")
        if not isinstance(result, dict) or type(result.get("revision")) is not int:
            raise ValidationError("Validation result cannot be recorded.")
        geometry_hash = result.get("geometry_sha256")
        if not isinstance(geometry_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", geometry_hash):
            raise ValidationError("Validation result geometry hash is invalid.")
        try:
            result_json = json.dumps(
                result,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise ValidationError("Validation result cannot be serialized.") from exc

        database_path = self.database_path(project_dir)
        self._validate_database_path(database_path)
        record_id = str(uuid4())
        created_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        try:
            connection = sqlite3.connect(database_path, timeout=5)
            try:
                with connection:
                    self._ensure_schema(connection)
                    connection.execute(
                        "INSERT INTO validation_record "
                        "(id, kind, revision, geometry_sha256, created_at, result_json) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            record_id,
                            kind,
                            result["revision"],
                            geometry_hash,
                            created_at,
                            result_json,
                        ),
                    )
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise ValidationError(f"Validation result could not be recorded: {exc}.") from exc
        return {
            "record_id": record_id,
            "kind": kind,
            "revision": result["revision"],
            "geometry_sha256": geometry_hash,
            "created_at": created_at,
        }

    def list(self, project_dir: Path, *, limit: int = 50) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValidationError("Validation record limit must be an integer from 1 to 200.")
        database_path = self.database_path(project_dir)
        self._validate_database_path(database_path)
        if not database_path.exists():
            return []
        try:
            connection = sqlite3.connect(database_path, timeout=5)
            try:
                rows = connection.execute(
                    "SELECT id, kind, revision, geometry_sha256, created_at, result_json "
                    "FROM validation_record ORDER BY created_at DESC, id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            finally:
                connection.close()
            return [
                {
                    "record_id": row[0],
                    "kind": row[1],
                    "revision": row[2],
                    "geometry_sha256": row[3],
                    "created_at": row[4],
                    "result": json.loads(row[5]),
                }
                for row in rows
            ]
        except (sqlite3.Error, json.JSONDecodeError) as exc:
            raise ValidationError(f"Validation records could not be read: {exc}.") from exc

    @staticmethod
    def _validate_database_path(database_path: Path) -> None:
        if database_path.is_symlink():
            raise ValidationError("Validation record database must not be a symbolic link.")

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS validation_record (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                revision INTEGER NOT NULL,
                geometry_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                result_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS validation_record_created_at
                ON validation_record(created_at DESC, id DESC)"""
        )
