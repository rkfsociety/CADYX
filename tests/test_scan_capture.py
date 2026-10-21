from __future__ import annotations

import tempfile
import unittest
import json
import os
import struct
from pathlib import Path
from unittest.mock import patch

from cadyx.errors import ConflictError, ValidationError
from cadyx.scan_capture import Scan360CaptureController


class Scan360CaptureTests(unittest.TestCase):
    @staticmethod
    def _marker_map(point_count: int = 4) -> bytes:
        records = [struct.pack("<2i2f6d", 0, -1, 1.0, 0.0, float(index), 0.0, 0.0, 0.0, 0.0, 1.0)
                   for index in range(point_count)]
        return struct.pack("<4I", 1, 256, 0, point_count) + b"".join(records)

    def test_default_scanner_calibration_uses_its_profile_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = root / "tools" / "revopoint" / "calibration" / "scanners" / "revopoint-mini-y16"
            profile.mkdir(parents=True)
            (profile / "Q.bin").write_bytes(bytes(64))
            (profile / "depth_scale.json").write_text(json.dumps({
                "depth_scale_mm_per_raw_unit": 0.005,
            }), encoding="utf-8")
            controller = Scan360CaptureController(root, root / "projects")
            with patch.dict(os.environ, {"LOCALAPPDATA": str(root / "appdata")}):
                q_file, depth_scale = controller._resolve_scanner_calibration(
                    "scanner-1", 1, "revopoint-mini-y16",
                )
        self.assertEqual(q_file, profile / "Q.bin")
        self.assertEqual(depth_scale, 0.005)

    def test_turntable_settings_and_marker_map_are_device_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "projects"
            workspace.mkdir()
            app_data = root / "appdata"
            controller = Scan360CaptureController(root, workspace)
            devices = {"scanners": [], "turntables": [
                {"device_id": "table-1", "profile_id": "revopoint-dual-axis-table"},
                {"device_id": "table-2", "profile_id": "revopoint-dual-axis-table"},
            ]}
            profile_map = (root / "tools" / "revopoint" / "calibration" / "tables"
                           / "revopoint-dual-axis-table" / "framesmall.mkf")
            profile_map.parent.mkdir(parents=True)
            profile_map.write_bytes(self._marker_map())
            catalog = root / "tools" / "revopoint" / "devices.json"
            catalog.write_text(json.dumps({"turntables": [{
                "id": "revopoint-dual-axis-table", "control_protocol": "revopoint-dual-axis-v1",
            }]}), encoding="utf-8")
            with patch.dict(os.environ, {"LOCALAPPDATA": str(app_data)}), \
                    patch.object(controller, "list_connected_devices", return_value=devices):
                self.assertEqual(controller.turntable_settings("table-1")["rotation_period_seconds"], 18.0)
                saved = controller.save_turntable_settings("table-1", 30.0)
                second = controller.turntable_settings("table-2")
                data, count = controller.turntable_marker_map("table-1")
                table_directory = controller._turntable_settings_directory("table-1")
                default_map_copied = (table_directory / "framesmall.mkf").read_bytes() == self._marker_map()
                with patch.object(controller, "list_connected_devices", side_effect=AssertionError("unexpected device rescan")):
                    saved_settings = controller.turntable_settings("table-1", "revopoint-dual-axis-table")
                    data, count = controller.turntable_marker_map("table-1", "revopoint-dual-axis-table")
                override = table_directory / "framesmall.mkf"
                override.write_bytes(self._marker_map(5))
                override_data, override_count = controller.turntable_marker_map("table-1")
                settings_exists = (table_directory / "settings.json").is_file()
        self.assertTrue(saved["configured"])
        self.assertEqual(saved["rotation_period_seconds"], 30.0)
        self.assertFalse(second["configured"])
        self.assertTrue(saved_settings["configured"])
        self.assertTrue(default_map_copied)
        self.assertEqual((data, count), (self._marker_map(), 4))
        self.assertEqual((override_data, override_count), (self._marker_map(5), 5))
        self.assertTrue(settings_exists)

    def test_turntable_map_rejects_invalid_record_data(self) -> None:
        with self.assertRaisesRegex(ValidationError, "invalid header or record count"):
            Scan360CaptureController._validate_marker_map(bytes(16))

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
