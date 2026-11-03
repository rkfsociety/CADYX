from __future__ import annotations

import unittest
import tempfile
import struct
import sys
from unittest.mock import patch
from pathlib import Path

import numpy as np

from tools.revopoint.clean_continuous_scan import support_mask
from tools.revopoint.fuse_continuous_scan import (
    _center_on_table,
    _marker_plane_below_fraction,
    _read_marker_anchor,
    closure_edge_transform,
    compose_camera_to_reference,
    fuse_all_frames,
    loop_edge_is_acceptable,
    marker_similarity_matrix,
    rigid_pose_residual,
)


class ContinuousAllFrameFusionTests(unittest.TestCase):
    def test_vectorized_reprojection_matches_scalar_including_boundaries(self) -> None:
        from tools.revopoint.reproject_y16 import RAW_BYTES, reproject_y16, reproject_y16_numpy
        raw = bytearray(RAW_BYTES)
        for index, value in enumerate((0, 19999, 20000, 27501, 50000, 50001)):
            struct.pack_into("<H", raw, (index * 4001) * 2, value)
        q = (1.0, 0.1, 0.0, -52.0, 0.02, 1.0, 0.0, -199.0,
             0.0, 0.0, 0.001, 891.0, 0.0, 0.0, 0.0, 1.0)
        np.testing.assert_allclose(reproject_y16_numpy(raw, q, .005), reproject_y16(raw, q, .005))

    def test_frame_support_scales_with_capture_length(self) -> None:
        mask, threshold = support_mask(np.array([0, 5, 11, 12, 30]), 596)
        self.assertEqual(threshold, 12)
        np.testing.assert_array_equal(mask, [False, False, False, True, True])
        self.assertEqual(support_mask(np.array([1, 2]), 12)[1], 2)

    def test_cancelled_registration_preserves_sources_without_writing_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            signal = root / "stop"
            signal.touch()
            output = root / "candidate.ply"
            with patch.dict(sys.modules, {"open3d": object()}):
                with self.assertRaisesRegex(RuntimeError, "cancelled during registration"):
                    fuse_all_frames(root, output, stop_path=signal)
            self.assertFalse(output.exists())

    def test_missing_marker_alignment_does_not_block_fusion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(_read_marker_anchor(Path(directory)))

    def test_marker_plane_validation_detects_lost_geometry(self) -> None:
        points = np.array([[0, 0, -20], [1, 1, -5], [0, 0, 0], [0, 0, 10]], dtype=float)
        self.assertEqual(_marker_plane_below_fraction(points), 0.5)

    def test_fallback_centers_bounds_and_places_bottom_on_xy(self) -> None:
        lower = np.array([-8.0, 2.0, -3.0])
        upper = np.array([4.0, 10.0, 5.0])
        shift = _center_on_table(lower, upper)[:3, 3]
        np.testing.assert_allclose((lower + shift + upper + shift)[:2] / 2, [0.0, 0.0])
        self.assertEqual((lower + shift)[2], 0.0)

    def test_composes_current_camera_to_reference_from_neighbor_pose(self) -> None:
        previous = np.eye(4)
        previous[0, 3] = 10.0
        current_to_previous = np.eye(4)
        current_to_previous[1, 3] = 2.0
        result = compose_camera_to_reference(previous, current_to_previous)
        np.testing.assert_allclose(result[:3, 3], [10.0, 2.0, 0.0])

    def test_table_marker_similarity_maps_plane_and_normal_once(self) -> None:
        frame = {
            "scanner_table_plane_origin": [1.0, 2.0, 3.0],
            "scanner_table_plane_axes": {
                "x": [1.0, 0.0, 0.0], "y": [0.0, 1.0, 0.0], "z": [0.0, 0.0, 1.0],
            },
            "scanner_to_map_scale": 2.0,
        }
        transform = {"a": 2.0, "b": 0.0, "c": 0.0, "d": 2.0, "tx": 3.0, "ty": 4.0}
        matrix = marker_similarity_matrix(frame, transform)
        point = np.array([2.0, 3.0, 4.0, 1.0])
        np.testing.assert_allclose(matrix @ point, [5.0, 6.0, 2.0, 1.0])

    def test_closure_correction_is_converted_into_target_frame(self) -> None:
        source = np.eye(4); source[0, 3] = 10
        target = np.eye(4); target[0, 3] = 3
        correction = np.eye(4); correction[0, 3] = -2
        np.testing.assert_allclose(closure_edge_transform(correction, source, target)[:3, 3], [5, 0, 0])

    def test_loop_closure_threshold_rejects_weak_or_high_residual_matches(self) -> None:
        self.assertTrue(loop_edge_is_acceptable(0.75, 1.0, 0.6, 3.0))
        self.assertFalse(loop_edge_is_acceptable(0.55, 1.0, 0.6, 3.0))
        self.assertFalse(loop_edge_is_acceptable(0.9, 3.1, 0.6, 3.0))

    def test_pose_graph_edge_residual_uses_source_to_target_transform_direction(self) -> None:
        target_pose = np.eye(4)
        source_pose = np.eye(4)
        source_pose[0, 3] = 5.0
        measured = np.eye(4)
        measured[0, 3] = 5.0

        self.assertEqual(rigid_pose_residual(measured, source_pose, target_pose), (0.0, 0.0))
        rotation, translation = rigid_pose_residual(np.eye(4), source_pose, target_pose)
        self.assertEqual(rotation, 0.0)
        self.assertEqual(translation, 5.0)


if __name__ == "__main__":
    unittest.main()
