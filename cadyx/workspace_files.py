"""User file and generated-artifact operations for CADYX workspaces."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import tempfile
from pathlib import Path
from typing import Any, Callable

from .errors import ConflictError, NotFoundError, ValidationError


class WorkspaceArtifactStore:
    """Own safe user-file copy, prune, resolution and artifact publication."""

    def __init__(self, is_internal_file: Callable[[Path, str], bool]) -> None:
        self._is_internal_file = is_internal_file

    def list_folder_files(self, folder: Path) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        for entry in folder.iterdir():
            if (
                entry.name.startswith(".cadyx-")
                or self._is_internal_file(folder, entry.name)
                or entry.is_symlink()
                or not entry.is_file()
            ):
                continue
            try:
                resolved = entry.resolve(strict=True)
                if resolved.parent != folder:
                    continue
                stat = resolved.stat()
            except OSError:
                continue
            files.append(
                {
                    "name": entry.name,
                    "size": stat.st_size,
                    "modified_at_ns": str(stat.st_mtime_ns),
                    "media_type": mimetypes.guess_type(entry.name)[0]
                    or "application/octet-stream",
                }
            )
        files.sort(key=lambda item: item["name"].casefold())
        return files

    @staticmethod
    def list_project_files(directory: Path) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        for entry in directory.iterdir():
            if (
                entry.name == "project.json"
                or entry.name.startswith(".cadyx-")
                or entry.is_symlink()
                or not entry.is_file()
            ):
                continue
            try:
                resolved = entry.resolve(strict=True)
                if resolved.parent != directory:
                    continue
                stat = resolved.stat()
            except OSError:
                continue
            files.append(
                {
                    "name": entry.name,
                    "size": stat.st_size,
                    "modified_at_ns": str(stat.st_mtime_ns),
                    "media_type": mimetypes.guess_type(entry.name)[0]
                    or "application/octet-stream",
                }
            )
        files.sort(key=lambda item: item["name"].casefold())
        return files

    @staticmethod
    def list_project_folders(directory: Path) -> list[dict[str, Any]]:
        folders: list[dict[str, Any]] = []
        for entry in directory.iterdir():
            if entry.name.startswith(".") or entry.is_symlink() or not entry.is_dir():
                continue
            try:
                resolved = entry.resolve(strict=True)
                if resolved.parent != directory:
                    continue
                stat = resolved.stat()
            except OSError:
                continue
            folders.append({"name": entry.name, "modified_at_ns": str(stat.st_mtime_ns)})
        folders.sort(key=lambda item: item["name"].casefold())
        return folders

    @staticmethod
    def _project_subfolder(directory: Path, folder_name: str) -> Path:
        if not isinstance(folder_name, str) or "\x00" in folder_name or ":" in folder_name:
            raise NotFoundError("Project folder was not found.")
        parts = folder_name.replace("\\", "/").split("/")
        if not parts or any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
            raise NotFoundError("Project folder was not found.")
        current = directory
        for part in parts:
            if not _is_single_path_segment(part):
                raise NotFoundError("Project folder was not found.")
            folder = current / part
            if folder.is_symlink() or not folder.is_dir():
                raise NotFoundError("Project folder was not found.")
            try:
                resolved = folder.resolve(strict=True)
            except OSError as exc:
                raise NotFoundError("Project folder was not found.") from exc
            if resolved.parent != current:
                raise NotFoundError("Project folder was not found.")
            current = resolved
        return current

    def list_project_subfolder_files(self, directory: Path, folder_name: str) -> list[dict[str, Any]]:
        return self.list_folder_files(self._project_subfolder(directory, folder_name))

    def list_project_subfolders(self, directory: Path, folder_name: str) -> list[dict[str, Any]]:
        return self.list_project_folders(self._project_subfolder(directory, folder_name))

    def resolve_project_subfolder_file(self, directory: Path, folder_name: str, file_name: str) -> Path:
        return self.resolve_folder_file(self._project_subfolder(directory, folder_name), file_name)

    @staticmethod
    def copy_project_files(
        source_project_id: str,
        target_project_id: str,
        source_root: Path,
        target_root: Path,
    ) -> dict[str, Any]:
        if source_project_id == target_project_id:
            raise ValidationError("Source and target projects must be different.")
        files: list[tuple[Path, Path, int]] = []
        for source in sorted(source_root.rglob("*"), key=lambda item: item.as_posix().casefold()):
            relative = source.relative_to(source_root)
            if any(part.startswith(".cadyx-") for part in relative.parts) or relative == Path(
                "project.json"
            ):
                continue
            if source.is_symlink():
                raise ValidationError(
                    f"Project merge does not copy symbolic links: {relative.as_posix()}."
                )
            if source.is_dir():
                continue
            if not source.is_file():
                continue
            resolved = source.resolve(strict=True)
            if source_root not in resolved.parents:
                raise ValidationError(
                    f"Project file escapes its source directory: {relative.as_posix()}."
                )
            destination = target_root / relative
            files.append((source, destination, resolved.stat().st_size))

        for _, destination, _ in files:
            current = target_root
            for part in destination.parent.relative_to(target_root).parts:
                current = current / part
                if current.exists() or current.is_symlink():
                    if current.is_symlink() or not current.is_dir():
                        raise ConflictError(
                            "Project merge destination is not a safe directory: "
                            f"{current.relative_to(target_root).as_posix()}.",
                            code="ARTIFACT_CONFLICT",
                        )
            if destination.exists() or destination.is_symlink():
                raise ConflictError(
                    "Project merge would overwrite "
                    f"{destination.relative_to(target_root).as_posix()}; no files were copied.",
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
                        mode="wb",
                        prefix=".cadyx-tmp-merge-",
                        dir=destination.parent,
                        delete=False,
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
                manifest.append(
                    {
                        "path": destination.relative_to(target_root).as_posix(),
                        "size": size,
                        "sha256": digest.hexdigest(),
                    }
                )
        except Exception:
            for destination in reversed(copied):
                destination.unlink(missing_ok=True)
            for directory in reversed(created_directories):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            raise
        return {
            "source_project_id": source_project_id,
            "target_project_id": target_project_id,
            "file_count": len(manifest),
            "files": manifest,
        }

    @staticmethod
    def prune_project_files(
        project_id: str,
        root: Path,
        keep_files: list[str],
    ) -> dict[str, Any]:
        if not keep_files or any(not _is_single_path_segment(name) for name in keep_files):
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
            if any(part.startswith(".cadyx-") for part in relative.parts) or relative == Path(
                "project.json"
            ):
                continue
            if entry.is_symlink():
                raise ValidationError(
                    "Refusing to prune a project containing a symbolic link: "
                    f"{relative.as_posix()}."
                )
            if entry.is_dir():
                continue
            if not entry.is_file():
                continue
            if relative.parts == (entry.name,) and entry.name in keep:
                continue
            resolved = entry.resolve(strict=True)
            if root not in resolved.parents:
                raise ValidationError(
                    f"Project file escapes its directory: {relative.as_posix()}."
                )
            files.append(entry)
        removed: list[str] = []
        for path in files:
            relative = path.relative_to(root).as_posix()
            path.unlink()
            removed.append(relative)
        for directory in sorted(
            (path for path in entries if path.is_dir() and not path.is_symlink()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            try:
                directory.rmdir()
            except OSError:
                pass
        retained_files = []
        for name in sorted(keep):
            path = root / name
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            retained_files.append(
                {"path": name, "size": path.stat().st_size, "sha256": digest.hexdigest()}
            )
        return {
            "project_id": project_id,
            "removed_count": len(removed),
            "removed": removed,
            "retained": retained_files,
        }

    @staticmethod
    def _publish_project_source(
        directory: Path, filename: str, content: bytes, *, suffixes: set[str], kind: str,
    ) -> dict[str, Any]:
        if (
            not _is_single_path_segment(filename)
            or Path(filename).suffix.casefold() not in suffixes
            or not isinstance(content, bytes)
            or not content
            or len(content) > 64 * 1024 * 1024
        ):
            raise ValidationError(
                f"{kind} upload needs a direct supported filename and a non-empty payload "
                "no larger than 64 MiB."
            )
        target = directory / filename
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".cadyx-tmp-",
                suffix=".project-source-upload",
                dir=directory,
                delete=False,
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
            raise ValidationError(f"{kind} source file could not be saved: {exc}.") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return {
            "name": filename,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    @staticmethod
    def publish_project_step(directory: Path, filename: str, content: bytes) -> dict[str, Any]:
        return WorkspaceArtifactStore._publish_project_source(
            directory, filename, content, suffixes={".step", ".stp"}, kind="STEP",
        )

    @staticmethod
    def publish_project_scan(directory: Path, filename: str, content: bytes) -> dict[str, Any]:
        return WorkspaceArtifactStore._publish_project_source(
            directory, filename, content, suffixes={".ply"}, kind="PLY scan",
        )

    @staticmethod
    def remove_project_step(path: Path, filename: str) -> dict[str, Any]:
        if (
            not _is_single_path_segment(filename)
            or Path(filename).suffix.casefold() not in {".step", ".stp"}
        ):
            raise ValidationError("Only a direct .step or .stp reference file can be removed.")
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError as exc:
            raise ValidationError(f"STEP reference file could not be removed: {exc}.") from exc
        return {"name": filename, "removed": True, "size": size}

    @staticmethod
    def resolve_project_file(directory: Path, file_name: str) -> Path:
        if (
            not _is_single_path_segment(file_name)
            or file_name in {"project.json", ".cadyx-project-id"}
            or file_name.startswith(".cadyx-")
        ):
            raise NotFoundError("Project file was not found.")
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

    def resolve_folder_file(self, folder: Path, file_name: str) -> Path:
        if not _is_single_path_segment(file_name):
            raise NotFoundError("Project file was not found.")
        if file_name.startswith(".cadyx-tmp-") or self._is_internal_file(folder, file_name):
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

    @staticmethod
    def publish_scan_output(folder: Path, folder_name: str, source_filename: str, content: bytes) -> dict[str, Any]:
        if not _is_single_path_segment(source_filename) or not isinstance(content, bytes) or not content:
            raise ValidationError("A source filename and non-empty scan output are required.")
        digest = hashlib.sha256(content).hexdigest()
        stem = Path(source_filename).stem[:100].rstrip(" .") or "scan"
        filename = f"{stem}.cadyx-{digest[:12]}.ply"
        target = folder / filename
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".cadyx-tmp-",
                suffix=".part",
                dir=folder,
                delete=False,
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

    @staticmethod
    def publish_project_scan_output(directory: Path, project_name: str, source_filename: str,
                                    content: bytes) -> dict[str, Any]:
        artifact = WorkspaceArtifactStore.publish_scan_output(
            directory, project_name, source_filename, content,
        )
        artifact.pop("folder", None)
        return {"project": project_name, **artifact}

    @staticmethod
    def publish_cad_delivery(directory: Path, files: dict[str, bytes]) -> list[dict[str, Any]]:
        if not isinstance(files, dict) or not files:
            raise ValidationError("CAD delivery needs at least one artifact file.")
        contents: list[tuple[str, bytes, str]] = []
        for filename, content in files.items():
            if (
                not _is_single_path_segment(filename)
                or filename in {"project.json", ".cadyx-project-id"}
                or filename.startswith((".cadyx-", ".cadyx-tmp-"))
                or Path(filename).suffix.casefold() not in {".step", ".stl", ".json"}
                or (
                    filename.casefold().endswith(".json")
                    and not filename.casefold().endswith(".cad-delivery.json")
                )
                or not isinstance(content, bytes)
                or not content
            ):
                raise ValidationError(
                    "CAD delivery contains an invalid artifact filename or empty payload."
                )
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
                    mode="wb",
                    prefix=".cadyx-tmp-",
                    suffix=".part",
                    dir=directory,
                    delete=False,
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
                published.append(
                    {
                        "file": filename,
                        "sha256": digest,
                        "size": len(content),
                        "created": created,
                    }
                )
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
