"""Launch and observe the repository's direct-UVC 360-degree capture workflow."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import ConflictError, ValidationError

_DEFAULT_FRAME_COUNT = 12


@dataclass
class _Scan360Job:
    project_id: str
    project_directory: Path
    run_id: str
    process: subprocess.Popen[bytes]
    log_path: Path
    started_at: str
    frame_count: int
    step_degrees: int
    align_to_markers: bool


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
                                           capture_output=True, timeout=12, check=True,
                                           creationflags=subprocess.CREATE_NO_WINDOW)
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
                    creationflags=subprocess.CREATE_NO_WINDOW,
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
        default_scanner_calibration = tool_root / "calibration" / "scanners" / "revopoint-mini-y16"
        default_q_file = default_scanner_calibration / "Q.bin"
        default_scale_file = default_scanner_calibration / "depth_scale.json"
        default_available = default_q_file.is_file() and default_scale_file.is_file()
        for scanner in scanners:
            q_file, scale_file = self._scanner_calibration_paths(scanner["device_id"])
            scanner["calibrated"] = (q_file.is_file() and scale_file.is_file()) or (
                len(scanners) == 1 and scanner["profile_id"] == "revopoint-mini-y16" and default_available
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
            calibration_root = (self._repository_root / "tools" / "revopoint" / "calibration"
                                / "scanners" / profile_id)
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

    def _turntable_settings_directory(self, turntable_device_id: str) -> Path:
        app_data = os.environ.get("LOCALAPPDATA")
        if not app_data:
            raise ValidationError("The Windows user profile path is unavailable for turntable settings storage.")
        profile_key = hashlib.sha256(turntable_device_id.encode("utf-8")).hexdigest()
        return Path(app_data) / "CADYX" / "turntable-settings" / profile_key

    def _connected_turntable(self, turntable_device_id: str | None) -> dict[str, Any]:
        turntables = self.list_connected_devices()["turntables"]
        if turntable_device_id is None and len(turntables) == 1:
            turntable_device_id = turntables[0]["device_id"]
        turntable = next((item for item in turntables if item["device_id"] == turntable_device_id), None)
        if turntable is None:
            raise ValidationError("Select a currently connected supported turntable in device settings.")
        return turntable

    @staticmethod
    def _validate_marker_map(data: bytes) -> int:
        header = struct.Struct("<4I")
        record = struct.Struct("<2i2f6d")
        if len(data) < header.size:
            raise ValidationError("The selected turntable marker map is shorter than its header.")
        version, _, _, count = header.unpack_from(data)
        if version != 1 or count < 4 or count > 10000 or len(data) != header.size + count * record.size:
            raise ValidationError("The selected turntable marker map has an invalid header or record count.")
        for index in range(count):
            fields = record.unpack_from(data, header.size + index * record.size)
            coordinates_and_normal = fields[4:]
            if not all(math.isfinite(value) for value in coordinates_and_normal):
                raise ValidationError(f"Turntable marker map record {index + 1} contains non-finite values.")
            normal_length = math.sqrt(sum(value * value for value in coordinates_and_normal[3:]))
            if not 0.5 <= normal_length <= 1.5:
                raise ValidationError(f"Turntable marker map record {index + 1} has an invalid normal.")
        return count

    def turntable_settings(self, turntable_device_id: str | None,
                           profile_id: str | None = None) -> dict[str, Any]:
        if profile_id:
            profile_id = self._known_turntable_profile(profile_id)
            if not turntable_device_id:
                turntable = self._connected_turntable(None)
                if profile_id != turntable["profile_id"]:
                    raise ValidationError("The selected turntable profile does not match the connected table.")
                turntable_device_id = turntable["device_id"]
        else:
            turntable = self._connected_turntable(turntable_device_id)
            turntable_device_id = turntable["device_id"]
            profile_id = turntable["profile_id"]
        settings_path = self._turntable_settings_directory(turntable_device_id) / "settings.json"
        if not settings_path.is_file():
            return {"turntable_device_id": turntable_device_id,
                    "profile_id": profile_id,
                    "rotation_period_seconds": 18.0, "configured": False}
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            period = settings["rotation_period_seconds"]
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValidationError("The selected turntable's settings file is invalid.") from exc
        if (isinstance(period, bool) or not isinstance(period, (int, float))
                or not math.isfinite(period) or not 18.0 <= period <= 90.0):
            raise ValidationError("The selected turntable's rotation period must be between 18 and 90 seconds.")
        saved_profile = settings.get("profile_id", profile_id)
        if saved_profile != profile_id:
            raise ValidationError("The selected turntable's saved profile does not match the device catalog.")
        return {"turntable_device_id": turntable_device_id,
                "profile_id": profile_id,
                "rotation_period_seconds": float(period), "configured": True}

    def save_turntable_settings(self, turntable_device_id: str, rotation_period_seconds: float,
                                profile_id: str | None = None) -> dict[str, Any]:
        if (not isinstance(turntable_device_id, str) or not turntable_device_id
                or isinstance(rotation_period_seconds, bool)
                or not isinstance(rotation_period_seconds, (int, float))
                or not math.isfinite(rotation_period_seconds)
                or not 18.0 <= rotation_period_seconds <= 90.0):
            raise ValidationError("Turntable settings require a connected device ID and a rotation period from 18 to 90 seconds.")
        if profile_id:
            profile_id = self._known_turntable_profile(profile_id)
        else:
            turntable = self._connected_turntable(turntable_device_id)
            profile_id = turntable["profile_id"]
        directory = self._turntable_settings_directory(turntable_device_id)
        directory.mkdir(parents=True, exist_ok=True)
        profile_map = (self._repository_root / "tools" / "revopoint" / "calibration"
                       / "tables" / profile_id / "framesmall.mkf")
        device_map = directory / "framesmall.mkf"
        if profile_map.is_file() and not device_map.exists():
            temporary_map = directory / "framesmall.mkf.tmp"
            try:
                shutil.copyfile(profile_map, temporary_map)
                temporary_map.replace(device_map)
            except OSError as exc:
                temporary_map.unlink(missing_ok=True)
                raise ValidationError("Could not initialize the selected table's marker map.") from exc
        destination = directory / "settings.json"
        temporary = directory / "settings.json.tmp"
        payload = json.dumps({"profile_id": profile_id,
                              "rotation_period_seconds": float(rotation_period_seconds)}, indent=2) + "\n"
        try:
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(destination)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise ValidationError("Could not save the selected turntable's settings file.") from exc
        return {"turntable_device_id": turntable_device_id,
                "profile_id": profile_id,
                "rotation_period_seconds": float(rotation_period_seconds), "configured": True}

    def _known_turntable_profile(self, profile_id: str | None) -> str:
        if not isinstance(profile_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", profile_id):
            raise ValidationError("The selected turntable profile ID is invalid.")
        catalog_path = self._repository_root / "tools" / "revopoint" / "devices.json"
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            known = any(item.get("id") == profile_id
                        and item.get("control_protocol") == "revopoint-dual-axis-v1"
                        for item in catalog.get("turntables", []))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValidationError("The turntable device catalog could not be read.") from exc
        if not known:
            raise ValidationError("The selected turntable profile is not supported by this host.")
        return profile_id

    def _turntable_marker_map_path(self, turntable_device_id: str | None,
                                   profile_id: str | None = None) -> Path:
        if not profile_id and turntable_device_id:
            settings_path = self._turntable_settings_directory(turntable_device_id) / "settings.json"
            try:
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                raise ValidationError("Select the turntable profile before loading its marker map.") from exc
            profile_id = settings.get("profile_id")
        if not profile_id:
            turntable = self._connected_turntable(turntable_device_id)
            profile_id = turntable.get("profile_id")
            turntable_device_id = turntable["device_id"]
        profile_id = self._known_turntable_profile(profile_id)
        device_directory = (self._turntable_settings_directory(turntable_device_id)
                            if turntable_device_id else None)
        if device_directory is not None:
            settings_path = device_directory / "settings.json"
            if settings_path.is_file():
                try:
                    configured_profile = json.loads(settings_path.read_text(encoding="utf-8")).get("profile_id")
                except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise ValidationError("The selected turntable's settings file is invalid.") from exc
                if configured_profile is not None and configured_profile != profile_id:
                    raise ValidationError("The selected table's marker map profile does not match its device settings.")
        device_map = device_directory / "framesmall.mkf" if device_directory is not None else None
        profile_map = (self._repository_root / "tools" / "revopoint" / "calibration"
                       / "tables" / profile_id / "framesmall.mkf")
        source = device_map if device_map is not None and device_map.is_file() else profile_map
        try:
            data = source.read_bytes()
        except OSError as exc:
            raise ValidationError("No marker map is configured for the selected turntable.") from exc
        self._validate_marker_map(data)
        return source.resolve(strict=True)

    def turntable_marker_map(self, turntable_device_id: str | None,
                             profile_id: str | None = None) -> tuple[bytes, int]:
        source = self._turntable_marker_map_path(turntable_device_id, profile_id)
        data = source.read_bytes()
        return data, self._validate_marker_map(data)

    def start(self, project_id: str, project_directory: Path,
              rotation_period_seconds: float = 18.0,
              scanner_device_id: str | None = None,
              turntable_device_id: str | None = None,
              step_degrees: int = 30,
              align_to_markers: bool = True) -> dict[str, Any]:
        if (isinstance(rotation_period_seconds, bool)
                or not isinstance(rotation_period_seconds, (int, float))
                or not 18.0 <= rotation_period_seconds <= 90.0):
            raise ValidationError("The turntable rotation period must be between 18 and 90 seconds per revolution.")
        if (isinstance(step_degrees, bool) or not isinstance(step_degrees, int)
                or not 1 <= step_degrees <= 30 or 360 % step_degrees != 0):
            raise ValidationError("The scan step must be an integer divisor of 360 between 1 and 30 degrees.")
        if not isinstance(align_to_markers, bool):
            raise ValidationError("align_to_markers must be a boolean.")
        frame_count = 360 // step_degrees
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
        marker_map_file = (self._turntable_marker_map_path(turntable_device_id, turntable["profile_id"])
                           if align_to_markers else None)
        python_executable = Path(sys.executable)
        if python_executable.name.lower() == "pythonw.exe":
            console_python = python_executable.with_name("python.exe")
            if console_python.is_file():
                python_executable = console_python

        with self._guard:
            if self._live_preview is not None and self._live_preview.process.poll() is None:
                raise ConflictError("Stop the live scanner preview before starting a 360 capture.", code="SCANNER_BUSY")
            if self._job is not None and self._job.process.poll() is None:
                if self._job.project_id == project_id:
                    return self._snapshot_locked()
                raise ConflictError("The scanner is already capturing a 360 scan for another project.", code="SCANNER_BUSY")
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            run_id = f"revopoint_{stamp}"
            run_directory = project_directory / run_id
            try:
                run_directory.mkdir()
            except FileExistsError as exc:
                raise ConflictError("A scan series folder already exists; no files were replaced.", code="SCAN_LOG_EXISTS") from exc
            log_path = run_directory / f".cadyx-scan360-{stamp}.log"
            startup_info = subprocess.STARTUPINFO()
            startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup_info.wShowWindow = subprocess.SW_HIDE
            try:
                with log_path.open("xb") as log_stream:
                    process = subprocess.Popen(
                        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                         "-File", str(script), "-ProjectPath", str(project_directory), "-RunId", run_id,
                         "-TurntableRotationPeriodSeconds", format(rotation_period_seconds, ".15g"),
                         "-StepDegrees", str(step_degrees),
                         "-ScannerDeviceId", scanner_device_id,
                         "-TurntableAddress", turntable_device_id,
                         "-TurntableName", turntable["expected_name"],
                         "-TurntableAdvertisementUuid", turntable["advertisement_uuid"],
                         "-TurntableServiceUuid", turntable["service_uuid"],
                         "-TurntableCharacteristicUuid", turntable["characteristic_uuid"],
                         "-CalibrationFile", str(calibration_file),
                         "-PythonExecutable", str(python_executable),
                         *(["-TurntableMarkerMap", str(marker_map_file)]
                           if align_to_markers else ["-SkipTableMarkerAlignment"]),
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
                frame_count=frame_count,
                step_degrees=step_degrees,
                align_to_markers=align_to_markers,
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
                    return {"state": "busy", "completed_frames": 0,
                            "frame_count": self._job.frame_count, "step_degrees": self._job.step_degrees}
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
        for value in re.findall(r"captured pose (\d+)/(\d+)", log_tail, flags=re.IGNORECASE):
            completed = max(completed, int(value[0]) + 1)
        completed = max(completed, self._count_run_frames(
            self._job.project_directory, self._job.run_id, self._job.frame_count))
        capture_sweep_completed = f"sweep completed: {self._job.frame_count} steps" in log_tail.lower()
        alignment_failed = "table-map alignment failed" in log_tail.lower()
        preprocessing = self._preprocessing_payload(
            self._job.project_directory, self._job.run_id, self._job.frame_count, active=True)
        metadata_warning = (state == "failed" and completed == self._job.frame_count
                            and capture_sweep_completed and not alignment_failed
                            and preprocessing["preprocessing_status"] == "completed")
        if state == "completed" or metadata_warning:
            completed = self._job.frame_count
            state = "completed"
        message = (f"Кадр {completed}/{self._job.frame_count}; очищено {preprocessing['preprocessed_frames']}/{self._job.frame_count}; ошибок фильтра {preprocessing['preprocessing_failed_frames']}" if state == "running" else
                   f"Съёмка завершилась, но очистка кадров не прошла ({preprocessing['preprocessed_frames']}/{self._job.frame_count}); исходники сохранены." if preprocessing["preprocessing_status"] == "failed" else
                   f"{self._job.frame_count} кадров сохранены, но привязка к центру карты стола не прошла; исходные кадры сохранены." if alignment_failed else
                   f"{self._job.frame_count} кадров сохранены. Запись манифеста завершилась ошибкой; журнал поз сохранён." if metadata_warning else
                   "Съёмка 360° завершена; кадры сохранены в проекте." if state == "completed" else
                   f"Съёмка завершилась с ошибкой (код {return_code}); исходные кадры, если они успели сохраниться, оставлены в проекте.")
        snapshot = {
            "state": state,
            "completed_frames": completed,
            "frame_count": self._job.frame_count,
            "step_degrees": self._job.step_degrees,
            "started_at": self._job.started_at,
            "message": message,
            **preprocessing,
        }
        snapshot.update(self._preview_payload(
            self._run_directory(self._job.project_directory, self._job.run_id),
            self._job.run_id, completed, self._job.frame_count,
        ))
        snapshot["table_alignment_status"] = self._alignment_status(
            self._job.project_directory, self._job.run_id,
        )
        if state == "completed":
            snapshot["message"] = {
                "aligned_provisional": "Все кадры привязаны к центру карты стола; физический масштаб остаётся предварительным.",
                "partial": "Часть кадров привязана к центру карты стола; остальные исходники сохранены для последующей привязки.",
                "unavailable": "Маркеры стола не удалось надёжно определить; привязку можно выполнить позднее по осям и плоскостям облака или сетки.",
                "pending": "Кадры сохранены; отчёт о привязке к карте стола отсутствует.",
                "not_performed": "Кадры сохранены в координатах камеры без привязки к столу; их можно сшить геометрически.",
                "failed": "Кадры сохранены, но отчёт о привязке к карте стола повреждён или не прошёл проверку.",
            }.get(snapshot["table_alignment_status"], message)
        return snapshot

    @staticmethod
    def _preprocessing_payload(project_directory: Path, run_id: str,
                               frame_count: int, *, active: bool = False) -> dict[str, Any]:
        report_path = (Scan360CaptureController._run_directory(project_directory, run_id)
                       / f"{run_id}_preprocessing.json")
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            frames = report.get("frames", [])
            failed = report.get("failed_frames", [])
            if not isinstance(frames, list) or not isinstance(failed, list):
                raise ValueError("invalid per-frame cleanup rows")
            return {
                "preprocessing_status": report.get("state", "failed"),
                "preprocessed_frames": report.get("cleaned_frame_count", len(frames)),
                "preprocessing_failed_frames": report.get("failed_frame_count", len(failed)),
                "preprocessing_frame_count": frame_count,
                "preprocessing_pending_frames": max(
                    0, frame_count - report.get("cleaned_frame_count", len(frames))
                    - report.get("failed_frame_count", len(failed))),
            }
        except FileNotFoundError:
            return {"preprocessing_status": "starting" if active else "not_available", "preprocessed_frames": 0,
                    "preprocessing_failed_frames": 0, "preprocessing_frame_count": frame_count,
                    "preprocessing_pending_frames": frame_count if active else 0}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {"preprocessing_status": "failed", "preprocessed_frames": 0,
                    "preprocessing_failed_frames": frame_count, "preprocessing_frame_count": frame_count,
                    "preprocessing_pending_frames": 0}

    @staticmethod
    def _alignment_status(project_directory: Path, run_id: str) -> str:
        run_directory = Scan360CaptureController._run_directory(project_directory, run_id)
        result_path = run_directory / f"{run_id}_table_alignment.json"
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            if result_path.exists():
                return "failed"
            manifest_path = run_directory / f"{run_id}_scan_360.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                return "pending"
            return "not_performed" if isinstance(manifest, dict) and manifest.get("table_alignment_status") == "not_performed" else "pending"
        return result.get("status", "failed") if isinstance(result, dict) else "failed"

    @staticmethod
    def _manifest_frame_count(project_directory: Path, run_id: str) -> int:
        manifest = Scan360CaptureController._run_directory(project_directory, run_id) / f"{run_id}_scan_360.json"
        try:
            count = json.loads(manifest.read_text(encoding="utf-8")).get("frame_count")
        except (OSError, ValueError, TypeError, json.JSONDecodeError, AttributeError):
            return _DEFAULT_FRAME_COUNT
        return count if isinstance(count, int) and not isinstance(count, bool) and 1 <= count <= 360 else _DEFAULT_FRAME_COUNT

    @staticmethod
    def _count_run_frames(project_directory: Path, run_id: str, frame_count: int | None = None) -> int:
        run_directory = Scan360CaptureController._run_directory(project_directory, run_id)
        count = frame_count or Scan360CaptureController._manifest_frame_count(project_directory, run_id)
        return sum(1 for index in range(count)
                   if (run_directory / f"{run_id}_pose_{index:03d}.raw").is_file()
                   or (run_directory / f"{run_id}_pose_pose_{index:03d}.raw").is_file())

    @staticmethod
    def _run_directory(project_directory: Path, run_id: str) -> Path:
        run_directory = project_directory / run_id
        return run_directory if run_directory.is_dir() else project_directory

    @classmethod
    def _latest_completed_snapshot(cls, project_directory: Path) -> dict[str, Any]:
        captures = sorted(
            (*project_directory.glob("revopoint_*_poses.csv"),
             *project_directory.glob("revopoint_*/revopoint_*_poses.csv")),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        for pose_log in captures:
            match = re.fullmatch(r"(revopoint_\d{8}T\d{6}Z)_poses\.csv", pose_log.name)
            if not match:
                continue
            run_id = match.group(1)
            run_directory = pose_log.parent
            frame_count = cls._manifest_frame_count(run_directory, run_id)
            completed = cls._count_run_frames(run_directory, run_id, frame_count)
            stamp = run_id.removeprefix("revopoint_")
            run_directory = pose_log.parent
            scan_log = run_directory / f".cadyx-scan360-{stamp}.log"
            if not scan_log.is_file():
                scan_log = project_directory / f".cadyx-scan360-{stamp}.log"
            try:
                completed_turn = f"sweep completed: {frame_count} steps" in scan_log.read_bytes().decode("utf-8", errors="replace").lower()
            except OSError:
                completed_turn = False
            if completed == frame_count and completed_turn:
                manifest = run_directory / f"{run_id}_scan_360.json"
                preprocessing = cls._preprocessing_payload(run_directory, run_id, frame_count)
                alignment_status = cls._alignment_status(run_directory, run_id)
                try:
                    log_text = scan_log.read_bytes().decode("utf-8", errors="replace").lower()
                except OSError:
                    log_text = ""
                alignment_failed = "table-map alignment failed" in log_text
                message = (
                    f"{frame_count} кадров сохранены, но привязка к центру карты стола не прошла; исходные кадры сохранены."
                    if alignment_failed else
                    "Часть кадров привязана к центру карты стола; остальные исходники сохранены для последующей привязки."
                    if alignment_status == "partial" else
                    "Маркеры стола не удалось надёжно определить; привязку можно выполнить позднее по осям и плоскостям облака или сетки."
                    if alignment_status == "unavailable" else
                    "Кадры сохранены; отчёт о привязке к карте стола отсутствует."
                    if alignment_status == "pending" else
                    "Кадры сохранены в координатах камеры без привязки к столу; предусмотрена геометрическая сшивка."
                    if alignment_status == "not_performed" else
                    "Все кадры привязаны к центру карты стола; физический масштаб остаётся предварительным."
                    if alignment_status == "aligned_provisional" else
                    "Кадры сохранены, но отчёт о привязке к карте стола повреждён или не прошёл проверку."
                    if alignment_status == "failed" else
                    f"{frame_count} кадров сохранены; журнал поз подтверждает полный оборот. JSON-манифест не записан."
                    if not manifest.is_file()
                    else f"Последний 360°-захват завершён; все {frame_count} кадров и манифест сохранены."
                )
                if preprocessing["preprocessing_status"] == "not_available":
                    message += " Отчёта очистки исходных кадров до сшивки для этой серии нет."
                return {
                    "state": "completed",
                    "completed_frames": completed,
                    "frame_count": frame_count,
                    "step_degrees": cls._manifest_step_degrees(run_directory, run_id),
                    "message": message,
                    "table_alignment_status": "failed" if alignment_failed else alignment_status,
                    **preprocessing,
                    **cls._preview_payload(run_directory, run_id, completed, frame_count),
                }
        return {"state": "idle", "completed_frames": 0, "frame_count": _DEFAULT_FRAME_COUNT}

    @staticmethod
    def _manifest_step_degrees(project_directory: Path, run_id: str) -> int:
        manifest = Scan360CaptureController._run_directory(project_directory, run_id) / f"{run_id}_scan_360.json"
        try:
            step = json.loads(manifest.read_text(encoding="utf-8")).get("step_degrees")
        except (OSError, ValueError, TypeError, json.JSONDecodeError, AttributeError):
            return 360 // Scan360CaptureController._manifest_frame_count(project_directory, run_id)
        return step if isinstance(step, int) and not isinstance(step, bool) and 1 <= step <= 360 else 360 // Scan360CaptureController._manifest_frame_count(project_directory, run_id)

    @classmethod
    def _preview_payload(cls, project_directory: Path, run_id: str, completed: int,
                         frame_count: int | None = None) -> dict[str, str | int]:
        frame_count = frame_count or cls._manifest_frame_count(project_directory, run_id)
        project_root = project_directory.parent if project_directory.name == run_id else project_directory
        series_name = project_directory.name if project_directory.name == run_id else None
        relative_series = series_name or ""
        try:
            project_record = json.loads((project_root / "project.json").read_text(encoding="utf-8"))
            project_id = project_record.get("id")
            project_name = project_record.get("name")
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            project_id = None
            project_name = None
        project_context = {"preview_project_id": project_id} if isinstance(project_id, str) else {}
        preview_folder = project_name if series_name and isinstance(project_name, str) else project_root.name
        if completed > 0:
            index = min(completed, frame_count) - 1
            alignment = project_directory / f"{run_id}_table_alignment.json"
            try:
                alignment_data = json.loads(alignment.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                alignment_data = {}
            if alignment_data.get("status") in ("aligned_provisional", "partial"):
                for aligned_index in range(index, -1, -1):
                    aligned_name = f"{run_id}_pose_{aligned_index:03d}_table_aligned.ply"
                    if (project_directory / aligned_name).is_file():
                        return {
                            "preview_folder": preview_folder,
                            **project_context,
                            **({"preview_subfolder": relative_series} if relative_series else {}),
                            "preview_file": aligned_name,
                            "preview_kind": "table_aligned",
                            "preview_frame_count": frame_count,
                            "preview_frame_index": aligned_index,
                            "preview_coordinate_frame": "table_marker_map_center_xy",
                            "origin_file": aligned_name,
                        }
        fused_name = f"{run_id}_first_frame_geometric_candidate.ply"
        if (project_directory / fused_name).is_file():
            origin_name = next((name for name in (f"{run_id}_pose_000.ply", f"{run_id}_pose_pose_000.ply")
                                if (project_directory / name).is_file()), None)
            return {
                "preview_folder": preview_folder,
                **project_context,
                **({"preview_subfolder": relative_series} if relative_series else {}),
                "preview_file": fused_name,
                "preview_kind": "provisional_fusion",
                "preview_frame_count": frame_count,
                **({"origin_file": origin_name} if origin_name else {}),
            }
        if completed > 0:
            index = min(completed, frame_count) - 1
            for name in (f"{run_id}_pose_{index:03d}.ply", f"{run_id}_pose_pose_{index:03d}.ply"):
                if (project_directory / name).is_file():
                    return {
                        "preview_folder": preview_folder,
                        **project_context,
                        **({"preview_subfolder": relative_series} if relative_series else {}),
                        "preview_file": name,
                        "preview_kind": "single",
                        "preview_frame_count": frame_count,
                        "origin_file": name,
                    }
        return {}
