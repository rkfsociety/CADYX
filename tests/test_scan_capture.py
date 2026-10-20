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
            (project / f"{run_id}_pose_pose_011.ply").touch()
            status = Scan360CaptureController._latest_completed_snapshot(project)
        self.assertEqual(status["state"], "completed")
        self.assertEqual((status["completed_frames"], status["frame_count"]), (12, 12))
        self.assertEqual(status["preview_file"], f"{run_id}_pose_pose_011.ply")

    def test_fused_first_frame_cloud_becomes_project_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            run_id = "revopoint_20261020T103638Z"
            (project / f"{run_id}_poses.csv").write_text("poses", encoding="utf-8")
            (project / f".cadyx-scan360-20261020T103638Z.log").write_text(
                "sweep completed: 12 steps", encoding="utf-8",
            )
            for index in range(12):
                (project / f"{run_id}_pose_{index:03d}.raw").touch()
            (project / f"{run_id}_first_frame_geometric_candidate.ply").touch()
            status = Scan360CaptureController._latest_completed_snapshot(project)
        self.assertEqual(status["preview_kind"], "provisional_fusion")
        self.assertEqual(status["preview_frame_count"], 12)

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
