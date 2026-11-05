"""Background, project-scoped stitched point-cloud cleanup jobs.

The worker writes only to private staging; the Core publishes verified artifacts
next to the untouched source cloud.
"""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .errors import ConflictError, ValidationError

_FILENAME = re.compile(r'^[^\\/:*?"<>|\x00-\x1f]{1,200}\.ply$')
_PHASES = {'load', 'isolation_filter', 'remove_overlaps', 'write'}


class ScanCloudProcessingJobs:
    def __init__(self, repository: Path, state_root: Path,
                 publish: Callable[[str, Path, str, Path, str], dict[str, Any]],
                 release: Callable[[str, str], Any]) -> None:
        self._repository = repository
        self._state_root = state_root
        self._publish = publish
        self._release = release
        self._guard = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def is_running(self, project_id: str, *, nonblocking: bool = False) -> bool:
        if not self._guard.acquire(blocking=not nonblocking):
            return True
        try:
            return self._jobs.get(project_id, {}).get('state') == 'running'
        finally:
            self._guard.release()

    def status(self, project_id: str, project_directory: Path) -> dict[str, Any]:
        with self._guard:
            job = self._jobs.get(project_id, {'state': 'idle'})
            if job.get('state') in {'completed', 'failed'} and not (project_directory / job['file']).is_file():
                return {'state': 'idle'}
            return copy.deepcopy(job)

    def start(self, project_id: str, project_directory: Path, folder_name: str | None,
              filename: str, lease_id: str, heartbeat: Any) -> dict[str, Any]:
        if not isinstance(filename, str) or not _FILENAME.fullmatch(filename):
            raise ValidationError('Укажите корректный PLY-файл облака точек.')
        project = project_directory.resolve(strict=True)
        folder = project if folder_name is None else self._subfolder(project, folder_name)
        source = folder / filename
        if (source.is_symlink() or not source.is_file() or source.resolve(strict=True).parent != folder
                or source.suffix.casefold() != '.ply'):
            raise ValidationError('Облако точек не найдено в проекте.')
        worker = self._repository / 'tools' / 'revopoint' / 'process_stitched_cloud.py'
        if not worker.is_file():
            raise ValidationError('Инструмент обработки облаков недоступен.')
        with self._guard:
            if self._jobs.get(project_id, {}).get('state') == 'running':
                raise ConflictError('В проекте уже выполняется обработка облака.', code='PROJECT_BUSY')
            job_id = uuid4().hex
            staging = self._state_root / 'scan-cloud-processing' / project_id / job_id
            command = [sys.executable, '-u', str(worker), '--source', str(source),
                       '--filename', filename, '--staging', str(staging)]
            env = dict(os.environ, PYTHONIOENCODING='utf-8')
            env.update(OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2', MKL_NUM_THREADS='2')
            try:
                process = subprocess.Popen(command, cwd=self._repository, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                           errors='replace', env=env,
                                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except OSError as exc:
                raise ValidationError(f'Не удалось запустить обработку облака: {exc}') from exc
            self._jobs[project_id] = {'id': job_id, 'state': 'running', 'file': filename,
                                      'folder_name': folder_name, 'phase': 'starting',
                                      'done': 0, 'total': None, 'result': None, 'error': None,
                                      'error_code': None, 'next_action': None}
            thread = threading.Thread(target=self._watch,
                                      args=(project_id, folder, filename, staging, process, lease_id, heartbeat),
                                      name=f'cad-scan-cloud-{job_id[:8]}', daemon=True)
            try:
                thread.start()
            except RuntimeError as exc:
                process.terminate()
                process.wait()
                self._jobs[project_id].update(state='failed', error=str(exc))
                raise ValidationError('Не удалось запустить наблюдение за обработкой.') from exc
            return copy.deepcopy(self._jobs[project_id])

    @staticmethod
    def _subfolder(directory: Path, folder_name: str) -> Path:
        if not isinstance(folder_name, str) or '\x00' in folder_name or ':' in folder_name:
            raise ValidationError('Папка облака не найдена в проекте.')
        parts = folder_name.replace('\\', '/').split('/')
        if not parts or any(part in {'', '.', '..'} or part.startswith('.') for part in parts):
            raise ValidationError('Папка облака не найдена в проекте.')
        current = directory.resolve(strict=True)
        for part in parts:
            folder = current / part
            if folder.is_symlink() or not folder.is_dir():
                raise ValidationError('Папка облака не найдена в проекте.')
            resolved = folder.resolve(strict=True)
            if resolved.parent != current:
                raise ValidationError('Папка облака не найдена в проекте.')
            current = resolved
        return current

    def _watch(self, project_id: str, folder: Path, filename: str, staging: Path,
               process: subprocess.Popen[str], lease_id: str, heartbeat: Any) -> None:
        error = None
        result = None
        try:
            assert process.stdout is not None
            for line in process.stdout:
                try:
                    value = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(value, dict):
                    continue
                if value.get('state') == 'failed' and isinstance(value.get('error'), str):
                    error = value['error'][:2000]
                phase = value.get('phase')
                done, total = value.get('done'), value.get('total')
                if (isinstance(phase, str) and phase in _PHASES and type(done) is int
                        and type(total) is int and 0 <= done <= total <= 10000):
                    with self._guard:
                        self._jobs[project_id].update(phase=phase, done=done, total=total)
            code = process.wait()
            if code:
                raise RuntimeError(error or f'Процесс обработки завершился с кодом {code}.')
            heartbeat.check()
            with self._guard:
                self._jobs[project_id].update(phase='publishing', done=0, total=None)
            result = self._publish(project_id, folder, filename, staging, lease_id)
        except Exception as exc:
            try:
                if process.poll() is None:
                    process.terminate()
                    process.wait()
            except OSError:
                pass
            error = str(exc)[:2000]
        finally:
            if process.stdout is not None:
                process.stdout.close()
            heartbeat.stop()
            try:
                self._release(project_id, lease_id)
            except Exception as exc:
                error = error or f'Не удалось освободить блокировку проекта: {exc}'
            with self._guard:
                if error:
                    self._jobs[project_id].update(state='failed', error=error)
                else:
                    self._jobs[project_id].update(state='completed', phase='completed', result=result)
