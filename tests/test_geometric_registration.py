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
    def test_pipeline_preserves_fusion_and_publishes_cleaned_candidate_in_staging(self):
        import hashlib
        from tools.revopoint.run_scan_pipeline import run_pipeline

        rng = np.random.default_rng(42)
        points = np.vstack((rng.normal([0, 0, 0], [8, 3, 2], (500, 3)),
                            rng.normal([8, 5, 4], [2, 1, 3], (250, 3))))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_id = 'revopoint_20261010T100000Z'
            series = root / run_id
            series.mkdir()
            (series / f'{run_id}_scan_360.json').write_text(json.dumps({'frame_count': 2}))
            original = []
            for index in range(2):
                cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points + [index, 0, 0]))
                path = series / f'{run_id}_pose_{index:03}.ply'
                self.assertTrue(o3d.io.write_point_cloud(str(path), cloud, write_ascii=True))
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                original.append(digest)
                path.with_name(path.stem + '_cleaned.json').write_text(json.dumps({
                    'format':'cadyx-preprocessed-scan-frame-v1', 'state':'completed',
                    'processing_mode':'in_place', 'source_file':path.name, 'cleaned_file':path.name,
                    'cleaned_sha256':digest, 'cleaned_points':len(points),
                    'passes':[{'isolation_filter':0, 'remove_overlaps':0}]}))
            progress = []
            report = run_pipeline(series, run_id, root / 'staging', progress=lambda *args: progress.append(args))
            self.assertEqual(report['quality'], 'provisional')
            self.assertEqual([x['method'] for x in report['cleanup']], ['isolation_filter', 'remove_overlaps'])
            self.assertEqual([x[:3] for x in progress if x[0] == 'cleanup'],
                             [('cleanup', 0, 2), ('cleanup', 1, 2), ('cleanup', 2, 2)])
            self.assertTrue((root / 'staging' / 'fused.ply').is_file())
            self.assertTrue((root / 'staging' / 'cleaned.ply').is_file())
            for index in range(2):
                self.assertEqual(hashlib.sha256((series / f'{run_id}_pose_{index:03}.ply').read_bytes()).hexdigest(), original[index])
            with self.assertRaises(FileExistsError):
                run_pipeline(series, run_id, root / 'staging')

    def test_background_pipeline_runs_full_series_and_releases_project(self):
        import hashlib
        import time
        from cadyx.core import CadyxCore

        rng = np.random.default_rng(42)
        points = np.vstack((rng.normal([0, 0, 0], [8, 3, 2], (500, 3)),
                            rng.normal([8, 5, 4], [2, 1, 3], (250, 3))))
        run_id = 'revopoint_20261010T100000Z'
        with tempfile.TemporaryDirectory() as temporary:
            core = CadyxCore(str(Path(temporary) / 'projects'), kernel=object())
            project_id = core.create_project('test scan')['project']['id']
            project = core.workspace._project_dir(project_id)
            series = project / run_id
            support = project / f'scan_support_{run_id}'
            series.mkdir(); support.mkdir()
            frames = []
            for index in range(12):
                filename = f'{run_id}_pose_{index:03}.ply'
                path = series / filename
                cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points + [index * .05, 0, 0]))
                self.assertTrue(o3d.io.write_point_cloud(str(path), cloud, write_ascii=True))
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                (support / f'{path.stem}_cleaned.json').write_text(json.dumps({
                    'format':'cadyx-preprocessed-scan-frame-v1', 'state':'completed',
                    'processing_mode':'in_place', 'source_file':filename, 'cleaned_file':filename,
                    'cleaned_sha256':digest, 'cleaned_points':len(points),
                    'passes':[{'isolation_filter':0, 'remove_overlaps':0}]}))
                frames.append({'index':index, 'raw_file':f'{path.stem}.raw', 'ply_file':filename,
                               'metadata_file':f'{path.stem}.json'})
            (support / f'{run_id}_scan_360.json').write_text(json.dumps({
                'format':'revopoint-y16-turntable-capture-v2', 'frame_count':12, 'step_degrees':30,
                'starting_position_preserved':True, 'preprocessing_status':'completed', 'frames':frames}))
            self.assertEqual(core.start_scan_fusion(project_id, run_id)['state'], 'running')
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                status = core.scan_fusion_status(project_id)
                if status['state'] != 'running': break
                time.sleep(.1)
            self.assertEqual(status['state'], 'completed', status)
            for key in ('fused', 'cleaned', 'report'):
                self.assertTrue((series / status['result'][key]).is_file())
            self.assertEqual(core.leases.snapshot(project_id)['state'], 'FREE')

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
            transform = np.asarray(result['frame_to_first_transforms'][1])
            recovered = (points @ rotation.T + [2, -1, .5]) @ transform[:3, :3].T + transform[:3, 3]
            self.assertLess(float(np.median(np.linalg.norm(recovered - points, axis=1))), .15)
            self.assertGreater(result['non_dominant_component_points'], 0)
            self.assertEqual(result['discarded_isolated_points'], 0)
            self.assertFalse(result['preprocessing']['until_both_report_zero_removals'])
            self.assertEqual(result['output_points'], len(o3d.io.read_point_cloud(str(root / 'fused.ply')).points))


if __name__ == '__main__':
    unittest.main()
