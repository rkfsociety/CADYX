from __future__ import annotations

import tempfile
import unittest
import csv
import hashlib
import json
import struct
import threading
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np

from tools.revopoint.preprocess_scan_frame import (
    _write_aggregate_report, preprocess_frame_file, preprocess_points,
    verified_preprocessed_frame, watch_capture,
)
from tools.revopoint.register_point_clouds_first_frame import source_paths
from tools.revopoint.register_point_clouds_first_frame import transform_points_to_table_map


class PointCloudRegistrationInputTests(unittest.TestCase):
    @staticmethod
    def _source_ply() -> bytes:
        points = [(x, y, 0.0) for x in range(12) for y in range(12)]
        points += [(50.0, 50.0, 50.0), (50.1, 50.0, 50.0),
                   (50.0, 50.1, 50.0), (50.1, 50.1, 50.0)]
        return ("ply\nformat ascii 1.0\nelement vertex 148\n"
                "property float x\nproperty float y\nproperty float z\nend_header\n" +
                "".join(f"{x} {y} {z}\n" for x, y, z in points)).encode("ascii")

    def test_frame_zero_marker_fit_rebases_fused_cloud_to_table_xy(self) -> None:
        frame = {
            "scanner_table_plane_origin": [1.0, 2.0, 3.0],
            "scanner_table_plane_axes": {
                "x": [0.0, 1.0, 0.0],
                "y": [0.0, 0.0, 1.0],
                "z": [1.0, 0.0, 0.0],
            },
            "map_similarity": {
                "a": 2.0, "b": 0.0, "c": 0.0, "d": 2.0,
                "tx": 10.0, "ty": -5.0, "scale": 2.0,
            },
        }
        source = np.asarray([[1.0, 2.0, 3.0], [5.0, 4.0, 6.0],
                             [0.0, 2.0, 3.0], [-2.0, 2.0, 3.0]])

        aligned, clipped = transform_points_to_table_map(source, frame)

        self.assertEqual(clipped, 1)
        np.testing.assert_allclose(aligned, [[10.0, -5.0, 0.0], [14.0, 1.0, 8.0],
                                             [10.0, -5.0, 0.0]])

    def test_source_frame_is_cleaned_before_registration(self) -> None:
        surface = np.array([(x, y, 0.0) for x in range(12) for y in range(12)], dtype=np.float64)
        debris = np.array([(50.0, 50.0, 50.0), (50.1, 50.0, 50.0),
                           (50.0, 50.1, 50.0), (50.1, 50.1, 50.0)], dtype=np.float64)
        source = np.vstack((surface, debris))

        cleaned, report = preprocess_points([tuple(point) for point in source], 7)

        self.assertEqual(len(source), 148)
        self.assertEqual(len(cleaned), 144)
        self.assertEqual(report["removed_by_method"]["isolation_filter"], 4)
        self.assertEqual(report["removed_by_method"]["remove_overlaps"], 0)
        self.assertEqual(len(report["passes"]), 1)
        self.assertEqual(report["passes"][0], {"isolation_filter": 4, "remove_overlaps": 0})

    def test_table_marker_pixels_are_removed_before_cloud_filters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            raw_path, q_path = folder / "frame.raw", folder / "Q.bin"
            ply_path, cleaned_path, report_path = folder / "frame.ply", folder / "frame_cleaned.ply", folder / "frame_cleaned.json"
            values = [0] * (640 * 400)
            for start_x in (100, 120, 140):
                for y in range(100, 105):
                    for x in range(start_x, start_x + 5):
                        values[y * 640 + x] = 2000
            for y in range(160, 200):
                for x in range(250, 290):
                    values[y * 640 + x] = 2000
            raw_path.write_bytes(struct.pack("<256000H", *values))
            q = (1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0)
            q_path.write_bytes(struct.pack("<16f", *q))
            points = [(float(index % 640), -float(index // 640), -200.0)
                      for index, value in enumerate(values) if value > 0]
            ply_path.write_text(
                f"ply\nformat ascii 1.0\nelement vertex {len(points)}\n"
                "property float x\nproperty float y\nproperty float z\nend_header\n" +
                "".join(f"{x} {y} {z}\n" for x, y, z in points), encoding="ascii")

            report = preprocess_frame_file(ply_path, cleaned_path, report_path, 0,
                                           raw_path=raw_path, calibration_path=q_path,
                                           depth_scale=0.1)

            self.assertEqual(report["table_marker_detection"], "completed")
            self.assertEqual(report["detected_table_markers"], 3)
            self.assertEqual(report["removed_table_marker_points"], 75)
            self.assertEqual(report["source_points"], 1675)
            self.assertEqual(report["cleaned_points"], 1600)
            self.assertEqual(report["parameters"]["remove_overlaps"]["radius"], 0.59)
            self.assertEqual(len(cleaned_path.read_text(encoding="ascii").splitlines()), 1600 + 8)

    def test_capture_report_retries_transient_windows_replace_denial(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report_path = Path(temporary) / "report.json"
            original_replace = Path.replace
            attempts = 0

            def replace_with_transient_denial(source: Path, target: Path) -> Path:
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise PermissionError("transient report-reader lock")
                return original_replace(source, target)

            with patch.object(Path, "replace", replace_with_transient_denial):
                _write_aggregate_report(report_path, {"state": "running"})

            self.assertEqual(attempts, 3)
            self.assertEqual(json.loads(report_path.read_text(encoding="utf-8")), {"state": "running"})

    def test_capture_worker_overwrites_source_and_keeps_no_cleaned_ply_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_directory = Path(temporary)
            run_id = "revopoint_20261023T091200Z"
            source_path = run_directory / f"{run_id}_pose_000.ply"
            source_points = [(x, y, 0.0) for x in range(12) for y in range(12)]
            source_points += [(50.0, 50.0, 50.0), (50.1, 50.0, 50.0),
                              (50.0, 50.1, 50.0), (50.1, 50.1, 50.0)]
            source_bytes = ("ply\nformat ascii 1.0\nelement vertex 148\n"
                            "property float x\nproperty float y\nproperty float z\nend_header\n" +
                            "".join(f"{x} {y} {z}\n" for x, y, z in source_points)).encode("ascii")
            source_path.write_bytes(source_bytes)
            pose_log = run_directory / f"{run_id}_poses.csv"
            with pose_log.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["step_index", "event", "command"])
                writer.writeheader()
                writer.writerow({"step_index": 0, "event": "capture_completed",
                                 "command": str(source_path.with_suffix(""))})
            stop_signal = run_directory / "stop.signal"
            stop_signal.touch()

            report = watch_capture(pose_log, run_directory, run_id, 1, stop_signal,
                                   run_directory / f"{run_id}_preprocessing.json", max_workers=1)

            report_path = run_directory / f"{run_id}_pose_000_cleaned.json"
            frame_report = json.loads(report_path.read_text(encoding="utf-8"))
            verified = verified_preprocessed_frame(source_path)
            self.assertEqual(report["state"], "completed")
            self.assertEqual(report["cleaned_frame_count"], 1)
            self.assertEqual(frame_report["removed_by_method"]["isolation_filter"], 4)
            self.assertEqual(frame_report["processing_mode"], "in_place")
            self.assertEqual(frame_report["source_sha256"], hashlib.sha256(source_bytes).hexdigest())
            self.assertEqual(verified[0], source_path.resolve())
            self.assertNotEqual(source_path.read_bytes(), source_bytes)
            self.assertFalse(source_path.with_name(source_path.stem + "_cleaned.ply").exists())

    def test_capture_worker_finishes_frame_before_scan_stop_signal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_directory = Path(temporary)
            run_id = "revopoint_20261023T091300Z"
            pose_log = run_directory / f"{run_id}_poses.csv"
            with pose_log.open("w", encoding="utf-8", newline="") as stream:
                csv.DictWriter(stream, fieldnames=["step_index", "event", "command"]).writeheader()
            stop_signal = run_directory / "stop.signal"
            result: list[dict] = []
            failure: list[BaseException] = []

            def run_worker() -> None:
                try:
                    result.append(watch_capture(
                        pose_log, run_directory, run_id, 2, stop_signal,
                        run_directory / f"{run_id}_preprocessing.json", max_workers=1,
                        poll_seconds=0.02))
                except BaseException as exc:
                    failure.append(exc)

            worker_thread = threading.Thread(target=run_worker, daemon=True)
            worker_thread.start()
            first_source = run_directory / f"{run_id}_pose_000.ply"
            first_source.write_bytes(self._source_ply())
            with pose_log.open("a", encoding="utf-8", newline="") as stream:
                csv.DictWriter(stream, fieldnames=["step_index", "event", "command"]).writerow({
                    "step_index": 0, "event": "capture_completed", "command": str(first_source.with_suffix("")),
                })

            first_report = run_directory / f"{run_id}_pose_000_cleaned.json"
            deadline = time.monotonic() + 5
            while not first_report.is_file() and time.monotonic() < deadline:
                time.sleep(0.02)
            first_was_cleaned_while_scanning = first_report.is_file() and not stop_signal.exists()

            second_source = run_directory / f"{run_id}_pose_001.ply"
            second_source.write_bytes(self._source_ply())
            with pose_log.open("a", encoding="utf-8", newline="") as stream:
                csv.DictWriter(stream, fieldnames=["step_index", "event", "command"]).writerow({
                    "step_index": 1, "event": "capture_completed", "command": str(second_source.with_suffix("")),
                })
            stop_signal.touch()
            worker_thread.join(timeout=10)

            self.assertFalse(worker_thread.is_alive(), "capture cleanup worker did not drain its queue")
            self.assertFalse(failure, str(failure))
            self.assertTrue(first_was_cleaned_while_scanning)
            self.assertEqual(result[0]["state"], "completed")
            self.assertEqual(result[0]["cleaned_frame_count"], 2)
            self.assertEqual(result[0]["worker_limit"], 1)
            self.assertEqual([frame["frame_index"] for frame in result[0]["frames"]], [0, 1])
            self.assertFalse(first_source.with_name(first_source.stem + "_cleaned.ply").exists())

    def test_capture_worker_rejects_parallel_processing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_directory = Path(temporary)
            with self.assertRaisesRegex(ValueError, "exactly one worker"):
                watch_capture(
                    run_directory / "poses.csv", run_directory, "revopoint_20261023T091400Z", 2,
                    run_directory / "stop.signal", run_directory / "report.json", max_workers=2,
                )

    def test_resolves_all_twelve_legacy_pose_clouds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            run_id = "revopoint_20261020T103638Z"
            for index in range(12):
                (project / f"{run_id}_pose_pose_{index:03d}.ply").touch()
            paths = source_paths(project, run_id)
        self.assertEqual(len(paths), 12)
        self.assertEqual(paths[0].name, f"{run_id}_pose_pose_000.ply")
        self.assertEqual(paths[-1].name, f"{run_id}_pose_pose_011.ply")

    def test_refuses_incomplete_frame_sets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            run_id = "revopoint_20261020T103638Z"
            for index in range(11):
                (project / f"{run_id}_pose_{index:03d}.ply").touch()
            with self.assertRaises(FileNotFoundError):
                source_paths(project, run_id)


if __name__ == "__main__":
    unittest.main()
