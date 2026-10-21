"""Capture live Y16 and RGB during one uninterrupted 360-degree table move, then fuse views."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[2]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from tools.revopoint.reproject_y16 import reproject_files


FRAME_BYTES = 640 * 400 * 2
TARGET_ANGLES = tuple(float(value) for value in range(0, 360, 30))
API_ROOT = "http://127.0.0.1:8888/api/v1/scanner"
TABLE_NAME = "REVO_DUAL_AXIS_TABLE"
ADVERTISEMENT_UUID = "0000FFE1-0000-1000-8000-00805F9B34FB"
SERVICE_UUID = "0000FFE0-0000-1000-8000-00805F9B34FB"
CHARACTERISTIC_UUID = "0000FFE1-0000-1000-8000-00805F9B34FB"


def request_preview(method: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{API_ROOT}/live-preview", data=data, method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))["preview"]
    except (OSError, urllib.error.URLError, json.JSONDecodeError, KeyError) as exc:
        raise RuntimeError(f"Could not use the local CADYX live-preview API: {exc}") from exc


def capture_frames(frame_path: Path, frame_dir: Path, csv_path: Path,
                   stop: threading.Event, rotation_started: threading.Event) -> None:
    """Copy each atomically published live frame once, keyed by its filesystem timestamp."""
    frame_dir.mkdir(parents=True, exist_ok=True)
    seen_mtime = -1
    index = 0
    with csv_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["index", "file", "frame_mtime_utc", "observed_utc", "bytes"])
        while not stop.is_set():
            if not rotation_started.is_set():
                time.sleep(0.005)
                continue
            try:
                before = frame_path.stat()
                if before.st_mtime_ns == seen_mtime:
                    time.sleep(0.005)
                    continue
                payload = frame_path.read_bytes()
                after = frame_path.stat()
            except OSError:
                time.sleep(0.005)
                continue
            if len(payload) != FRAME_BYTES or before.st_mtime_ns != after.st_mtime_ns:
                time.sleep(0.005)
                continue
            seen_mtime = after.st_mtime_ns
            name = f"frame_{index:05d}.raw"
            (frame_dir / name).write_bytes(payload)
            mtime = datetime.fromtimestamp(after.st_mtime, timezone.utc).isoformat()
            observed = datetime.now(timezone.utc).isoformat()
            writer.writerow([index, name, mtime, observed, len(payload)])
            output.flush()
            index += 1
            time.sleep(0.005)


def load_encoder_rows(path: Path) -> list[tuple[datetime, float]]:
    rows: list[tuple[datetime, float]] = []
    with path.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            if row.get("event") not in {"baseline", "command_sent", "telemetry", "target_reached"}:
                continue
            if not row.get("utc") or not row.get("unwrapped_degrees"):
                continue
            rows.append((datetime.fromisoformat(row["utc"].replace("Z", "+00:00")),
                         float(row["unwrapped_degrees"])))
    return sorted(rows)


def interpolate_angle(timestamp: datetime, samples: list[tuple[datetime, float]]) -> float:
    if not samples:
        raise ValueError("The turntable log contains no angle telemetry.")
    if timestamp <= samples[0][0]:
        return samples[0][1]
    if timestamp >= samples[-1][0]:
        return samples[-1][1]
    for (left_time, left_angle), (right_time, right_angle) in zip(samples, samples[1:]):
        if left_time <= timestamp <= right_time:
            span = (right_time - left_time).total_seconds()
            if span <= 0:
                return right_angle
            fraction = (timestamp - left_time).total_seconds() / span
            return left_angle + (right_angle - left_angle) * fraction
    return samples[-1][1]


def select_uniform_frames(frame_rows: list[dict[str, str]],
                          samples: list[tuple[datetime, float]]) -> list[tuple[dict[str, str], float]]:
    if len(frame_rows) < len(TARGET_ANGLES):
        raise ValueError(f"Only {len(frame_rows)} Y16 frames were archived; at least {len(TARGET_ANGLES)} are required.")
    positioned = []
    for row in frame_rows:
        timestamp = datetime.fromisoformat(row["frame_mtime_utc"].replace("Z", "+00:00"))
        angle = interpolate_angle(timestamp, samples)
        positioned.append((row, angle))
    direction = 1.0 if samples[-1][1] >= samples[0][1] else -1.0
    start = samples[0][1]
    selected: list[tuple[dict[str, str], float]] = []
    used: set[str] = set()
    for target in TARGET_ANGLES:
        candidates = [(row, angle) for row, angle in positioned if row["file"] not in used]
        row, angle = min(candidates, key=lambda item: abs((item[1] - start) * direction - target))
        delta = (angle - start) * direction
        if abs(delta - target) > 8.0:
            raise ValueError(f"No distinct Y16 frame near {target:g} degrees (best delta {delta:.1f}).")
        used.add(row["file"])
        selected.append((row, angle))
    return selected


def pair_rgb_frames(depth_rows: list[dict[str, str]], rgb_csv: Path,
                    tolerance_ms: float = 45.0) -> list[dict[str, Any]]:
    rgb_rows = []
    with rgb_csv.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            rgb_rows.append((int(row["index"]), int(row["utc_epoch_us"])))
    result = [{"depth_frame": row["file"], "rgb_frame": None, "delta_ms": None} for row in depth_rows]
    candidates = []
    for row in depth_rows:
        depth_us = int(datetime.fromisoformat(row["frame_mtime_utc"].replace("Z", "+00:00")).timestamp() * 1_000_000)
        depth_index = int(row["index"])
        for rgb_index, rgb_us in rgb_rows:
            delta = abs(rgb_us - depth_us) / 1000
            if delta <= tolerance_ms:
                candidates.append((delta, depth_index, rgb_index))
    used_depth: set[int] = set()
    used_rgb: set[int] = set()
    by_depth = {int(row["index"]): offset for offset, row in enumerate(depth_rows)}
    for delta, depth_index, rgb_index in sorted(candidates):
        if depth_index in used_depth or rgb_index in used_rgb:
            continue
        used_depth.add(depth_index)
        used_rgb.add(rgb_index)
        result[by_depth[depth_index]]["rgb_frame"] = rgb_index
        result[by_depth[depth_index]]["delta_ms"] = round(delta, 3)
    return result


def wait_ready(path: Path, process: subprocess.Popen[bytes], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return
        if process.poll() is not None:
            raise RuntimeError(f"Capture process exited before its first frame (exit {process.returncode}).")
        time.sleep(0.05)
    raise TimeoutError(f"Timed out waiting for first capture frame: {path.name}")


def run_capture(project: Path, run_id: str, repository: Path) -> dict[str, Any]:
    project = project.resolve(strict=True)
    project_record = json.loads((project / "project.json").read_text(encoding="utf-8"))
    if project_record.get("format") != "cadyx-project":
        raise ValueError("The output directory is not a CADYX project.")
    if project.parent != (repository / "projects").resolve():
        raise ValueError("The target must be a direct child project of this repository's projects folder.")
    run_dir = project / run_id
    if run_dir.exists():
        raise FileExistsError(f"Refusing to overwrite scan output: {run_dir}")
    if shutil.disk_usage(project).free < 1_000_000_000:
        raise OSError("At least 1 GB of free disk space is required for the continuous RGB/Y16 burst.")

    tools = repository / "tools" / "revopoint"
    capture_rgb = tools / "build" / "CaptureRgb.exe"
    table = tools / "build" / "TurntableController.exe"
    live_path = repository / "projects" / ".revopoint-live.raw"
    q_path = tools / "calibration" / "scanners" / "revopoint-mini-y16" / "Q.bin"
    scale_path = tools / "calibration" / "scanners" / "revopoint-mini-y16" / "depth_scale.json"
    for path in (capture_rgb, table, q_path, scale_path):
        if not path.is_file():
            raise FileNotFoundError(f"Required scan input is missing: {path}")

    preview = request_preview("GET")
    if preview.get("state") not in {"running", "starting"}:
        preview = request_preview("POST", {})
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and preview.get("state") not in {"running", "failed"}:
            time.sleep(0.2)
            preview = request_preview("GET")
        if preview.get("state") != "running":
            raise RuntimeError(f"Could not start live preview: {preview}")
    if (not live_path.is_file() or live_path.stat().st_size != FRAME_BYTES
            or time.time() - live_path.stat().st_mtime > 2.0):
        raise RuntimeError("The live preview is marked running but its frame buffer is missing or malformed.")

    run_dir.mkdir()
    (run_dir / "depth_frames").mkdir()
    (run_dir / "rgb_frames").mkdir()
    shutil.copy2(q_path, run_dir / "Q.bin")
    shutil.copy2(scale_path, run_dir / "depth_scale.json")
    frame_rows_path = run_dir / "depth_frames.csv"
    rgb_prefix = run_dir / "rgb_frames" / "rgb"
    rgb_ready = run_dir / "rgb.ready"
    rgb_stop = run_dir / "rgb.stop"
    rgb_log = (run_dir / "rgb.log").open("wb")
    rgb_process: subprocess.Popen[bytes] | None = None
    stop_capture = threading.Event()
    rotation_started = threading.Event()
    watcher = threading.Thread(
        target=capture_frames,
        args=(live_path, run_dir / "depth_frames", frame_rows_path, stop_capture, rotation_started),
        daemon=True,
    )
    manifest: dict[str, Any] = {
        "format": "cadyx-continuous-turn-scan-v1",
        "run_id": run_id,
        "project_id": project_record["id"],
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "status": "capturing",
        "rotation_command": "+CT,TURNANGLE=360;",
        "motion_mode": "single continuous move; no intermediate stops",
        "depth_camera": "DepthCam Y16 640x400",
        "rgb_camera": "RGBCam MJPG 1280x800",
        "tilt_expected_degrees": 0,
        "depth_scale_status": "provisional_for_direct_UVC",
        "cloud_registration": "not_started",
    }
    (run_dir / "capture_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    turntable_args: list[str] = []
    controller_args = [str(table), "query"]
    turntable_log = (run_dir / "turntable.log").open("wb")
    try:
        query = subprocess.run(controller_args, cwd=repository, capture_output=True, timeout=20, check=True)
        query_text = (query.stdout + query.stderr).decode("utf-8", errors="replace")
        manifest["turntable_baseline_query"] = query_text
        if not any(line.startswith("+QR,TILTVALUE; => +DATA=0.0") for line in query_text.splitlines()):
            raise RuntimeError("The turntable tilt must be zero before starting a continuous scan.")
        import re
        address_match = re.search(r"Connected to REVO_DUAL_AXIS_TABLE at ([0-9A-Fa-f]+)", query_text)
        if not address_match:
            raise RuntimeError("Could not identify the connected turntable from its verified GATT name.")
        turntable_args = [str(table), "continuous-turn", str(run_dir / "turntable.csv"),
                          address_match.group(1), TABLE_NAME, ADVERTISEMENT_UUID, SERVICE_UUID, CHARACTERISTIC_UUID]
        table_key = hashlib.sha256(address_match.group(1).encode("utf-8")).hexdigest()
        app_data = Path(os.environ.get("LOCALAPPDATA", ""))
        override_map = app_data / "CADYX" / "turntable-settings" / table_key / "framesmall.mkf"
        profile_map = tools / "calibration" / "tables" / "revopoint-dual-axis-table" / "framesmall.mkf"
        active_map = override_map if override_map.is_file() else profile_map
        if not active_map.is_file():
            raise FileNotFoundError("The active turntable marker map is unavailable.")
        shutil.copy2(active_map, run_dir / "framesmall.mkf")

        watcher.start()
        rgb_process = subprocess.Popen(
            [str(capture_rgb), "--out-prefix", str(rgb_prefix), "--frames", "1000", "--fps", "15",
             "--format", "mjpg", "--ready-file", str(rgb_ready), "--stop-file", str(rgb_stop)],
            cwd=repository, stdout=rgb_log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        wait_ready(rgb_ready, rgb_process, 20)
        rotation_started.set()
        turntable = subprocess.run(turntable_args, cwd=repository, stdout=turntable_log,
                                   stderr=subprocess.STDOUT, timeout=130)
        if turntable.returncode != 0:
            raise RuntimeError(f"Continuous turntable run failed ({turntable.returncode}); see turntable.log.")
        rgb_stop.touch(exist_ok=True)
        rgb_process.wait(timeout=20)
        if rgb_process.returncode != 0:
            raise RuntimeError(f"RGB capture failed ({rgb_process.returncode}); see rgb.log.")
    except BaseException as exc:
        manifest["status"] = "capture_failed"
        manifest["error"] = str(exc)
        try:
            rgb_stop.touch(exist_ok=True)
            if rgb_process is not None:
                rgb_process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            if rgb_process is not None and rgb_process.poll() is None:
                rgb_process.terminate()
        manifest["finished_utc"] = datetime.now(timezone.utc).isoformat()
        (run_dir / "capture_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        raise
    finally:
        stop_capture.set()
        if watcher.is_alive():
            watcher.join(timeout=5)
        turntable_log.close()
        rgb_log.close()

    frames = list(csv.DictReader(frame_rows_path.open(encoding="utf-8", newline="")))
    encoder = load_encoder_rows(run_dir / "turntable.csv")
    if not encoder or abs(abs(encoder[-1][1] - encoder[0][1]) - 360) > 3:
        manifest["status"] = "capture_incomplete"
        manifest["error"] = "Turntable telemetry does not confirm one complete 360-degree revolution."
        (run_dir / "capture_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        raise RuntimeError(manifest["error"])

    selected = select_uniform_frames(frames, encoder)
    scale = float(json.loads(scale_path.read_text(encoding="utf-8"))["depth_scale_mm_per_raw_unit"])
    for pose_index, (row, angle) in enumerate(selected):
        raw_path = run_dir / f"{run_id}_pose_{pose_index:03d}.raw"
        raw_path.write_bytes((run_dir / "depth_frames" / row["file"]).read_bytes())
        reproject_files(raw_path, run_dir / "Q.bin", run_dir / f"{run_id}_pose_{pose_index:03d}", scale)
        row["selected_angle_degrees"] = f"{angle:.3f}"
        row["pose_index"] = str(pose_index)

    with (run_dir / "selected_poses.csv").open("w", encoding="utf-8", newline="") as output:
        fields = ["pose_index", "file", "frame_mtime_utc", "selected_angle_degrees"]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row, _ in selected)

    pairs = pair_rgb_frames(frames, rgb_prefix.with_name(rgb_prefix.name + "_timestamps.csv"))
    with (run_dir / "depth_rgb_pairs.csv").open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=["depth_frame", "rgb_frame", "delta_ms"])
        writer.writeheader()
        writer.writerows(pairs)

    manifest.update({
        "status": "captured",
        "finished_utc": datetime.now(timezone.utc).isoformat(),
        "y16_frame_count": len(frames),
        "rgb_frame_count": sum(1 for _ in csv.DictReader((rgb_prefix.with_name(rgb_prefix.name + "_timestamps.csv")).open(encoding="utf-8-sig", newline=""))),
        "loop_closure_anchors": len(selected),
        "frame_fusion_source": "all archived Y16 frames; 30-degree samples are loop-closure anchors only",
        "rgb_depth_pairs_within_45ms": sum(pair["rgb_frame"] is not None for pair in pairs),
        "rgb_depth_unique_pairs_within_45ms": sum(pair["rgb_frame"] is not None for pair in pairs),
        "frame_registration": "pending",
        "color_mapping": "not_performed; RGB-to-depth projection needs validated extrinsics",
    })
    manifest["frame_registration"] = "running"
    (run_dir / "capture_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    output = repository / ".runtime" / "scan-reconstruction" / f"{run_id}_all_frames_candidate.ply"
    try:
        from tools.revopoint.align_y16_to_table import align_run
        from tools.revopoint.fuse_continuous_scan import fuse_all_frames
        alignment = align_run(run_dir, run_id, run_dir / "framesmall.mkf", run_dir / "Q.bin", scale)
        manifest["marker_alignment"] = {
            "status": alignment["status"],
            "aligned_frames": alignment["aligned_frame_count"],
            "deferred_frames": alignment["deferred_frame_count"],
        }
        registration = fuse_all_frames(run_dir, output)
    except Exception as exc:
        manifest["status"] = "registration_failed"
        manifest["frame_registration"] = "failed"
        manifest["registration_error"] = str(exc)
        (run_dir / "capture_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        raise
    manifest["status"] = "candidate_ready"
    manifest["frame_registration"] = f"provisional_all_{len(frames)}_frames"
    manifest["cloud_registration"] = manifest["frame_registration"]
    manifest["all_captured_frames_used"] = registration["all_captured_frames_used"]
    manifest["selected_depth_poses_role"] = "loop-closure anchors only; all archived frames are fused"
    saved_candidate = run_dir / f"{run_id}_all_frames_fused.ply"
    saved_report = run_dir / f"{run_id}_all_frames_registration.json"
    shutil.copy2(output, saved_candidate)
    shutil.copy2(output.with_suffix(".json"), saved_report)
    manifest["fused_candidate"] = saved_candidate.name
    manifest["registration_report"] = saved_report.name
    manifest["registration_metrics"] = registration
    (run_dir / "capture_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"run_directory": str(run_dir), "frames": len(frames), "rgb_frames": manifest["rgb_frame_count"],
                      "loop_closure_anchors": len(selected), "paired_rgb_depth": manifest["rgb_depth_pairs_within_45ms"]}, indent=2))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_directory", type=Path)
    parser.add_argument("--run-id", help="UTC run ID; defaults to the current UTC second")
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    run_id = args.run_id or datetime.now(timezone.utc).strftime("revopoint_%Y%m%dT%H%M%SZ")
    if not run_id.startswith("revopoint_") or len(run_id) != 26:
        parser.error("run ID must use revopoint_YYYYMMDDTHHMMSSZ")
    try:
        run_capture(args.project_directory, run_id, args.repository.resolve(strict=True))
        return 0
    except (OSError, RuntimeError, ValueError, TimeoutError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
