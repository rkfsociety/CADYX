from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cadyx.scan_capture import Scan360CaptureController


class Scan360CaptureTests(unittest.TestCase):
    def test_latest_complete_capture_survives_host_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            run_id = "revopoint_20261020T103638Z"
            (project / f"{run_id}_poses.csv").write_text("poses", encoding="utf-8")
            (project / ".cadyx-scan360-20261020T103638Z.log").write_text(
                "sweep completed: 12 steps", encoding="utf-8",
            )
            for index in range(12):
                (project / f"{run_id}_pose_pose_{index:03d}.raw").touch()
            status = Scan360CaptureController._latest_completed_snapshot(project)
        self.assertEqual(status["state"], "completed")
        self.assertEqual((status["completed_frames"], status["frame_count"]), (12, 12))

    def test_partial_capture_is_not_reported_as_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            run_id = "revopoint_20261020T103638Z"
            (project / f"{run_id}_poses.csv").write_text("poses", encoding="utf-8")
            for index in range(11):
                (project / f"{run_id}_pose_{index:03d}.raw").touch()
            status = Scan360CaptureController._latest_completed_snapshot(project)
        self.assertEqual(status["state"], "idle")


if __name__ == "__main__":
    unittest.main()
