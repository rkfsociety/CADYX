from __future__ import annotations
import hashlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from cadyx.core import CadyxCore
from cadyx.errors import ConflictError, ValidationError

RUN = 'revopoint_20261010T100000Z'


def make_manifest(directory: Path) -> None:
    support = directory / f'scan_support_{RUN}'
    support.mkdir()
    frames = [{'index': i, 'raw_file': f'{RUN}_pose_{i:03}.raw',
               'ply_file': f'{RUN}_pose_{i:03}.ply',
               'metadata_file': f'{RUN}_pose_{i:03}.json'} for i in range(12)]
    (support / f'{RUN}_scan_360.json').write_text(json.dumps({
        'format':'revopoint-y16-turntable-capture-v2', 'frame_count':12, 'step_degrees':30,
        'starting_position_preserved':True, 'preprocessing_status':'completed', 'frames':frames}))


class ScanFusionQualityTests(unittest.TestCase):
    def test_geometry_failure_requests_rescan_without_automatic_capture(self):
        from tools.revopoint import run_scan_pipeline
        from tools.revopoint.register_point_clouds_first_frame import RegistrationInsufficientError
        stderr = io.StringIO()
        args = ['pipeline', '--series', '.', '--run-id', RUN, '--staging', 'candidate']
        with (patch.object(run_scan_pipeline, 'run_pipeline', side_effect=RegistrationInsufficientError('ambiguous')),
              patch('sys.argv', args), patch('sys.stderr', stderr)):
            self.assertEqual(run_scan_pipeline.main(), 1)
        result = json.loads(stderr.getvalue())
        self.assertEqual(result['error_code'], 'SCAN_RECAPTURE_REQUIRED')
        self.assertIn('том же положении', result['next_action'])

    def test_large_loop_disagreement_requires_review(self):
        from tools.revopoint.run_scan_pipeline import assess_registration
        report = {'registration_dimensions_q':[111.5, 107.8, 103.8],
                  'last_to_first_local_correction':{'rotation_degrees':74.7,
                      'translation_q':[68.8, -141.4, -34.3]},
                  'last_to_first_direct_overlap':{'source_to_target_within_5':.06,
                      'target_to_source_within_5':.023}}
        self.assertEqual(len(assess_registration(report)), 3)
        report['last_to_first_local_correction'] = {'rotation_degrees':.2, 'translation_q':[.1, .2, .3]}
        report['last_to_first_direct_overlap'] = {'source_to_target_within_5':.8, 'target_to_source_within_5':.7}
        self.assertEqual(assess_registration(report), [])
        self.assertEqual(assess_registration({}), ['Недостаточно данных для проверки замыкания серии.'])


class ScanFusionCoreTests(unittest.TestCase):
    def test_incomplete_series_is_rejected_before_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            core = CadyxCore(temp, kernel=object())
            project_id = core.create_project('scan')['project']['id']
            project = core.workspace._project_dir(project_id)
            (project / RUN).mkdir()
            with self.assertRaises(ValidationError):
                core.start_scan_fusion(project_id, RUN)
            self.assertFalse(core._scan_fusion.is_running(project_id))

    def test_running_job_blocks_project_file_operations(self):
        class FakeProcess:
            def __init__(self):
                self.stdout = self
                self.ready = threading.Event()
            def __iter__(self):
                yield json.dumps({'phase':'register', 'done':1, 'total':11, 'file':'frame.ply'})
                self.ready.wait(5)
                yield json.dumps({'state':'failed', 'error':'intentional failure', 'error_code':'SCAN_RECAPTURE_REQUIRED', 'next_action':'Повторите съёмку в том же положении.'})
            def wait(self): return 1
            def poll(self): return 1
            def close(self): pass
        with tempfile.TemporaryDirectory() as temp:
            core = CadyxCore(temp, kernel=object())
            project_id = core.create_project('scan')['project']['id']
            project = core.workspace._project_dir(project_id)
            (project / RUN).mkdir()
            make_manifest(project)
            proc = FakeProcess()
            # This test mocks the fusion worker, so provide a matching fake
            # runtime layout rather than depending on a locally installed Open3D.
            fake_repository = Path(temp) / 'fake-worker'
            fake_python = fake_repository / '.runtime' / 'scan-reconstruction' / 'venv' / (
                'Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            fake_worker = fake_repository / 'tools' / 'revopoint' / 'run_scan_pipeline.py'
            for path in (fake_python, fake_worker):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            core._scan_fusion._repository = fake_repository
            with patch('cadyx.scan_fusion.subprocess.Popen', return_value=proc):
                result = core.start_scan_fusion(project_id, RUN)
                self.assertEqual(result['state'], 'running')
                self.assertTrue(core._project_availability(project_id).running_operation)
                other_core = CadyxCore(temp, kernel=object())
                self.assertEqual(other_core.leases.snapshot(project_id)['state'], 'SYSTEM_LOCKED')
                with self.assertRaises(ConflictError):
                    other_core.remove_project_subfolder(project_id, RUN)
                with self.assertRaises(ConflictError):
                    core.start_scan_fusion(project_id, RUN)
                with self.assertRaises(ConflictError):
                    core.remove_project_subfolder(project_id, RUN)
                proc.ready.set()
                limit = time.monotonic() + 5
                while core._scan_fusion.is_running(project_id) and time.monotonic() < limit:
                    time.sleep(.01)
                status = core.scan_fusion_status(project_id)
                self.assertEqual(status['state'], 'failed')
                self.assertEqual(status['error'], 'intentional failure')
                self.assertEqual(status['error_code'], 'SCAN_RECAPTURE_REQUIRED')
                self.assertIn('том же положении', status['next_action'])
                self.assertFalse(core._project_availability(project_id).running_operation)

    def test_publication_verifies_sources_and_never_overwrites(self):
        def sha(data): return hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            core = CadyxCore(str(root / 'projects'), kernel=object())
            project_id = core.create_project('scan')['project']['id']
            series = core.workspace._project_dir(project_id) / RUN
            series.mkdir()
            inputs = {f'{RUN}_pose_{i:03}.ply': f'input{i}'.encode() for i in range(2)}
            for name, data in inputs.items(): (series / name).write_bytes(data)
            lease = core.acquire_lease(project_id, 'system', 'test-fusion')['lease_id']
            stage = root / 'stage'
            stage.mkdir()
            for name in ('fused', 'cleaned'): (stage / f'{name}.ply').write_bytes(name.encode())
            report = {'format':'cadyx-scan-pipeline-v1', 'state':'completed', 'quality':'provisional',
                      'run_id':RUN, 'input_sha256':{name:sha(data) for name,data in inputs.items()},
                      **{name:{'file':f'{name}.ply', 'sha256':sha(name.encode())} for name in ('fused','cleaned')}}
            (stage / 'pipeline.json').write_text(json.dumps(report))
            (series / next(iter(inputs))).write_bytes(b'changed')
            with self.assertRaises(ValidationError): core._lifecycle.publish_scan_fusion(project_id, RUN, stage, lease)
            self.assertEqual(len(list(series.glob('*.cadyx-*.ply'))), 0)
            for name, data in inputs.items(): (series / name).write_bytes(data)
            original_link = os.link
            calls = []
            def fail_second_link(source, target):
                calls.append(target)
                if len(calls) == 2:
                    raise OSError('simulated publication failure')
                return original_link(source, target)
            with patch('cadyx.core_services.os.link', side_effect=fail_second_link):
                with self.assertRaises(ValidationError): core._lifecycle.publish_scan_fusion(project_id, RUN, stage, lease)
            self.assertEqual(len(list(series.glob('*.cadyx-*.ply'))), 0)
            (stage / 'published-report.json').unlink()
            result = core._lifecycle.publish_scan_fusion(project_id, RUN, stage, lease)
            self.assertEqual((series / result['fused']).read_bytes(), b'fused')
            self.assertEqual((series / result['cleaned']).read_bytes(), b'cleaned')
            self.assertTrue((series / result['report']).is_file())
            with self.assertRaises(ValidationError): core._lifecycle.publish_scan_fusion(project_id, RUN, stage, lease)
            self.assertEqual((series / result['fused']).read_bytes(), b'fused')


if __name__ == '__main__': unittest.main()
