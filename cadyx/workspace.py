"""Local project storage using atomic versioned project.json replacement."""

from __future__ import annotations

import json
import hashlib
import mimetypes
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .errors import ConflictError, NotFoundError, ValidationError
from .models import Document, OwnerType
from .migrations import PROJECT_VERSION, migrate_project

_PROJECT_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectWorkspace:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _project_dir(self, project_id: str) -> Path:
        if not isinstance(project_id, str) or not _PROJECT_ID.fullmatch(project_id):
            raise NotFoundError("Project was not found.")
        path = (self.root / project_id).resolve()
        if path.parent != self.root:
            raise NotFoundError("Project was not found.")
        return path

    def create(self, name: str) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValidationError("Project name must be a non-empty string of at most 120 characters.")
        project_id = str(uuid4())
        now = utc_now()
        project: dict[str, Any] = {
            "format": "cadyx-project",
            "version": PROJECT_VERSION,
            "id": project_id,
            "name": name.strip(),
            "created_at": now,
            "modified_at": now,
            "document": Document.create().to_dict(),
            "history": [],
        }
        project_dir = self._project_dir(project_id)
        project_dir.mkdir()
        self._write_atomic(project_dir / "project.json", project)
        return project

    def load(self, project_id: str) -> dict[str, Any]:
        project_file = self._project_dir(project_id) / "project.json"
        try:
            with project_file.open("r", encoding="utf-8") as stream:
                project = json.load(stream)
        except FileNotFoundError as exc:
            raise NotFoundError("Project was not found.") from exc
        except (json.JSONDecodeError, OSError) as exc:
            raise ValidationError(f"Project file could not be read: {exc}.") from exc
        project = migrate_project(project)
        self._validate_current(project, expected_id=project_id)
        return project

    def save(self, project: dict[str, Any]) -> None:
        project = migrate_project(project)
        project_id = project.get("id") if isinstance(project, dict) else None
        project_dir = self._project_dir(project_id)
        self._validate_current(project, expected_id=project_id)
        self._write_atomic(project_dir / "project.json", project)

    def list(self) -> list[dict[str, Any]]:
        projects: list[dict[str, Any]] = []
        for entry in self.root.iterdir():
            if not entry.is_dir() or not _PROJECT_ID.fullmatch(entry.name):
                continue
            project_file = entry / "project.json"
            if not project_file.exists():
                continue
            projects.append(self.load(entry.name))
        projects.sort(key=lambda item: (item["name"].casefold(), item["id"]))
        return projects

    def list_folder_projects(self) -> list[str]:
        """Return direct workspace directories that do not contain a CADYX project record."""
        folders: list[str] = []
        for entry in self.root.iterdir():
            if not entry.is_dir() or entry.is_symlink():
                continue
            if _PROJECT_ID.fullmatch(entry.name) and (entry / "project.json").is_file():
                continue
            try:
                if entry.resolve(strict=True).parent != self.root:
                    continue
            except OSError:
                continue
            folders.append(entry.name)
        folders.sort(key=str.casefold)
        return folders

    def list_folder_files(self, folder_name: str) -> list[dict[str, Any]]:
        folder = self._workspace_folder_path(folder_name)
        files: list[dict[str, Any]] = []
        for entry in folder.iterdir():
            if entry.name.startswith(".cadyx-tmp-") or entry.is_symlink() or not entry.is_file():
                continue
            try:
                resolved = entry.resolve(strict=True)
                if resolved.parent != folder:
                    continue
                stat = resolved.stat()
            except OSError:
                continue
            files.append({
                "name": entry.name,
                "size": stat.st_size,
                "media_type": mimetypes.guess_type(entry.name)[0] or "application/octet-stream",
            })
        files.sort(key=lambda item: item["name"].casefold())
        return files

    def resolve_folder_file(self, folder_name: str, file_name: str) -> Path:
        folder = self._workspace_folder_path(folder_name)
        if not self._is_single_path_segment(file_name):
            raise NotFoundError("Project file was not found.")
        if file_name.startswith(".cadyx-tmp-"):
            raise NotFoundError("Project file was not found.")
        path = folder / file_name
        if path.is_symlink() or not path.is_file():
            raise NotFoundError("Project file was not found.")
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise NotFoundError("Project file was not found.") from exc
        if resolved.parent != folder:
            raise NotFoundError("Project file was not found.")
        return resolved

    def publish_scan_output(self, folder_name: str, source_filename: str, content: bytes) -> dict[str, Any]:
        """Create a content-addressed PLY sidecar beside its source without overwriting files."""
        folder = self._workspace_folder_path(folder_name)
        if not self._is_single_path_segment(source_filename) or not isinstance(content, bytes) or not content:
            raise ValidationError("A source filename and non-empty scan output are required.")
        digest = hashlib.sha256(content).hexdigest()
        stem = Path(source_filename).stem[:100].rstrip(" .") or "scan"
        filename = f"{stem}.cadyx-{digest[:12]}.ply"
        target = folder / filename
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=".cadyx-tmp-", suffix=".part", dir=folder, delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target)
                created = True
            except FileExistsError:
                if target.is_symlink() or not target.is_file():
                    raise ConflictError(
                        "A non-file already occupies the generated scan output path.",
                        code="ARTIFACT_CONFLICT",
                    )
                existing_hash = hashlib.sha256(target.read_bytes()).hexdigest()
                if existing_hash != digest:
                    raise ConflictError(
                        "A different file already exists at the generated scan output path.",
                        code="ARTIFACT_CONFLICT",
                    )
                created = False
        except ConflictError:
            raise
        except OSError as exc:
            raise ValidationError(f"Processed scan output could not be saved: {exc}.") from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except FileNotFoundError:
                    pass
        return {
            "folder": folder_name,
            "file": filename,
            "sha256": digest,
            "size": len(content),
            "created": created,
        }

    def _workspace_folder_path(self, folder_name: str) -> Path:
        if not self._is_single_path_segment(folder_name):
            raise NotFoundError("Project folder was not found.")
        path = self.root / folder_name
        if path.is_symlink() or not path.is_dir():
            raise NotFoundError("Project folder was not found.")
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise NotFoundError("Project folder was not found.") from exc
        if resolved.parent != self.root:
            raise NotFoundError("Project folder was not found.")
        return resolved

    @staticmethod
    def _is_single_path_segment(value: str) -> bool:
        return (
            isinstance(value, str)
            and value not in {"", ".", ".."}
            and "\x00" not in value
            and ":" not in value
            and "/" not in value
            and "\\" not in value
            and Path(value).name == value
        )

    @staticmethod
    def validate(project: Any, *, expected_id: str | None = None) -> None:
        ProjectWorkspace._validate_current(migrate_project(project), expected_id=expected_id)

    @staticmethod
    def _validate_current(project: Any, *, expected_id: str | None = None) -> None:
        if not isinstance(project, dict):
            raise ValidationError("Project file must contain a JSON object.")
        if project.get("format") != "cadyx-project" or type(project.get("version")) is not int or project.get("version") != PROJECT_VERSION:
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
    def _write_atomic(path: Path, value: dict[str, Any]) -> None:
        encoded = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False,
            ) as stream:
                temporary_path = stream.name
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
            temporary_path = None
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.unlink(temporary_path)
