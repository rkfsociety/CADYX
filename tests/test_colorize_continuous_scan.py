from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

from tools.revopoint.colorize_continuous_scan import (
    parse_rgb_calibration,
    project_depth_points_to_rgb,
    _load_unique_pairs,
    visible_projected_points,
)


class ContinuousRgbColorizationTests(unittest.TestCase):
    @staticmethod
    def calibration_bytes() -> tuple[bytes, bytes]:
        extrinsic = struct.pack("<12f", 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0)
        projection = struct.pack("<10f", 0, 900, 0, 320, 0, 900, 200, 0, 0, 1)
        return extrinsic, projection

    def test_parses_camera_to_rgb_pose_and_intrinsics(self) -> None:
        extrinsic, projection = self.calibration_bytes()

        rotation, translation, intrinsics = parse_rgb_calibration(extrinsic, projection)

        np.testing.assert_allclose(rotation, np.eye(3))
        np.testing.assert_allclose(translation, [0, 0, 0])
        np.testing.assert_allclose(intrinsics, [[900, 0, 320], [0, 900, 200], [0, 0, 1]])

    def test_projects_depth_camera_coordinates_into_doubled_rgb_resolution(self) -> None:
        extrinsic, projection = self.calibration_bytes()
        rotation, translation, intrinsics = parse_rgb_calibration(extrinsic, projection)
        points = np.asarray([[0, 0, -100], [10, 0, -100]], dtype=np.float64)

        u, v, z = project_depth_points_to_rgb(points, rotation, translation, intrinsics)

        np.testing.assert_allclose(u, [640, 820], atol=1e-9)
        np.testing.assert_allclose(v, [400, 400], atol=1e-9)
        np.testing.assert_allclose(z, [100, 100], atol=1e-9)

    def test_visibility_raster_keeps_front_surfaces_and_rejects_occluded_points(self) -> None:
        indices, x, y = visible_projected_points(
            np.asarray([100.1, 100.2, 200.0]),
            np.asarray([200.1, 200.2, 400.0]),
            np.asarray([10.0, 12.0, 5.0]),
            1280,
            800,
        )

        np.testing.assert_array_equal(indices, [0, 2])
        np.testing.assert_array_equal(x, [100, 200])
        np.testing.assert_array_equal(y, [200, 400])

    def test_rejects_invalid_extrinsic_rotation(self) -> None:
        extrinsic = struct.pack("<12f", *([0.0] * 12))
        _, projection = self.calibration_bytes()
        with self.assertRaisesRegex(ValueError, "proper rigid rotation"):
            parse_rgb_calibration(extrinsic, projection)

    def test_color_pair_loader_keeps_unique_frames_within_fifteen_milliseconds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            (run / "depth_rgb_pairs.csv").write_text(
                "depth_frame,rgb_frame,delta_ms\n"
                "frame_00000.raw,0,4.2\n"
                "frame_00001.raw,1,15.0\n"
                "frame_00002.raw,2,15.1\n"
                "frame_00003.raw,,\n",
                encoding="utf-8",
            )

            pairs = _load_unique_pairs(run)

        self.assertEqual(pairs, {"frame_00000.raw": (0, 4.2), "frame_00001.raw": (1, 15.0)})


if __name__ == "__main__":
    unittest.main()
