"""Launch and observe the repository's direct-UVC 360-degree capture workflow."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import shutil
import struct
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


@dataclass
class _LivePreviewJob:
    process: subprocess.Popen[bytes]
    raw_path: Path
    stop_path: Path
    ready_path: Path
    log_path: Path


class Scan360CaptureController:
    """Own one active physical 360 capture and expose compact progress."""

    def __init__(self, repository_root: Path, workspace_root: Path) -> None:
        self._repository_root = repository_root.resolve()
        self._workspace_root = workspace_root.resolve()
        self._guard = threading.RLock()
        self._job: _Scan360Job | None = None
        self._live_preview: _LivePreviewJob | None = None

    def list_connected_devices(self) -> dict[str, list[dict[str, Any]]]:
        if os.name != "nt":
            raise ValidationError("Revopoint device discovery is available only on the Windows scanner host.")
        tool_root = self._repository_root / "tools" / "revopoint"
        capture_executable = tool_root / "build" / "CaptureY16.exe"
        turntable_executable = tool_root / "build" / "TurntableController.exe"
        catalog_path = tool_root / "devices.json"
        if not capture_executable.is_file() or not turntable_executable.is_file():
            raise ValidationError("Build the Revopoint tools before discovering connected devices.")
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            camera_result = subprocess.run([str(capture_executable), "--list-devices"],
                                           capture_output=True, timeout=12, check=True)
            camera_text = camera_result.stdout.decode("utf-8")
        except (OSError, subprocess.SubprocessError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValidationError(f"Could not discover Revopoint devices: {exc}.") from exc
        scanner_profiles = catalog.get("scanners", [])
        table_profiles = catalog.get("turntables", [])
        scanners: list[dict[str, str]] = []
        for line in camera_text.splitlines():
            device_id, separator, friendly_name = line.partition("\t")
            if not separator:
                continue
            for profile in scanner_profiles:
                prefix = profile.get("friendly_name_prefix", "")
                if (prefix and friendly_name.startswith(prefix)
                        and profile.get("capture_protocol") == "media-foundation-y16"):
                    scanners.append({
                        "device_id": device_id,
                        "name": friendly_name,
                        "profile_id": profile["id"],
                        "profile_name": profile["name"],
                    })
                    break
        tables: list[dict[str, str]] = []
        supported_table_profiles = [profile for profile in table_profiles
                                    if profile.get("control_protocol") == "revopoint-dual-axis-v1"]
        if supported_table_profiles:
            try:
                result = subprocess.run(
                    [str(turntable_executable), "devices", ",".join(
                        sorted({profile["advertisement_uuid"] for profile in supported_table_profiles})
                    )],
                    capture_output=True, timeout=15, check=True,
                )
                table_text = result.stdout.decode("utf-8")
            except (OSError, subprocess.SubprocessError, UnicodeError, KeyError) as exc:
                raise ValidationError(f"Could not discover Bluetooth turntables: {exc}.") from exc
            for line in table_text.splitlines():
                address, separator, friendly_name = line.partition("\t")
                if not separator:
                    continue
                for profile in supported_table_profiles:
                    if friendly_name != profile.get("gatt_name"):
                        continue
                    if not any(item["device_id"] == address for item in tables):
                        tables.append({
                            "device_id": address,
                            "name": friendly_name,
                            "profile_id": profile["id"],
                            "profile_name": profile["name"],
                            "expected_name": profile["gatt_name"],
                            "advertisement_uuid": profile["advertisement_uuid"],
                            "service_uuid": profile["service_uuid"],
                            "characteristic_uuid": profile["characteristic_uuid"],
                        })
                    break
        legacy_q_file = tool_root / "calibration" / "Q.bin"
        legacy_scale_file = tool_root / "calibration" / "depth_scale.json"
        legacy_available = legacy_q_file.is_file() and legacy_scale_file.is_file()
        for scanner in scanners:
            q_file, scale_file = self._scanner_calibration_paths(scanner["device_id"])
            scanner["calibrated"] = (q_file.is_file() and scale_file.is_file()) or (
                len(scanners) == 1 and scanner["profile_id"] == "revopoint-mini-y16" and legacy_available
            )
        return {"scanners": scanners, "turntables": tables}

    def save_scanner_calibration(self, scanner_device_id: str, q_matrix: bytes, depth_scale: float) -> dict[str, Any]:
        if not scanner_device_id or len(q_matrix) != 64 or not math.isfinite(depth_scale) or depth_scale <= 0:
            raise ValidationError("A scanner calibration requires a connected device, a 64-byte Q matrix, and a positive depth scale.")
        if not all(math.isfinite(value) for value in struct.unpack("<16f", q_matrix)):
            raise ValidationError("The scanner Q matrix must contain only finite float values.")
        devices = self.list_connected_devices()
        if not any(item["device_id"] == scanner_device_id for item in devices["scanners"]):
            raise ValidationError("The selected scanner is not currently connected.")
        q_file, scale_file = self._scanner_calibration_paths(scanner_device_id)
        q_file.parent.mkdir(parents=True, exist_ok=True)
        q_file.write_bytes(q_matrix)
        scale_file.write_text(json.dumps({
            "depth_scale_mm_per_raw_unit": depth_scale,
            "status": "user_configured_for_selected_device",
        }, indent=2) + "\n", encoding="utf-8")
        return {"calibrated": True, "scanner_device_id": scanner_device_id}

    def _scanner_calibration_paths(self, scanner_device_id: str) -> tuple[Path, Path]:
        app_data = os.environ.get("LOCALAPPDATA")
        if not app_data:
            raise ValidationError("The Windows user profile path is unavailable for scanner calibration storage.")
        profile_key = hashlib.sha256(scanner_device_id.encode("utf-8")).hexdigest()
        profile = Path(app_data) / "CADYX" / "scanner-calibration" / profile_key
        return profile / "Q.bin", profile / "depth_scale.json"

    def _resolve_scanner_calibration(self, scanner_device_id: str, scanner_count: int,
                                     profile_id: str) -> tuple[Path, float]:
        q_file, scale_file = self._scanner_calibration_paths(scanner_device_id)
        if not (q_file.is_file() and scale_file.is_file()):
            if scanner_count != 1 or profile_id != "revopoint-mini-y16":
                raise ValidationError("This scanner has no device-specific calibration; add its Q.bin and depth scale in device settings.")
            calibration_root = self._repository_root / "tools" / "revopoint" / "calibration"
            q_file = calibration_root / "Q.bin"
            scale_file = calibration_root / "depth_scale.json"
        if not q_file.is_file() or not scale_file.is_file() or q_file.stat().st_size != 64:
            raise ValidationError("The selected scanner has no valid local calibration profile.")
        try:
            depth_scale = float(json.loads(scale_file.read_text(encoding="utf-8"))["depth_scale_mm_per_raw_unit"])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValidationError("The selected scanner's depth-scale profile is invalid.") from exc
        if not math.isfinite(depth_scale) or depth_scale <= 0:
            raise ValidationError("The selected scanner's depth scale must be positive and finite.")
        return q_file, depth_scale

    def scanner_calibration(self, scanner_device_id: str | None) -> dict[str, Any]:
        scanners = self.list_connected_devices()["scanners"]
        if scanner_device_id is None and len(scanners) == 1:
            scanner_device_id = scanners[0]["device_id"]
        if not any(item["device_id"] == scanner_device_id for item in scanners):
            raise ValidationError("Select a currently connected supported scanner in device settings.")
        scanner = next(item for item in scanners if item["device_id"] == scanner_device_id)
        q_file, depth_scale = self._resolve_scanner_calibration(
            scanner_device_id, len(scanners), scanner["profile_id"],
        )
        try:
            matrix = struct.unpack("<16f", q_file.read_bytes())
            profile_path = self._scanner_calibration_paths(scanner_device_id)[1]
            scale_config = json.loads((profile_path if profile_path.is_file()
                                        else q_file.with_name("depth_scale.json")).read_text(encoding="utf-8"))
        except (OSError, ValueError, struct.error, json.JSONDecodeError) as exc:
            raise ValidationError("The selected scanner calibration profile could not be read.") from exc
        if not all(math.isfinite(value) for value in matrix):
            raise ValidationError("The selected scanner Q matrix contains non-finite values.")
        return {
            "width": 640,
            "height": 400,
            "matrix": matrix,
            "depth_scale_mm_per_raw_unit": depth_scale,
            "depth_scale_status": scale_config.get("status", "unverified"),
            "depth_scale_source": scale_config.get("source", "device-specific profile"),
        }

    def start(self, project_id: str, project_directory: Path,
              rotation_period_seconds: float = 18.0,
              scanner_device_id: str | None = None,
              turntable_device_id: str | None = None) -> dict[str, Any]:
        if (isinstance(rotation_period_seconds, bool)
                or not isinstance(rotation_period_seconds, (int, float))
                or not 18.0 <= rotation_period_seconds <= 90.0):
            raise ValidationError("The turntable rotation period must be between 18 and 90 seconds per revolution.")
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
        devices = self.list_connected_devices()
        scanners = devices["scanners"]
        tables = devices["turntables"]
        if scanner_device_id is None and len(scanners) == 1:
            scanner_device_id = scanners[0]["device_id"]
        if turntable_device_id is None and len(tables) == 1:
            turntable_device_id = tables[0]["device_id"]
        scanner = next((item for item in scanners if item["device_id"] == scanner_device_id), None)
        turntable = next((item for item in tables if item["device_id"] == turntable_device_id), None)
        if scanner is None:
            raise ValidationError("Select a currently connected supported scanner in device settings.")
        if turntable is None:
            raise ValidationError("Select a currently connected supported turntable in device settings.")
        calibration_file, depth_scale = self._resolve_scanner_calibration(
            scanner_device_id, len(scanners), scanner["profile_id"],
        )

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
                         "-File", str(script), "-ProjectPath", str(project_directory), "-RunId", run_id,
                         "-TurntableRotationPeriodSeconds", format(rotation_period_seconds, ".15g"),
                         "-ScannerDeviceId", scanner_device_id,
                         "-TurntableAddress", turntable_device_id,
                         "-TurntableName", turntable["expected_name"],
                         "-TurntableAdvertisementUuid", turntable["advertisement_uuid"],
                         "-TurntableServiceUuid", turntable["service_uuid"],
                         "-TurntableCharacteristicUuid", turntable["characteristic_uuid"],
                         "-CalibrationFile", str(calibration_file),
                         "-DepthScale", format(depth_scale, ".15g")],
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

    def start_live_preview(self, scanner_device_id: str | None = None) -> dict[str, Any]:
        if os.name != "nt":
            raise ValidationError("Live Revopoint preview is available only on the Windows scanner host.")
        capture_executable = self._repository_root / "tools" / "revopoint" / "build" / "CaptureY16.exe"
        if not capture_executable.is_file():
            raise ValidationError("The CaptureY16 live-preview executable is unavailable; build the Revopoint tools first.")
        scanners = self.list_connected_devices()["scanners"]
        if scanner_device_id is None and len(scanners) == 1:
            scanner_device_id = scanners[0]["device_id"]
        scanner = next((item for item in scanners if item["device_id"] == scanner_device_id), None)
        if scanner is None:
            raise ValidationError("Select a currently connected supported scanner in device settings.")
        self._resolve_scanner_calibration(scanner_device_id, len(scanners), scanner["profile_id"])

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
                         "--stop-file", str(stop_path), "--ready-file", str(ready_path), "--fps", "15",
                         "--device-id", scanner_device_id],
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

    def stop_live_preview(self) -> dict[str, Any]:
        with self._guard:
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
