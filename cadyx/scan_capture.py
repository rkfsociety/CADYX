"""Launch and observe the repository's direct-UVC 360-degree capture workflow."""

from __future__ import annotations

import hashlib
import csv
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import ConflictError, ValidationError
from .workspace_state import prepare_workspace_state

_DEFAULT_FRAME_COUNT = 12
_TURNTABLE_POSITION_CACHE_SECONDS = 6.0
_LIVE_PREVIEW_METRICS_PREFIX = "preview_metrics "
_LIVE_PREVIEW_CLIENT_METRIC_FIELDS = frozenset({
    "window_seconds", "frames", "request_avg_ms", "request_max_ms", "process_avg_ms",
    "process_max_ms", "render_avg_ms", "render_max_ms", "total_avg_ms", "total_max_ms", "points_avg",
})


def _normalize_turntable_angle(angle_degrees: float) -> float:
    """Return the equivalent signed table orientation in [-180, 180)."""
    return (angle_degrees + 180.0) % 360.0 - 180.0


def _capture_log_progress(log_tail: str, frame_count: int) -> int:
    """Count only bounded, in-range pose reports for this capture."""
    completed = 0
    for pose, total in re.findall(
        r"captured pose ([0-9]{1,3})/([0-9]{1,3})(?![0-9])",
        log_tail, flags=re.IGNORECASE,
    ):
        if int(total) == frame_count and int(pose) < frame_count:
            completed = max(completed, int(pose) + 1)
    return completed


def _read_log_tail(log_path: Path, limit: int) -> str:
    """Read only the newest bytes of a potentially long-lived device log."""
    try:
        with log_path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - limit))
            return stream.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _scan_capture_log_markers(log_path: Path, frame_count: int) -> tuple[bool, bool]:
    """Search the full capture log with bounded memory, including split markers."""
    completion = f"sweep completed: {frame_count} steps".encode("ascii")
    failure = b"table-map alignment failed"
    overlap = max(len(completion), len(failure)) - 1
    completed = failed = False
    try:
        with log_path.open("rb") as stream:
            suffix = b""
            while chunk := stream.read(64 * 1024):
                text = (suffix + chunk).lower()
                completed = completed or completion in text
                failed = failed or failure in text
                if completed and failed:
                    break
                suffix = text[-overlap:]
    except OSError:
        return False, False
    return completed, failed


def _read_live_preview_metrics(log_path: Path) -> dict[str, Any] | None:
    try:
        with log_path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - 8192))
            lines = stream.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if not line.startswith(_LIVE_PREVIEW_METRICS_PREFIX):
            continue
        try:
            metrics = json.loads(line[len(_LIVE_PREVIEW_METRICS_PREFIX):])
        except json.JSONDecodeError:
            continue
        if not isinstance(metrics, dict):
            continue
        required = ("window_seconds", "frames", "read_calls", "null_samples", "capture_fps",
                    "read_avg_ms", "read_max_ms", "copy_avg_ms", "copy_max_ms",
                    "publish_avg_ms", "publish_max_ms")
        if any(isinstance(metrics.get(key), bool) or not isinstance(metrics.get(key), (int, float))
               or not math.isfinite(metrics[key]) for key in required):
            continue
        optional_values = (metrics.get("camera_fps"), metrics.get("process_cpu_percent"))
        if any(value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value)) for value in optional_values):
            continue
        return metrics
    return None


def _validate_live_preview_client_metrics(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _LIVE_PREVIEW_CLIENT_METRIC_FIELDS:
        raise ValidationError("Live-preview client metrics have an invalid field set.")
    frames = value["frames"]
    if isinstance(frames, bool) or not isinstance(frames, int) or not 1 <= frames <= 300:
        raise ValidationError("Live-preview client metrics require 1–300 frames per window.")
    window_seconds = value["window_seconds"]
    if (isinstance(window_seconds, bool) or not isinstance(window_seconds, (int, float))
            or not math.isfinite(window_seconds) or not 0.5 <= window_seconds <= 10):
        raise ValidationError("Live-preview client metric windows must span 0.5–10 seconds.")
    for field in _LIVE_PREVIEW_CLIENT_METRIC_FIELDS - {"frames", "window_seconds"}:
        measurement = value[field]
        maximum = 8000 if field == "points_avg" else 10000
        if (isinstance(measurement, bool) or not isinstance(measurement, (int, float))
                or not math.isfinite(measurement) or not 0 <= measurement <= maximum):
            raise ValidationError(f"Invalid live-preview client metric: {field}.")
    return dict(value)


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
    frames_path: Path
    stop_path: Path
    ready_path: Path
    log_path: Path
    client_metrics: dict[str, Any] | None = None
    client_metrics_at: datetime | None = None


@dataclass
class _TurntableSpinJob:
    process: subprocess.Popen[bytes]
    stop_path: Path
    heartbeat_path: Path
    log_path: Path
    device_id: str
    profile_id: str
    started_at: str
    started_monotonic: float
    preview_process: subprocess.Popen[bytes]
    preview_ready_path: Path


class Scan360CaptureController:
    """Own one active physical 360 capture and expose compact progress."""

    def __init__(self, repository_root: Path, workspace_root: Path) -> None:
        self._repository_root = repository_root.resolve()
        self._workspace_root = workspace_root.resolve()
        self._state_root = prepare_workspace_state(self._workspace_root)
        self._guard = threading.RLock()
        self._job: _Scan360Job | None = None
        self._live_preview: _LivePreviewJob | None = None
        self._turntable_spin: _TurntableSpinJob | None = None
        self._last_turntable_position: dict[str, Any] | None = None
        self._last_turntable_position_at = 0.0

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

    def turntable_control(self, command: str, turntable_device_id: str | None = None,
                          profile_id: str | None = None, value: float | None = None) -> dict[str, Any]:
        if os.name != "nt":
            raise ValidationError("Revopoint turntable control is available only on the Windows scanner host.")
        if command not in {"query", "speed", "rotate", "tilt", "zero", "stop", "monitor",
                           "spin-start", "spin-stop", "spin-status"}:
            raise ValidationError("Unsupported turntable control command.")
        if command in {"query", "zero", "stop", "spin-start", "spin-stop", "spin-status"} and value is not None:
            raise ValidationError(f"Turntable {command} does not accept a value.")
        with self._guard:
            spin = self._turntable_spin
            if command == "spin-status":
                snapshot = self._turntable_spin_snapshot_locked()
                snapshot["scan_active"] = self._job is not None and self._job.process.poll() is None
                return snapshot
            if command == "spin-stop":
                return self._stop_turntable_spin_locked()
            if command == "spin-start":
                preview = self._live_preview
                if (preview is None or preview.process.poll() is not None
                        or not preview.ready_path.is_file()):
                    raise ConflictError("Start the live scanner preview before continuous table rotation.",
                                        code="SCANNER_PREVIEW_REQUIRED")
            if command == "stop" and spin is None:
                stop_path = self._state_root / ".revopoint-turntable-spin.stop"
                heartbeat_path = Path(str(stop_path) + ".heartbeat")
                log_path = self._state_root / ".revopoint-turntable-spin.log"
                self._recover_stale_turntable_spin_files(stop_path, heartbeat_path, log_path)
            if self._job is not None and self._job.process.poll() is None and command != "stop":
                raise ConflictError("Turntable motion and configuration are locked while a 360 scan is capturing.",
                                    code="SCAN_ACTIVE")
            if spin is not None and spin.process.poll() is None:
                raise ConflictError("Stop continuous table rotation before sending another command.",
                                    code="TURNTABLE_SPIN_ACTIVE")
            profile, address = self._resolve_turntable_control_profile(turntable_device_id, profile_id)
            executable = self._repository_root / "tools" / "revopoint" / "build" / "TurntableController.exe"
            if not executable.is_file():
                raise ValidationError("Build the Revopoint tools before controlling the turntable.")
            profile_args = [address, profile["gatt_name"], profile["advertisement_uuid"],
                            profile["service_uuid"], profile["characteristic_uuid"]]
            if command == "monitor":
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value) or not 5 <= value <= 60 or not float(value).is_integer()):
                    raise ValidationError("Turntable monitor duration must be a whole number from 5 to 60 seconds.")
                duration = int(value)
                try:
                    with tempfile.TemporaryDirectory(prefix=".turntable-monitor-",
                                                     dir=self._state_root) as temporary:
                        telemetry_path = Path(temporary) / "telemetry.csv"
                        result = subprocess.run([str(executable), "monitor", str(duration), str(telemetry_path),
                            *profile_args], cwd=str(self._repository_root), capture_output=True,
                            timeout=duration + 30, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
                        output = result.stdout.decode("utf-8", errors="replace").strip()
                        samples: list[dict[str, Any]] = []
                        with telemetry_path.open("r", encoding="utf-8-sig", newline="") as stream:
                            for row in csv.DictReader(stream):
                                try:
                                    angle = float(row["angle_degrees"])
                                    tilt = float(row["tilt_degrees"])
                                except (KeyError, TypeError, ValueError):
                                    continue
                                if not math.isfinite(angle) or not math.isfinite(tilt):
                                    continue
                                samples.append({"utc": row.get("utc", ""), "angle_degrees": angle,
                                                "tilt_degrees": tilt})
                except subprocess.CalledProcessError as exc:
                    output = (exc.stdout or b"").decode("utf-8", errors="replace").strip()
                    error = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
                    raise ValidationError(error or output or "The turntable telemetry monitor failed.") from exc
                except (OSError, subprocess.SubprocessError) as exc:
                    raise ValidationError(f"The turntable telemetry monitor could not complete: {exc}.") from exc
                if len(samples) < 2:
                    raise ValidationError("The turntable telemetry monitor returned fewer than two valid samples.")
                unwrapped_delta = 0.0
                for previous, current in zip(samples, samples[1:]):
                    delta = current["angle_degrees"] - previous["angle_degrees"]
                    if delta > 180.0:
                        delta -= 360.0
                    elif delta < -180.0:
                        delta += 360.0
                    unwrapped_delta += delta
                final_sample = samples[-1]
                self._remember_turntable_position_locked({"turntable_device_id": address,
                    "profile_id": profile["id"], "angle_degrees": final_sample["angle_degrees"],
                    "tilt_degrees": final_sample["tilt_degrees"]})
                return {"state": "completed", "command": "monitor", "duration_seconds": duration,
                        "sample_count": len(samples), "net_rotation_degrees": unwrapped_delta,
                        "samples": samples, "output": output,
                        "turntable_device_id": address, "profile_id": profile["id"]}
            if command == "spin-start":
                stop_path = self._state_root / ".revopoint-turntable-spin.stop"
                heartbeat_path = Path(str(stop_path) + ".heartbeat")
                log_path = self._state_root / ".revopoint-turntable-spin.log"
                self._recover_stale_turntable_spin_files(stop_path, heartbeat_path, log_path)
                try:
                    heartbeat_path.touch(exist_ok=True)
                    log_stream = log_path.open("xb")
                except OSError as exc:
                    heartbeat_path.unlink(missing_ok=True)
                    raise ValidationError(f"Could not create the turntable-control log: {exc}.") from exc
                startup_info = subprocess.STARTUPINFO()
                startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                try:
                    process = subprocess.Popen([str(executable), "continuous-spin", str(stop_path), *profile_args],
                        cwd=str(self._repository_root), stdin=subprocess.DEVNULL, stdout=log_stream,
                        stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW,
                        startupinfo=startup_info)
                except OSError as exc:
                    log_stream.close()
                    log_path.unlink(missing_ok=True)
                    heartbeat_path.unlink(missing_ok=True)
                    raise ValidationError(f"The turntable could not be started: {exc}.") from exc
                log_stream.close()
                self._turntable_spin = _TurntableSpinJob(process, stop_path, heartbeat_path, log_path, address,
                    profile["id"], datetime.now(timezone.utc).isoformat(), time.monotonic(),
                    self._live_preview.process, self._live_preview.ready_path)
                threading.Thread(target=self._maintain_turntable_spin_heartbeat,
                    args=(self._turntable_spin,), daemon=True, name="cadyx-turntable-heartbeat").start()
                return self._turntable_spin_snapshot_locked()

            expected_fields = {"query", "zero", "stop"}
            if command in expected_fields and value is not None:
                raise ValidationError(f"Turntable {command} does not accept a value.")
            if command in {"speed", "rotate", "tilt"}:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValidationError(f"Turntable {command} requires a finite numeric value.")
                lower, upper = {"speed": (18.0, 90.0), "rotate": (-30.0, 30.0),
                                "tilt": (-10.0, 10.0)}[command]
                if not lower <= value <= upper or command == "rotate" and abs(value) < 0.1:
                    raise ValidationError(f"Turntable {command} value must be between {lower:g} and {upper:g}.")
            elif command not in expected_fields:
                raise ValidationError("The turntable command requires a value or a supported action.")
            arguments = [str(executable), command, *([format(value, ".15g")] if value is not None else []),
                         *profile_args]
            timeout = 15 if command in {"query", "speed", "stop"} else 45
            try:
                result = subprocess.run(arguments, cwd=str(self._repository_root), capture_output=True,
                    timeout=timeout, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
                output = result.stdout.decode("utf-8", errors="replace").strip()
            except subprocess.CalledProcessError as exc:
                output = (exc.stdout or b"").decode("utf-8", errors="replace").strip()
                error = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
                raise ValidationError(error or output or "The turntable command failed.") from exc
            except (OSError, subprocess.SubprocessError) as exc:
                raise ValidationError(f"The turntable command could not be completed: {exc}.") from exc
            response: dict[str, Any] = {"state": "completed", "command": command, "output": output,
                                        "turntable_device_id": address, "profile_id": profile["id"]}
            for line in output.splitlines():
                if line.startswith("CADYX_STATUS\t"):
                    fields = line.split("\t")
                    if len(fields) == 5:
                        try:
                            response["angle_degrees"], response["tilt_degrees"], \
                                response["rotation_period_seconds"], response["tilt_speed"] = map(float, fields[1:])
                            response["angle_degrees"] = _normalize_turntable_angle(response["angle_degrees"])
                        except ValueError:
                            pass
            angle_match = re.search(r"angle after(?: zero)?:\s*(-?\d+(?:\.\d+)?)", output)
            tilt_match = re.search(r"tilt after(?: zero)?:\s*(-?\d+(?:\.\d+)?)", output)
            if angle_match:
                response["angle_degrees"] = _normalize_turntable_angle(float(angle_match.group(1)))
            if tilt_match:
                response["tilt_degrees"] = float(tilt_match.group(1))
            if command == "speed":
                response["rotation_period_seconds"] = float(value)
            self._remember_turntable_position_locked(response)
            return response

    def _remember_turntable_position_locked(self, response: dict[str, Any]) -> None:
        device_id = response.get("turntable_device_id")
        profile_id = response.get("profile_id")
        if not isinstance(device_id, str) or not isinstance(profile_id, str):
            return
        previous = self._last_turntable_position
        if (previous is None or previous.get("turntable_device_id") != device_id
                or previous.get("profile_id") != profile_id
                or time.monotonic() - self._last_turntable_position_at > _TURNTABLE_POSITION_CACHE_SECONDS):
            position: dict[str, Any] = {}
        else:
            position = dict(previous)
        for key in ("angle_degrees", "tilt_degrees", "rotation_period_seconds", "tilt_speed"):
            candidate = response.get(key)
            if isinstance(candidate, (int, float)) and not isinstance(candidate, bool) and math.isfinite(candidate):
                position[key] = float(candidate)
        if "angle_degrees" not in position:
            return
        self._last_turntable_position = {
            **position,
            "state": "cached",
            "command": "query",
            "turntable_device_id": device_id,
            "profile_id": profile_id,
        }
        self._last_turntable_position_at = time.monotonic()

    def cached_turntable_position(self, turntable_device_id: str | None = None,
                                  profile_id: str | None = None,
                                  allow_stale: bool = False) -> dict[str, Any] | None:
        with self._guard:
            position = self._last_turntable_position
            if position is None:
                return None
            if turntable_device_id and position["turntable_device_id"] != turntable_device_id.upper():
                return None
            if profile_id and position["profile_id"] != profile_id:
                return None
            age_seconds = max(0.0, time.monotonic() - self._last_turntable_position_at)
            if age_seconds > _TURNTABLE_POSITION_CACHE_SECONDS and not allow_stale:
                return None
            return {**position, "age_seconds": age_seconds}

    @staticmethod
    def _maintain_turntable_spin_heartbeat(job: _TurntableSpinJob) -> None:
        interval = threading.Event()
        while job.process.poll() is None and not job.stop_path.exists():
            if job.preview_process.poll() is not None or not job.preview_ready_path.is_file():
                try:
                    with job.stop_path.open("xb"):
                        pass
                except (FileExistsError, OSError):
                    pass
                return
            try:
                job.heartbeat_path.touch()
            except OSError:
                try:
                    with job.stop_path.open("xb"):
                        pass
                except (FileExistsError, OSError):
                    pass
                return
            interval.wait(2)

    def _resolve_turntable_control_profile(self, device_id: str | None,
                                           profile_id: str | None) -> tuple[dict[str, Any], str]:
        catalog_path = self._repository_root / "tools" / "revopoint" / "devices.json"
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            profiles = [item for item in catalog.get("turntables", [])
                        if item.get("control_protocol") == "revopoint-dual-axis-v1"]
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValidationError("The supported turntable catalog could not be read.") from exc
        if profile_id is None:
            profile_id = profiles[0].get("id") if len(profiles) == 1 else None
        profile = next((item for item in profiles if item.get("id") == profile_id), None)
        if profile is None:
            raise ValidationError("Select a supported turntable in device settings.")
        if device_id is None:
            tables = self.list_connected_devices()["turntables"]
            matching = [item for item in tables if item.get("profile_id") == profile_id]
            if len(matching) != 1:
                raise ValidationError("Select the connected turntable in device settings.")
            device_id = matching[0]["device_id"]
        if not isinstance(device_id, str) or not re.fullmatch(r"[0-9A-Fa-f]{12}", device_id):
            raise ValidationError("The selected turntable address is invalid.")
        return profile, device_id.upper()

    def _turntable_spin_snapshot_locked(self) -> dict[str, Any]:
        job = self._turntable_spin
        if job is None:
            return {"state": "idle"}
        return_code = job.process.poll()
        output = _read_log_tail(job.log_path, 4096).strip()
        spin_started = any(line.startswith("CADYX_SPIN\t") for line in output.splitlines())
        if return_code is None:
            if (not spin_started and not job.stop_path.exists()
                    and time.monotonic() - job.started_monotonic >= 15):
                try:
                    with job.stop_path.open("xb"):
                        pass
                except FileExistsError:
                    pass
                except OSError:
                    return {"state": "failed", "message": "The turntable startup timed out and its stop signal could not be written.",
                            "turntable_device_id": job.device_id, "started_at": job.started_at}
            state = "stopping" if job.stop_path.exists() else "running" if spin_started else "starting"
            snapshot: dict[str, Any] = {"state": state,
                                        "turntable_device_id": job.device_id, "started_at": job.started_at}
            if spin_started:
                for line in reversed(output.splitlines()):
                    if line.startswith("CADYX_SPIN\t"):
                        try:
                            _, angle, tilt = line.split("\t")
                            snapshot["angle_degrees"] = _normalize_turntable_angle(float(angle))
                            snapshot["tilt_degrees"] = float(tilt)
                        except ValueError:
                            pass
                        break
            if state == "starting" and output:
                snapshot["message"] = output.splitlines()[-1]
            return snapshot
        stop_requested = job.stop_path.exists()
        for path in (job.stop_path, job.heartbeat_path, job.log_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        self._turntable_spin = None
        final_position = None
        if spin_started:
            for line in reversed(output.splitlines()):
                if not line.startswith("CADYX_SPIN\t"):
                    continue
                try:
                    _, angle, tilt = line.split("\t")
                    final_position = {
                        "angle_degrees": _normalize_turntable_angle(float(angle)),
                        "tilt_degrees": float(tilt),
                    }
                    self._remember_turntable_position_locked({
                        **final_position,
                        "turntable_device_id": job.device_id,
                        "profile_id": job.profile_id,
                    })
                except ValueError:
                    pass
                break
        if return_code != 0 or not spin_started and not stop_requested:
            return {"state": "failed", "return_code": return_code, "message": output}
        return {"state": "idle", "message": output, **(final_position or {})}

    def _active_turntable_controller_process_ids(self) -> list[int]:
        if os.name != "nt":
            return []
        import ctypes
        from ctypes import wintypes

        class ProcessEntry32(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                        ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32)]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32)]
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                        wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ValidationError("Could not inspect stale turntable controller processes.")
        expected_path = os.path.normcase(str(self._repository_root / "tools" / "revopoint" / "build"
                                             / "TurntableController.exe"))
        process_ids: list[int] = []
        try:
            entry = ProcessEntry32()
            entry.dwSize = ctypes.sizeof(ProcessEntry32)
            more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                if entry.szExeFile.casefold() == "turntablecontroller.exe":
                    handle = kernel32.OpenProcess(0x1000, False, entry.th32ProcessID)
                    if not handle:
                        process_ids.append(int(entry.th32ProcessID))
                        more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
                        continue
                    try:
                        image_path = ctypes.create_unicode_buffer(32768)
                        image_length = wintypes.DWORD(len(image_path))
                        if (not kernel32.QueryFullProcessImageNameW(handle, 0, image_path,
                                ctypes.byref(image_length))
                                or os.path.normcase(image_path.value) == expected_path):
                            process_ids.append(int(entry.th32ProcessID))
                    finally:
                        kernel32.CloseHandle(handle)
                more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        return process_ids

    def _matching_live_preview_process_ids(self, capture_executable: Path, frames_path: Path,
                                           stop_path: Path, ready_path: Path) -> list[int]:
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell is None:
            raise ValidationError("Windows PowerShell is required to recover an orphaned live-preview process.")

        def quote(value: Path) -> str:
            return "'" + str(value).replace("'", "''") + "'"

        script = (f"$exe={quote(capture_executable)}; $frames={quote(frames_path)}; "
                  f"$stop={quote(stop_path)}; $ready={quote(ready_path)}; "
                  "$ids = @(Get-CimInstance Win32_Process -Filter \"Name = 'CaptureY16.exe'\" | "
                  "Where-Object { $_.ExecutablePath -ieq $exe -and $_.CommandLine.Contains('--live-only') "
                  "-and $_.CommandLine.Contains($frames) -and $_.CommandLine.Contains($stop) "
                  "-and $_.CommandLine.Contains($ready) } | Select-Object -ExpandProperty ProcessId); "
                  "ConvertTo-Json -Compress -InputObject @($ids)")
        try:
            result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=8, check=True,
                creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValidationError(f"Could not inspect the orphaned live-preview process: {exc}.") from exc
        output = result.stdout.strip()
        if not output:
            return []
        try:
            decoded = json.loads(output)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValidationError("Windows PowerShell returned invalid live-preview process data.") from exc
        values = decoded if isinstance(decoded, list) else [decoded]
        return [int(value) for value in values if isinstance(value, int) and value > 0]

    def _stop_orphaned_live_preview_processes(self, capture_executable: Path, frames_path: Path,
                                              stop_path: Path, ready_path: Path) -> list[int]:
        if not any(path.exists() for path in (frames_path, stop_path, ready_path)):
            return []
        process_ids = self._matching_live_preview_process_ids(
            capture_executable, frames_path, stop_path, ready_path,
        )
        if not process_ids:
            return []
        try:
            with stop_path.open("xb"):
                pass
        except FileExistsError:
            pass
        except OSError as exc:
            raise ValidationError(f"Could not signal the orphaned live preview to stop: {exc}.") from exc

        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell is None:
            raise ValidationError("Windows PowerShell is required to stop an orphaned live-preview process.")
        id_list = ",".join(str(pid) for pid in process_ids)
        exe = "'" + str(capture_executable).replace("'", "''") + "'"
        frames = "'" + str(frames_path).replace("'", "''") + "'"
        stop = "'" + str(stop_path).replace("'", "''") + "'"
        ready = "'" + str(ready_path).replace("'", "''") + "'"
        script = (f"$exe={exe}; $frames={frames}; $stop={stop}; $ready={ready}; "
                  f"$ids=@({id_list}); foreach($processId in $ids) {{ "
                  "$p=Get-CimInstance Win32_Process -Filter \"ProcessId = $processId\"; "
                  "if($p -and $p.ExecutablePath -ieq $exe -and $p.CommandLine.Contains('--live-only') "
                  "-and $p.CommandLine.Contains($frames) -and $p.CommandLine.Contains($stop) "
                  "-and $p.CommandLine.Contains($ready)) { "
                  "try { Wait-Process -Id $processId -Timeout 4 -ErrorAction Stop } catch { } "
                  "$p=Get-CimInstance Win32_Process -Filter \"ProcessId = $processId\"; "
                  "if($p -and $p.ExecutablePath -ieq $exe -and $p.CommandLine.Contains('--live-only') "
                  "-and $p.CommandLine.Contains($frames) -and $p.CommandLine.Contains($stop) "
                  "-and $p.CommandLine.Contains($ready)) { Stop-Process -Id $processId -Force } } }")
        try:
            subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=8, check=True,
                creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValidationError(f"The orphaned live-preview process could not be stopped: {exc}.") from exc
        still_running = self._matching_live_preview_process_ids(
            capture_executable, frames_path, stop_path, ready_path,
        )
        if still_running:
            raise ValidationError("The orphaned live-preview process is still running after its stop request.")
        return process_ids

    def _recover_stale_turntable_spin_files(self, stop_path: Path, heartbeat_path: Path,
                                            log_path: Path) -> str | None:
        legacy_heartbeat_path = self._state_root / ".revopoint-turntable-spin.heartbeat"
        stale_paths = (stop_path, heartbeat_path, legacy_heartbeat_path, log_path)
        if not any(path.exists() for path in stale_paths):
            return None
        active = self._active_turntable_controller_process_ids()
        if active:
            raise ConflictError("A Revopoint turntable controller is still active; stop it before recovering stale spin files.",
                                code="TURNTABLE_CONTROLLER_ACTIVE")
        archived_log = None
        if log_path.is_file():
            suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            archived_path = log_path.with_name(f"{log_path.stem}.stale-{suffix}{log_path.suffix}")
            try:
                log_path.replace(archived_path)
            except OSError as exc:
                raise ValidationError(f"The stale turntable log could not be preserved: {exc}.") from exc
            archived_log = archived_path.name
        for path in (stop_path, heartbeat_path, legacy_heartbeat_path):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                raise ValidationError(f"A stale turntable signal could not be cleared: {exc}.") from exc
        return archived_log

    def _stop_turntable_spin_locked(self) -> dict[str, Any]:
        job = self._turntable_spin
        if job is None:
            stop_path = self._state_root / ".revopoint-turntable-spin.stop"
            heartbeat_path = Path(str(stop_path) + ".heartbeat")
            log_path = self._state_root / ".revopoint-turntable-spin.log"
            archived_log = self._recover_stale_turntable_spin_files(stop_path, heartbeat_path, log_path)
            return ({"state": "idle", "message": "Stale turntable stop state cleared.",
                     "archived_log": archived_log} if archived_log else {"state": "idle"})
        if job.process.poll() is None:
            try:
                with job.stop_path.open("xb"):
                    pass
            except FileExistsError:
                pass
            except OSError as exc:
                raise ValidationError(f"Could not signal the turntable to stop: {exc}.") from exc
            try:
                job.process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                job.process.terminate()
                try:
                    job.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    job.process.kill()
                    job.process.wait(timeout=2)
                log_started = False
                log_started = any(line.startswith("CADYX_SPIN\t") for line in
                                  _read_log_tail(job.log_path, 4096).splitlines())
                self._turntable_spin_snapshot_locked()
                if not log_started:
                    return {"state": "failed", "message": "The turntable driver did not confirm movement before it was stopped during startup.",
                            "turntable_device_id": job.device_id, "profile_id": job.profile_id}
                stop_result = self.turntable_control("stop", job.device_id, job.profile_id)
                return {"state": "idle", "message": "The continuous-spin driver stopped responding; the table STOP command was sent.",
                        "stop_result": stop_result, "turntable_device_id": job.device_id,
                        "profile_id": job.profile_id}
        return self._turntable_spin_snapshot_locked()

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
            if self._turntable_spin is not None and self._turntable_spin.process.poll() is None:
                raise ConflictError("Stop continuous table rotation before starting a 360 scan.",
                                    code="TURNTABLE_SPIN_ACTIVE")
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
            support_directory = project_directory / f"scan_support_{run_id}"
            try:
                support_directory.mkdir()
            except FileExistsError as exc:
                run_directory.rmdir()
                raise ConflictError("A scan support folder already exists; no files were replaced.", code="SCAN_LOG_EXISTS") from exc
            log_path = support_directory / f".cadyx-scan360-{stamp}.log"
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

    def start_live_preview(self, scanner_device_id: str | None = None, fps: int = 10) -> dict[str, Any]:
        if os.name != "nt":
            raise ValidationError("Live Revopoint preview is available only on the Windows scanner host.")
        if isinstance(fps, bool) or not isinstance(fps, int) or fps not in {5, 10, 15}:
            raise ValidationError("Live scanner preview FPS must be 5, 10, or 15.")
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

        raw_path = self._state_root / ".revopoint-live.raw"
        frames_path = self._state_root / ".revopoint-live.raw.frames"
        stop_path = self._state_root / ".revopoint-live-preview.stop"
        ready_path = self._state_root / ".revopoint-live-preview.ready"
        log_path = self._state_root / ".revopoint-live-preview.log"
        with self._guard:
            if self._job is not None and self._job.process.poll() is None:
                raise ConflictError("Stop the 360 capture before starting a live scanner preview.", code="SCANNER_BUSY")
            if self._live_preview is not None and self._live_preview.process.poll() is None:
                return self._live_preview_snapshot_locked()
            self._live_preview = None
            self._stop_orphaned_live_preview_processes(
                capture_executable, frames_path, stop_path, ready_path,
            )
            for path in (raw_path, stop_path, ready_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise ValidationError(f"The previous live-preview buffer could not be cleared: {exc}.") from exc
            self._clear_live_frame_queue(frames_path)

            startup_info = subprocess.STARTUPINFO()
            startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup_info.wShowWindow = subprocess.SW_HIDE
            try:
                with log_path.open("wb") as log_stream:
                    process = subprocess.Popen(
                        [str(capture_executable), "--live-only", "--live-frames-dir", str(frames_path),
                         "--stop-file", str(stop_path), "--ready-file", str(ready_path), "--fps", str(fps),
                         "--device-id", scanner_device_id],
                        cwd=str(self._repository_root), stdin=subprocess.DEVNULL,
                        stdout=log_stream, stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=startup_info,
                    )
            except OSError as exc:
                raise ValidationError(f"The live scanner preview could not be started: {exc}.") from exc
            self._live_preview = _LivePreviewJob(process, raw_path, frames_path, stop_path, ready_path, log_path)
            return self._live_preview_snapshot_locked()

    def live_preview_status(self) -> dict[str, Any]:
        with self._guard:
            if self._live_preview is None:
                return {"state": "idle"}
            return self._live_preview_snapshot_locked()

    def record_live_preview_client_metrics(self, value: Any) -> dict[str, Any]:
        metrics = _validate_live_preview_client_metrics(value)
        with self._guard:
            job = self._live_preview
            if job is None or job.process.poll() is not None:
                raise ConflictError("The live scanner preview is not running.", code="SCANNER_NOT_RUNNING")
            job.client_metrics = metrics
            job.client_metrics_at = datetime.now(timezone.utc)
            return {"recorded": True}

    def stop_live_preview(self) -> dict[str, Any]:
        with self._guard:
            job = self._live_preview
            if job is None:
                raw_path = self._state_root / ".revopoint-live.raw"
                frames_path = self._state_root / ".revopoint-live.raw.frames"
                stop_path = self._state_root / ".revopoint-live-preview.stop"
                ready_path = self._state_root / ".revopoint-live-preview.ready"
                capture_executable = self._repository_root / "tools" / "revopoint" / "build" / "CaptureY16.exe"
                orphaned = self._stop_orphaned_live_preview_processes(
                    capture_executable, frames_path, stop_path, ready_path,
                )
                for path in (stop_path, ready_path):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError as exc:
                        raise ValidationError(f"A stale live-preview signal could not be cleared: {exc}.") from exc
                self._clear_live_frame_queue(frames_path)
                if orphaned:
                    try:
                        raw_path.unlink(missing_ok=True)
                    except OSError as exc:
                        raise ValidationError(f"A stale live-preview buffer could not be removed: {exc}.") from exc
                return {"state": "idle", "recovered_orphan": bool(orphaned)}
            spin_status = self._turntable_spin_snapshot_locked()
            if spin_status.get("state") in {"starting", "running", "stopping"}:
                spin_status = self._stop_turntable_spin_locked()
                if spin_status.get("state") in {"starting", "running", "stopping"}:
                    raise ConflictError("Stop continuous table rotation before stopping the scanner preview.",
                                        code="TURNTABLE_SPIN_ACTIVE")
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
            self._clear_live_frame_queue(job.frames_path)
            return {"state": "idle"}

    def _live_preview_snapshot_locked(self) -> dict[str, Any]:
        assert self._live_preview is not None
        job = self._live_preview
        return_code = job.process.poll()
        if return_code is not None:
            message = _read_log_tail(job.log_path, 4096).strip()
            self._live_preview = None
            for path in (job.raw_path, job.stop_path, job.ready_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    message = f"{message}\nTemporary frame cleanup failed: {exc}.".strip()
            try:
                self._clear_live_frame_queue(job.frames_path)
            except ValidationError as exc:
                message = f"{message}\nTemporary frame queue cleanup failed: {exc}.".strip()
            return {"state": "failed", "return_code": return_code, "message": message}
        if not job.ready_path.is_file():
            return {"state": "starting"}
        try:
            mtime_ns = job.frames_path.stat().st_mtime_ns
        except OSError:
            return {"state": "starting"}
        snapshot: dict[str, Any] = {"state": "running", "frame_mtime_ns": mtime_ns}
        metrics = _read_live_preview_metrics(job.log_path)
        if metrics is not None:
            snapshot["metrics"] = metrics
        if job.client_metrics is not None and job.client_metrics_at is not None:
            snapshot["client_metrics"] = job.client_metrics
            snapshot["client_metrics_age_ms"] = max(
                0, int((datetime.now(timezone.utc) - job.client_metrics_at).total_seconds() * 1000),
            )
        return snapshot

    @staticmethod
    def _clear_live_frame_queue(frames_path: Path) -> None:
        try:
            if frames_path.exists() and not frames_path.is_dir():
                raise ValidationError("The live-preview frame queue path is not a directory.")
            frames_path.mkdir(parents=True, exist_ok=True)
            for pattern in ("frame-*.y16", "frame-*.y16.tmp"):
                for frame_path in frames_path.glob(pattern):
                    try:
                        frame_path.unlink()
                    except FileNotFoundError:
                        continue
            if not any(frames_path.iterdir()):
                frames_path.rmdir()
        except ValidationError:
            raise
        except OSError as exc:
            raise ValidationError(f"The temporary live-preview frame queue could not be cleared: {exc}.") from exc

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

    def is_running(self, project_id: str, *, nonblocking: bool = False) -> bool:
        # Device I/O can hold this guard for seconds. Display-only availability
        # must not wait for it; unknown scan state stays conservatively busy.
        if not self._guard.acquire(blocking=not nonblocking):
            return True
        try:
            return bool(
                self._job is not None
                and self._job.project_id == project_id
                and self._job.process.poll() is None
            )
        finally:
            self._guard.release()

    def _snapshot_locked(self) -> dict[str, Any]:
        assert self._job is not None
        return_code = self._job.process.poll()
        state = "running" if return_code is None else "completed" if return_code == 0 else "failed"
        log_tail = _read_log_tail(self._job.log_path, 64 * 1024)
        completed = _capture_log_progress(log_tail, self._job.frame_count)
        completed = max(completed, self._count_run_frames(
            self._job.project_directory, self._job.run_id, self._job.frame_count))
        capture_sweep_completed = f"sweep completed: {self._job.frame_count} steps" in log_tail.lower()
        alignment_failed = "table-map alignment failed" in log_tail.lower()
        preprocessing = self._preprocessing_payload(
            self._job.project_directory, self._job.run_id, self._job.frame_count, active=True)
        run_directory = self._run_directory(self._job.project_directory, self._job.run_id)
        manifest_path = self._support_directory(self._job.project_directory, self._job.run_id) / f"{self._job.run_id}_scan_360.json"
        alignment_status = self._alignment_status(self._job.project_directory, self._job.run_id)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            manifest = None
        manifest_ready = (isinstance(manifest, dict)
                          and manifest.get("frame_count") == self._job.frame_count
                          and manifest.get("preprocessing_status") == "completed")
        pipeline_outputs_ready = (completed == self._job.frame_count and capture_sweep_completed
                                  and not alignment_failed
                                  and preprocessing["preprocessing_status"] == "completed"
                                  and manifest_ready
                                  and alignment_status in {
                                      "aligned_provisional", "partial", "unavailable", "not_performed",
                                  })
        metadata_warning = return_code not in (None, 0) and pipeline_outputs_ready
        state = "running" if return_code is None else "completed" if pipeline_outputs_ready else "failed"
        if state == "completed" and not metadata_warning:
            completed = self._job.frame_count
        message = (f"Кадр {completed}/{self._job.frame_count}; очищено {preprocessing['preprocessed_frames']}/{self._job.frame_count}; маркеров стола найдено {preprocessing['detected_table_marker_count']}, точек удалено {preprocessing['removed_table_marker_points']}; ошибок фильтра {preprocessing['preprocessing_failed_frames']}" if state == "running" else
                   f"Съёмка завершилась, но очистка кадров не прошла ({preprocessing['preprocessed_frames']}/{self._job.frame_count}); исходники сохранены." if preprocessing["preprocessing_status"] == "failed" else
                   f"{self._job.frame_count} кадров сохранены, но привязка к центру карты стола не прошла; исходные кадры сохранены." if alignment_failed else
                   f"{self._job.frame_count} кадров сохранены, но привязка или манифест не подтверждены; исходники сохранены." if capture_sweep_completed and state == "failed" else
                   f"{self._job.frame_count} кадров, очистка, привязка и манифест проверены; процесс вернул код {return_code}." if metadata_warning else
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
        snapshot["table_alignment_status"] = alignment_status
        if state == "completed" and not metadata_warning:
            message = {
                "aligned_provisional": "Все кадры привязаны к центру карты стола; физический масштаб остаётся предварительным.",
                "partial": "Часть кадров привязана к центру карты стола; остальные исходники сохранены для последующей привязки.",
                "unavailable": "Маркеры стола не удалось надёжно определить; привязку можно выполнить позднее по осям и плоскостям облака или сетки.",
                "pending": "Кадры сохранены; отчёт о привязке к карте стола отсутствует.",
                "not_performed": "Кадры сохранены в координатах камеры без привязки к столу; их можно сшить геометрически.",
                "failed": "Кадры сохранены, но отчёт о привязке к карте стола повреждён или не прошёл проверку.",
            }.get(snapshot["table_alignment_status"], message)
            if preprocessing["table_marker_detection_status"] == "completed":
                message += (f" Обнаружено областей маркеров: {preprocessing['detected_table_marker_count']}; "
                            f"удалено точек маркеров: {preprocessing['removed_table_marker_points']}.")
            snapshot["message"] = message
        return snapshot

    @staticmethod
    def _preprocessing_payload(project_directory: Path, run_id: str,
                               frame_count: int, *, active: bool = False) -> dict[str, Any]:
        report_path = (Scan360CaptureController._support_directory(project_directory, run_id)
                       / f"{run_id}_preprocessing.json")
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if not isinstance(report, dict):
                raise ValueError("invalid preprocessing report")
            frames = report.get("frames", [])
            failed = report.get("failed_frames", [])
            if not isinstance(frames, list) or not isinstance(failed, list):
                raise ValueError("invalid per-frame cleanup rows")
            cleaned_count = report.get("cleaned_frame_count", len(frames))
            failed_count = report.get("failed_frame_count", len(failed))
            if (any(isinstance(count, bool) or not isinstance(count, int)
                    or not 0 <= count <= frame_count for count in (cleaned_count, failed_count))
                    or cleaned_count + failed_count > frame_count
                    or (report.get("state") == "completed"
                        and (cleaned_count != frame_count or failed_count != 0))):
                raise ValueError("invalid preprocessing frame counters")
            marker_detection_count = sum(
                frame.get("table_marker_detection") == "completed" for frame in frames
                if isinstance(frame, dict)
            )
            marker_status = ("completed" if report.get("state") == "completed"
                             and cleaned_count == frame_count == marker_detection_count else
                             "failed" if report.get("state") == "failed" else
                             "running" if report.get("state") == "running" else "not_available")
            return {
                "preprocessing_status": report.get("state", "failed"),
                "preprocessed_frames": cleaned_count,
                "preprocessing_failed_frames": failed_count,
                "preprocessing_frame_count": frame_count,
                "preprocessing_pending_frames": max(
                    0, frame_count - cleaned_count
                    - failed_count),
                "table_marker_detection_status": marker_status,
                "detected_table_marker_count": report.get(
                    "detected_table_marker_count",
                    sum(frame.get("detected_table_markers", 0) for frame in frames
                        if isinstance(frame, dict))),
                "removed_table_marker_points": report.get(
                    "removed_table_marker_points",
                    sum(frame.get("removed_table_marker_points", 0) for frame in frames
                        if isinstance(frame, dict))),
            }
        except FileNotFoundError:
            return {"preprocessing_status": "starting" if active else "not_available", "preprocessed_frames": 0,
                    "preprocessing_failed_frames": 0, "preprocessing_frame_count": frame_count,
                    "preprocessing_pending_frames": frame_count if active else 0,
                    "table_marker_detection_status": "starting" if active else "not_available",
                    "detected_table_marker_count": 0, "removed_table_marker_points": 0}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {"preprocessing_status": "failed", "preprocessed_frames": 0,
                    "preprocessing_failed_frames": frame_count, "preprocessing_frame_count": frame_count,
                    "preprocessing_pending_frames": 0, "table_marker_detection_status": "failed",
                    "detected_table_marker_count": 0, "removed_table_marker_points": 0}

    @staticmethod
    def _alignment_status(project_directory: Path, run_id: str) -> str:
        run_directory = Scan360CaptureController._support_directory(project_directory, run_id)
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
        status = result.get("status") if isinstance(result, dict) else None
        return status if isinstance(status, str) and status in {
            "aligned_provisional", "partial", "unavailable", "not_performed", "pending", "failed",
        } else "failed"

    @staticmethod
    def _manifest_frame_count(project_directory: Path, run_id: str) -> int:
        manifest = Scan360CaptureController._support_directory(project_directory, run_id) / f"{run_id}_scan_360.json"
        try:
            count = json.loads(manifest.read_text(encoding="utf-8")).get("frame_count")
        except (OSError, ValueError, TypeError, json.JSONDecodeError, AttributeError):
            return _DEFAULT_FRAME_COUNT
        return count if isinstance(count, int) and not isinstance(count, bool) and 1 <= count <= 360 else _DEFAULT_FRAME_COUNT

    @staticmethod
    def _count_run_frames(project_directory: Path, run_id: str, frame_count: int | None = None) -> int:
        run_directory = Scan360CaptureController._support_directory(project_directory, run_id)
        count = frame_count or Scan360CaptureController._manifest_frame_count(project_directory, run_id)
        return sum(1 for index in range(count)
                   if (run_directory / f"{run_id}_pose_{index:03d}.raw").is_file()
                   or (run_directory / f"{run_id}_pose_pose_{index:03d}.raw").is_file())

    @staticmethod
    def _run_directory(project_directory: Path, run_id: str) -> Path:
        run_directory = project_directory / run_id
        return run_directory if run_directory.is_dir() else project_directory

    @staticmethod
    def _support_directory(project_directory: Path, run_id: str) -> Path:
        root = project_directory.parent if project_directory.name == run_id else project_directory
        support = root / f"scan_support_{run_id}"
        if support.is_dir() and not support.is_symlink():
            return support
        return Scan360CaptureController._run_directory(project_directory, run_id)

    @classmethod
    def _latest_completed_snapshot(cls, project_directory: Path) -> dict[str, Any]:
        captures = sorted(
            (*project_directory.glob("revopoint_*_poses.csv"),
             *project_directory.glob("revopoint_*/revopoint_*_poses.csv"),
             *project_directory.glob("scan_support_revopoint_*/revopoint_*_poses.csv")),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        for pose_log in captures:
            match = re.fullmatch(r"(revopoint_\d{8}T\d{6}Z)_poses\.csv", pose_log.name)
            if not match:
                continue
            run_id = match.group(1)
            run_directory = (project_directory / run_id if pose_log.parent.name == f"scan_support_{run_id}"
                             else pose_log.parent)
            frame_count = cls._manifest_frame_count(run_directory, run_id)
            completed = cls._count_run_frames(run_directory, run_id, frame_count)
            stamp = run_id.removeprefix("revopoint_")
            scan_log = cls._support_directory(run_directory, run_id) / f".cadyx-scan360-{stamp}.log"
            if not scan_log.is_file():
                scan_log = project_directory / f".cadyx-scan360-{stamp}.log"
            completed_turn, alignment_failed = _scan_capture_log_markers(scan_log, frame_count)
            if completed == frame_count and completed_turn:
                manifest = cls._support_directory(run_directory, run_id) / f"{run_id}_scan_360.json"
                preprocessing = cls._preprocessing_payload(run_directory, run_id, frame_count)
                alignment_status = cls._alignment_status(run_directory, run_id)
                # Legacy sweeps predate JSON reports. New captures must verify
                # the same final artifacts after restart as while running.
                legacy_capture = (not manifest.is_file()
                                  and preprocessing["preprocessing_status"] == "not_available"
                                  and alignment_status == "pending")
                try:
                    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    manifest_data = None
                # Manifest-only repair predates preprocessing fields. Accept its
                # explicit, complete frame inventory, but never treat a damaged
                # modern manifest or a failed preprocessing report as completed.
                repaired_manifest = (
                    isinstance(manifest_data, dict)
                    and manifest_data.get("format") == "revopoint-y16-turntable-capture-v2"
                    and "preprocessing_status" not in manifest_data
                    and "preprocessing_report" not in manifest_data
                    and manifest_data.get("table_alignment_status") == alignment_status
                    and isinstance(manifest_data.get("frames"), list)
                    and len(manifest_data["frames"]) == frame_count
                    and all(
                        isinstance(frame, dict)
                        and type(frame.get("index")) is int
                        and frame["index"] == index
                        and frame.get("raw_file") in (
                            f"{run_id}_pose_{index:03d}.raw",
                            f"{run_id}_pose_pose_{index:03d}.raw",
                        )
                        and frame.get("ply_file") == frame["raw_file"][:-4] + ".ply"
                        and frame.get("metadata_file") == frame["raw_file"][:-4] + ".json"
                        for index, frame in enumerate(manifest_data["frames"])
                    )
                )
                manifest_ready = (isinstance(manifest_data, dict)
                                  and type(manifest_data.get("frame_count")) is int
                                  and manifest_data["frame_count"] == frame_count
                                  and (manifest_data.get("preprocessing_status") == "completed"
                                       or repaired_manifest))
                reports_ready = ((preprocessing["preprocessing_status"] == "completed"
                                  or (repaired_manifest
                                      and preprocessing["preprocessing_status"] == "not_available"))
                                 and alignment_status in {
                                     "aligned_provisional", "partial", "unavailable", "not_performed",
                                 })
                verified = not alignment_failed and (
                    legacy_capture or (manifest_ready and reports_ready)
                )
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
                if not verified:
                    message = ("Кадры сохранены, но отчёты очистки, привязки или итоговый "
                               "манифест не подтверждены; исходники сохранены.")
                elif preprocessing["preprocessing_status"] == "not_available":
                    message += " Отчёта очистки исходных кадров до сшивки для этой серии нет."
                return {
                    "state": "completed" if verified else "failed",
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
        manifest = Scan360CaptureController._support_directory(project_directory, run_id) / f"{run_id}_scan_360.json"
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
            if cls._alignment_status(project_directory, run_id) in ("aligned_provisional", "partial"):
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
            for index in range(index, -1, -1):
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
