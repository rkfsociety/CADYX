from __future__ import annotations

import unittest

import numpy as np

from tools.revopoint.fuse_continuous_scan import (
    compose_camera_to_reference,
    loop_edge_is_acceptable,
    marker_similarity_matrix,
    rigid_pose_residual,
)


class ContinuousAllFrameFusionTests(unittest.TestCase):
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
