from __future__ import annotations

import hashlib
import struct
import tempfile
import unittest
from pathlib import Path

from tools.revopoint.align_y16_to_table import align_run, register_markers


class TableMarkerAlignmentTests(unittest.TestCase):
    @staticmethod
    def _map_bytes(points: list[tuple[float, float, float]]) -> bytes:
        records = [struct.pack("<2i2f6d", 0, -1, 1.0, 0.0, *point, 0.0, 0.0, 1.0)
                   for point in points]
        return struct.pack("<4I", 1, 256, 0, len(records)) + b"".join(records)

    @staticmethod
    def _raw_frame(*, markers: bool = True) -> bytes:
        values = [0] * (640 * 400)
        if markers:
            for center_x, center_y in ((170, 180), (120, 130), (220, 130), (120, 230), (240, 200)):
                for y in range(center_y - 2, center_y + 3):
                    for x in range(center_x - 2, center_x + 3):
                        values[y * 640 + x] = 2000
        return struct.pack("<256000H", *values)

    @staticmethod
    def _write_ply(path: Path) -> None:
        path.write_text(
            "ply\nformat ascii 1.0\nelement vertex 3\nproperty float x\nproperty float y\nproperty float z\nend_header\n"
            "170 -180 -10\n170 -180 -5\n120 -130 -10\n",
            encoding="ascii",
        )

    def test_similarity_registration_recovers_reflected_map_correspondences(self) -> None:
        target = [(0.0, 0.0), (-50.0, 50.0), (50.0, 50.0), (-50.0, -50.0),
                  (70.0, -20.0), (15.0, -65.0)]
        source = [((point[0] + 7.0) / 0.4, -(point[1] - 3.0) / 0.4) for point in target]
        source.extend(((800.0, 300.0), (-450.0, 770.0)))

        transform = register_markers(source, target, 0.25)

        self.assertEqual(transform["matches"], 6)
        self.assertAlmostEqual(transform["scale"], 0.4, places=7)
        self.assertAlmostEqual(transform["tx"], -7.0, places=7)
        self.assertAlmostEqual(transform["ty"], 3.0, places=7)
        self.assertLess(transform["median_residual"], 1e-8)

    def test_capture_aligns_frames_with_markers_and_defers_occluded_frames(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            run_id = "revopoint_20261021T120000Z"
            marker_points = [(0.0, 0.0, 0.0), (-50.0, 50.0, 0.0), (50.0, 50.0, 0.0),
                             (-50.0, -50.0, 0.0), (70.0, -20.0, 0.0)]
            map_path = project / f"{run_id}_table_marker_map.mkf"
            map_path.write_bytes(self._map_bytes(marker_points))
            q_path = project / f"{run_id}_Q.bin"
            q_path.write_bytes(struct.pack("<16f", 1, 0, 0, 0, 0, 1, 0, 0,
                                           0, 0, 1, 0, 0, 0, 0, 1))
            original_hashes = {}
            for index in range(12):
                stem = f"{run_id}_pose_{index:03d}"
                raw_path = project / f"{stem}.raw"
                raw_path.write_bytes(self._raw_frame(markers=index < 11))
                ply_path = project / f"{stem}.ply"
                self._write_ply(ply_path)
                original_hashes[raw_path.name] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
                original_hashes[ply_path.name] = hashlib.sha256(ply_path.read_bytes()).hexdigest()

            result = align_run(project, run_id, map_path, q_path, 0.005)

            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["aligned_frame_count"], 11)
            self.assertEqual(result["deferred_frame_count"], 1)
            self.assertEqual(result["frames"][-1]["status"], "deferred")
            self.assertTrue((project / f"{run_id}_pose_000_table_aligned.ply").is_file())
            self.assertFalse((project / f"{run_id}_pose_011_table_aligned.ply").exists())
            for filename, digest in original_hashes.items():
                self.assertEqual(hashlib.sha256((project / filename).read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
