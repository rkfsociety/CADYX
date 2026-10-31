from __future__ import annotations

import tempfile
import unittest
import json
import os
import subprocess
import struct
import sys
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from cadyx.errors import ConflictError, ValidationError
from cadyx.scan_capture import Scan360CaptureController, _capture_log_progress


class Scan360CaptureTests(unittest.TestCase):
    def test_capture_log_progress_ignores_oversized_and_unrelated_counts(self) -> None:
        huge = "9" * 5000
        log = (f"captured pose {huge}/12\n"
               f"captured pose 3/{huge}\n"
               "captured pose 99/12\n"
               "captured pose 5/24\n"
               "captured pose 12/12\n"
               "captured pose 1/12\n")
        self.assertEqual(_capture_log_progress(log, 12), 2)

    def test_capture_log_progress_accepts_last_valid_pose(self) -> None:
        self.assertEqual(_capture_log_progress("captured pose 0/12\ncaptured pose 11/12", 12), 12)
        self.assertEqual(_capture_log_progress("captured pose 359/360", 360), 360)

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
                self.assertTrue(q_file.samefile(profile / "Q.bin"))
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

    def test_failed_post_capture_cleanup_is_not_reported_complete_without_alignment_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "projects"
            project = workspace / "project-id"
            run_id = "revopoint_20261029T120000Z"
            run_directory = project / run_id
            run_directory.mkdir(parents=True)
            (project / "project.json").write_text(json.dumps({"id": "project-id", "name": "Part"}),
                                                   encoding="utf-8")
            log_path = run_directory / ".cadyx-scan360-20261029T120000.log"
            log_path.write_text("sweep completed: 12 steps\n", encoding="utf-8")
            frames = []
            for index in range(12):
                (run_directory / f"{run_id}_pose_{index:03d}.raw").touch()
                (run_directory / f"{run_id}_pose_{index:03d}.ply").write_text("ply\n", encoding="ascii")
                frames.append({"frame_index": index, "state": "completed",
                               "table_marker_detection": "completed"})
            (run_directory / f"{run_id}_preprocessing.json").write_text(json.dumps({
                "state": "completed", "frames": frames, "failed_frames": [],
                "cleaned_frame_count": 12, "failed_frame_count": 0,
            }), encoding="utf-8")

            controller = Scan360CaptureController(root, workspace)
            controller._job = SimpleNamespace(
                process=SimpleNamespace(poll=lambda: 1), log_path=log_path,
                project_directory=project, run_id=run_id, frame_count=12,
                step_degrees=30, started_at="2026-10-29T12:00:00+00:00",
            )
            status = controller._snapshot_locked()

        self.assertEqual(status["completed_frames"], 12)
        self.assertEqual(status["preprocessing_status"], "completed")
        self.assertEqual(status["table_alignment_status"], "pending")
        self.assertEqual(status["state"], "failed")

    def test_nonzero_process_exit_is_accepted_only_after_final_artifacts_are_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "projects"
            project = workspace / "project-id"
            run_id = "revopoint_20261029T120000Z"
            run_directory = project / run_id
            run_directory.mkdir(parents=True)
            (project / "project.json").write_text(json.dumps({"id": "project-id", "name": "Part"}),
                                                   encoding="utf-8")
            log_path = run_directory / ".cadyx-scan360-20261029T120000.log"
            log_path.write_text("sweep completed: 12 steps\n", encoding="utf-8")
            frames = []
            for index in range(12):
                (run_directory / f"{run_id}_pose_{index:03d}.raw").touch()
                (run_directory / f"{run_id}_pose_{index:03d}.ply").write_text("ply\n", encoding="ascii")
                frames.append({"frame_index": index, "state": "completed",
                               "table_marker_detection": "completed"})
            (run_directory / f"{run_id}_preprocessing.json").write_text(json.dumps({
                "state": "completed", "frames": frames, "failed_frames": [],
                "cleaned_frame_count": 12, "failed_frame_count": 0,
            }), encoding="utf-8")
            (run_directory / f"{run_id}_table_alignment.json").write_text(
                json.dumps({"status": "aligned_provisional"}), encoding="utf-8")
            (run_directory / f"{run_id}_scan_360.json").write_text(json.dumps({
                "frame_count": 12, "preprocessing_status": "completed",
                "table_alignment_status": "aligned_provisional",
            }), encoding="utf-8")

            controller = Scan360CaptureController(root, workspace)
            controller._job = SimpleNamespace(
                process=SimpleNamespace(poll=lambda: 1), log_path=log_path,
                project_directory=project, run_id=run_id, frame_count=12,
                step_degrees=30, started_at="2026-10-29T12:00:00+00:00",
            )
            status = controller._snapshot_locked()

        self.assertEqual(status["state"], "completed")
        self.assertEqual(status["table_alignment_status"], "aligned_provisional")
        self.assertIn("вернул код 1", status["message"])

    def test_turntable_monitor_returns_read_only_wrapped_angle_samples(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "projects"
            workspace.mkdir()
            build = root / "tools" / "revopoint" / "build"
            build.mkdir(parents=True)
            (build / "TurntableController.exe").write_bytes(b"test executable placeholder")
            profile_id = "revopoint-dual-axis-table"
            (root / "tools" / "revopoint" / "devices.json").write_text(json.dumps({"turntables": [{
                "id": profile_id, "control_protocol": "revopoint-dual-axis-v1",
                "gatt_name": "REVO_DUAL_AXIS_TABLE", "advertisement_uuid": "ffe1",
                "service_uuid": "ffe0", "characteristic_uuid": "ffe1",
            }]}), encoding="utf-8")
            controller = Scan360CaptureController(root, workspace)

            def write_samples(arguments, **options):
                Path(arguments[3]).write_text(
                    "utc,angle_degrees,tilt_degrees,rotation_response,tilt_response\n"
                    "t0,-350,0,angle,tilt\n"
                    "t1,-10,0,angle,tilt\n"
                    "t2,-20,0,angle,tilt\n", encoding="utf-8")
                return subprocess.CompletedProcess(arguments, 0, b"monitor complete", b"")

            with patch("cadyx.scan_capture.subprocess.run", side_effect=write_samples) as monitor:
                result = controller.turntable_control("monitor", "0123456789AB", profile_id, 10)
            self.assertEqual(monitor.call_args.args[0][1:3], ["monitor", "10"])
            self.assertEqual(monitor.call_args.kwargs["timeout"], 40)
        self.assertEqual(result["sample_count"], 3)
        self.assertEqual(result["net_rotation_degrees"], -30)
        self.assertEqual(result["samples"][0]["angle_degrees"], -350)
        self.assertEqual(result["samples"][-1]["angle_degrees"], -20)

    @unittest.skipUnless(os.name == "nt", "Turntable controller executable is Windows-only")
    def test_turntable_control_uses_validated_native_commands_and_scan_only_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "projects"
            workspace.mkdir()
            tool_root = root / "tools" / "revopoint"
            build = tool_root / "build"
            build.mkdir(parents=True)
            executable = build / "TurntableController.exe"
            executable.write_bytes(b"test executable placeholder")
            profile_id = "revopoint-dual-axis-table"
            (tool_root / "devices.json").write_text(json.dumps({"turntables": [{
                "id": profile_id, "control_protocol": "revopoint-dual-axis-v1",
                "gatt_name": "REVO_DUAL_AXIS_TABLE", "advertisement_uuid": "ffe1",
                "service_uuid": "ffe0", "characteristic_uuid": "ffe1",
            }]}), encoding="utf-8")
            controller = Scan360CaptureController(root, workspace)
            completed = subprocess.CompletedProcess([], 0,
                b"angle before: 0.0\nangle after: -345.0\nmeasured change: 15.0\n", b"")
            with patch("cadyx.scan_capture.subprocess.run", return_value=completed) as run:
                result = controller.turntable_control("rotate", "0123456789ab", profile_id, 15)
            self.assertEqual(result["state"], "completed")
            self.assertEqual(result["turntable_device_id"], "0123456789AB")
            self.assertEqual(result["angle_degrees"], 15.0)
            cached = controller.cached_turntable_position("0123456789AB", profile_id)
            self.assertEqual(cached["angle_degrees"], 15.0)
            self.assertEqual(cached["command"], "query")
            self.assertIsNone(controller.cached_turntable_position("FFFFFFFFFFFF", profile_id))
            controller._last_turntable_position_at -= 1
            with patch("cadyx.scan_capture._TURNTABLE_POSITION_CACHE_SECONDS", 0):
                self.assertIsNone(controller.cached_turntable_position("0123456789AB", profile_id))
                stale = controller.cached_turntable_position("0123456789AB", profile_id, allow_stale=True)
            self.assertEqual(stale["angle_degrees"], 15.0)
            self.assertGreater(stale["age_seconds"], 0)
            self.assertEqual(run.call_args.args[0][1:], ["rotate", "15", "0123456789AB",
                "REVO_DUAL_AXIS_TABLE", "ffe1", "ffe0", "ffe1"])

            with self.assertRaisesRegex(ValidationError, "between -30 and 30"):
                controller.turntable_control("rotate", "0123456789AB", profile_id, 45)
            run.assert_called_once()

            query = subprocess.CompletedProcess([], 0,
                b"CADYX_STATUS\t-355.00\t0.00\t17.50\t7.00\n", b"")
            with patch("cadyx.scan_capture.subprocess.run", return_value=query):
                position = controller.turntable_control("query", "0123456789AB", profile_id)
            self.assertEqual(position["angle_degrees"], 5.0,
                "a cyclic encoder reading of -355 degrees is displayed as the equivalent +5 degree orientation")

            class RunningProcess:
                def poll(self):
                    return None

            controller._job = type("ScanJob", (), {"process": RunningProcess()})()
            with self.assertRaisesRegex(ConflictError, "while a 360 scan is capturing"):
                controller.turntable_control("tilt", "0123456789AB", profile_id, 5)
            self.assertEqual(controller.turntable_control("spin-status")["scan_active"], True)

            controller._job = None
            with self.assertRaisesRegex(ConflictError, "live scanner preview"):
                controller.turntable_control("spin-start", "0123456789AB", profile_id)
            ready_path = (workspace.parent / f".{workspace.name}-state") / ".revopoint-live-preview.ready"
            ready_path.touch()
            controller._live_preview = type("LivePreview", (), {
                "process": RunningProcess(), "ready_path": ready_path,
            })()

            class SpinProcess:
                running = True

                def poll(self):
                    return None if self.running else 0

                def wait(self, timeout):
                    self.running = False
                    return 0

            spin_process = SpinProcess()
            with patch("cadyx.scan_capture.subprocess.Popen", return_value=spin_process) as start_spin:
                started = controller.turntable_control("spin-start", "0123456789AB", profile_id)
            self.assertEqual(started["state"], "starting",
                             "a live process is not reported as spinning before driver telemetry confirms motion")
            self.assertEqual(start_spin.call_args.args[0][1], "continuous-spin")
            self.assertEqual(controller._turntable_spin.heartbeat_path.name,
                             ".revopoint-turntable-spin.stop.heartbeat",
                             "the host heartbeat path matches the native driver's stop-path suffix")
            log_path = (workspace.parent / f".{workspace.name}-state") / ".revopoint-turntable-spin.log"
            log_path.write_text("Connected to REVO_DUAL_AXIS_TABLE\n", encoding="utf-8")
            starting = controller.turntable_control("spin-status")
            self.assertEqual(starting["state"], "starting")
            log_path.write_text("CADYX_SPIN\t-355.0\t0.0\n", encoding="utf-8")
            running = controller.turntable_control("spin-status")
            self.assertEqual(running["state"], "running")
            self.assertEqual(running["angle_degrees"], 5.0)
            self.assertFalse(controller.turntable_control("spin-status")["scan_active"])
            stopped = controller.turntable_control("spin-stop")
            self.assertEqual(stopped["state"], "idle")
            self.assertEqual(stopped["angle_degrees"], 5.0)
            self.assertEqual(controller.cached_turntable_position("0123456789AB", profile_id)["angle_degrees"], 5.0)

    @unittest.skipUnless(os.name == "nt", "Turntable controller executable is Windows-only")
    def test_unresponsive_spin_driver_is_fenced_and_explicit_stop_is_sent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "projects"
            workspace.mkdir()
            tool_root = root / "tools" / "revopoint"
            build = tool_root / "build"
            build.mkdir(parents=True)
            (build / "TurntableController.exe").write_bytes(b"test executable placeholder")
            profile_id = "revopoint-dual-axis-table"
            (tool_root / "devices.json").write_text(json.dumps({"turntables": [{
                "id": profile_id, "control_protocol": "revopoint-dual-axis-v1",
                "gatt_name": "REVO_DUAL_AXIS_TABLE", "advertisement_uuid": "ffe1",
                "service_uuid": "ffe0", "characteristic_uuid": "ffe1",
            }]}), encoding="utf-8")
            controller = Scan360CaptureController(root, workspace)
            class StuckSpinProcess:
                running = True

                def poll(self):
                    return None if self.running else 0

                def wait(self, timeout):
                    if self.running:
                        raise subprocess.TimeoutExpired("TurntableController.exe", timeout)
                    return 0

                def terminate(self):
                    self.running = False

            spin_process = StuckSpinProcess()
            stop_result = subprocess.CompletedProcess([], 0, b"Stop sent", b"")
            ready_path = (workspace.parent / f".{workspace.name}-state") / ".revopoint-live-preview.ready"
            ready_path.touch()
            controller._live_preview = type("LivePreview", (), {
                "process": type("RunningProcess", (), {"poll": lambda self: None})(),
                "ready_path": ready_path,
            })()
            with patch("cadyx.scan_capture.subprocess.Popen", return_value=spin_process), \
                    patch("cadyx.scan_capture.subprocess.run", return_value=stop_result) as send_stop:
                controller.turntable_control("spin-start", "0123456789AB", profile_id)
                ((workspace.parent / f".{workspace.name}-state") / ".revopoint-turntable-spin.log").write_text(
                    "CADYX_SPIN\t15.0\t0.0\n", encoding="utf-8")
                result = controller.turntable_control("spin-stop", "0123456789AB", profile_id)
            self.assertEqual(result["state"], "idle")
            self.assertEqual(result["stop_result"]["state"], "completed")
            self.assertEqual(send_stop.call_args.args[0][1:], ["stop", "0123456789AB",
                "REVO_DUAL_AXIS_TABLE", "ffe1", "ffe0", "ffe1"])

    @unittest.skipUnless(os.name == "nt", "Turntable controller process inspection is Windows-only")
    def test_spin_stop_recovers_stale_files_only_after_controller_exits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "projects"
            workspace.mkdir()
            controller = Scan360CaptureController(root, workspace)
            stop_path = (workspace.parent / f".{workspace.name}-state") / ".revopoint-turntable-spin.stop"
            heartbeat_path = (workspace.parent / f".{workspace.name}-state") / ".revopoint-turntable-spin.stop.heartbeat"
            legacy_heartbeat_path = (workspace.parent / f".{workspace.name}-state") / ".revopoint-turntable-spin.heartbeat"
            log_path = (workspace.parent / f".{workspace.name}-state") / ".revopoint-turntable-spin.log"
            stop_path.touch()
            heartbeat_path.touch()
            legacy_heartbeat_path.touch()
            log_path.write_text("Connected to REVO_DUAL_AXIS_TABLE\n", encoding="utf-8")
            with patch.object(controller, "_active_turntable_controller_process_ids", return_value=[1234]):
                with self.assertRaisesRegex(ConflictError, "controller is still active"):
                    controller.turntable_control("spin-stop")
            self.assertTrue(stop_path.exists(), "active controllers keep their stop signal")
            with patch.object(controller, "_active_turntable_controller_process_ids", return_value=[]):
                result = controller.turntable_control("spin-stop")
            self.assertEqual(result["state"], "idle")
            self.assertFalse(stop_path.exists())
            self.assertFalse(heartbeat_path.exists())
            self.assertFalse(legacy_heartbeat_path.exists())
            archived = controller._state_root / result["archived_log"]
            self.assertEqual(archived.read_text(encoding="utf-8"), "Connected to REVO_DUAL_AXIS_TABLE\n")
            self.assertFalse(log_path.exists())

    def test_capture_status_reports_per_frame_cleanup_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            run_id = "revopoint_20261023T091200Z"
            run_directory = project / run_id
            run_directory.mkdir()
            (run_directory / f"{run_id}_preprocessing.json").write_text(json.dumps({
                "state": "running", "cleaned_frame_count": 2, "failed_frame_count": 1,
                "detected_table_marker_count": 5, "removed_table_marker_points": 91,
                "frames": [{"table_marker_detection": "completed"},
                           {"table_marker_detection": "completed"}], "failed_frames": [{}],
            }), encoding="utf-8")

            status = Scan360CaptureController._preprocessing_payload(project, run_id, 8)

        self.assertEqual(status["preprocessing_status"], "running")
        self.assertEqual(status["preprocessed_frames"], 2)
        self.assertEqual(status["preprocessing_failed_frames"], 1)
        self.assertEqual(status["preprocessing_pending_frames"], 5)
        self.assertEqual(status["table_marker_detection_status"], "running")
        self.assertEqual(status["detected_table_marker_count"], 5)
        self.assertEqual(status["removed_table_marker_points"], 91)

    def test_malformed_preprocessing_reports_fail_closed(self) -> None:
        reports = [
            None,
            [],
            {"state": "completed", "cleaned_frame_count": -1},
            {"state": "completed", "cleaned_frame_count": True},
            {"state": "completed", "cleaned_frame_count": 5},
            {"state": "completed", "cleaned_frame_count": 3, "failed_frame_count": 2},
            {"state": "completed", "cleaned_frame_count": 3, "failed_frame_count": 0},
            {"state": "running", "cleaned_frame_count": "2"},
            {"state": "running", "cleaned_frame_count": 2, "failed_frame_count": -1},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            run_id = "revopoint_20261023T091300Z"
            run_directory = project / run_id
            run_directory.mkdir()
            report_path = run_directory / f"{run_id}_preprocessing.json"
            for report in reports:
                with self.subTest(report=report):
                    report_path.write_text(json.dumps(report), encoding="utf-8")
                    status = Scan360CaptureController._preprocessing_payload(project, run_id, 4)
                    self.assertEqual(status["preprocessing_status"], "failed")
                    self.assertEqual(status["preprocessed_frames"], 0)
                    self.assertEqual(status["preprocessing_failed_frames"], 4)

    def test_legacy_capture_without_preprocessing_report_is_not_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            run_id = "revopoint_20261023T091400Z"
            run_directory = project / run_id
            run_directory.mkdir()

            status = Scan360CaptureController._preprocessing_payload(project, run_id, 24)

        self.assertEqual(status["preprocessing_status"], "not_available")
        self.assertEqual(status["preprocessed_frames"], 0)
        self.assertEqual(status["preprocessing_pending_frames"], 0)

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
            devices = {"scanners": [{"device_id": "scanner-1", "profile_id": "revopoint-mini-y16"}],
                       "turntables": []}
            with patch.object(controller, "list_connected_devices", return_value=devices), \
                    patch.object(controller, "_resolve_scanner_calibration", return_value=(root / "Q.bin", 0.005)), \
                    patch("cadyx.scan_capture.subprocess.Popen", return_value=process) as popen:
                self.assertEqual(controller.start_live_preview(fps=15)["state"], "starting")
                command = popen.call_args.args[0]
                self.assertIn("--live-only", command)
                self.assertIn("--live-frames-dir", command)
                self.assertNotIn("--live-raw", command)
                self.assertEqual(command[command.index("--fps") + 1], "15")
                self.assertNotIn("--out-prefix", command)
                self.assertNotIn("--frames", command)
                self.assertNotIn("--q-file", command)
                raw_path = (workspace.parent / f".{workspace.name}-state") / ".revopoint-live.raw"
                frames_path = (workspace.parent / f".{workspace.name}-state") / ".revopoint-live.raw.frames"
                stop_path = (workspace.parent / f".{workspace.name}-state") / ".revopoint-live-preview.stop"
                controller._live_preview.ready_path.touch()
                frames_path.mkdir()
                (frames_path / "frame-1.y16").write_bytes(b"current frame")
                self.assertFalse(raw_path.exists(), "live preview no longer writes a second current-frame file")
                metrics = {
                    "window_seconds": 1.0, "frames": 4, "read_calls": 4, "null_samples": 0,
                    "last_no_frame_flags": 0, "raw_link_fallbacks": 0,
                    "capture_fps": 4.0, "camera_fps": 4.0,
                    "read_avg_ms": 240.0, "read_max_ms": 251.0,
                    "copy_avg_ms": 1.0, "copy_max_ms": 1.4,
                    "publish_avg_ms": 7.0, "publish_max_ms": 10.0,
                    "process_cpu_percent": 1.2,
                }
                controller._live_preview.log_path.write_text(
                    "preview_metrics " + json.dumps(metrics) + "\n", encoding="utf-8",
                )
                status = controller.live_preview_status()
                self.assertEqual(status["state"], "running")
                self.assertEqual(status["frame_mtime_ns"], frames_path.stat().st_mtime_ns)
                self.assertEqual(status["metrics"], metrics)
                client_metrics = {
                    "window_seconds": 1.2, "frames": 5,
                    "request_avg_ms": 220.0, "request_max_ms": 250.0,
                    "process_avg_ms": 18.0, "process_max_ms": 24.0,
                    "render_avg_ms": 8.0, "render_max_ms": 11.0,
                    "total_avg_ms": 246.0, "total_max_ms": 285.0, "points_avg": 6100.0,
                }
                self.assertEqual(controller.record_live_preview_client_metrics(client_metrics), {"recorded": True})
                status = controller.live_preview_status()
                self.assertEqual(status["client_metrics"], client_metrics)
                self.assertGreaterEqual(status["client_metrics_age_ms"], 0)
                with self.assertRaises(ValidationError):
                    controller.record_live_preview_client_metrics({**client_metrics, "render_max_ms": float("inf")})
                self.assertEqual(controller.stop_live_preview(), {"state": "idle"})
                self.assertFalse(raw_path.exists(), "stopping the preview removes any legacy current-frame cache")
                self.assertFalse(frames_path.exists(), "stopping the preview removes its temporary frame queue")
                self.assertFalse(stop_path.exists())

    @unittest.skipUnless(os.name == "nt", "Live preview executable is Windows-only")
    def test_live_preview_start_recovers_a_matching_orphan_process_before_restarting(self) -> None:
        class FakeProcess:
            def poll(self):
                return None

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "tools" / "revopoint" / "build" / "CaptureY16.exe"
            executable.parent.mkdir(parents=True)
            executable.touch()
            workspace = root / "projects"
            workspace.mkdir()
            controller = Scan360CaptureController(root, workspace)
            frames_path = (workspace.parent / f".{workspace.name}-state") / ".revopoint-live.raw.frames"
            frames_path.mkdir()
            stop_path = (workspace.parent / f".{workspace.name}-state") / ".revopoint-live-preview.stop"
            ready_path = (workspace.parent / f".{workspace.name}-state") / ".revopoint-live-preview.ready"
            ready_path.touch()
            devices = {"scanners": [{"device_id": "scanner-1", "profile_id": "revopoint-mini-y16"}],
                       "turntables": []}
            with patch.object(controller, "list_connected_devices", return_value=devices), \
                    patch.object(controller, "_resolve_scanner_calibration", return_value=(root / "Q.bin", 0.005)), \
                    patch.object(controller, "_stop_orphaned_live_preview_processes", return_value=[4321]) as recover, \
                    patch("cadyx.scan_capture.subprocess.Popen", return_value=FakeProcess()):
                result = controller.start_live_preview("scanner-1", fps=10)
            self.assertEqual(result["state"], "starting")
            recover.assert_called_once_with(executable.resolve(), frames_path.resolve(),
                                            stop_path.resolve(), ready_path.resolve())
            self.assertFalse(frames_path.exists(), "restart clears the old orphan's temporary frame queue")
            self.assertFalse(ready_path.exists())

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
            devices = {
                "scanners": [{"device_id": "scanner-1", "profile_id": "revopoint-mini-y16"}],
                "turntables": [{"device_id": "table-1", "profile_id": "revopoint-dual-axis-table",
                                "expected_name": "table", "advertisement_uuid": "adv",
                                "service_uuid": "service", "characteristic_uuid": "char"}],
            }
            with patch.object(controller, "list_connected_devices", return_value=devices), \
                    patch.object(controller, "_resolve_scanner_calibration", return_value=(root / "Q.bin", 0.005)), \
                    patch.object(controller, "_turntable_marker_map_path", return_value=root / "table.mkf"), \
                    patch("cadyx.scan_capture.shutil.which", return_value="powershell.exe"):
                with self.assertRaises(ConflictError):
                    controller.start("part", project)

    @unittest.skipUnless(os.name == "nt", "Revopoint 360 capture runs on the Windows scanner host")
    def test_360_capture_passes_the_selected_table_map_and_python_runtime(self) -> None:
        class FakeProcess:
            def poll(self):
                return None

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "projects" / "part"
            project.mkdir(parents=True)
            script = root / "tools" / "revopoint" / "scan_360.ps1"
            script.parent.mkdir(parents=True)
            script.touch()
            marker_map = root / "table.mkf"
            marker_map.touch()
            calibration = root / "Q.bin"
            calibration.touch()
            controller = Scan360CaptureController(root, project.parent)
            devices = {
                "scanners": [{"device_id": "scanner-1", "profile_id": "revopoint-mini-y16"}],
                "turntables": [{"device_id": "table-1", "profile_id": "revopoint-dual-axis-table",
                                "expected_name": "table", "advertisement_uuid": "adv",
                                "service_uuid": "service", "characteristic_uuid": "char"}],
            }
            with patch.object(controller, "list_connected_devices", return_value=devices), \
                    patch.object(controller, "_resolve_scanner_calibration", return_value=(calibration, 0.005)), \
                    patch.object(controller, "_turntable_marker_map_path", return_value=marker_map), \
                    patch("cadyx.scan_capture.shutil.which", return_value="powershell.exe"), \
                    patch("cadyx.scan_capture.subprocess.Popen", return_value=FakeProcess()) as popen:
                controller.start("part", project, scanner_device_id="scanner-1", turntable_device_id="table-1")
                command = popen.call_args.args[0]
                run_id = command[command.index("-RunId") + 1]
                series = project / run_id
                support = project / f"scan_support_{run_id}"
                self.assertEqual(list(series.iterdir()), [])
                self.assertTrue((support / f".cadyx-scan360-{run_id.removeprefix('revopoint_')}.log").is_file())

        self.assertEqual(command[command.index("-TurntableMarkerMap") + 1], str(marker_map))
        expected_python = Path(sys.executable)
        if expected_python.name.lower() == "pythonw.exe" and expected_python.with_name("python.exe").is_file():
            expected_python = expected_python.with_name("python.exe")
        self.assertEqual(command[command.index("-PythonExecutable") + 1], str(expected_python))

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

    def test_latest_complete_capture_is_discovered_inside_its_project_series_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            run_id = "revopoint_20261021T103419Z"
            series = project / run_id
            series.mkdir()
            (project / "project.json").write_text(json.dumps({"id": "project-id"}), encoding="utf-8")
            (series / f"{run_id}_poses.csv").write_text("poses", encoding="utf-8")
            (project / ".cadyx-scan360-20261021T103419Z.log").write_text(
                "sweep completed: 12 steps", encoding="utf-8",
            )
            for index in range(12):
                (series / f"{run_id}_pose_{index:03d}.raw").touch()
            (series / f"{run_id}_pose_011.ply").touch()

            status = Scan360CaptureController._latest_completed_snapshot(project)

        self.assertEqual(status["state"], "completed")
        self.assertEqual(status["preview_project_id"], "project-id")
        self.assertEqual(status["preview_subfolder"], run_id)
        self.assertEqual(status["preview_file"], f"{run_id}_pose_011.ply")

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

    def test_partial_marker_alignment_previews_the_latest_aligned_frame(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            run_id = "revopoint_20261020T103638Z"
            (project / f"{run_id}_table_alignment.json").write_text(
                json.dumps({"status": "partial"}), encoding="utf-8",
            )
            (project / f"{run_id}_pose_010_table_aligned.ply").touch()
            (project / f"{run_id}_pose_011.ply").touch()
            (project / f"{run_id}_first_frame_geometric_candidate.ply").touch()

            preview = Scan360CaptureController._preview_payload(project, run_id, 12)

        self.assertEqual(preview["preview_file"], f"{run_id}_pose_010_table_aligned.ply")
        self.assertEqual(preview["preview_coordinate_frame"], "table_marker_map_center_xy")
        self.assertEqual(preview["preview_frame_index"], 10)

    def test_malformed_table_alignment_reports_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            run_id = "revopoint_20261020T103638Z"
            alignment = project / f"{run_id}_table_alignment.json"
            for report in ([], {"status": []}, {"status": {"value": "partial"}},
                           {"status": 12}, {"status": None}, {"status": "unexpected"}):
                with self.subTest(report=report):
                    alignment.write_text(json.dumps(report), encoding="utf-8")
                    self.assertEqual(
                        Scan360CaptureController._alignment_status(project, run_id), "failed",
                    )
            for status in ("aligned_provisional", "partial", "unavailable", "not_performed"):
                with self.subTest(status=status):
                    alignment.write_text(json.dumps({"status": status}), encoding="utf-8")
                    self.assertEqual(
                        Scan360CaptureController._alignment_status(project, run_id), status,
                    )

    def test_malformed_table_alignment_does_not_break_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            run_id = "revopoint_20261020T103638Z"
            (project / f"{run_id}_table_alignment.json").write_text("[]", encoding="utf-8")
            (project / f"{run_id}_pose_011.ply").touch()

            preview = Scan360CaptureController._preview_payload(project, run_id, 12)

        self.assertEqual(preview["preview_file"], f"{run_id}_pose_011.ply")
        self.assertEqual(preview["origin_file"], f"{run_id}_pose_011.ply")

    def test_unaligned_capture_preview_keeps_the_current_frame_coordinate_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            run_id = "revopoint_20261020T103638Z"
            (project / f"{run_id}_pose_011.ply").touch()

            preview = Scan360CaptureController._preview_payload(project, run_id, 12)

        self.assertEqual(preview["preview_file"], f"{run_id}_pose_011.ply")
        self.assertEqual(preview["origin_file"], f"{run_id}_pose_011.ply")

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
