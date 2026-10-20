"""Launch and observe the repository's direct-UVC 360-degree capture workflow."""

from __future__ import annotations

import csv
import io
import math
import os
import re
import shutil
import subprocess
import tempfile
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


@dataclass
class _LivePreviewJob:
    process: subprocess.Popen[bytes]
    raw_path: Path
    stop_path: Path
    ready_path: Path
    log_path: Path


@dataclass
class _TurntableMonitorJob:
    process: subprocess.Popen[bytes]
    csv_path: Path
    log_path: Path


class Scan360CaptureController:
    """Own one active physical 360 capture and expose compact progress."""

    def __init__(self, repository_root: Path, workspace_root: Path) -> None:
        self._repository_root = repository_root.resolve()
        self._workspace_root = workspace_root.resolve()
        self._guard = threading.RLock()
        self._job: _Scan360Job | None = None
        self._live_preview: _LivePreviewJob | None = None
        self._turntable_monitor: _TurntableMonitorJob | None = None

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
            if self._live_preview is not None and self._live_preview.process.poll() is None:
                raise ConflictError("Stop the live scanner preview before starting a 360 capture.", code="SCANNER_BUSY")
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

    def start_live_preview(self) -> dict[str, Any]:
        if os.name != "nt":
            raise ValidationError("Live Revopoint preview is available only on the Windows scanner host.")
        capture_executable = self._repository_root / "tools" / "revopoint" / "build" / "CaptureY16.exe"
        if not capture_executable.is_file():
            raise ValidationError("The CaptureY16 live-preview executable is unavailable; build the Revopoint tools first.")

        raw_path = self._workspace_root / ".revopoint-live.raw"
        stop_path = self._workspace_root / ".revopoint-live-preview.stop"
        ready_path = self._workspace_root / ".revopoint-live-preview.ready"
        log_path = self._workspace_root / ".revopoint-live-preview.log"
        with self._guard:
            if self._job is not None and self._job.process.poll() is None:
                raise ConflictError("Stop the 360 capture before starting a live scanner preview.", code="SCANNER_BUSY")
            if self._live_preview is not None and self._live_preview.process.poll() is None:
                return self._live_preview_snapshot_locked()
            self._live_preview = None
            for path in (raw_path, stop_path, ready_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise ValidationError(f"The previous live-preview buffer could not be cleared: {exc}.") from exc

            startup_info = subprocess.STARTUPINFO()
            startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup_info.wShowWindow = subprocess.SW_HIDE
            try:
                with log_path.open("wb") as log_stream:
                    process = subprocess.Popen(
                        [str(capture_executable), "--live-only", "--live-raw", str(raw_path),
                         "--stop-file", str(stop_path), "--ready-file", str(ready_path), "--fps", "15"],
                        cwd=str(self._repository_root), stdin=subprocess.DEVNULL,
                        stdout=log_stream, stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=startup_info,
                    )
            except OSError as exc:
                raise ValidationError(f"The live scanner preview could not be started: {exc}.") from exc
            self._live_preview = _LivePreviewJob(process, raw_path, stop_path, ready_path, log_path)
            return self._live_preview_snapshot_locked()

    def live_preview_status(self) -> dict[str, Any]:
        with self._guard:
            if self._live_preview is None:
                return {"state": "idle"}
            return self._live_preview_snapshot_locked()

    def turntable_angle_status(self) -> dict[str, Any]:
        with self._guard:
            if self._live_preview is None or self._live_preview.process.poll() is not None:
                self._stop_turntable_monitor_locked()
                return {"state": "idle"}
            monitor = self._turntable_monitor
            if monitor is None:
                return self._start_turntable_monitor_locked()
            sample = self._read_turntable_sample(monitor.csv_path)
            return_code = monitor.process.poll()
            if sample is not None:
                return {"state": "running" if return_code is None else "stale", **sample}
            if return_code is not None:
                try:
                    message = monitor.log_path.read_bytes()[-2048:].decode("utf-8", errors="replace").strip()
                except OSError:
                    message = ""
                self._stop_turntable_monitor_locked()
                return {"state": "unavailable", "return_code": return_code, "message": message}
            return {"state": "starting"}

    def _start_turntable_monitor_locked(self) -> dict[str, Any]:
        executable = self._repository_root / "tools" / "revopoint" / "build" / "TurntableController.exe"
        if not executable.is_file():
            return {"state": "unavailable", "message": "TurntableController.exe is unavailable."}
        descriptor, csv_name = tempfile.mkstemp(prefix=".revopoint-turntable-live-", suffix=".csv", dir=self._workspace_root)
        os.close(descriptor)
        csv_path = Path(csv_name)
        log_path = csv_path.with_suffix(".log")
        startup_info = subprocess.STARTUPINFO()
        startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup_info.wShowWindow = subprocess.SW_HIDE
        try:
            with log_path.open("xb") as log_stream:
                process = subprocess.Popen(
                    [str(executable), "monitor", "300", str(csv_path)],
                    cwd=str(self._repository_root), stdin=subprocess.DEVNULL,
                    stdout=log_stream, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=startup_info,
                )
        except OSError as exc:
            csv_path.unlink(missing_ok=True)
            log_path.unlink(missing_ok=True)
            return {"state": "unavailable", "message": f"Turntable telemetry could not be started: {exc}."}
        self._turntable_monitor = _TurntableMonitorJob(process, csv_path, log_path)
        return {"state": "starting"}

    @staticmethod
    def _read_turntable_sample(csv_path: Path) -> dict[str, Any] | None:
        try:
            content = csv_path.read_text(encoding="utf-8-sig")
        except OSError:
            return None
        try:
            rows = csv.DictReader(io.StringIO(content))
            for row in reversed(list(rows)):
                try:
                    angle = float(row.get("angle_degrees", ""))
                except (ValueError, TypeError):
                    continue
                observed_at = row.get("utc", "")
                if math.isfinite(angle) and observed_at:
                    return {"angle_degrees": angle, "observed_at": observed_at}
        except csv.Error:
            return None
        return None

    def _stop_turntable_monitor_locked(self) -> None:
        monitor = self._turntable_monitor
        if monitor is None:
            return
        if monitor.process.poll() is None:
            monitor.process.terminate()
            try:
                monitor.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                monitor.process.kill()
                monitor.process.wait(timeout=2)
        for path in (monitor.csv_path, monitor.log_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        self._turntable_monitor = None

    def stop_live_preview(self) -> dict[str, Any]:
        with self._guard:
            self._stop_turntable_monitor_locked()
            job = self._live_preview
            if job is None:
                return {"state": "idle"}
            if job.process.poll() is None:
                try:
                    with job.stop_path.open("xb"):
                        pass
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise ValidationError(f"The live scanner preview could not be stopped cleanly: {exc}.") from exc
                try:
                    job.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    job.process.terminate()
                    job.process.wait(timeout=2)
            self._live_preview = None
            for path in (job.raw_path, job.stop_path, job.ready_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise ValidationError(f"A temporary live-preview file could not be removed: {exc}.") from exc
            return {"state": "idle"}

    def _live_preview_snapshot_locked(self) -> dict[str, Any]:
        assert self._live_preview is not None
        job = self._live_preview
        return_code = job.process.poll()
        if return_code is not None:
            try:
                message = job.log_path.read_bytes()[-4096:].decode("utf-8", errors="replace").strip()
            except OSError:
                message = ""
            self._live_preview = None
            for path in (job.raw_path, job.stop_path, job.ready_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    message = f"{message}\nTemporary frame cleanup failed: {exc}.".strip()
            return {"state": "failed", "return_code": return_code, "message": message}
        if not job.ready_path.is_file():
            return {"state": "starting"}
        try:
            mtime_ns = job.raw_path.stat().st_mtime_ns
        except OSError:
            return {"state": "starting"}
        return {"state": "running", "frame_mtime_ns": mtime_ns}

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
        snapshot = {
            "state": state,
            "completed_frames": completed,
            "frame_count": _FRAME_COUNT,
            "started_at": self._job.started_at,
            "message": message,
        }
        snapshot.update(self._preview_payload(
            self._job.project_directory, self._job.run_id, completed,
        ))
        return snapshot

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
                    **cls._preview_payload(project_directory, run_id, completed),
                }
        return {"state": "idle", "completed_frames": 0, "frame_count": _FRAME_COUNT}

    @classmethod
    def _preview_payload(cls, project_directory: Path, run_id: str, completed: int) -> dict[str, str | int]:
        fused_name = f"{run_id}_first_frame_geometric_candidate.ply"
        if (project_directory / fused_name).is_file():
            origin_name = next((name for name in (f"{run_id}_pose_000.ply", f"{run_id}_pose_pose_000.ply")
                                if (project_directory / name).is_file()), None)
            return {
                "preview_folder": project_directory.name,
                "preview_file": fused_name,
                "preview_kind": "provisional_fusion",
                "preview_frame_count": _FRAME_COUNT,
                **({"origin_file": origin_name} if origin_name else {}),
            }
        if completed > 0:
            index = min(completed, _FRAME_COUNT) - 1
            for name in (f"{run_id}_pose_{index:03d}.ply", f"{run_id}_pose_pose_{index:03d}.ply"):
                if (project_directory / name).is_file():
                    origin_name = next((candidate for candidate in (f"{run_id}_pose_000.ply", f"{run_id}_pose_pose_000.ply")
                                        if (project_directory / candidate).is_file()), None)
                    return {
                        "preview_folder": project_directory.name,
                        "preview_file": name,
                        "preview_kind": "single",
                        "preview_frame_count": _FRAME_COUNT,
                        **({"origin_file": origin_name} if origin_name else {}),
                    }
        return {}
