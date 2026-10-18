from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

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
