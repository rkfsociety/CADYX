import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from cadyx.errors import ConflictError
from cadyx.workspace import ProjectWorkspace
from cadyx.workspace_state import prepare_workspace_state


class WorkspaceStateTests(unittest.TestCase):
    def test_migrates_databases_and_scanner_artifacts_preserving_projects(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "projects"
            root.mkdir()
            project = root / "housing"
            project.mkdir()
            (project / "model.json").write_text("original")
            database = root / ".cadyx-project-groups.sqlite3"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("CREATE TABLE evidence (value TEXT)")
                connection.execute("INSERT INTO evidence VALUES ('preserved')")
            frames = root / ".revopoint-live.raw.frames"
            frames.mkdir()
            (frames / "frame.raw").write_bytes(b"capture")
            workspace = ProjectWorkspace(root)
            self.assertEqual(list(root.iterdir()), [project])
            self.assertEqual((project / "model.json").read_text(), "original")
            self.assertEqual(workspace._groups_db_path.parent, workspace.state_root)
            with closing(sqlite3.connect(workspace.state_root / database.name)) as connection:
                self.assertEqual(connection.execute("SELECT value FROM evidence").fetchone(), ("preserved",))
            self.assertEqual((workspace.state_root / frames.name / "frame.raw").read_bytes(), b"capture")
            self.assertEqual(prepare_workspace_state(root), workspace.state_root)

    def test_collision_does_not_overwrite_or_partially_migrate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "projects"
            root.mkdir()
            state = prepare_workspace_state(root)
            (root / ".cadyx-first").write_text("first")
            (root / ".cadyx-conflict").write_text("old")
            (state / ".cadyx-conflict").write_text("new")
            with self.assertRaises(ConflictError):
                prepare_workspace_state(root)
            self.assertEqual((root / ".cadyx-first").read_text(), "first")
            self.assertEqual((root / ".cadyx-conflict").read_text(), "old")
            self.assertEqual((state / ".cadyx-conflict").read_text(), "new")
