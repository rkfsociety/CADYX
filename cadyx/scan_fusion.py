"""Background, project-scoped scanner registration jobs.

The worker writes only to private staging; the Core publishes verified artifacts.
"""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .errors import ConflictError, ValidationError

_RUN_ID = re.compile(r'^revopoint_\d{8}T\d{6}Z$')
_PHASES = {'load', 'register', 'loops', 'optimize', 'merge', 'cleanup'}


class ScanFusionJobs:
    def __init__(self, repository: Path, state_root: Path,
                 publish: Callable[[str, str, Path, str], dict[str, Any]],
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

    def status(self, project_id: str) -> dict[str, Any]:
        with self._guard:
            return copy.deepcopy(self._jobs.get(project_id, {'state': 'idle'}))

    def start(self, project_id: str, project_directory: Path, run_id: str,
              lease_id: str, heartbeat: Any) -> dict[str, Any]:
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise ValidationError('Укажите корректную серию сканирования.')
        project = project_directory.resolve(strict=True)
        candidate = project / run_id
        if candidate.is_symlink() or not candidate.is_dir() or candidate.resolve().parent != project:
            raise ValidationError('Серия сканирования не найдена в проекте.')
        series = candidate.resolve(strict=True)
        support = project / f'scan_support_{run_id}'
        manifest = support / f'{run_id}_scan_360.json'
        if (support.is_symlink() or manifest.is_symlink() or not support.is_dir()
                or support.resolve().parent != project):
            raise ValidationError('Отчёт полной серии сканирования недоступен.')
        try:
            from .scan_capture import _capture_manifest_frames_valid
            info = json.loads(manifest.read_text(encoding='utf-8'))
            count = info['frame_count']
            step = info['step_degrees']
            if (info.get('format') != 'revopoint-y16-turntable-capture-v2'
                    or type(count) is not int or type(step) is not int
                    or not 1 <= step <= 30 or count * step != 360
                    or info.get('starting_position_preserved') is not True
                    or info.get('preprocessing_status') != 'completed'
                    or not _capture_manifest_frames_valid(info, run_id, count)):
                raise ValueError('invalid completed capture')
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ValidationError('Сшивка требует полной серии 360° с завершённой очисткой кадров.') from exc
        python = self._repository / '.runtime' / 'scan-reconstruction' / 'venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        worker = self._repository / 'tools' / 'revopoint' / 'run_scan_pipeline.py'
        if not python.is_file() or not worker.is_file():
            raise ValidationError('Среда сшивки Open3D недоступна; настройте среду обработки сканов.')
        with self._guard:
            if self._jobs.get(project_id, {}).get('state') == 'running':
                raise ConflictError('В проекте уже выполняется сшивка.', code='PROJECT_BUSY')
            job_id = uuid4().hex
            staging = self._state_root / 'scan-fusion' / project_id / job_id
            command = [str(python), '-u', str(worker), '--series', str(series), '--run-id', run_id,
                       '--staging', str(staging)]
            env = dict(os.environ, PYTHONIOENCODING='utf-8')
            env.update(OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2', MKL_NUM_THREADS='2')
            try:
                process = subprocess.Popen(command, cwd=self._repository, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                           errors='replace', env=env,
                                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except OSError as exc:
                raise ValidationError(f'Не удалось запустить сшивку: {exc}') from exc
            self._jobs[project_id] = {'id': job_id, 'state': 'running', 'run_id': run_id,
                                      'phase': 'starting', 'done': 0, 'total': None,
                                      'file': None, 'result': None, 'error': None,
                                      'error_code': None, 'next_action': None}
            thread = threading.Thread(target=self._watch, args=(project_id, run_id, staging, process, lease_id, heartbeat),
                                      name=f'cad-scan-fusion-{job_id[:8]}', daemon=True)
            try:
                thread.start()
            except RuntimeError as exc:
                process.terminate()
                process.wait()
                self._jobs[project_id].update(state='failed', error=str(exc))
                raise ValidationError('Не удалось запустить наблюдение за сшивкой.') from exc
            return copy.deepcopy(self._jobs[project_id])

    def _watch(self, project_id: str, run_id: str, staging: Path, process: subprocess.Popen[str],
               lease_id: str, heartbeat: Any) -> None:
        error = None
        error_code = None
        next_action = None
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
                    if value.get('error_code') == 'SCAN_RECAPTURE_REQUIRED':
                        error_code = value['error_code']
                        if isinstance(value.get('next_action'), str):
                            next_action = value['next_action'][:1000]
                phase = value.get('phase')
                done, total = value.get('done'), value.get('total')
                if (isinstance(phase, str) and phase in _PHASES and type(done) is int
                        and type(total) is int and 0 <= done <= total <= 10000):
                    file = value.get('file')
                    with self._guard:
                        self._jobs[project_id].update(phase=phase, done=done, total=total,
                                                      file=file if isinstance(file, str) else None)
            code = process.wait()
            if code:
                raise RuntimeError(error or f'Процесс сшивки завершился с кодом {code}.')
            heartbeat.check()
            with self._guard:
                self._jobs[project_id].update(phase='publishing', done=0, total=None, file=None)
            result = self._publish(project_id, run_id, staging, lease_id)
        except Exception as exc:
            try:
                if process.poll() is None:
                    process.terminate()
                    process.wait()
            except OSError:
                # The worker may have exited between poll and terminate.
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
                    self._jobs[project_id].update(state='failed', error=error, error_code=error_code,
                                                  next_action=next_action)
                else:
                    self._jobs[project_id].update(state='completed', phase='completed', result=result)
