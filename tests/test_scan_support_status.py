import json
import tempfile
import unittest
from pathlib import Path
from cadyx.scan_capture import Scan360CaptureController as Controller

class SplitScanStatusTests(unittest.TestCase):
    def test_completed_capture_from_separate_support(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = "revopoint_20261030T103826Z"
            series, support = root / run, root / f"scan_support_{run}"
            series.mkdir()
            support.mkdir()
            (root / "project.json").write_text(json.dumps({"id": "p", "name": "test"}))
            (series / f"{run}_pose_000.ply").write_text("ply")
            (support / f"{run}_pose_000.raw").write_bytes(b"raw")
            (support / f"{run}_poses.csv").write_text("step_index,event\n")
            (support / f"{run}_scan_360.json").write_text(json.dumps({"frame_count": 1, "step_degrees": 30, "table_alignment_status": "not_performed"}))
            (support / f"{run}_preprocessing.json").write_text(json.dumps({"state": "completed", "cleaned_frame_count": 1, "frames": [{"table_marker_detection": "completed"}], "failed_frames": []}))
            (support / ".cadyx-scan360-20261030T103826Z.log").write_text("sweep completed: 1 steps")
            status = Controller._latest_completed_snapshot(root)
            self.assertEqual(status["state"], "completed")
            self.assertEqual(status["completed_frames"], 1)
            self.assertEqual(status["preprocessing_status"], "completed")
            self.assertEqual(status["table_alignment_status"], "not_performed")
            self.assertEqual(status["preview_subfolder"], run)
            self.assertEqual(status["preview_file"], f"{run}_pose_000.ply")
