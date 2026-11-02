from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np
    import open3d as o3d
except ImportError:
    np = o3d = None


@unittest.skipIf(o3d is None, 'Open3D scan reconstruction environment is unavailable')
class GeometricRegistrationIntegrationTests(unittest.TestCase):
    def test_registered_cloud_retains_non_dominant_samples(self):
        from tools.revopoint.register_point_clouds_first_frame import register_sequence

        rng = np.random.default_rng(42)
        points = np.vstack((rng.normal([0, 0, 0], [8, 3, 2], (500, 3)),
                            rng.normal([8, 5, 4], [2, 1, 3], (250, 3))))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_id = 'revopoint_20261010T100000Z'
            series = root / run_id
            series.mkdir()
            (series / f'{run_id}_scan_360.json').write_text(json.dumps({'frame_count': 2}))
            theta = .18
            rotation = np.array([[np.cos(theta), -np.sin(theta), 0],
                                 [np.sin(theta), np.cos(theta), 0], [0, 0, 1]])
            for index, values in enumerate((points, points @ rotation.T + [2, -1, .5])):
                cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(values))
                path = series / f'{run_id}_pose_{index:03}.ply'
                self.assertTrue(o3d.io.write_point_cloud(str(path), cloud, write_ascii=True))
            result = register_sequence(series, run_id, root / 'fused.ply', 1.0, .5)
            self.assertGreater(result['adjacent_edges'][0]['fitness'], .9)
            self.assertLess(result['last_to_first_local_correction']['rotation_degrees'], 1)
            self.assertGreater(result['non_dominant_component_points'], 0)
            self.assertEqual(result['discarded_isolated_points'], 0)
            self.assertFalse(result['preprocessing']['until_both_report_zero_removals'])
            self.assertEqual(result['output_points'], len(o3d.io.read_point_cloud(str(root / 'fused.ply')).points))


if __name__ == '__main__':
    unittest.main()
