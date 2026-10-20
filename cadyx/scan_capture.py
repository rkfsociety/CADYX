"""Launch and observe the repository's direct-UVC 360-degree capture workflow."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import ConflictError, ValidationError

_FRAME_COUNT = 12


@dataclass
class _Scan360Job:
    project_id: str
    project_directory: Path
    run_id: str
    process: subprocess.Popen[bytes]
    log_path: Path
    started_at: str


class Scan360CaptureController:
    """Own one active physical 360 capture and expose compact progress."""

    def __init__(self, repository_root: Path, workspace_root: Path) -> None:
        self._repository_root = repository_root.resolve()
        self._workspace_root = workspace_root.resolve()
        self._guard = threading.RLock()
        self._job: _Scan360Job | None = None

    def start(self, project_id: str, project_directory: Path) -> dict[str, Any]:
        if os.name != "nt":
            raise ValidationError("360-degree Revopoint capture is available only on the Windows scanner host.")
        project_directory = project_directory.resolve(strict=True)
        if project_directory.parent != self._workspace_root:
            raise ValidationError("The scan target must be a direct CADYX project directory.")
        script = self._repository_root / "tools" / "revopoint" / "scan_360.ps1"
        if not script.is_file():
            raise ValidationError("The CADYX 360 scan script is unavailable.")
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell is None:
            raise ValidationError("Windows PowerShell is unavailable on the CADYX scanner host.")

        with self._guard:
            if self._job is not None and self._job.process.poll() is None:
                if self._job.project_id == project_id:
                    return self._snapshot_locked()
                raise ConflictError("The scanner is already capturing a 360 scan for another project.", code="SCANNER_BUSY")
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            run_id = f"revopoint_{stamp}"
            log_path = project_directory / f".cadyx-scan360-{stamp}.log"
            startup_info = subprocess.STARTUPINFO()
            startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup_info.wShowWindow = subprocess.SW_HIDE
            try:
                with log_path.open("xb") as log_stream:
                    process = subprocess.Popen(
                        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                         "-File", str(script), "-ProjectPath", str(project_directory), "-RunId", run_id],
                        cwd=str(self._repository_root), stdin=subprocess.DEVNULL,
                        stdout=log_stream, stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=startup_info,
                    )
            except FileExistsError as exc:
                raise ConflictError("A scan log already exists for this capture; no files were replaced.", code="SCAN_LOG_EXISTS") from exc
            except OSError as exc:
                raise ValidationError(f"The CADYX 360 scan could not be started: {exc}.") from exc
            self._job = _Scan360Job(
                project_id=project_id,
                project_directory=project_directory,
                run_id=run_id,
                process=process,
                log_path=log_path,
                started_at=datetime.now(timezone.utc).isoformat(),
            )
            return self._snapshot_locked()

    def status(self, project_id: str, project_directory: Path) -> dict[str, Any]:
        with self._guard:
            if self._job is None:
                return self._latest_completed_snapshot(project_directory)
            if self._job.project_id != project_id:
                if self._job.process.poll() is None:
                    return {"state": "busy", "completed_frames": 0, "frame_count": _FRAME_COUNT}
                return self._latest_completed_snapshot(project_directory)
            return self._snapshot_locked()

    def is_running(self, project_id: str) -> bool:
        with self._guard:
            return bool(
                self._job is not None
                and self._job.project_id == project_id
                and self._job.process.poll() is None
            )

    def _snapshot_locked(self) -> dict[str, Any]:
        assert self._job is not None
        return_code = self._job.process.poll()
        state = "running" if return_code is None else "completed" if return_code == 0 else "failed"
        try:
            log_tail = self._job.log_path.read_bytes()[-64 * 1024:].decode("utf-8", errors="replace")
        except OSError:
            log_tail = ""
        completed = 0
        for value in re.findall(r"captured pose (\d+)/11", log_tail, flags=re.IGNORECASE):
            completed = max(completed, int(value) + 1)
        completed = max(completed, self._count_run_frames(self._job.project_directory, self._job.run_id))
        capture_sweep_completed = "sweep completed: 12 steps" in log_tail.lower()
        metadata_warning = state == "failed" and completed == _FRAME_COUNT and capture_sweep_completed
        if state == "completed" or metadata_warning:
            completed = _FRAME_COUNT
            state = "completed"
        message = (f"Кадр {completed}/{_FRAME_COUNT}" if state == "running" else
                   "12 кадров сохранены. Запись манифеста завершилась ошибкой; журнал поз сохранён." if metadata_warning else
                   "Съёмка 360° завершена; кадры сохранены в проекте." if state == "completed" else
                   f"Съёмка завершилась с ошибкой (код {return_code}); исходные кадры, если они успели сохраниться, оставлены в проекте.")
        return {
            "state": state,
            "completed_frames": completed,
            "frame_count": _FRAME_COUNT,
            "started_at": self._job.started_at,
            "message": message,
        }

    @staticmethod
    def _count_run_frames(project_directory: Path, run_id: str) -> int:
        return sum(1 for index in range(_FRAME_COUNT)
                   if (project_directory / f"{run_id}_pose_{index:03d}.raw").is_file()
                   or (project_directory / f"{run_id}_pose_pose_{index:03d}.raw").is_file())

    @classmethod
    def _latest_completed_snapshot(cls, project_directory: Path) -> dict[str, Any]:
        captures = sorted(project_directory.glob("revopoint_*_poses.csv"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
        for pose_log in captures:
            match = re.fullmatch(r"(revopoint_\d{8}T\d{6}Z)_poses\.csv", pose_log.name)
            if not match:
                continue
            run_id = match.group(1)
            completed = cls._count_run_frames(project_directory, run_id)
            stamp = run_id.removeprefix("revopoint_")
            scan_log = project_directory / f".cadyx-scan360-{stamp}.log"
            try:
                completed_turn = "sweep completed: 12 steps" in scan_log.read_bytes().decode("utf-8", errors="replace").lower()
            except OSError:
                completed_turn = False
            if completed == _FRAME_COUNT and completed_turn:
                manifest = project_directory / f"{run_id}_scan_360.json"
                message = (
                    "12 кадров сохранены; журнал поз подтверждает полный оборот. JSON-манифест не записан."
                    if not manifest.is_file()
                    else "Последний 360°-захват завершён; все 12 кадров и манифест сохранены."
                )
                return {
                    "state": "completed",
                    "completed_frames": completed,
                    "frame_count": _FRAME_COUNT,
                    "message": message,
                }
        return {"state": "idle", "completed_frames": 0, "frame_count": _FRAME_COUNT}
