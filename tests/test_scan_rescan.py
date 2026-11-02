from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from cadyx.core import CadyxCore
from cadyx.errors import ConflictError, ValidationError

RUN = 'revopoint_20261010T100000Z'
OTHER = 'revopoint_20261010T110000Z'

class ScanRescanTests(unittest.TestCase):
    def setup_series(self, root):
        core = CadyxCore(root, kernel=object())
        project_id = core.create_project('scan')['project']['id']
        directory = core.workspace._project_dir(project_id)
        for name in (RUN, f'scan_support_{RUN}', OTHER, f'scan_support_{OTHER}'):
            folder = directory / name
            folder.mkdir()
            (folder / 'data.ply').write_text(name)
        (directory / f'scan_support_{RUN}' / '.cadyx-scan360-20261010T100000Z.log').write_text('log')
        (directory / f'{RUN}_old.ply').write_text('legacy')
        return core, project_id, directory

    def test_explicit_rescan_removes_only_selected_series_before_capture(self):
        with tempfile.TemporaryDirectory() as root:
            core, project_id, directory = self.setup_series(root)
            def start(*args, before_capture=None, **kwargs):
                self.assertTrue((directory / RUN).exists())
                before_capture()
                self.assertFalse((directory / RUN).exists())
                self.assertFalse((directory / f'scan_support_{RUN}').exists())
                self.assertFalse((directory / f'{RUN}_old.ply').exists())
                self.assertTrue((directory / OTHER / 'data.ply').is_file())
                self.assertTrue((directory / f'scan_support_{OTHER}' / 'data.ply').is_file())
                return {'state':'running', 'frame_count':24}
            with patch.object(core._scan_capture, 'start', side_effect=start):
                result = core.start_scan_360(project_id, step_degrees=15, replace_run_id=RUN)
            self.assertEqual(result['frame_count'], 24)

    def test_hardware_preflight_failure_preserves_old_series(self):
        with tempfile.TemporaryDirectory() as root:
            core, project_id, directory = self.setup_series(root)
            with patch.object(core._scan_capture, 'start', side_effect=ValidationError('scanner unavailable')):
                with self.assertRaises(ValidationError):
                    core.start_scan_360(project_id, replace_run_id=RUN)
            self.assertTrue((directory / RUN / 'data.ply').is_file())
            self.assertTrue((directory / f'scan_support_{RUN}' / 'data.ply').is_file())

    def test_failed_move_restores_original_files_and_does_not_start_capture(self):
        with tempfile.TemporaryDirectory() as root:
            core, project_id, directory = self.setup_series(root)
            original = Path.rename
            def rename(path, destination):
                if path.name == f'scan_support_{RUN}': raise OSError('injected move failure')
                return original(path, destination)
            def start(*args, before_capture=None, **kwargs):
                before_capture()
                self.fail('capture must not start when old series could not be moved')
            with patch.object(Path, 'rename', rename), patch.object(core._scan_capture, 'start', side_effect=start):
                with self.assertRaises(OSError): core.start_scan_360(project_id, replace_run_id=RUN)
            self.assertTrue((directory / RUN / 'data.ply').is_file())
            self.assertTrue((directory / f'scan_support_{RUN}' / 'data.ply').is_file())
            self.assertFalse(any(path.name.startswith('.cadyx-rescan-') for path in directory.iterdir()))

    def test_referenced_series_cannot_be_deleted(self):
        with tempfile.TemporaryDirectory() as root:
            core, project_id, directory = self.setup_series(root)
            document = SimpleNamespace(objects=[{'type':'feature', 'source':{'project_folder':RUN + '/nested'}}])
            def start(*args, before_capture=None, **kwargs):
                before_capture()
                self.fail('capture must not start')
            with patch('cadyx.core_services.Document.from_validated_dict', return_value=document), \
                 patch.object(core._scan_capture, 'start', side_effect=start):
                with self.assertRaises(ConflictError): core.start_scan_360(project_id, replace_run_id=RUN)
            self.assertTrue((directory / RUN / 'data.ply').is_file())
            self.assertTrue((directory / f'scan_support_{RUN}' / 'data.ply').is_file())

    def test_unknown_internal_file_prevents_deletion(self):
        with tempfile.TemporaryDirectory() as root:
            core, project_id, directory = self.setup_series(root)
            (directory / RUN / '.cadyx-important').write_text('keep')
            def start(*args, before_capture=None, **kwargs):
                before_capture()
                self.fail('capture must not start')
            with patch.object(core._scan_capture, 'start', side_effect=start):
                with self.assertRaises(ValidationError): core.start_scan_360(project_id, replace_run_id=RUN)
            self.assertTrue((directory / RUN / '.cadyx-important').is_file())
            self.assertTrue((directory / f'scan_support_{RUN}' / 'data.ply').is_file())

    def test_invalid_series_id_does_not_remove_files(self):
        with tempfile.TemporaryDirectory() as root:
            core, project_id, directory = self.setup_series(root)
            with self.assertRaises(ValidationError): core.start_scan_360(project_id, replace_run_id='../other')
            self.assertTrue((directory / RUN / 'data.ply').is_file())

if __name__ == '__main__': unittest.main()
