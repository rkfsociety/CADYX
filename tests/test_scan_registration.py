from __future__ import annotations

import tempfile
import unittest
import csv
import json
import threading
import time
from pathlib import Path

import numpy as np

from tools.revopoint.preprocess_scan_frame import preprocess_points, verified_preprocessed_frame, watch_capture
from tools.revopoint.register_point_clouds_first_frame import source_paths


class PointCloudRegistrationInputTests(unittest.TestCase):
    @staticmethod
    def _source_ply() -> bytes:
        points = [(x, y, 0.0) for x in range(12) for y in range(12)]
        points += [(50.0, 50.0, 50.0), (50.1, 50.0, 50.0),
                   (50.0, 50.1, 50.0), (50.1, 50.1, 50.0)]
        return ("ply\nformat ascii 1.0\nelement vertex 148\n"
                "property float x\nproperty float y\nproperty float z\nend_header\n" +
                "".join(f"{x} {y} {z}\n" for x, y, z in points)).encode("ascii")

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
        self.assertEqual(report["passes"][-1], {"isolation_filter": 0, "remove_overlaps": 0})

    def test_capture_worker_cleans_completed_source_without_modifying_it(self) -> None:
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

            cleaned_path = run_directory / f"{run_id}_pose_000_cleaned.ply"
            frame_report = json.loads(cleaned_path.with_suffix(".json").read_text(encoding="utf-8"))
            verified = verified_preprocessed_frame(source_path)
            self.assertEqual(report["state"], "completed")
            self.assertEqual(report["cleaned_frame_count"], 1)
            self.assertEqual(frame_report["removed_by_method"]["isolation_filter"], 4)
            self.assertEqual(verified[0], cleaned_path)
            self.assertEqual(source_path.read_bytes(), source_bytes)

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
                        run_directory / f"{run_id}_preprocessing.json", max_workers=2,
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

            first_cleaned = run_directory / f"{run_id}_pose_000_cleaned.ply"
            deadline = time.monotonic() + 5
            while not first_cleaned.is_file() and time.monotonic() < deadline:
                time.sleep(0.02)
            first_was_cleaned_while_scanning = first_cleaned.is_file() and not stop_signal.exists()

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
