from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cadyx.errors import ConflictError
from cadyx.scan_capture import Scan360CaptureController


class Scan360CaptureTests(unittest.TestCase):
    def test_live_preview_reports_encoder_angle_and_stops_read_only_monitor(self) -> None:
        class FakeProcess:
            returncode = None
            terminated = False

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                self.returncode = 0
                return self.returncode

            def terminate(self):
                self.terminated = True
                self.returncode = -1

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build = root / "tools" / "revopoint" / "build"
            build.mkdir(parents=True)
            (build / "CaptureY16.exe").touch()
            (build / "TurntableController.exe").touch()
            workspace = root / "projects"
            workspace.mkdir()
            controller = Scan360CaptureController(root, workspace)
            camera_process, table_process = FakeProcess(), FakeProcess()
            with patch("cadyx.scan_capture.subprocess.Popen", side_effect=[camera_process, table_process]) as popen:
                controller.start_live_preview()
                self.assertEqual(controller.turntable_angle_status(), {"state": "starting"})
                monitor = controller._turntable_monitor
                monitor.csv_path.write_text(
                    "utc,angle_degrees,tilt_degrees,rotation_response,tilt_response\n"
                    "2026-10-20T12:00:00Z,41.25,0,angle,tilt\n",
                    encoding="utf-8",
                )
                self.assertEqual(controller.turntable_angle_status(), {
                    "state": "running", "angle_degrees": 41.25, "observed_at": "2026-10-20T12:00:00Z",
                })
                table_command = popen.call_args_list[1].args[0]
                self.assertEqual(table_command[1:3], ["monitor", "300"])
                self.assertNotIn("rotate", table_command)
                self.assertNotIn("stop", table_command)
                controller.stop_live_preview()
                self.assertTrue(table_process.terminated)
                self.assertFalse(monitor.csv_path.exists())
                self.assertFalse(monitor.log_path.exists())

    def test_live_preview_uses_only_the_ephemeral_frame_buffer_and_stops_cleanly(self) -> None:
        class FakeProcess:
            returncode = None

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                self.returncode = 0
                return self.returncode

            def terminate(self):
                self.returncode = -1

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "tools" / "revopoint" / "build" / "CaptureY16.exe"
            executable.parent.mkdir(parents=True)
            executable.touch()
            workspace = root / "projects"
            workspace.mkdir()
            controller = Scan360CaptureController(root, workspace)
            process = FakeProcess()
            with patch("cadyx.scan_capture.subprocess.Popen", return_value=process) as popen:
                self.assertEqual(controller.start_live_preview()["state"], "starting")
                command = popen.call_args.args[0]
                self.assertIn("--live-only", command)
                self.assertIn("--live-raw", command)
                self.assertNotIn("--out-prefix", command)
                self.assertNotIn("--frames", command)
                self.assertNotIn("--q-file", command)
                raw_path = workspace / ".revopoint-live.raw"
                stop_path = workspace / ".revopoint-live-preview.stop"
                controller._live_preview.ready_path.touch()
                raw_path.write_bytes(b"current frame")
                self.assertEqual(controller.live_preview_status()["state"], "running")
                self.assertEqual(controller.stop_live_preview(), {"state": "idle"})
                self.assertFalse(raw_path.exists(), "stopping the preview removes its one-frame cache")
                self.assertFalse(stop_path.exists())

    def test_live_preview_and_360_capture_share_one_scanner_lease(self) -> None:
        class FakeProcess:
            returncode = None

            def poll(self):
                return self.returncode

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "tools" / "revopoint" / "build" / "CaptureY16.exe"
            executable.parent.mkdir(parents=True)
            executable.touch()
            project = root / "projects" / "part"
            project.mkdir(parents=True)
            script = root / "tools" / "revopoint" / "scan_360.ps1"
            script.touch()
            controller = Scan360CaptureController(root, project.parent)
            controller._live_preview = type("Preview", (), {"process": FakeProcess()})()
            with patch("cadyx.scan_capture.shutil.which", return_value="powershell.exe"):
                with self.assertRaises(ConflictError):
                    controller.start("part", project)

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
            (project / f"{run_id}_pose_000.ply").touch()
            (project / f"{run_id}_first_frame_geometric_candidate.ply").touch()
            status = Scan360CaptureController._latest_completed_snapshot(project)
        self.assertEqual(status["preview_kind"], "provisional_fusion")
        self.assertEqual(status["preview_frame_count"], 12)
        self.assertEqual(status["origin_file"], f"{run_id}_pose_000.ply")

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
