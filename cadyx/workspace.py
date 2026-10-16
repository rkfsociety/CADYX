"""Local project storage using atomic versioned project.json replacement."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from .errors import ConflictError, NotFoundError, ValidationError
from .models import Document, OwnerType
from .migrations import PROJECT_VERSION, migrate_project

_PROJECT_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectWorkspace:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._project_path_guard = threading.RLock()
        self._project_path_by_id: dict[str, Path] = {}
        self._project_id_by_path: dict[Path, str] = {}
        self._groups_db_path = self.root / ".cadyx-project-groups.sqlite3"

    @contextmanager
    def _groups_connection(self) -> Iterator[sqlite3.Connection]:
        if self._groups_db_path.is_symlink():
            raise ValidationError("Project group database must not be a symbolic link.")
        connection = sqlite3.connect(self._groups_db_path, timeout=5)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("""CREATE TABLE IF NOT EXISTS project_group (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                name_key TEXT NOT NULL UNIQUE
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS project_group_assignment (
                project_id TEXT PRIMARY KEY,
                group_id TEXT REFERENCES project_group(id) ON DELETE SET NULL
            )""")
            connection.commit()
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list_project_groups(self) -> list[dict[str, str]]:
        with self._groups_connection() as connection:
            rows = connection.execute(
                "SELECT id, name FROM project_group ORDER BY name COLLATE NOCASE, id"
            ).fetchall()
            return [{"id": group_id, "name": name} for group_id, name in rows]

    def project_group_assignments(self) -> dict[str, str]:
        with self._groups_connection() as connection:
            rows = connection.execute(
                "SELECT project_id, group_id FROM project_group_assignment WHERE group_id IS NOT NULL"
            ).fetchall()
            return {project_id: group_id for project_id, group_id in rows}

    def create_project_group(self, name: str) -> dict[str, str]:
        if (not isinstance(name, str) or not name.strip() or len(name.strip()) > 80
                or any(ord(character) < 32 for character in name)):
            raise ValidationError("Project group name must be 1 to 80 characters without control characters.")
        group = {"id": str(uuid4()), "name": name.strip()}
        try:
            with self._groups_connection() as connection:
                connection.execute(
                    "INSERT INTO project_group (id, name, name_key) VALUES (?, ?, ?)",
                    (group["id"], group["name"], group["name"].casefold()),
                )
        except sqlite3.IntegrityError as exc:
            raise ConflictError("A project group with this name already exists.", code="PROJECT_GROUP_EXISTS") from exc
        return group

    def assign_project_group(self, project_id: str, group_id: str | None) -> None:
        self.load(project_id)
        if group_id is not None and (not isinstance(group_id, str) or not group_id):
            raise ValidationError("group_id must be a group ID or null.")
        try:
            with self._groups_connection() as connection:
                connection.execute(
                    "INSERT INTO project_group_assignment (project_id, group_id) VALUES (?, ?) "
                    "ON CONFLICT(project_id) DO UPDATE SET group_id = excluded.group_id",
                    (project_id, group_id),
                )
        except sqlite3.IntegrityError as exc:
            raise NotFoundError("Project group was not found.") from exc

    def mark_project_deleting(self, project_id: str) -> None:
        directory = self._project_dir(project_id)
        marker = directory / ".cadyx-deleting"
        try:
            descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise ConflictError("Project deletion is already in progress.", code="PROJECT_BUSY") from exc
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
            with self._groups_connection() as connection:
                connection.execute(
                    "DELETE FROM project_group_assignment WHERE project_id = ?", (project_id,)
                )
                directory.rename(tombstone)
        except Exception as exc:
            if directory.exists() and marker.exists():
                marker.unlink(missing_ok=True)
            if isinstance(exc, OSError):
                raise ValidationError(f"Project directory could not be detached for deletion: {exc}.") from exc
            raise
        self._forget_project_path(project_id, directory)
        # A viewer may still hold a read handle briefly; the hidden tombstone
        # is retried by workspace listing if Windows refuses immediate cleanup.
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
                if (not cached.is_symlink() and cached.is_dir() and cached.resolve(strict=True).parent == self.root
                        and not (cached / ".cadyx-deleting").exists()):
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
            raise ValidationError("Project name must be a non-empty string of at most 120 characters.")
        project_id = str(uuid4())
        project = self._new_project_record(project_id, name.strip())
        project_dir = self._create_project_directory(project["name"], project_id)
        self._write_atomic(project_dir / "project.json", project)
        self._remember_project_path(project_id, project_dir)
        return project

    def rename(self, project_id: str, name: str) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValidationError("Project name must be a non-empty string of at most 120 characters.")
        project = self.load(project_id)
        source = self._project_dir(project_id)
        base_name = self._safe_project_directory_name(name.strip())
        target = self._unique_project_directory_path(base_name, exclude=source)
        project["name"] = name.strip()
        project["modified_at"] = utc_now()
        try:
            source.rename(target)
            self._write_atomic(target / "project.json", project)
        except OSError as exc:
            if target.exists() and not source.exists():
                try:
                    target.rename(source)
                except OSError as rollback_error:
                    raise ConflictError(
                        f"Project folder rename failed and rollback needs recovery: {rollback_error}.",
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
        now = utc_now()
        return {
            "format": "cadyx-project",
            "version": PROJECT_VERSION,
            "id": project_id,
            "name": name,
            "created_at": now,
            "modified_at": now,
            "document": Document.create().to_dict(),
            "history": [],
        }

    def load(self, project_id: str) -> dict[str, Any]:
        return self._load_project_from_directory(self._project_dir(project_id), project_id)

    def _load_project_from_directory(self, directory: Path, project_id: str) -> dict[str, Any]:
        project_file = directory / "project.json"
        try:
            with project_file.open("r", encoding="utf-8") as stream:
                project = json.load(stream)
        except FileNotFoundError as exc:
            raise NotFoundError("Project was not found.") from exc
        except (json.JSONDecodeError, OSError) as exc:
            raise ValidationError(f"Project file could not be read: {exc}.") from exc
        project = migrate_project(project)
        self._validate_current(project, expected_id=project_id)
        self._remember_project_path(project_id, directory)
        return project

    def save(self, project: dict[str, Any]) -> None:
        project = migrate_project(project)
        project_id = project.get("id") if isinstance(project, dict) else None
        project_dir = self._project_dir(project_id)
        self._validate_current(project, expected_id=project_id)
        self._write_atomic(project_dir / "project.json", project)

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
            if not self._write_new_file(marker, f"{project_id}\n".encode("ascii")):
                project_id = self._project_record_id(directory)
                if project_id is None:
                    return None

        project = self._new_project_record(project_id, directory.name)
        encoded = (json.dumps(project, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        if not self._write_new_file(project_file, encoded):
            return self._project_id_for_directory(directory)
        self._remember_project_path(project_id, directory)
        return project_id

    def list_folder_projects(self) -> list[str]:
        """Return direct workspace directories that do not contain a CADYX project record."""
        folders: list[str] = []
        for entry in self._project_directories():
            if self._project_id_for_directory(entry) is not None:
                continue
            folders.append(entry.name)
        folders.sort(key=str.casefold)
        return folders

    def list_folder_files(self, folder_name: str) -> list[dict[str, Any]]:
        folder = self._workspace_folder_path(folder_name)
        files: list[dict[str, Any]] = []
        for entry in folder.iterdir():
            if (entry.name.startswith(".cadyx-tmp-")
                    or self._is_internal_project_file(folder, entry.name)
                    or entry.is_symlink() or not entry.is_file()):
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
                "modified_at_ns": str(stat.st_mtime_ns),
                "media_type": mimetypes.guess_type(entry.name)[0] or "application/octet-stream",
            })
        files.sort(key=lambda item: item["name"].casefold())
        return files

    def list_project_files(self, project_id: str) -> list[dict[str, Any]]:
        directory = self._project_dir(project_id)
        files: list[dict[str, Any]] = []
        for entry in directory.iterdir():
            if (entry.name == "project.json" or entry.name.startswith(".cadyx-")
                    or entry.is_symlink() or not entry.is_file()):
                continue
            try:
                resolved = entry.resolve(strict=True)
                if resolved.parent != directory:
                    continue
                stat = resolved.stat()
            except OSError:
                continue
            files.append({
                "name": entry.name,
                "size": stat.st_size,
                "modified_at_ns": str(stat.st_mtime_ns),
                "media_type": mimetypes.guess_type(entry.name)[0] or "application/octet-stream",
            })
        files.sort(key=lambda item: item["name"].casefold())
        return files

    def copy_project_files(self, source_project_id: str, target_project_id: str) -> dict[str, Any]:
        """Copy user files recursively between projects without copying project state or overwriting."""
        if source_project_id == target_project_id:
            raise ValidationError("Source and target projects must be different.")
        source_root = self._project_dir(source_project_id)
        target_root = self._project_dir(target_project_id)
        files: list[tuple[Path, Path, int]] = []
        for source in sorted(source_root.rglob("*"), key=lambda item: item.as_posix().casefold()):
            relative = source.relative_to(source_root)
            if any(part.startswith(".cadyx-") for part in relative.parts) or relative == Path("project.json"):
                continue
            if source.is_symlink():
                raise ValidationError(f"Project merge does not copy symbolic links: {relative.as_posix()}.")
            if source.is_dir():
                continue
            if not source.is_file():
                continue
            resolved = source.resolve(strict=True)
            if source_root not in resolved.parents:
                raise ValidationError(f"Project file escapes its source directory: {relative.as_posix()}.")
            destination = target_root / relative
            files.append((source, destination, resolved.stat().st_size))

        for source, destination, _ in files:
            current = target_root
            for part in destination.parent.relative_to(target_root).parts:
                current = current / part
                if current.exists() or current.is_symlink():
                    if current.is_symlink() or not current.is_dir():
                        raise ConflictError(
                            f"Project merge destination is not a safe directory: {current.relative_to(target_root).as_posix()}.",
                            code="ARTIFACT_CONFLICT",
                        )
            if destination.exists() or destination.is_symlink():
                raise ConflictError(
                    f"Project merge would overwrite {destination.relative_to(target_root).as_posix()}; no files were copied.",
                    code="ARTIFACT_CONFLICT",
                )

        created_directories: list[Path] = []
        copied: list[Path] = []
        manifest: list[dict[str, Any]] = []
        try:
            for source, destination, size in files:
                missing: list[Path] = []
                parent = destination.parent
                while parent != target_root and not parent.exists():
                    missing.append(parent)
                    parent = parent.parent
                for directory in reversed(missing):
                    directory.mkdir()
                    created_directories.append(directory)
                digest = hashlib.sha256()
                temporary: Path | None = None
                try:
                    with source.open("rb") as input_stream, tempfile.NamedTemporaryFile(
                        mode="wb", prefix=".cadyx-tmp-merge-", dir=destination.parent, delete=False,
                    ) as output_stream:
                        temporary = Path(output_stream.name)
                        while chunk := input_stream.read(1024 * 1024):
                            output_stream.write(chunk)
                            digest.update(chunk)
                        output_stream.flush()
                        os.fsync(output_stream.fileno())
                    os.link(temporary, destination)
                    copied.append(destination)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                manifest.append({
                    "path": destination.relative_to(target_root).as_posix(),
                    "size": size,
                    "sha256": digest.hexdigest(),
                })
        except Exception:
            for destination in reversed(copied):
                destination.unlink(missing_ok=True)
            for directory in reversed(created_directories):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            raise
        return {"source_project_id": source_project_id, "target_project_id": target_project_id,
                "file_count": len(manifest), "files": manifest}

    def prune_project_files(self, project_id: str, keep_files: list[str]) -> dict[str, Any]:
        """Remove project user files outside an explicit direct-file allowlist."""
        root = self._project_dir(project_id)
        if not keep_files or any(not self._is_single_path_segment(name) for name in keep_files):
            raise ValidationError("keep_files must contain direct project filenames only.")
        if len(set(keep_files)) != len(keep_files):
            raise ValidationError("keep_files must not contain duplicates.")
        keep = set(keep_files)
        entries = sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold())
        for name in keep:
            retained = root / name
            if retained.is_symlink() or not retained.is_file():
                raise NotFoundError(f"Required project source file was not found: {name}.")
        files: list[Path] = []
        for entry in entries:
            relative = entry.relative_to(root)
            if any(part.startswith(".cadyx-") for part in relative.parts) or relative == Path("project.json"):
                continue
            if entry.is_symlink():
                raise ValidationError(f"Refusing to prune a project containing a symbolic link: {relative.as_posix()}.")
            if entry.is_dir():
                continue
            if not entry.is_file():
                continue
            if relative.parts == (entry.name,) and entry.name in keep:
                continue
            resolved = entry.resolve(strict=True)
            if root not in resolved.parents:
                raise ValidationError(f"Project file escapes its directory: {relative.as_posix()}.")
            files.append(entry)
        removed: list[str] = []
        for path in files:
            relative = path.relative_to(root).as_posix()
            path.unlink()
            removed.append(relative)
        for directory in sorted((p for p in entries if p.is_dir() and not p.is_symlink()),
                                key=lambda p: len(p.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
        retained = []
        for name in sorted(keep):
            path = root / name
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            retained.append({"path": name, "size": path.stat().st_size, "sha256": digest.hexdigest()})
        return {"project_id": project_id, "removed_count": len(removed), "removed": removed,
                "retained": retained}

    def publish_project_step(self, project_id: str, filename: str, content: bytes) -> dict[str, Any]:
        """Atomically add one STEP source file without replacing project data."""
        if (not self._is_single_path_segment(filename)
                or Path(filename).suffix.casefold() not in {".step", ".stp"}
                or not isinstance(content, bytes) or not content
                or len(content) > 64 * 1024 * 1024):
            raise ValidationError("STEP upload needs a direct .step/.stp filename and a non-empty payload no larger than 64 MiB.")
        directory = self._project_dir(project_id)
        target = directory / filename
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=".cadyx-tmp-", suffix=".step-upload", dir=directory, delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError as exc:
                raise ConflictError(
                    "A file with this name already exists in the project; uploads never overwrite files.",
                    code="ARTIFACT_CONFLICT",
                ) from exc
        except ConflictError:
            raise
        except OSError as exc:
            raise ValidationError(f"STEP source file could not be saved: {exc}.") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return {
            "name": filename,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    def remove_project_step(self, project_id: str, filename: str) -> dict[str, Any]:
        """Remove one direct STEP reference file after its document dependency is cleared."""
        if (not self._is_single_path_segment(filename)
                or Path(filename).suffix.casefold() not in {".step", ".stp"}):
            raise ValidationError("Only a direct .step or .stp reference file can be removed.")
        path = self.resolve_project_file(project_id, filename)
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError as exc:
            raise ValidationError(f"STEP reference file could not be removed: {exc}.") from exc
        return {"name": filename, "removed": True, "size": size}

    def workspace_revision_token(self) -> str:
        """Return a cheap token for project, folder and group-list changes."""
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
                # A concurrent workspace change is picked up on the next poll.
                continue
        try:
            groups_stat = self._groups_db_path.stat()
            if self._groups_db_path.is_file() and not self._groups_db_path.is_symlink():
                entries.append((self._groups_db_path.name, "groups", groups_stat.st_mtime_ns, groups_stat.st_size))
        except OSError:
            pass
        return self._revision_token(entries)

    def folder_revision_token(self, folder_name: str) -> str:
        """Return a token for the visible metadata of direct files in one folder."""
        files = self.list_folder_files(folder_name)
        return self._revision_token([
            (item["name"], item["size"], item["modified_at_ns"])
            for item in files
        ])

    def project_revision_token(self, project_id: str) -> str:
        """Return a stat-only token for one saved project document."""
        project_file = self._project_dir(project_id) / "project.json"
        if project_file.is_symlink() or not project_file.is_file():
            raise NotFoundError("Project was not found.")
        try:
            stat = project_file.stat()
        except OSError as exc:
            raise NotFoundError("Project was not found.") from exc
        return f"{stat.st_mtime_ns}:{stat.st_size}"

    def resolve_project_file(self, project_id: str, file_name: str) -> Path:
        """Resolve one regular file directly inside a saved project directory."""
        if (not self._is_single_path_segment(file_name)
                or file_name in {"project.json", ".cadyx-project-id"}
                or file_name.startswith(".cadyx-")):
            raise NotFoundError("Project file was not found.")
        directory = self._project_dir(project_id)
        path = directory / file_name
        if path.is_symlink() or not path.is_file():
            raise NotFoundError("Project file was not found.")
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise NotFoundError("Project file was not found.") from exc
        if resolved.parent != directory:
            raise NotFoundError("Project file was not found.")
        return resolved

    @staticmethod
    def _revision_token(value: Any) -> str:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def resolve_folder_file(self, folder_name: str, file_name: str) -> Path:
        folder = self._workspace_folder_path(folder_name)
        if not self._is_single_path_segment(file_name):
            raise NotFoundError("Project file was not found.")
        if file_name.startswith(".cadyx-tmp-") or self._is_internal_project_file(folder, file_name):
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
                with target.open("rb") as existing_stream:
                    existing_hash = hashlib.file_digest(existing_stream, "sha256").hexdigest()
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

    def publish_cad_delivery(self, project_id: str, files: dict[str, bytes]) -> list[dict[str, Any]]:
        """Atomically publish CAD files without replacing any existing project file."""
        if not isinstance(files, dict) or not files:
            raise ValidationError("CAD delivery needs at least one artifact file.")
        directory = self._project_dir(project_id)
        contents: list[tuple[str, bytes, str]] = []
        for filename, content in files.items():
            if (not self._is_single_path_segment(filename)
                    or filename in {"project.json", ".cadyx-project-id"}
                    or filename.startswith((".cadyx-", ".cadyx-tmp-"))
                    or Path(filename).suffix.casefold() not in {".step", ".stl", ".json"}
                    or (filename.casefold().endswith(".json") and not filename.casefold().endswith(".cad-delivery.json"))
                    or not isinstance(content, bytes) or not content):
                raise ValidationError("CAD delivery contains an invalid artifact filename or empty payload.")
            contents.append((filename, content, hashlib.sha256(content).hexdigest()))
        contents.sort(key=lambda item: item[0].casefold().endswith(".cad-delivery.json"))

        temporary_by_name: dict[str, Path] = {}
        published: list[dict[str, Any]] = []
        created_paths: list[tuple[Path, Path]] = []

        def matching_existing(target: Path, digest: str) -> bool:
            if target.is_symlink() or not target.is_file():
                return False
            try:
                with target.open("rb") as stream:
                    return hashlib.file_digest(stream, "sha256").hexdigest() == digest
            except OSError:
                return False

        try:
            for filename, content, _ in contents:
                with tempfile.NamedTemporaryFile(
                    mode="wb", prefix=".cadyx-tmp-", suffix=".part", dir=directory, delete=False,
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary_by_name[filename] = temporary

            for filename, content, digest in contents:
                target = directory / filename
                if target.exists() or target.is_symlink():
                    if not matching_existing(target, digest):
                        raise ConflictError(
                            f"A different file already exists at CAD delivery path '{filename}'.",
                            code="ARTIFACT_CONFLICT",
                        )
                    created = False
                else:
                    temporary = temporary_by_name[filename]
                    try:
                        os.link(temporary, target)
                        created_paths.append((target, temporary))
                        created = True
                    except FileExistsError:
                        if not matching_existing(target, digest):
                            raise ConflictError(
                                f"A different file already exists at CAD delivery path '{filename}'.",
                                code="ARTIFACT_CONFLICT",
                            )
                        created = False
                published.append({"file": filename, "sha256": digest, "size": len(content), "created": created})
        except ConflictError:
            for target, temporary in reversed(created_paths):
                try:
                    if target.exists() and os.path.samefile(target, temporary):
                        target.unlink()
                except OSError:
                    pass
            raise
        except OSError as exc:
            for target, temporary in reversed(created_paths):
                try:
                    if target.exists() and os.path.samefile(target, temporary):
                        target.unlink()
                except OSError:
                    pass
            raise ValidationError(f"CAD delivery artifacts could not be saved: {exc}.") from exc
        finally:
            for temporary in temporary_by_name.values():
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        return published

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
                if entry.name.startswith(".cadyx-deleted-") or entry.is_symlink() or not entry.is_dir():
                    continue
                try:
                    resolved = entry.resolve(strict=True)
                except OSError:
                    continue
                if resolved.parent == self.root and not (resolved / ".cadyx-deleting").exists():
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
        if (not isinstance(project, dict) or project.get("format") != "cadyx-project"
                or not isinstance(project_id, str) or not _PROJECT_ID.fullmatch(project_id)):
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
        raise ConflictError("A unique project folder name could not be allocated.", code="PROJECT_FOLDER_CONFLICT")

    @staticmethod
    def _safe_project_directory_name(name: str) -> str:
        base_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name.strip())
        base_name = re.sub(r"\s+", " ", base_name).strip(" .")[:80].rstrip(" .")
        if not base_name:
            base_name = "CADYX project"
        if base_name.split(".", maxsplit=1)[0].upper() in _WINDOWS_RESERVED_NAMES:
            base_name = f"_{base_name}"
        return base_name

    def _unique_project_directory_paths(self, base_name: str, *, exclude: Path | None = None):
        for suffix in range(1, 10_000):
            candidate = base_name if suffix == 1 else f"{base_name[:72].rstrip(' .')} ({suffix})"
            directory = self.root / candidate
            if directory == exclude:
                continue
            if not directory.exists() and not directory.is_symlink():
                yield directory

    def _unique_project_directory_path(self, base_name: str, *, exclude: Path | None = None) -> Path:
        try:
            return next(self._unique_project_directory_paths(base_name, exclude=exclude))
        except StopIteration as exc:
            raise ConflictError("A unique project folder name could not be allocated.", code="PROJECT_FOLDER_CONFLICT") from exc

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
            for attempt in range(5):
                try:
                    os.replace(temporary_path, path)
                    break
                except PermissionError as exc:
                    # Windows can briefly deny replacement while another
                    # process opens the project file. Keep the old document
                    # intact and retry only known sharing/access violations.
                    if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 4:
                        raise
                    time.sleep(0.05 * (2 ** attempt))
            temporary_path = None
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.unlink(temporary_path)

    @staticmethod
    def _write_new_file(path: Path, contents: bytes) -> bool:
        """Publish a new file atomically without replacing a concurrent or user file."""
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent, prefix=".cadyx-tmp-", suffix=".tmp", delete=False,
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
