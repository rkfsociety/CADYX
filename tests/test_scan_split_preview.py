import tempfile
import unittest
from pathlib import Path
from cadyx.scan_capture import Scan360CaptureController as Controller
class PreviewProcessedFramesTests(unittest.TestCase):
    def test_latest_processed_frame_is_visible_while_capture_runs_ahead(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = "revopoint_20261030T103826Z"
            series = root / run
            series.mkdir()
            (series / f"{run}_pose_001.ply").write_text("ply")
            preview = Controller._preview_payload(series, run, 4, 24)
            self.assertEqual(preview["preview_subfolder"], run)
            self.assertEqual(preview["preview_file"], f"{run}_pose_001.ply")
