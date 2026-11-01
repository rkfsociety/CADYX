from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cadyx.errors import ConflictError
from cadyx.lease import EditLeaseManager


class LeaseSnapshotJournalTests(unittest.TestCase):
    def test_listing_recovers_hot_rollback_journal_after_writer_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            project_dir = Path(directory) / "project"
            project_dir.mkdir()
            (project_dir / "project.json").write_text("{}", encoding="utf-8")
            database = project_dir / ".cadyx-leases.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("""CREATE TABLE lease (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                owner_type TEXT NOT NULL, owner_id TEXT NOT NULL,
                acquired_at REAL NOT NULL, heartbeat_at REAL NOT NULL,
                lease_id TEXT NOT NULL, timeout_seconds REAL NOT NULL
            )""")
            connection.execute("INSERT INTO lease VALUES (1,'agent','committed',100,190,'committed',60)")
            connection.execute("CREATE TABLE padding (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
            connection.executemany("INSERT INTO padding VALUES (?,?)",
                                   ((index, "a" * 1000) for index in range(1, 401)))
            connection.commit()
            connection.close()

            writer = sqlite3.connect(database, isolation_level=None)
            writer.execute("PRAGMA cache_size=1")
            writer.execute("PRAGMA cache_spill=ON")
            writer.execute("BEGIN EXCLUSIVE")
            writer.execute("UPDATE lease SET owner_id='uncommitted', lease_id='uncommitted'")
            writer.execute("UPDATE padding SET payload=substr(payload,1,999)||'b'")
            journal = Path(str(database) + "-journal")
            journal_bytes = journal.read_bytes()
            self.assertGreaterEqual(len(journal_bytes), 512)
            self.assertNotEqual(journal_bytes[:8], b"\x00" * 8)
            writer.commit()
            writer.close()
            journal.write_bytes(journal_bytes)

            read_only = sqlite3.connect(
                f"{database.resolve().as_uri()}?mode=ro", uri=True, timeout=0, isolation_level=None,
            )
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    read_only.execute("SELECT owner_id FROM lease").fetchone()
            finally:
                read_only.close()

            manager = EditLeaseManager(lambda _project_id: project_dir, clock=lambda: 200)
            snapshot = manager.snapshot_for_listing("test-project")

            self.assertFalse(journal.exists(), "SQLite should clear the recovered journal")
            self.assertEqual(snapshot["lease"]["lease_id"], "committed")
            self.assertEqual(snapshot["lease"]["owner_id"], "committed")
            self.assertFalse(snapshot["recovery_required"])

    def test_heartbeat_remains_monotonic_after_wall_clock_moves_backwards(self):
        with tempfile.TemporaryDirectory() as directory:
            project_dir = Path(directory)
            (project_dir / "project.json").write_text("{}", encoding="utf-8")
            now = [100.0]
            manager = EditLeaseManager(lambda _project_id: project_dir, clock=lambda: now[0])
            lease = manager.acquire("project", "agent", "worker")

            now[0] = 110.0
            self.assertEqual(manager.heartbeat("project", lease.lease_id).heartbeat_at, 110.0)
            now[0] = 105.0
            self.assertEqual(manager.heartbeat("project", lease.lease_id).heartbeat_at, 110.0)
            self.assertEqual(manager.get("project").heartbeat_at, 110.0)
            now[0] = 111.0
            self.assertEqual(manager.heartbeat("project", lease.lease_id).heartbeat_at, 111.0)


    def test_extended_sqlite_contention_returns_retryable_project_busy(self):
        with tempfile.TemporaryDirectory() as directory:
            project_dir = Path(directory)
            (project_dir / "project.json").write_text("{}", encoding="utf-8")
            manager = EditLeaseManager(lambda _project_id: project_dir)
            for context in ("guard", "agent_control"):
                for code in (sqlite3.SQLITE_BUSY_SNAPSHOT, sqlite3.SQLITE_LOCKED_SHAREDCACHE):
                    with self.subTest(context=context, code=code):
                        error = sqlite3.OperationalError("database is locked")
                        error.sqlite_errorcode = code
                        connection = MagicMock()
                        connection.in_transaction = False
                        connection.execute.side_effect = error
                        with patch("cadyx.lease.sqlite3.connect", return_value=connection):
                            with self.assertRaises(ConflictError) as caught:
                                with getattr(manager, context)("project"):
                                    pass
                        self.assertEqual(caught.exception.code, "PROJECT_BUSY")
                        connection.close.assert_called_once()

    def test_connect_contention_is_retryable_for_both_lease_databases(self):
        with tempfile.TemporaryDirectory() as directory:
            project_dir = Path(directory)
            (project_dir / "project.json").write_text("{}", encoding="utf-8")
            manager = EditLeaseManager(lambda _project_id: project_dir)
            for context in ("guard", "agent_control"):
                for code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED_SHAREDCACHE):
                    with self.subTest(context=context, code=code):
                        error = sqlite3.OperationalError("database is locked")
                        error.sqlite_errorcode = code
                        with patch("cadyx.lease.sqlite3.connect", side_effect=error):
                            with self.assertRaises(ConflictError) as caught:
                                with getattr(manager, context)("project"):
                                    pass
                        self.assertEqual(caught.exception.code, "PROJECT_BUSY")

    def test_connect_non_contention_failure_is_not_masked(self):
        with tempfile.TemporaryDirectory() as directory:
            project_dir = Path(directory)
            (project_dir / "project.json").write_text("{}", encoding="utf-8")
            manager = EditLeaseManager(lambda _project_id: project_dir)
            for context in ("guard", "agent_control"):
                with self.subTest(context=context):
                    error = sqlite3.OperationalError("unable to open database file")
                    error.sqlite_errorcode = sqlite3.SQLITE_CANTOPEN
                    with patch("cadyx.lease.sqlite3.connect", side_effect=error):
                        with self.assertRaises(sqlite3.OperationalError) as caught:
                            with getattr(manager, context)("project"):
                                pass
                    self.assertIs(caught.exception, error)

    def test_failed_rollback_preserves_original_sqlite_contention(self):
        with tempfile.TemporaryDirectory() as directory:
            project_dir = Path(directory)
            (project_dir / "project.json").write_text("{}", encoding="utf-8")
            manager = EditLeaseManager(lambda _project_id: project_dir)
            error = sqlite3.OperationalError("database is locked")
            error.sqlite_errorcode = sqlite3.SQLITE_BUSY
            rollback_error = sqlite3.OperationalError("disk I/O error")
            connection = MagicMock()
            connection.in_transaction = True

            def execute(statement, *args):
                if statement == "BEGIN IMMEDIATE":
                    raise error
                if statement == "ROLLBACK":
                    raise rollback_error
                self.fail(f"Unexpected SQL: {statement}")

            connection.execute.side_effect = execute
            with patch("cadyx.lease.sqlite3.connect", return_value=connection):
                with self.assertRaises(ConflictError) as caught:
                    with manager.guard("project"):
                        pass
            self.assertEqual(caught.exception.code, "PROJECT_BUSY")
            self.assertIs(caught.exception.__cause__, error)
            connection.execute.assert_any_call("ROLLBACK")
            connection.close.assert_called_once()

    def test_non_contention_sqlite_errors_are_not_masked(self):
        with tempfile.TemporaryDirectory() as directory:
            project_dir = Path(directory)
            (project_dir / "project.json").write_text("{}", encoding="utf-8")
            manager = EditLeaseManager(lambda _project_id: project_dir)
            for context in ("guard", "agent_control"):
                with self.subTest(context=context):
                    error = sqlite3.OperationalError("database is corrupt")
                    error.sqlite_errorcode = sqlite3.SQLITE_CORRUPT
                    connection = MagicMock()
                    connection.in_transaction = False
                    connection.execute.side_effect = error
                    with patch("cadyx.lease.sqlite3.connect", return_value=connection):
                        with self.assertRaises(sqlite3.OperationalError):
                            with getattr(manager, context)("project"):
                                pass
                    connection.close.assert_called_once()
