"""Project record persistence and validation for a CADYX workspace."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from .errors import NotFoundError, ValidationError
from .migrations import DOCUMENT_VERSION, PROJECT_VERSION, migrate_project
from .models import Document, OwnerType

_PROJECT_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_URL_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_CYRILLIC_TO_LATIN = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
})
_GEOMETRY_REF = "$cadyx_geometry_ref"
_STORAGE_KEY = "$cadyx_storage"
_HISTORY_FILE = ".cadyx-history.jsonl"
PROJECT_TYPES = frozenset({"single_model", "assembly"})


def _brep_key(geometry: dict[str, Any]) -> str:
    digest = hashlib.sha256(geometry["data"].encode("ascii"))
    metadata = {key: value for key, value in geometry.items() if key != "data"}
    digest.update(json.dumps(metadata, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    return digest.hexdigest()


def _externalize_project_geometries(project: dict[str, Any], directory: Path) -> dict[str, Any]:
    written: set[str] = set()

    def replace(value: Any) -> Any:
        if isinstance(value, dict):
            if value.get("kind") == "imported_brep" and isinstance(value.get("data"), str):
                key = _brep_key(value)
                if key not in written:
                    path = directory / f".cadyx-brep-{key}.json"
                    if path.is_symlink():
                        raise ValidationError("Project geometry blob path cannot be a symbolic link.")
                    if path.exists() and not path.is_file():
                        raise ValidationError("Project geometry blob path is not a regular file.")
                    if not path.exists():
                        temporary = tempfile.NamedTemporaryFile(
                            mode="w", encoding="utf-8", newline="\n", dir=directory,
                            prefix=f".{path.name}.", suffix=".tmp", delete=False,
                        )
                        temporary_path = temporary.name
                        try:
                            with temporary:
                                temporary.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
                                temporary.flush()
                                os.fsync(temporary.fileno())
                            try:
                                os.link(temporary_path, path)
                            except FileExistsError:
                                pass
                        finally:
                            if os.path.exists(temporary_path):
                                os.unlink(temporary_path)
                    written.add(key)
                return {_GEOMETRY_REF: key}
            return {key: replace(child) for key, child in value.items()}
        if isinstance(value, list):
            return [replace(child) for child in value]
        return value

    return replace(project)


def _unpack_project_geometries(project: dict[str, Any], directory: Path) -> dict[str, Any]:
    storage = project.pop(_STORAGE_KEY, None)
    if storage is None:
        def contains_reference(value: Any) -> bool:
            if isinstance(value, dict):
                return set(value) == {_GEOMETRY_REF} or any(contains_reference(child) for child in value.values())
            if isinstance(value, list):
                return any(contains_reference(child) for child in value)
            return False

        if contains_reference(project):
            raise ValidationError("Project geometry storage table is missing.")
        return project
    if not isinstance(storage, dict):
        raise ValidationError("Project geometry storage is invalid.")

    def restore(value: Any) -> Any:
        if isinstance(value, dict):
            if set(value) == {_GEOMETRY_REF}:
                key = value[_GEOMETRY_REF]
                geometry = None
                if isinstance(key, str) and re.fullmatch(r"[0-9a-f]{64}", key):
                    blob_path = directory / f".cadyx-brep-{key}.json"
                    if blob_path.is_symlink():
                        raise ValidationError("Project geometry blob path cannot be a symbolic link.")
                    try:
                        with blob_path.open("r", encoding="utf-8") as stream:
                            geometry = json.load(stream)
                    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
                        raise ValidationError("Project geometry blob is missing or invalid.") from exc
                if not isinstance(geometry, dict) or geometry.get("kind") != "imported_brep":
                    raise ValidationError("Project geometry reference is invalid.")
                try:
                    actual_key = _brep_key(geometry)
                except (KeyError, TypeError, ValueError, UnicodeError) as exc:
                    raise ValidationError("Project geometry blob descriptor is invalid.") from exc
                if actual_key != key:
                    raise ValidationError("Project geometry blob checksum is invalid.")
                return geometry
            return {key: restore(child) for key, child in value.items()}
        if isinstance(value, list):
            return [restore(child) for child in value]
        return value

    return restore(project)


def _read_history_log(directory: Path, pointer: Any) -> tuple[list[dict[str, Any]], int]:
    if (not isinstance(pointer, dict) or pointer.get("file") != _HISTORY_FILE
            or type(pointer.get("count")) is not int or pointer["count"] < 0
            or type(pointer.get("bytes")) is not int or pointer["bytes"] < 0):
        raise ValidationError("Project history pointer is invalid.")
    count, committed_bytes = pointer["count"], pointer["bytes"]
    path = directory / _HISTORY_FILE
    if path.is_symlink():
        raise ValidationError("Project history log cannot be a symbolic link.")
    if count == 0 and committed_bytes == 0 and not path.exists():
        return [], 0
    entries: list[dict[str, Any]] = []
    consumed = 0
    try:
        with path.open("rb") as stream:
            while consumed < committed_bytes:
                line = stream.readline(committed_bytes - consumed + 1)
                if not line or not line.endswith(b"\n") or consumed + len(line) > committed_bytes:
                    raise ValidationError("Project history log is truncated before its committed offset.")
                entry = json.loads(line)
                if not isinstance(entry, dict):
                    raise ValidationError("Project history log entry must be an object.")
                entries.append(entry)
                consumed += len(line)
    except FileNotFoundError as exc:
        raise ValidationError("Project history log is missing.") from exc
    except (json.JSONDecodeError, OSError) as exc:
        raise ValidationError(f"Project history log could not be read: {exc}.") from exc
    if consumed != committed_bytes or len(entries) != count:
        raise ValidationError("Project history log does not match its committed count and offset.")
    return entries, committed_bytes


class ProjectRecordStore:
    """Own project.json reads, migrations, validation and atomic writes."""

    def __init__(self) -> None:
        self._history_state: dict[str, tuple[int, int, bool]] = {}

    def invalidate_history_state(self, project_file: Path) -> None:
        self._history_state.pop(str(project_file), None)

    @staticmethod
    def new_record(
        project_id: str, name: str, *, timestamp: str, url_slug: str | None = None,
        project_type: str = "single_model",
    ) -> dict[str, Any]:
        return {
            "format": "cadyx-project",
            "version": PROJECT_VERSION,
            "id": project_id,
            "name": name,
            "project_type": ProjectRecordStore.normalize_project_type(project_type),
            "url_slug": ProjectRecordStore.normalize_url_slug(url_slug) if url_slug is not None else
            ProjectRecordStore.default_url_slug(name),
            "created_at": timestamp,
            "modified_at": timestamp,
            "document": Document.create().to_dict(),
            "history": [],
        }

    def load(self, directory: Path, project_id: str, *, include_history: bool = True) -> dict[str, Any]:
        project_file = directory / "project.json"
        try:
            with project_file.open("r", encoding="utf-8") as stream:
                project = json.load(stream)
        except FileNotFoundError as exc:
            raise NotFoundError("Project was not found.") from exc
        except (json.JSONDecodeError, OSError) as exc:
            raise ValidationError(f"Project file could not be read: {exc}.") from exc
        if not isinstance(project, dict):
            raise ValidationError("Project file must contain a JSON object.")
        if isinstance(project, dict):
            storage = project.get(_STORAGE_KEY)
            history_pointer = storage.get("history") if isinstance(storage, dict) else None
            if history_pointer is not None:
                if (not isinstance(history_pointer, dict)
                        or history_pointer.get("file") != _HISTORY_FILE
                        or type(history_pointer.get("count")) is not int
                        or type(history_pointer.get("bytes")) is not int):
                    raise ValidationError("Project history pointer is invalid.")
                count, offset = history_pointer["count"], history_pointer["bytes"]
                if count < 0 or offset < 0 or (count == 0 and offset != 0):
                    raise ValidationError("Project history pointer is invalid.")
                if include_history:
                    history, offset = _read_history_log(directory, history_pointer)
                    project["history"] = history
                else:
                    history_path = directory / _HISTORY_FILE
                    if count and (history_path.is_symlink() or not history_path.is_file()
                                  or history_path.stat().st_size < offset):
                        raise ValidationError("Project history log is missing or shorter than its committed offset.")
                    project["history"] = []
                    project["_history_count"] = count
                self._history_state[str(project_file)] = (count, offset, True)
            else:
                if not isinstance(project.get("history"), list):
                    raise ValidationError("Project history is missing.")
                self._history_state[str(project_file)] = (len(project["history"]), 0, False)
            project = _unpack_project_geometries(project, directory)
        if (project.get("version") != PROJECT_VERSION
                or not isinstance(project.get("document"), dict)
                or project["document"].get("version") != DOCUMENT_VERSION):
            project = migrate_project(project)
        if "url_slug" not in project and isinstance(project.get("name"), str):
            project["url_slug"] = self.default_url_slug(project["name"])
        if "project_type" not in project:
            project["project_type"] = self.infer_project_type(project)
        self.validate_current(
            project, expected_id=project_id, history_start=project.get("_history_count", 0),
        )
        return project

    def save(
        self, directory: Path, project: dict[str, Any], *, validated_history_prefix: int = 0,
        document_validated: bool = False,
    ) -> dict[str, Any]:
        if (not isinstance(project, dict) or project.get("version") != PROJECT_VERSION
                or not isinstance(project.get("document"), dict)
                or project["document"].get("version") != DOCUMENT_VERSION):
            project = migrate_project(project)
        project_id = project.get("id") if isinstance(project, dict) else None
        self.validate_current(
            project, expected_id=project_id, history_start=validated_history_prefix,
            document_validated=document_validated,
        )
        project_file = directory / "project.json"
        key = str(project_file)
        previous_count, committed_offset, externalized = self._history_state.get(key, (0, 0, False))
        if validated_history_prefix != previous_count:
            raise ValidationError("Project history prefix changed since its committed snapshot was loaded.")
        history_base_count = project.get("_history_count", 0)
        suffix = (project["history"] if "_history_count" in project else
                  project["history"][validated_history_prefix:])
        storage_input = dict(project)
        storage_input["history"] = suffix if externalized else project["history"]
        storage_input.pop("_history_count", None)
        persisted_project = _externalize_project_geometries(storage_input, directory)
        persisted_history = persisted_project["history"]
        if externalized:
            appended = b"".join(
                (json.dumps(entry, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
                for entry in persisted_history
            )
            history_path = directory / _HISTORY_FILE
            if history_path.is_symlink() or (history_path.exists() and not history_path.is_file()):
                raise ValidationError("Project history log path is not a regular file.")
            with history_path.open("a+b") as stream:
                stream.seek(committed_offset)
                stream.truncate()
                stream.write(appended)
                stream.flush()
                os.fsync(stream.fileno())
            history_offset = committed_offset + len(appended)
        else:
            all_history = b"".join(
                (json.dumps(entry, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
                for entry in persisted_history
            )
            history_path = directory / _HISTORY_FILE
            temporary = tempfile.NamedTemporaryFile(
                mode="wb", dir=directory, prefix=f".{_HISTORY_FILE}.", suffix=".tmp", delete=False,
            )
            temporary_path = temporary.name
            try:
                with temporary:
                    temporary.write(all_history)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                os.replace(temporary_path, history_path)
            finally:
                if os.path.exists(temporary_path):
                    os.unlink(temporary_path)
            history_offset = len(all_history)
        pointer = {
            "file": _HISTORY_FILE,
            "count": history_base_count + len(project["history"]),
            "bytes": history_offset,
        }
        stored_project = persisted_project
        stored_project = dict(stored_project)
        stored_project.pop("history", None)
        stored_project.pop("_history_count", None)
        storage = stored_project.setdefault(_STORAGE_KEY, {})
        if not isinstance(storage, dict):
            raise ValidationError("Project storage envelope is invalid.")
        storage["history"] = pointer
        self.write_atomic(project_file, stored_project)
        self._history_state[key] = (pointer["count"], history_offset, True)
        return project

    @staticmethod
    def validate(project: Any, *, expected_id: str | None = None) -> None:
        migrated = migrate_project(project)
        if isinstance(migrated, dict) and "url_slug" not in migrated and isinstance(migrated.get("name"), str):
            migrated["url_slug"] = ProjectRecordStore.default_url_slug(migrated["name"])
        ProjectRecordStore.validate_current(migrated, expected_id=expected_id)

    @staticmethod
    def validate_current(
        project: Any, *, expected_id: str | None = None, history_start: int = 0,
        document_validated: bool = False,
    ) -> None:
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
        if not isinstance(project.get("url_slug"), str) or not _URL_SLUG.fullmatch(project["url_slug"]):
            raise ValidationError("Project URL slug must contain lowercase English letters, digits and single hyphens.")
        ProjectRecordStore.normalize_project_type(project.get("project_type", "single_model"))
        if not isinstance(project.get("created_at"), str) or not isinstance(project.get("modified_at"), str):
            raise ValidationError("Project timestamps are invalid.")
        document = (Document.from_validated_dict(project.get("document")) if document_validated else
                    Document.from_current_dict(project.get("document")))
        history = project.get("history")
        if not isinstance(history, list):
            raise ValidationError("Project history must be a list.")
        history_base_count = project.get("_history_count", 0)
        if type(history_base_count) is not int or history_base_count < 0:
            raise ValidationError("Project history base count is invalid.")
        if document.revision != history_base_count + len(history):
            raise ValidationError("Document revision must match the number of committed history entries.")
        partial_history = "_history_count" in project
        expected_start = history_base_count if partial_history else 0
        if (type(history_start) is not int or history_start < expected_start
                or history_start > history_base_count + len(history)
                or (partial_history and history_start != expected_start)):
            raise ValidationError("Validated history prefix is invalid.")
        first_entry = 0 if partial_history else history_start
        for index in range(first_entry, len(history)):
            entry = history[index]
            if not isinstance(entry, dict):
                raise ValidationError("Every project history entry must be an object.")
            if entry.get("actor_type") not in {owner.value for owner in OwnerType}:
                raise ValidationError("History entry actor_type is invalid.")
            if not isinstance(entry.get("actor_id"), str) or not entry["actor_id"]:
                raise ValidationError("History entry actor_id is missing.")
            if not isinstance(entry.get("command"), dict) or not isinstance(entry.get("result"), dict):
                raise ValidationError("History entry command and result must be objects.")
            if not isinstance(entry.get("timestamp"), str) or entry.get("revision") != history_base_count + index + 1:
                raise ValidationError("History entry timestamp or revision is invalid.")

    @staticmethod
    def normalize_project_type(value: Any) -> str:
        if not isinstance(value, str) or value not in PROJECT_TYPES:
            raise ValidationError("Project type must be 'single_model' or 'assembly'.")
        return value

    @staticmethod
    def infer_project_type(project: dict[str, Any]) -> str:
        document = project.get("document")
        assembly = document.get("assembly") if isinstance(document, dict) else None
        if isinstance(assembly, dict) and any(
            assembly.get(key) for key in ("parts", "subassemblies", "instances", "mates")
        ):
            return "assembly"
        return "single_model"

    @staticmethod
    def normalize_url_slug(value: str) -> str:
        if not isinstance(value, str) or not _URL_SLUG.fullmatch(value):
            raise ValidationError("Project URL slug must contain lowercase English letters, digits and single hyphens.")
        return value

    @staticmethod
    def default_url_slug(name: str) -> str:
        transliterated = name.lower().translate(_CYRILLIC_TO_LATIN)
        slug = re.sub(r"[^a-z0-9]+", "-", transliterated).strip("-")
        return slug[:120].strip("-") or "project"

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
