"""Local project storage facade over focused workspace persistence services."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from .errors import ConflictError, NotFoundError, ValidationError
from .migrations import migrate_project
from .workspace_files import WorkspaceArtifactStore, _is_single_path_segment
from .workspace_groups import ProjectGroupStore
from .workspace_projects import ProjectRecordStore, _PROJECT_ID
from .workspace_validations import ValidationRecordStore

_WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectWorkspace:
    """Compatibility facade composing project, group, validation and artifact stores."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._project_path_guard = threading.RLock()
        self._project_path_by_id: dict[str, Path] = {}
        self._project_id_by_path: dict[Path, str] = {}
        self._projects = ProjectRecordStore()
        self._groups = ProjectGroupStore(self.root)
        self._validations = ValidationRecordStore()
        self._artifacts = WorkspaceArtifactStore(self._is_internal_project_file)
        # Preserve the private path used by existing revision-token code/tests.
        self._groups_db_path = self._groups.database_path

    @contextmanager
    def _groups_connection(self) -> Iterator[Any]:
        """Compatibility shim; the group store owns the connection lifecycle."""
        with self._groups.connection() as connection:
            yield connection

    def list_project_groups(self) -> list[dict[str, str]]:
        return self._groups.list_groups()

    def project_group_assignments(self) -> dict[str, str]:
        return self._groups.assignments()

    def create_project_group(self, name: str) -> dict[str, str]:
        return self._groups.create(name)

    def assign_project_group(self, project_id: str, group_id: str | None) -> None:
        self.load(project_id)
        self._groups.assign(project_id, group_id)

    def mark_project_deleting(self, project_id: str) -> None:
        directory = self._project_dir(project_id)
        marker = directory / ".cadyx-deleting"
        try:
            descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise ConflictError(
                "Project deletion is already in progress.", code="PROJECT_BUSY"
            ) from exc
        try:
            with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                stream.write(f"{project_id}\n")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            marker.unlink(missing_ok=True)
            raise ValidationError(f"Project deletion could not be fenced: {exc}.") from exc

    def delete_project(self, project_id: str) -> bool:
        with self._project_path_guard:
            directory = self._project_path_by_id.get(project_id)
        if directory is None or directory.parent != self.root or not directory.is_dir():
            raise NotFoundError("Project was not found.")
        marker = directory / ".cadyx-deleting"
        if not marker.is_file() or marker.is_symlink():
            raise ConflictError("Project deletion was not prepared.", code="PROJECT_BUSY")
        tombstone = self.root / f".cadyx-deleted-{project_id}-{uuid4()}"
        try:
            with self._groups.connection() as connection:
                connection.execute(
                    "DELETE FROM project_group_assignment WHERE project_id = ?", (project_id,)
                )
                directory.rename(tombstone)
        except Exception as exc:
            if directory.exists() and marker.exists():
                marker.unlink(missing_ok=True)
            if isinstance(exc, OSError):
                raise ValidationError(
                    f"Project directory could not be detached for deletion: {exc}."
                ) from exc
            raise
        self._forget_project_path(project_id, directory)
        try:
            shutil.rmtree(tombstone)
        except OSError:
            return True
        return False

    def _project_dir(self, project_id: str) -> Path:
        if not isinstance(project_id, str) or not _PROJECT_ID.fullmatch(project_id):
            raise NotFoundError("Project was not found.")
        with self._project_path_guard:
            cached = self._project_path_by_id.get(project_id)
        if cached is not None:
            try:
                if (
                    not cached.is_symlink()
                    and cached.is_dir()
                    and cached.resolve(strict=True).parent == self.root
                    and not (cached / ".cadyx-deleting").exists()
                ):
                    return cached
            except OSError:
                pass
            if (cached / ".cadyx-deleting").exists():
                raise NotFoundError("Project is being deleted.")
            self._forget_project_path(project_id, cached)
        legacy_path = self.root / project_id
        if legacy_path.is_dir() and not legacy_path.is_symlink():
            resolved = legacy_path.resolve()
            self._remember_project_path(project_id, resolved)
            return resolved
        for entry in self._project_directories():
            if self._project_id_for_directory(entry) == project_id:
                return entry
        raise NotFoundError("Project was not found.")

    def create(self, name: str) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValidationError(
                "Project name must be a non-empty string of at most 120 characters."
            )
        project_id = str(uuid4())
        project = self._new_project_record(project_id, name.strip())
        project_dir = self._create_project_directory(project["name"], project_id)
        self._projects.write_atomic(project_dir / "project.json", project)
        self._remember_project_path(project_id, project_dir)
        return project

    def rename(self, project_id: str, name: str) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValidationError(
                "Project name must be a non-empty string of at most 120 characters."
            )
        project = self.load(project_id)
        source = self._project_dir(project_id)
        base_name = self._safe_project_directory_name(name.strip())
        target = self._unique_project_directory_path(base_name, exclude=source)
        project["name"] = name.strip()
        project["modified_at"] = utc_now()
        try:
            source.rename(target)
            self._projects.write_atomic(target / "project.json", project)
        except OSError as exc:
            if target.exists() and not source.exists():
                try:
                    target.rename(source)
                except OSError as rollback_error:
                    raise ConflictError(
                        "Project folder rename failed and rollback needs recovery: "
                        f"{rollback_error}.",
                        code="PROJECT_RENAME_RECOVERY_REQUIRED",
                    ) from exc
            raise ValidationError(f"Project folder could not be renamed: {exc}.") from exc
        except Exception:
            if target.exists() and not source.exists():
                target.rename(source)
            raise
        self._remember_project_path(project_id, target)
        return project

    @staticmethod
    def _new_project_record(project_id: str, name: str) -> dict[str, Any]:
        return ProjectRecordStore.new_record(project_id, name, timestamp=utc_now())

    def load(self, project_id: str) -> dict[str, Any]:
        return self._load_project_from_directory(self._project_dir(project_id), project_id)

    def _load_project_from_directory(self, directory: Path, project_id: str) -> dict[str, Any]:
        project = self._projects.load(directory, project_id)
        self._remember_project_path(project_id, directory)
        return project

    def save(self, project: dict[str, Any]) -> None:
        project = migrate_project(project)
        project_id = project.get("id") if isinstance(project, dict) else None
        project_dir = self._project_dir(project_id)
        self._projects.save(project_dir, project)

    def record_validation(
        self, project_id: str, kind: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        self.load(project_id)
        return self._validations.record(self._project_dir(project_id), kind, result)

    def list_validation_records(
        self, project_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        self.load(project_id)
        return self._validations.list(self._project_dir(project_id), limit=limit)

    def list(self) -> list[dict[str, Any]]:
        projects: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        self._cleanup_deleted_project_directories()
        for entry in self._project_directories():
            project_id = self._project_id_for_directory(entry)
            if project_id is None:
                project_id = self._adopt_workspace_directory(entry)
            if project_id is None:
                continue
            if project_id in seen_ids:
                raise ConflictError(
                    f"More than one workspace folder contains project '{project_id}'.",
                    code="DUPLICATE_PROJECT_ID",
                )
            seen_ids.add(project_id)
            projects.append(self._load_project_from_directory(entry, project_id))
        projects.sort(key=lambda item: (item["name"].casefold(), item["id"]))
        return projects

    def _adopt_workspace_directory(self, directory: Path) -> str | None:
        """Initialize an unclaimed top-level workspace directory without touching its files."""
        project_file = directory / "project.json"
        if project_file.exists() or project_file.is_symlink():
            return self._project_id_for_directory(directory)

        marker = directory / ".cadyx-project-id"
        project_id = self._project_record_id(directory)
        if project_id is None:
            if marker.exists() or marker.is_symlink():
                return None
            project_id = str(uuid4())
            if not self._projects.write_new_file(marker, f"{project_id}\n".encode("ascii")):
                project_id = self._project_record_id(directory)
                if project_id is None:
                    return None

        project = self._new_project_record(project_id, directory.name)
        encoded = (
            json.dumps(project, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        ).encode("utf-8")
        if not self._projects.write_new_file(project_file, encoded):
            return self._project_id_for_directory(directory)
        self._remember_project_path(project_id, directory)
        return project_id

    def list_folder_projects(self) -> list[str]:
        folders: list[str] = []
        for entry in self._project_directories():
            if self._project_id_for_directory(entry) is not None:
                continue
            folders.append(entry.name)
        folders.sort(key=str.casefold)
        return folders

    def list_folder_files(self, folder_name: str) -> list[dict[str, Any]]:
        return self._artifacts.list_folder_files(self._workspace_folder_path(folder_name))

    def list_project_files(self, project_id: str) -> list[dict[str, Any]]:
        return self._artifacts.list_project_files(self._project_dir(project_id))

    def copy_project_files(
        self, source_project_id: str, target_project_id: str
    ) -> dict[str, Any]:
        return self._artifacts.copy_project_files(
            source_project_id,
            target_project_id,
            self._project_dir(source_project_id),
            self._project_dir(target_project_id),
        )

    def prune_project_files(self, project_id: str, keep_files: list[str]) -> dict[str, Any]:
        return self._artifacts.prune_project_files(
            project_id, self._project_dir(project_id), keep_files
        )

    def publish_project_step(
        self, project_id: str, filename: str, content: bytes
    ) -> dict[str, Any]:
        return self._artifacts.publish_project_step(
            self._project_dir(project_id), filename, content
        )

    def publish_project_scan(self, project_id: str, filename: str, content: bytes) -> dict[str, Any]:
        return self._artifacts.publish_project_scan(
            self._project_dir(project_id), filename, content
        )

    def publish_project_scan_output(
        self, project_id: str, source_filename: str, content: bytes
    ) -> dict[str, Any]:
        directory = self._project_dir(project_id)
        return self._artifacts.publish_project_scan_output(
            directory, directory.name, source_filename, content,
        )

    def remove_project_step(self, project_id: str, filename: str) -> dict[str, Any]:
        path = self.resolve_project_file(project_id, filename)
        return self._artifacts.remove_project_step(path, filename)

    def workspace_revision_token(self) -> str:
        entries: list[tuple[str, str, int, int]] = []
        for entry in sorted(self._project_directories(), key=lambda item: item.name.casefold()):
            try:
                if self._project_id_for_directory(entry) is not None:
                    project_file = entry / "project.json"
                    stat = project_file.stat()
                    entries.append((entry.name, "project", stat.st_mtime_ns, stat.st_size))
                else:
                    entries.append((entry.name, "folder", 0, 0))
            except OSError:
                continue
        try:
            groups_stat = self._groups.database_path.stat()
            if self._groups.database_path.is_file() and not self._groups.database_path.is_symlink():
                entries.append(
                    (
                        self._groups.database_path.name,
                        "groups",
                        groups_stat.st_mtime_ns,
                        groups_stat.st_size,
                    )
                )
        except OSError:
            pass
        return self._revision_token(entries)

    def folder_revision_token(self, folder_name: str) -> str:
        files = self.list_folder_files(folder_name)
        return self._revision_token(
            [(item["name"], item["size"], item["modified_at_ns"]) for item in files]
        )

    def project_revision_token(self, project_id: str) -> str:
        project_file = self._project_dir(project_id) / "project.json"
        if project_file.is_symlink() or not project_file.is_file():
            raise NotFoundError("Project was not found.")
        try:
            stat = project_file.stat()
        except OSError as exc:
            raise NotFoundError("Project was not found.") from exc
        return f"{stat.st_mtime_ns}:{stat.st_size}"

    def resolve_project_file(self, project_id: str, file_name: str) -> Path:
        return self._artifacts.resolve_project_file(self._project_dir(project_id), file_name)

    @staticmethod
    def _revision_token(value: Any) -> str:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        return hashlib.sha256(encoded).hexdigest()

    def resolve_folder_file(self, folder_name: str, file_name: str) -> Path:
        return self._artifacts.resolve_folder_file(
            self._workspace_folder_path(folder_name), file_name
        )

    def publish_scan_output(
        self, folder_name: str, source_filename: str, content: bytes
    ) -> dict[str, Any]:
        return self._artifacts.publish_scan_output(
            self._workspace_folder_path(folder_name), folder_name, source_filename, content
        )

    def publish_cad_delivery(
        self, project_id: str, files: dict[str, bytes]
    ) -> list[dict[str, Any]]:
        return self._artifacts.publish_cad_delivery(self._project_dir(project_id), files)

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

    def is_workspace_folder(self, folder_name: str) -> bool:
        try:
            self._workspace_folder_path(folder_name)
        except NotFoundError:
            return False
        return True

    def _project_directories(self) -> list[Path]:
        directories: list[Path] = []
        try:
            entries = self.root.iterdir()
            for entry in entries:
                if (
                    entry.name.startswith(".cadyx-deleted-")
                    or entry.is_symlink()
                    or not entry.is_dir()
                ):
                    continue
                try:
                    resolved = entry.resolve(strict=True)
                except OSError:
                    continue
                if (
                    resolved.parent == self.root
                    and not (resolved / ".cadyx-deleting").exists()
                ):
                    directories.append(resolved)
        except OSError:
            return directories
        return directories

    def _cleanup_deleted_project_directories(self) -> None:
        try:
            entries = self.root.glob(".cadyx-deleted-*")
            for entry in entries:
                if entry.is_symlink() or not entry.is_dir():
                    continue
                try:
                    if entry.resolve(strict=True).parent == self.root:
                        shutil.rmtree(entry)
                except OSError:
                    continue
        except OSError:
            pass

    def _project_id_for_directory(self, directory: Path) -> str | None:
        project_file = directory / "project.json"
        if project_file.is_symlink() or not project_file.is_file():
            return None
        if _PROJECT_ID.fullmatch(directory.name):
            self._remember_project_path(directory.name, directory)
            return directory.name
        with self._project_path_guard:
            cached = self._project_id_by_path.get(directory)
        if cached is not None:
            return cached
        project_id = self._project_record_id(directory)
        if project_id is not None:
            self._remember_project_path(project_id, directory)
        return project_id

    def _remember_project_path(self, project_id: str, directory: Path) -> None:
        with self._project_path_guard:
            previous_path = self._project_path_by_id.get(project_id)
            if previous_path is not None and previous_path != directory:
                self._project_id_by_path.pop(previous_path, None)
            previous_id = self._project_id_by_path.get(directory)
            if previous_id is not None and previous_id != project_id:
                self._project_path_by_id.pop(previous_id, None)
            self._project_path_by_id[project_id] = directory
            self._project_id_by_path[directory] = project_id

    def _forget_project_path(self, project_id: str, directory: Path) -> None:
        with self._project_path_guard:
            if self._project_path_by_id.get(project_id) == directory:
                self._project_path_by_id.pop(project_id, None)
            if self._project_id_by_path.get(directory) == project_id:
                self._project_id_by_path.pop(directory, None)

    @staticmethod
    def _project_record_id(directory: Path) -> str | None:
        marker = directory / ".cadyx-project-id"
        if not marker.is_symlink() and marker.is_file():
            try:
                project_id = marker.read_text(encoding="ascii").strip()
            except OSError:
                project_id = ""
            if _PROJECT_ID.fullmatch(project_id):
                return project_id
        project_file = directory / "project.json"
        if project_file.is_symlink() or not project_file.is_file():
            return None
        try:
            with project_file.open("r", encoding="utf-8") as stream:
                project = json.load(stream)
        except (OSError, json.JSONDecodeError):
            return None
        project_id = project.get("id") if isinstance(project, dict) else None
        if (
            not isinstance(project, dict)
            or project.get("format") != "cadyx-project"
            or not isinstance(project_id, str)
            or not _PROJECT_ID.fullmatch(project_id)
        ):
            return None
        return project_id

    def _is_project_directory(self, directory: Path) -> bool:
        return self._project_id_for_directory(directory) is not None

    def _is_internal_project_file(self, directory: Path, file_name: str) -> bool:
        return self._project_id_for_directory(directory) is not None and (
            file_name == "project.json" or file_name.startswith(".cadyx-")
        )

    def _create_project_directory(self, name: str, project_id: str) -> Path:
        base_name = self._safe_project_directory_name(name)
        for directory in self._unique_project_directory_paths(base_name):
            try:
                directory.mkdir()
                marker = directory / ".cadyx-project-id"
                with marker.open("x", encoding="ascii", newline="\n") as stream:
                    stream.write(f"{project_id}\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                return directory
            except FileExistsError:
                continue
        raise ConflictError(
            "A unique project folder name could not be allocated.",
            code="PROJECT_FOLDER_CONFLICT",
        )

    @staticmethod
    def _safe_project_directory_name(name: str) -> str:
        base_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name.strip())
        base_name = re.sub(r"\s+", " ", base_name).strip(" .")[:80].rstrip(" .")
        if not base_name:
            base_name = "CADYX project"
        if base_name.split(".", maxsplit=1)[0].upper() in _WINDOWS_RESERVED_NAMES:
            base_name = f"_{base_name}"
        return base_name

    def _unique_project_directory_paths(
        self, base_name: str, *, exclude: Path | None = None
    ) -> Iterator[Path]:
        for suffix in range(1, 10_000):
            candidate = (
                base_name
                if suffix == 1
                else f"{base_name[:72].rstrip(' .')} ({suffix})"
            )
            directory = self.root / candidate
            if directory == exclude:
                continue
            if not directory.exists() and not directory.is_symlink():
                yield directory

    def _unique_project_directory_path(
        self, base_name: str, *, exclude: Path | None = None
    ) -> Path:
        try:
            return next(self._unique_project_directory_paths(base_name, exclude=exclude))
        except StopIteration as exc:
            raise ConflictError(
                "A unique project folder name could not be allocated.",
                code="PROJECT_FOLDER_CONFLICT",
            ) from exc

    @staticmethod
    def _is_single_path_segment(value: str) -> bool:
        return _is_single_path_segment(value)

    @staticmethod
    def validate(project: Any, *, expected_id: str | None = None) -> None:
        ProjectRecordStore.validate(project, expected_id=expected_id)

    @staticmethod
    def _validate_current(project: Any, *, expected_id: str | None = None) -> None:
        ProjectRecordStore.validate_current(project, expected_id=expected_id)

    @staticmethod
    def _write_atomic(path: Path, value: dict[str, Any]) -> None:
        ProjectRecordStore.write_atomic(path, value)

    @staticmethod
    def _write_new_file(path: Path, contents: bytes) -> bool:
        return ProjectRecordStore.write_new_file(path, contents)
