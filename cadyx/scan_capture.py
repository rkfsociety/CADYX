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
            log_path = project_directory / f".cadyx-scan360-{stamp}.log"
            startup_info = subprocess.STARTUPINFO()
            startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup_info.wShowWindow = subprocess.SW_HIDE
            try:
                with log_path.open("xb") as log_stream:
                    process = subprocess.Popen(
                        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                         "-File", str(script), "-ProjectPath", str(project_directory)],
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
                process=process,
                log_path=log_path,
                started_at=datetime.now(timezone.utc).isoformat(),
            )
            return self._snapshot_locked()

    def status(self, project_id: str) -> dict[str, Any]:
        with self._guard:
            if self._job is None:
                return {"state": "idle", "completed_frames": 0, "frame_count": _FRAME_COUNT}
            if self._job.project_id != project_id:
                if self._job.process.poll() is None:
                    return {"state": "busy", "completed_frames": 0, "frame_count": _FRAME_COUNT}
                return {"state": "idle", "completed_frames": 0, "frame_count": _FRAME_COUNT}
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
        if state == "completed":
            completed = _FRAME_COUNT
        message = (f"Кадр {completed}/{_FRAME_COUNT}" if state == "running" else
                   "Съёмка 360° завершена; кадры сохранены в проекте." if state == "completed" else
                   f"Съёмка завершилась с ошибкой (код {return_code}); исходные кадры, если они успели сохраниться, оставлены в проекте.")
        return {
            "state": state,
            "completed_frames": completed,
            "frame_count": _FRAME_COUNT,
            "started_at": self._job.started_at,
            "message": message,
        }
