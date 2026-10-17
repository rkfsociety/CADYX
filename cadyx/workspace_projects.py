"""Project record persistence and validation for a CADYX workspace."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from .errors import NotFoundError, ValidationError
from .migrations import PROJECT_VERSION, migrate_project
from .models import Document, OwnerType

_PROJECT_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class ProjectRecordStore:
    """Own project.json reads, migrations, validation and atomic writes."""

    @staticmethod
    def new_record(project_id: str, name: str, *, timestamp: str) -> dict[str, Any]:
        return {
            "format": "cadyx-project",
            "version": PROJECT_VERSION,
            "id": project_id,
            "name": name,
            "created_at": timestamp,
            "modified_at": timestamp,
            "document": Document.create().to_dict(),
            "history": [],
        }

    def load(self, directory: Path, project_id: str) -> dict[str, Any]:
        project_file = directory / "project.json"
        try:
            with project_file.open("r", encoding="utf-8") as stream:
                project = json.load(stream)
        except FileNotFoundError as exc:
            raise NotFoundError("Project was not found.") from exc
        except (json.JSONDecodeError, OSError) as exc:
            raise ValidationError(f"Project file could not be read: {exc}.") from exc
        project = migrate_project(project)
        self.validate_current(project, expected_id=project_id)
        return project

    def save(self, directory: Path, project: dict[str, Any]) -> dict[str, Any]:
        project = migrate_project(project)
        project_id = project.get("id") if isinstance(project, dict) else None
        self.validate_current(project, expected_id=project_id)
        self.write_atomic(directory / "project.json", project)
        return project

    @staticmethod
    def validate(project: Any, *, expected_id: str | None = None) -> None:
        ProjectRecordStore.validate_current(migrate_project(project), expected_id=expected_id)

    @staticmethod
    def validate_current(project: Any, *, expected_id: str | None = None) -> None:
        if not isinstance(project, dict):
            raise ValidationError("Project file must contain a JSON object.")
        if (
            project.get("format") != "cadyx-project"
            or type(project.get("version")) is not int
            or project.get("version") != PROJECT_VERSION
        ):
            raise ValidationError("Unsupported project format or version.")
        project_id = project.get("id")
        if not isinstance(project_id, str) or not _PROJECT_ID.fullmatch(project_id):
            raise ValidationError("Project id is invalid.")
        if expected_id is not None and project_id != expected_id:
            raise ValidationError("Project file id does not match its workspace directory.")
        if not isinstance(project.get("name"), str) or not project["name"].strip():
            raise ValidationError("Project name is invalid.")
        if not isinstance(project.get("created_at"), str) or not isinstance(project.get("modified_at"), str):
            raise ValidationError("Project timestamps are invalid.")
        document = Document.from_dict(project.get("document"))
        history = project.get("history")
        if not isinstance(history, list):
            raise ValidationError("Project history must be a list.")
        if document.revision != len(history):
            raise ValidationError("Document revision must match the number of committed history entries.")
        for index, entry in enumerate(history, start=1):
            if not isinstance(entry, dict):
                raise ValidationError("Every project history entry must be an object.")
            if entry.get("actor_type") not in {owner.value for owner in OwnerType}:
                raise ValidationError("History entry actor_type is invalid.")
            if not isinstance(entry.get("actor_id"), str) or not entry["actor_id"]:
                raise ValidationError("History entry actor_id is missing.")
            if not isinstance(entry.get("command"), dict) or not isinstance(entry.get("result"), dict):
                raise ValidationError("History entry command and result must be objects.")
            if not isinstance(entry.get("timestamp"), str) or entry.get("revision") != index:
                raise ValidationError("History entry timestamp or revision is invalid.")

    @staticmethod
    def write_atomic(path: Path, value: dict[str, Any]) -> None:
        encoded = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = stream.name
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            for attempt in range(5):
                try:
                    os.replace(temporary_path, path)
                    break
                except PermissionError as exc:
                    if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 4:
                        raise
                    time.sleep(0.05 * (2**attempt))
            temporary_path = None
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.unlink(temporary_path)

    @staticmethod
    def write_new_file(path: Path, contents: bytes) -> bool:
        """Publish a new file atomically without replacing a concurrent or user file."""
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=".cadyx-tmp-",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = stream.name
                stream.write(contents)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary_path, path)
            except FileExistsError:
                return False
            return True
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.unlink(temporary_path)
