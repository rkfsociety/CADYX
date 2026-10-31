"""Workspace-wide project group persistence."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from .errors import ConflictError, NotFoundError, ValidationError


class ProjectGroupStore:
    """Own the workspace-wide project-group SQLite schema and lifecycle."""

    def __init__(self, root: Path) -> None:
        self.database_path = root / ".cadyx-project-groups.sqlite3"
        # Initialize once before serving requests. Re-running DDL for every
        # read takes SQLite schema locks and delays concurrent HTTP readers.
        with self.connection() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS project_group (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    name_key TEXT NOT NULL UNIQUE
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS project_group_assignment (
                    project_id TEXT PRIMARY KEY,
                    group_id TEXT REFERENCES project_group(id) ON DELETE SET NULL
                )"""
            )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        if self.database_path.is_symlink():
            raise ValidationError("Project group database must not be a symbolic link.")
        connection = sqlite3.connect(self.database_path, timeout=5)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list_groups(self) -> list[dict[str, str]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT id, name FROM project_group ORDER BY name COLLATE NOCASE, id"
            ).fetchall()
        return [{"id": group_id, "name": name} for group_id, name in rows]

    def assignments(self) -> dict[str, str]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT project_id, group_id FROM project_group_assignment "
                "WHERE group_id IS NOT NULL"
            ).fetchall()
        return {project_id: group_id for project_id, group_id in rows}

    def create(self, name: str) -> dict[str, str]:
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name.strip()) > 80
            or any(ord(character) < 32 for character in name)
        ):
            raise ValidationError(
                "Project group name must be 1 to 80 characters without control characters."
            )
        group = {"id": str(uuid4()), "name": name.strip()}
        try:
            with self.connection() as connection:
                connection.execute(
                    "INSERT INTO project_group (id, name, name_key) VALUES (?, ?, ?)",
                    (group["id"], group["name"], group["name"].casefold()),
                )
        except sqlite3.IntegrityError as exc:
            raise ConflictError(
                "A project group with this name already exists.",
                code="PROJECT_GROUP_EXISTS",
            ) from exc
        return group

    def assign(self, project_id: str, group_id: str | None) -> None:
        if group_id is not None and (not isinstance(group_id, str) or not group_id):
            raise ValidationError("group_id must be a group ID or null.")
        try:
            with self.connection() as connection:
                connection.execute(
                    "INSERT INTO project_group_assignment (project_id, group_id) VALUES (?, ?) "
                    "ON CONFLICT(project_id) DO UPDATE SET group_id = excluded.group_id",
                    (project_id, group_id),
                )
        except sqlite3.IntegrityError as exc:
            raise NotFoundError("Project group was not found.") from exc

    def remove_assignment(self, project_id: str) -> None:
        with self.connection() as connection:
            connection.execute(
                "DELETE FROM project_group_assignment WHERE project_id = ?", (project_id,)
            )
