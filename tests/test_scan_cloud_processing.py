from __future__ import annotations
import hashlib
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from cadyx.core import CadyxCore
from cadyx.errors import ConflictError, ValidationError

FOLDER = 'Сшитые серии'


def ply_bytes(points: list[tuple[float, float, float]]) -> bytes:
    header = ('ply\nformat binary_little_endian 1.0\n' f'element vertex {len(points)}\n'
              'property float x\nproperty float y\nproperty float z\nend_header\n').encode('ascii')
    return header + b''.join(struct.pack('<fff', *point) for point in points)


def cloud_with_floaters(count: int = 1500, floaters: int = 30,
                        layer: int = 40) -> list[tuple[float, float, float]]:
    points = []
    for index in range(count):
        angle = index * 0.35
        radius = 8 + (index % 50) * 0.1
        points.append((radius * math.cos(angle), radius * math.sin(angle), 0.1 * math.sin(angle * 3)))
    # A redundant near-duplicate layer within the overlap tolerances.
    for index in range(layer):
        x, y, z = points[index]
        points.append((x, y, z + 0.05))
    for index in range(floaters):
        points.append((90.0 + index, 90.0, 40.0))
    return points


class ProcessStitchedCloudWorkerTests(unittest.TestCase):
    def test_cleanup_cycle_removes_floaters_and_publishes_report(self):
        points = cloud_with_floaters()
        floaters = 30
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'stitched.ply'
            source.write_bytes(ply_bytes(points))
            staging = root / 'staging'
            result = subprocess.run(
                [sys.executable, '-u', 'tools/revopoint/process_stitched_cloud.py',
                 '--source', str(source), '--filename', 'stitched.ply', '--staging', str(staging)],
                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((staging / 'report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['format'], 'cadyx-cloud-processing-v1')
            self.assertEqual(report['state'], 'completed')
            self.assertEqual(report['input_points'], len(points))
            self.assertEqual(report['output_points'], report['stages'][-1]['output_points'])
            self.assertEqual([stage['method'] for stage in report['stages']],
                             ['isolation_filter', 'remove_overlaps'])
            self.assertGreaterEqual(report['stages'][0]['removed_points'], floaters)
            self.assertEqual(report['stages'][0]['output_points'],
                             report['stages'][0]['input_points'] - report['stages'][0]['removed_points'])
            self.assertLess(report['stages'][1]['output_points'], report['stages'][1]['input_points'])
            self.assertEqual(report['output']['sha256'],
                             hashlib.sha256((staging / 'processed.ply').read_bytes()).hexdigest())
            header = (staging / 'processed.ply').read_bytes().split(b'end_header\n')[0]
            self.assertIn(f'element vertex {report["output_points"]}'.encode('ascii'), header)

    def test_invalid_source_fails_without_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'broken.ply'
            source.write_bytes(b'not a ply')
            staging = root / 'staging'
            result = subprocess.run(
                [sys.executable, '-u', 'tools/revopoint/process_stitched_cloud.py',
                 '--source', str(source), '--filename', 'broken.ply', '--staging', str(staging)],
                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=120)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('"state": "failed"', result.stdout)
            self.assertFalse((staging / 'report.json').exists())


class ScanCloudProcessingCoreTests(unittest.TestCase):
    def test_missing_and_invalid_sources_are_rejected_before_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            core = CadyxCore(temp, kernel=object())
            project_id = core.create_project('scan')['project']['id']
            with self.assertRaises(ValidationError):
                core.start_cloud_processing(project_id, None, 'missing.ply')
            with self.assertRaises(ValidationError):
                core.start_cloud_processing(project_id, None, '../escape.ply')
            with self.assertRaises(ValidationError):
                core.start_cloud_processing(project_id, None, 'cloud.json')
            self.assertFalse(core._scan_cloud_processing.is_running(project_id))
            self.assertEqual(core.cloud_processing_status(project_id)['state'], 'idle')

    def test_deleted_source_clears_finished_status(self):
        with tempfile.TemporaryDirectory() as temp:
            core = CadyxCore(temp, kernel=object())
            project_id = core.create_project('scan')['project']['id']
            project = core.workspace._project_dir(project_id)
            (project / 'stitched.ply').write_bytes(ply_bytes(cloud_with_floaters(50, 0)))
            core._scan_cloud_processing._jobs[project_id] = {'state': 'completed', 'file': 'stitched.ply'}
            self.assertEqual(core.cloud_processing_status(project_id)['state'], 'completed')
            (project / 'stitched.ply').unlink()
            self.assertEqual(core.cloud_processing_status(project_id)['state'], 'idle')

    def test_running_job_blocks_second_launch_and_file_operations(self):
        class FakeProcess:
            def __init__(self):
                self.stdout = self
                self.ready = threading.Event()
            def __iter__(self):
                yield json.dumps({'phase': 'isolation_filter', 'done': 1, 'total': 3})
                self.ready.wait(5)
                yield json.dumps({'state': 'failed', 'error': 'intentional failure'})
            def wait(self): return 1
            def poll(self): return 1
            def close(self): pass
        with tempfile.TemporaryDirectory() as temp:
            core = CadyxCore(temp, kernel=object())
            project_id = core.create_project('scan')['project']['id']
            project = core.workspace._project_dir(project_id)
            (project / 'stitched.ply').write_bytes(ply_bytes(cloud_with_floaters(50, 0)))
            proc = FakeProcess()
            fake_repository = Path(temp) / 'fake-worker'
            fake_worker = fake_repository / 'tools' / 'revopoint' / 'process_stitched_cloud.py'
            fake_worker.parent.mkdir(parents=True)
            fake_worker.touch()
            core._scan_cloud_processing._repository = fake_repository
            with patch('cadyx.scan_cloud_processing.subprocess.Popen', return_value=proc):
                result = core.start_cloud_processing(project_id, None, 'stitched.ply')
                self.assertEqual(result['state'], 'running')
                self.assertTrue(core._project_availability(project_id).running_operation)
                other_core = CadyxCore(temp, kernel=object())
                self.assertEqual(other_core.leases.snapshot(project_id)['state'], 'SYSTEM_LOCKED')
                with self.assertRaises(ConflictError):
                    other_core.remove_project_files(project_id, ['stitched.ply'])
                with self.assertRaises(ConflictError):
                    core.start_cloud_processing(project_id, None, 'stitched.ply')
                proc.ready.set()
                limit = time.monotonic() + 5
                while core._scan_cloud_processing.is_running(project_id) and time.monotonic() < limit:
                    time.sleep(.01)
                status = core.cloud_processing_status(project_id)
                self.assertEqual(status['state'], 'failed')
                self.assertEqual(status['error'], 'intentional failure')
                self.assertFalse(core._project_availability(project_id).running_operation)
                self.assertEqual(other_core.leases.snapshot(project_id)['state'], 'FREE')

    def test_publication_verifies_source_and_never_overwrites(self):
        def sha(data: bytes) -> str: return hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            core = CadyxCore(str(root / 'projects'), kernel=object())
            project_id = core.create_project('scan')['project']['id']
            folder = core.workspace._project_dir(project_id) / FOLDER
            folder.mkdir()
            source_data = ply_bytes(cloud_with_floaters(60, 0))
            (folder / 'stitched.ply').write_bytes(source_data)
            lease = core.acquire_lease(project_id, 'system', 'test-processing')['lease_id']
            stage = root / 'stage'
            stage.mkdir()
            (stage / 'processed.ply').write_bytes(b'processed')
            report = {'format': 'cadyx-cloud-processing-v1', 'state': 'completed',
                      'source_file': 'stitched.ply', 'source_sha256': sha(source_data),
                      'quality': 'provisional', 'input_points': 60, 'output_points': 58,
                      'removed_points': 2, 'stages': [],
                      'output': {'file': 'processed.ply', 'sha256': sha(b'processed')}}
            (stage / 'report.json').write_text(json.dumps(report))
            (folder / 'stitched.ply').write_bytes(b'changed')
            with self.assertRaises(ValidationError):
                core._lifecycle.publish_cloud_processing(project_id, folder, 'stitched.ply', stage, lease)
            self.assertEqual(list(folder.glob('*.cadyx-*')), [])
            (folder / 'stitched.ply').write_bytes(source_data)
            original_link = os.link
            calls = []
            def fail_second_link(source, target):
                calls.append(target)
                if len(calls) == 2:
                    raise OSError('simulated publication failure')
                return original_link(source, target)
            with patch('cadyx.core_services.os.link', side_effect=fail_second_link):
                with self.assertRaises(ValidationError):
                    core._lifecycle.publish_cloud_processing(project_id, folder, 'stitched.ply', stage, lease)
            self.assertEqual(list(folder.glob('*.cadyx-*')), [])
            (stage / 'published-report.json').unlink()
            result = core._lifecycle.publish_cloud_processing(project_id, folder, 'stitched.ply', stage, lease)
            self.assertEqual((folder / result['file']).read_bytes(), b'processed')
            self.assertTrue((folder / result['report']).is_file())
            self.assertEqual((folder / 'stitched.ply').read_bytes(), source_data)
            self.assertTrue(result['file'].endswith(f"_processed.cadyx-{sha(b'processed')[:12]}.ply"))
            with self.assertRaises(ValidationError):
                core._lifecycle.publish_cloud_processing(project_id, folder, 'stitched.ply', stage, lease)
            self.assertEqual((folder / result['file']).read_bytes(), b'processed')


if __name__ == '__main__': unittest.main()
