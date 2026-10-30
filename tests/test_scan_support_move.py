import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch
from cadyx.workspace_files import WorkspaceArtifactStore
from cadyx.errors import ConflictError, NotFoundError

class ScanSupportMoveTests(unittest.TestCase):
    @unittest.skipIf("packages" in os.environ.get("TEMP", "").lower(),
                     "AppContainer cannot open final-path handles for its temporary files")
    def test_move_with_real_path_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "series").mkdir()
            (root / "series" / "frame.raw").write_bytes(b"raw data")
            store = WorkspaceArtifactStore(lambda folder, name: False)
            store.move_project_files_to_subfolder(root, ["series/frame.raw"], "support")
            self.assertEqual((root / "support" / "frame.raw").read_bytes(), b"raw data")
            self.assertFalse((root / "series" / "frame.raw").exists())

    def test_move_from_series_preserves_bytes_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).absolute()
            series = root / "series"
            series.mkdir()
            (series / "frame.raw").write_bytes(b"original")
            store = WorkspaceArtifactStore(lambda folder, name: False)
            # AppContainer denies Windows final-path handles for its own temp files.
            with patch.object(Path, "resolve", lambda self, strict=False: self.absolute()):
                result = store.move_project_files_to_subfolder(root, ["series/frame.raw"], "support")
                self.assertEqual(result["moved_count"], 1)
                self.assertEqual((root / "support" / "frame.raw").read_bytes(), b"original")
                self.assertFalse((series / "frame.raw").exists())
                (series / "frame.raw").write_bytes(b"other")
                with self.assertRaises(ConflictError):
                    store.move_project_files_to_subfolder(root, ["series/frame.raw"], "support")
                self.assertEqual((series / "frame.raw").read_bytes(), b"other")
                self.assertEqual((root / "support" / "frame.raw").read_bytes(), b"original")
                with self.assertRaises(NotFoundError):
                    store.move_project_files_to_subfolder(root, ["../frame.raw"], "support")
