from __future__ import annotations

import unittest

import numpy as np

from tools.revopoint.fuse_continuous_scan import compose_camera_to_reference, marker_similarity_matrix


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


if __name__ == "__main__":
    unittest.main()
