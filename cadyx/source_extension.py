"""AppContainer-tested source extensions with durable, hash-checked rollback."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .errors import ConflictError, ValidationError
from .source_sandbox import (
    SourceSandboxUnavailable,
    build_scratch_runtime,
    run_appcontainer_process,
)

_ROOT = Path(__file__).resolve().parent.parent
_ALLOWED_ENV = {
    "ALLUSERSPROFILE", "APPDATA", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)",
    "COMMONPROGRAMW6432", "COMPUTERNAME", "COMSPEC", "DRIVERDATA", "HOMEDRIVE",
    "HOMEPATH", "LOCALAPPDATA", "LOGONSERVER", "NUMBER_OF_PROCESSORS", "OS", "PATH",
    "PATHEXT", "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER", "PROCESSOR_LEVEL",
    "PROCESSOR_REVISION", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "PROGRAMW6432", "PUBLIC", "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TMP",
    "USERDOMAIN", "USERDOMAIN_ROAMINGPROFILE", "USERNAME", "USERPROFILE", "WINDIR",
}
_MAX_FILE_BYTES = 256 * 1024
_MAX_TOTAL_BYTES = 2 * 1024 * 1024
_EXTENSION_ID = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_MODULE = re.compile(r"^tests\.test_[a-zA-Z0-9_]+$")


class SourceExtensionEngine:
    """Only commits candidate source after bounded tests pass in AppContainer."""

    def __init__(self, database_path: str | Path, *, runtime_root: str | Path | None = None) -> None:
        self.database_path = Path(database_path).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.runtime_root = Path(runtime_root).resolve() if runtime_root else _ROOT / ".runtime"
        self._lock = threading.RLock()
        with self._connection() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS source_extensions ("
                "sequence INTEGER PRIMARY KEY AUTOINCREMENT, extension_id TEXT NOT NULL, "
                "title TEXT NOT NULL, version TEXT NOT NULL, status TEXT NOT NULL, "
                "manifest_json TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS source_extensions_id_sequence "
                "ON source_extensions(extension_id, sequence DESC)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _digest(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _source_path(relative_path: str) -> Path:
        if not isinstance(relative_path, str) or "\\" in relative_path:
            raise ValidationError("Source paths must be repository-relative POSIX paths.")
        parsed = PurePosixPath(relative_path)
        if parsed.is_absolute() or not parsed.parts or any(part in {".", ".."} for part in parsed.parts):
            raise ValidationError("Source path escapes the CADYX repository.")
        from .source_native import NATIVE_SOURCE_NAMES
        allowed = (
            len(parsed.parts) == 2 and parsed.parts[0] == "native" and parsed.name in NATIVE_SOURCE_NAMES
            or
            len(parsed.parts) == 2 and parsed.parts[0] == "cadyx" and parsed.suffix == ".py"
            or len(parsed.parts) == 2 and parsed.parts[0] == "tests"
            and parsed.name.startswith("test_") and parsed.suffix == ".py"
            or len(parsed.parts) == 3 and parsed.parts[:2] == (".codex", "docs")
            and parsed.suffix == ".md"
            or len(parsed.parts) == 2 and parsed.parts[0] == "docs" and parsed.suffix == ".md"
        )
        if not allowed:
            raise ValidationError("Source extensions may only change Python modules/tests, existing native bridge sources, or Markdown documentation.")
        destination = _ROOT.joinpath(*parsed.parts)
        if destination.is_symlink() or destination.resolve().parent != destination.parent.resolve():
            raise ValidationError("Source extension targets cannot be symlinks or escape their approved directory.")
        return destination

    def read_file(self, relative_path: str) -> dict[str, Any]:
        path = self._source_path(relative_path)
        if not path.is_file() or path.stat().st_size > _MAX_FILE_BYTES:
            raise ValidationError("The requested source file is missing or exceeds the read limit.")
        content = path.read_bytes()
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("Source-extension inputs must be UTF-8 text.") from exc
        return {"path": relative_path, "sha256": self._digest(content), "content": text}

    def available(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT sequence, extension_id, title, version, status, created_at "
                "FROM source_extensions ORDER BY sequence DESC"
            ).fetchall()
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            latest.setdefault(row["extension_id"], {
                "id": row["extension_id"], "title": row["title"], "version": row["version"],
                "status": row["status"], "created_at": row["created_at"],
            })
        return list(latest.values())

    def develop(self, proposal: Mapping[str, Any], *, progress: Any = None) -> dict[str, Any]:
        required = {"id", "title", "description", "version", "files", "tests"}
        if not isinstance(proposal, Mapping) or set(proposal) != required:
            raise ValidationError("A source extension must contain id, title, description, version, files, and tests.")
        extension_id, version = proposal["id"], proposal["version"]
        if not isinstance(extension_id, str) or not _EXTENSION_ID.fullmatch(extension_id):
            raise ValidationError("Source extension id must be a stable lowercase identifier.")
        if not isinstance(version, str) or not _VERSION.fullmatch(version):
            raise ValidationError("Source extension version must use MAJOR.MINOR.PATCH format.")
        if not isinstance(proposal["title"], str) or not proposal["title"].strip() or len(proposal["title"]) > 160:
            raise ValidationError("Source extension title must be non-empty and at most 160 characters.")
        if not isinstance(proposal["description"], str) or not proposal["description"].strip() or len(proposal["description"]) > 2000:
            raise ValidationError("Source extension description must be non-empty and at most 2000 characters.")
        files, tests = proposal["files"], proposal["tests"]
        if not isinstance(files, list) or not files or len(files) > 12:
            raise ValidationError("A source extension must contain between 1 and 12 file changes.")
        if not isinstance(tests, list) or not tests or len(tests) > 8 or any(
            not isinstance(name, str) or not _MODULE.fullmatch(name) for name in tests
        ) or len(set(tests)) != len(tests):
            raise ValidationError("Source extensions require 1 to 8 unique tests.test_* module names.")
        normalized: list[dict[str, Any]] = []
        total = 0
        seen: set[str] = set()
        for change in files:
            if not isinstance(change, Mapping) or set(change) != {"path", "base_sha256", "content"}:
                raise ValidationError("Each file change must contain exactly path, base_sha256, and content.")
            relative = change["path"]
            destination = self._source_path(relative)
            if relative in seen or not isinstance(change["content"], str):
                raise ValidationError("Source changes must have unique paths and UTF-8 string content.")
            seen.add(relative)
            encoded = change["content"].encode("utf-8")
            total += len(encoded)
            if len(encoded) > _MAX_FILE_BYTES or total > _MAX_TOTAL_BYTES:
                raise ValidationError("Source extension content exceeds the per-file or total size limit.")
            current = destination.read_bytes() if destination.is_file() else None
            expected = change["base_sha256"]
            if (current is None and expected is not None) or (current is not None and expected != self._digest(current)):
                raise ConflictError(f"Source file changed since it was read: {relative}.", code="SOURCE_REVISION_CONFLICT")
            if current is None and not destination.parent.is_dir():
                raise ValidationError("New source files must be placed in an existing approved directory.")
            normalized.append({"path": relative, "before": current.decode("utf-8") if current else None,
                               "before_sha256": self._digest(current) if current else None,
                               "after": change["content"], "after_sha256": self._digest(encoded)})
        for name in tests:
            self._source_path(name.replace(".", "/") + ".py")
            if (not (_ROOT.joinpath(*name.split(".")) .with_suffix(".py")).is_file()
                    and name.replace(".", "/") + ".py" not in seen):
                raise ValidationError(f"Requested test module does not exist: {name}.")
        with self._lock:
            with self._connection() as connection:
                current = connection.execute(
                    "SELECT version,status FROM source_extensions WHERE extension_id=? "
                    "ORDER BY sequence DESC LIMIT 1", (extension_id,),
                ).fetchone()
            if current and current["status"] == "active" and tuple(map(int, version.split("."))) <= tuple(map(int, current["version"].split("."))):
                raise ConflictError("A source extension replacement must use a newer version.", code="SOURCE_VERSION_CONFLICT")
            if progress:
                progress("DEVELOPING TOOL", 0.1)
            test_result = self._test_candidate(normalized, tests, progress=progress)
            if not test_result["passed"]:
                return {"status": "tests_failed", "id": extension_id, "version": version, "tests": test_result,
                        "files_applied": False, "restart_required": False}
            if progress:
                progress("INTEGRATING TOOL", 0.85)
            return self._apply_tested(extension_id, proposal["title"].strip(), version, normalized, tests, test_result)

    def _test_candidate(self, files: list[dict[str, Any]], tests: list[str], *, progress: Any) -> dict[str, Any]:
        if os.name != "nt":
            raise SourceSandboxUnavailable("CADYX source extensions require the Windows AppContainer sandbox; no fallback is permitted.")
        if progress:
            progress("TESTING TOOL", 0.25)
        sandbox_parent = self.runtime_root / "source-sandbox"
        sandbox_parent.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix="candidate-", dir=sandbox_parent)).resolve()
        source_root, work_root = scratch / "source", scratch / "work"
        source_root.mkdir()
        work_root.mkdir()
        try:
            for directory in ("cadyx", "tests", "native", "docs", "scripts"):
                origin = _ROOT / directory
                if origin.exists():
                    shutil.copytree(origin, source_root / directory, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
            for directory in (".codex/docs",):
                origin = _ROOT / directory
                if origin.exists():
                    shutil.copytree(origin, source_root / directory)
            for name in ("pyproject.toml", "README.md", "AGENTS.md"):
                origin = _ROOT / name
                if origin.is_file():
                    shutil.copy2(origin, source_root / name)
            for change in files:
                target = source_root.joinpath(*PurePosixPath(change["path"]).parts)
                target.write_text(change["after"], encoding="utf-8", newline="")
            runner = source_root / "_cadyx_source_test_runner.py"
            runner.write_text(
                "import json, sys, unittest, tempfile\n"
                "from pathlib import Path\n"
                "def _appcontainer_mkdtemp(suffix=None, prefix=None, dir=None):\n"
                "    prefix, suffix, directory, output_type = tempfile._sanitize_params(prefix, suffix, dir)\n"
                "    for _ in range(tempfile.TMP_MAX):\n"
                "        path = tempfile._os.path.join(tempfile._get_default_tempdir() if directory is None else directory, prefix + next(tempfile._get_candidate_names()) + suffix)\n"
                "        try: tempfile._os.mkdir(path)\n"
                "        except FileExistsError: continue\n"
                "        return output_type(path)\n"
                "    raise FileExistsError('No unique AppContainer temporary directory could be created')\n"
                "tempfile.mkdtemp = _appcontainer_mkdtemp\n"
                f"NAMES = {tests!r}\n"
                "OUT = str(Path(tempfile.gettempdir()) / 'result.json')\n"
                "LOG = str(Path(tempfile.gettempdir()) / 'test-output.txt')\n"
                "with open(LOG, 'w', encoding='utf-8') as stream:\n"
                "    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in NAMES)\n"
                "    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)\n"
                "report = {'tests_run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped)}\n"
                "with open(OUT, 'w', encoding='utf-8') as stream: json.dump(report, stream)\n"
                "sys.exit(0 if result.wasSuccessful() and result.testsRun > 0 else 1)\n",
                encoding="utf-8", newline="",
            )
            from .source_native import build_candidate, publish_verified
            native_bridge = None
            if any(change["path"].startswith("native/") for change in files):
                native_bridge = build_candidate(source_root, work_root, self.runtime_root)
            python, read_only = build_scratch_runtime(source_root, self.runtime_root)
            read_only.append(work_root)
            if native_bridge is not None:
                runtime_manifest = source_root / ".runtime" / "manifest.json"
                candidate_manifest = json.loads(runtime_manifest.read_text(encoding="utf-8"))
                candidate_manifest["bridge"] = str(native_bridge.resolve())
                runtime_manifest.write_text(json.dumps(candidate_manifest), encoding="utf-8")
                read_only.append(native_bridge.parent)
                native_digest = self._digest(native_bridge.read_bytes())
            allowed_env = {key: value for key, value in os.environ.items() if key.upper() in _ALLOWED_ENV}
            allowed_env.update({"PATH": str(python.parent)})
            launch = run_appcontainer_process(
                python, ["-I", "-B", str(runner)], current_directory=source_root,
                read_only=read_only, environment=allowed_env,
                result_files=["result.json", "test-output.txt"],
                timeout_seconds=180, memory_limit_bytes=2_000_000_000,
            )
            if launch["timed_out"]:
                report = {"tests_run": 0, "failures": 0, "errors": 1, "skipped": 0,
                          "output": "AppContainer test execution timed out."}
            elif "result.json" in launch["files"]:
                report = json.loads(launch["files"]["result.json"])
                if "test-output.txt" in launch["files"]:
                    report["output"] = launch["files"]["test-output.txt"][-12000:]
                report["exit_code"] = launch["exit_code"]
            else:
                report = {"tests_run": 0, "failures": 0, "errors": 1, "skipped": 0,
                          "exit_code": launch["exit_code"],
                          "output": launch.get("diagnostics") or "Sandbox test runner exited without a result file."}
            report["passed"] = launch["exit_code"] == 0 and report.get("tests_run", 0) > 0 and not report.get("failures") and not report.get("errors")
            if report["passed"] and native_bridge is not None:
                if self._digest(native_bridge.read_bytes()) != native_digest:
                    raise ValidationError("The native candidate changed during sandbox tests.")
                publish_verified(source_root, self.runtime_root, native_bridge)
            if progress:
                progress("TESTING TOOL", 0.7 if report["passed"] else 0.4)
            return report
        finally:
            shutil.rmtree(scratch)

    def _apply_tested(self, extension_id: str, title: str, version: str, files: list[dict[str, Any]],
                      tests: list[str], test_result: dict[str, Any]) -> dict[str, Any]:
        manifest = {"files": files, "tests": tests, "test_result": test_result}
        payload = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
        created = datetime.now(timezone.utc).isoformat()
        applied: list[dict[str, Any]] = []
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO source_extensions(extension_id,title,version,status,manifest_json,created_at) "
                "VALUES(?,?,?,?,?,?)", (extension_id, title, version, "applying", payload, created),
            )
            sequence = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
            connection.commit()
        try:
            for change in files:
                destination = self._source_path(change["path"])
                current = destination.read_bytes() if destination.is_file() else None
                if (self._digest(current) if current is not None else None) != change["before_sha256"]:
                    raise ConflictError(f"Source file changed while tests ran: {change['path']}.", code="SOURCE_REVISION_CONFLICT")
            for change in files:
                destination = self._source_path(change["path"])
                previous = destination.read_bytes() if destination.is_file() else None
                temporary = destination.with_name(destination.name + ".cadyx-tmp")
                temporary.write_text(change["after"], encoding="utf-8", newline="")
                os.replace(temporary, destination)
                applied.append({"path": change["path"], "previous": previous})
            with self._connection() as connection:
                connection.execute("UPDATE source_extensions SET status='active' WHERE sequence=?", (sequence,))
        except BaseException as exc:
            for item in reversed(applied):
                destination = self._source_path(item["path"])
                if item["previous"] is None:
                    destination.unlink(missing_ok=True)
                else:
                    temporary = destination.with_name(destination.name + ".cadyx-rollback-tmp")
                    temporary.write_bytes(item["previous"])
                    os.replace(temporary, destination)
            with self._connection() as connection:
                connection.execute("UPDATE source_extensions SET status='failed' WHERE sequence=?", (sequence,))
            raise exc
        return {"status": "ready", "id": extension_id, "title": title, "version": version,
                "tests": test_result, "files_applied": True, "restart_required": True}

    def rollback(self, extension_id: str) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT sequence,manifest_json FROM source_extensions WHERE extension_id=? AND status='active' "
                "ORDER BY sequence DESC LIMIT 1", (extension_id,),
            ).fetchone()
            if row is None:
                raise ValidationError("No active source extension checkpoint exists for this id.")
            manifest = json.loads(row["manifest_json"])
            changes = manifest["files"]
            for change in changes:
                path = self._source_path(change["path"])
                if not path.is_file() or self._digest(path.read_bytes()) != change["after_sha256"]:
                    raise ConflictError(f"Cannot roll back {change['path']}; it has changed since the extension was applied.", code="SOURCE_REVISION_CONFLICT")
            restored: list[dict[str, Any]] = []
            try:
                for change in changes:
                    path = self._source_path(change["path"])
                    before = path.read_bytes()
                    if change["before"] is None:
                        path.unlink()
                    else:
                        temporary = path.with_name(path.name + ".cadyx-rollback-tmp")
                        temporary.write_text(change["before"], encoding="utf-8", newline="")
                        os.replace(temporary, path)
                    restored.append({"path": path, "before": before})
                connection.execute("UPDATE source_extensions SET status='rolled_back' WHERE sequence=?", (row["sequence"],))
            except BaseException:
                for item in reversed(restored):
                    temporary = item["path"].with_name(item["path"].name + ".cadyx-rollback-tmp")
                    temporary.write_bytes(item["before"])
                    os.replace(temporary, item["path"])
                raise
        return {"status": "rolled_back", "id": extension_id, "version": "restored", "files_applied": False}
