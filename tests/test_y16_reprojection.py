from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path

from tools.revopoint.align_y16_to_table import detect_marker_regions
from tools.revopoint.reproject_y16 import RAW_BYTES, reproject_files, reproject_y16


Q = (
    1.0, 0.0, 0.0, -52.766502,
    0.0, 1.0, 0.0, -199.493652,
    0.0, 0.0, 0.0, 891.2453,
    0.0, 0.0, 0.011310811, 5.890967,
)


class Y16ReprojectionTests(unittest.TestCase):
    def test_q_ray_is_scaled_to_the_measured_depth(self) -> None:
        raw = bytearray(RAW_BYTES)
        struct.pack_into("<H", raw, (299 * 640 + 153) * 2, 25000)

        points = reproject_y16(bytes(raw), Q, 0.005)

        self.assertEqual(len(points), 1)
        x, y, z = points[0]
        self.assertAlmostEqual(z, -125.0)
        self.assertGreater(x, 10.0)
        self.assertLess(y, 0.0)

    def test_rejects_near_zero_depth_artifacts(self) -> None:
        raw = bytearray(RAW_BYTES)
        struct.pack_into("<H", raw, 0, 12)

        self.assertEqual(reproject_y16(bytes(raw), Q, 0.005), [])

    def test_enforces_100_to_250_mm_camera_ray_range(self) -> None:
        raw = bytearray(RAW_BYTES)
        for pixel, value in enumerate((19_999, 20_000, 50_000, 50_001)):
            struct.pack_into("<H", raw, pixel * 2, value)

        points = reproject_y16(bytes(raw), Q, 0.005)

        self.assertEqual([point[2] for point in points], [-100.0, -250.0])

    def test_marker_regions_ignore_depth_outside_camera_ray_range(self) -> None:
        raw = bytearray(RAW_BYTES)
        for top, left, value in ((20, 20, 20_000), (20, 40, 19_999)):
            for y in range(top, top + 7):
                for x in range(left, left + 7):
                    struct.pack_into("<H", raw, (y * 640 + x) * 2, value)

        regions = detect_marker_regions(bytes(raw), 0.005)

        self.assertEqual([len(region) for region in regions], [49])

    def test_file_reprojection_records_provenance_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            raw_path = root / "frame.raw"
            q_path = root / "Q.bin"
            prefix = root / "frame_qdepth"
            raw = bytearray(RAW_BYTES)
            for index in range(120):
                struct.pack_into("<H", raw, index * 2, 25000)
            raw_path.write_bytes(raw)
            q_path.write_bytes(struct.pack("<16f", *Q))

            ply_path, metadata_path = reproject_files(raw_path, q_path, prefix, 0.005)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["projection_method"], "Q_ray_scaled_to_depth")
            self.assertEqual(metadata["depth_scale_mm_per_raw_unit"], 0.005)
            self.assertEqual(metadata["point_count"], 120)
            self.assertTrue(ply_path.exists())
            with self.assertRaises(FileExistsError):
                reproject_files(raw_path, q_path, prefix, 0.005)


if __name__ == "__main__":
    unittest.main()
