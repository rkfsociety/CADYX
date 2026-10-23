"""Clean individual Y16-derived PLY frames while a 360 capture is running."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[2]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

_RUN_ID = re.compile(r"^revopoint_\d{8}T\d{6}Z$")
_METHODS = ("isolation_filter", "remove_overlaps")
_PARAMETERS = {
    "isolation_filter": {"rate": 15.0, "component_factor": 3.0},
    # Keep the search neighborhood bounded for dense scanner frames. 0.59 still
    # contains every pair allowed by the default overlap tolerances (0.56 normal,
    # 0.1 tangent; maximum Euclidean separation <0.569).
    "remove_overlaps": {"radius": 0.59},
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_ascii_xyz_ply(path: Path) -> tuple[list[tuple[float, float, float]], bytes]:
    """Read the ASCII XYZ vertices produced by CaptureY16 without touching the file."""
    data = path.read_bytes()
    try:
        lines = data.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path.name} is not an ASCII PLY frame") from exc
    if not lines or lines[0] != "ply":
        raise ValueError(f"{path.name} has no PLY header")
    format_seen = False
    vertex_count: int | None = None
    properties: list[str] = []
    in_vertices = False
    header_end = None
    for line_index, line in enumerate(lines[1:], 1):
        fields = line.split()
        if not fields:
            continue
        if fields[0] == "format":
            if len(fields) < 2 or fields[1] != "ascii":
                raise ValueError(f"{path.name} must use ASCII PLY")
            format_seen = True
        elif fields[0] == "element":
            if len(fields) != 3:
                raise ValueError(f"{path.name} has a malformed PLY element")
            in_vertices = fields[1] == "vertex"
            if in_vertices:
                try:
                    vertex_count = int(fields[2])
                except ValueError as exc:
                    raise ValueError(f"{path.name} has an invalid vertex count") from exc
        elif fields[0] == "property" and in_vertices:
            if len(fields) != 3:
                raise ValueError(f"{path.name} has an unsupported vertex property")
            properties.append(fields[2])
        elif line.strip() == "end_header":
            header_end = line_index + 1
            break
    if (not format_seen or vertex_count is None or vertex_count <= 0 or header_end is None
            or not all(axis in properties for axis in ("x", "y", "z"))):
        raise ValueError(f"{path.name} has an incomplete XYZ PLY header")
    axis_indices = tuple(properties.index(axis) for axis in ("x", "y", "z"))
    vertex_lines = lines[header_end:header_end + vertex_count]
    if len(vertex_lines) != vertex_count:
        raise ValueError(f"{path.name} vertex data is truncated")
    points = []
    for line in vertex_lines:
        fields = line.split()
        if len(fields) < len(properties):
            raise ValueError(f"{path.name} contains a malformed vertex")
        try:
            point = tuple(float(fields[index]) for index in axis_indices)
        except ValueError as exc:
            raise ValueError(f"{path.name} contains a nonnumeric XYZ vertex") from exc
        if not all(map(lambda value: value == value and abs(value) != float("inf"), point)):
            raise ValueError(f"{path.name} contains a non-finite XYZ vertex")
        points.append(point)
    return points, data


def preprocess_points(points: list[tuple[float, float, float]], frame_index: int, *,
                      marker_point_indices: set[int] | None = None,
                      detected_marker_count: int = 0
                      ) -> tuple[list[tuple[float, float, float]], dict[str, Any]]:
    """Apply the same two CADYX filters, alternating until both report zero removals."""
    from cadyx.point_cloud import normalize_cloud_parameters, process_points

    source_count = len(points)
    excluded = marker_point_indices or set()
    if any(index < 0 or index >= source_count for index in excluded):
        raise ValueError(f"frame {frame_index} marker mask references a point outside the PLY")
    current = [point for index, point in enumerate(points) if index not in excluded]
    marker_removed = source_count - len(current)
    if source_count < 100:
        raise ValueError(f"frame {frame_index} contains fewer than 100 points before cleanup")
    if len(current) < 100:
        raise ValueError(f"frame {frame_index} has fewer than 100 points after table-marker removal")
    parameters = {method: normalize_cloud_parameters(method, _PARAMETERS[method]) for method in _METHODS}
    removed_by_method = {method: 0 for method in _METHODS}
    passes = []
    for pass_index in range(1, 101):
        pass_removed = {}
        for method in _METHODS:
            before = len(current)
            diagnostics: dict[str, Any] = {}
            current = process_points(current, method, parameters[method], diagnostics=diagnostics)
            removed = before - len(current)
            if removed < 0:
                raise RuntimeError(f"{method} increased frame {frame_index} point count")
            removed_by_method[method] += removed
            pass_removed[method] = removed
        passes.append(pass_removed)
        if not any(pass_removed.values()):
            break
    else:
        raise RuntimeError(f"frame {frame_index} cleanup did not converge in 100 passes")
    if len(current) < 100:
        raise ValueError(f"frame {frame_index} contains fewer than 100 points after cleanup")
    return current, {
        "frame_index": frame_index,
        "source_points": source_count,
        "cleaned_points": len(current),
        "removed_points": source_count - len(current),
        "detected_table_markers": detected_marker_count,
        "removed_table_marker_points": marker_removed,
        "removed_by_method": removed_by_method,
        "parameters": parameters,
        "passes": passes,
    }


def _table_marker_point_indices(raw_path: Path, calibration_path: Path, depth_scale: float,
                                point_count: int) -> tuple[set[int], int, str]:
    from tools.revopoint.align_y16_to_table import detect_marker_regions
    from tools.revopoint.reproject_y16 import HEIGHT, RAW_BYTES, WIDTH

    raw = raw_path.read_bytes()
    if len(raw) != RAW_BYTES:
        raise ValueError(f"{raw_path.name} is not a complete Y16 frame")
    q_data = calibration_path.read_bytes()
    if len(q_data) != 64:
        raise ValueError("scanner Q calibration must contain 16 float32 values")
    q = struct.unpack("<16f", q_data)
    if not all(map(lambda value: value == value and abs(value) != float("inf"), q)):
        raise ValueError("scanner Q calibration contains non-finite values")
    if not (depth_scale > 0 and depth_scale < float("inf")):
        raise ValueError("depth scale must be positive and finite")
    marker_regions = detect_marker_regions(raw)
    marker_pixels = {pixel for region in marker_regions for pixel in region}

    values = struct.unpack(f"<{WIDTH * HEIGHT}H", raw)
    point_index = 0
    removed_indices: set[int] = set()
    for pixel_index, value in enumerate(values):
        depth = value * depth_scale
        if not math.isfinite(depth) or depth < 1.0:
            continue
        x, y = pixel_index % WIDTH, pixel_index // WIDTH
        ray_x = q[0] * x + q[1] * y + q[2] * depth + q[3]
        ray_y = q[4] * x + q[5] * y + q[6] * depth + q[7]
        ray_z = q[8] * x + q[9] * y + q[10] * depth + q[11]
        if not math.isfinite(ray_z) or abs(ray_z) < 1e-12:
            continue
        point = (ray_x * depth / ray_z, -ray_y * depth / ray_z, -depth)
        if not all(math.isfinite(coordinate) for coordinate in point):
            continue
        if pixel_index in marker_pixels:
            removed_indices.add(point_index)
        point_index += 1
    if point_index != point_count:
        raise ValueError(f"{raw_path.name} projects to {point_index} vertices but the paired PLY contains {point_count}; marker removal refused")
    return removed_indices, len(marker_regions), _sha256(raw)


def preprocess_frame_file(source_path: Path, cleaned_path: Path, report_path: Path,
                          frame_index: int, *, timing: str = "during_capture",
                          raw_path: Path | None = None, calibration_path: Path | None = None,
                          depth_scale: float | None = None) -> dict[str, Any]:
    source_path = source_path.resolve(strict=True)
    cleaned_path = cleaned_path.resolve()
    report_path = report_path.resolve()
    if cleaned_path.exists() or report_path.exists():
        raise FileExistsError("refusing to overwrite a cleaned frame or its report")
    started = time.perf_counter()
    points, source_bytes = read_ascii_xyz_ply(source_path)
    marker_indices: set[int] = set()
    marker_count = 0
    raw_hash = None
    if any(value is not None for value in (raw_path, calibration_path, depth_scale)):
        if raw_path is None or calibration_path is None or depth_scale is None:
            raise ValueError("table-marker removal requires RAW, Q calibration, and depth scale")
        marker_indices, marker_count, raw_hash = _table_marker_point_indices(
            raw_path.resolve(strict=True), calibration_path.resolve(strict=True), depth_scale, len(points))
    cleaned, stats = preprocess_points(points, frame_index, marker_point_indices=marker_indices,
                                      detected_marker_count=marker_count)
    header = ("ply\nformat ascii 1.0\n"
              "comment CADYX per-frame isolation and overlap cleanup\n"
              f"element vertex {len(cleaned)}\n"
              "property float x\nproperty float y\nproperty float z\nend_header\n")
    cleaned_text = header + "".join(
        f"{point[0]:.9g} {point[1]:.9g} {point[2]:.9g}\n" for point in cleaned)
    cleaned_bytes = cleaned_text.encode("ascii")
    payload = {
        "format": "cadyx-preprocessed-scan-frame-v1",
        "state": "completed",
        "timing": timing,
        "source_file": source_path.name,
        "source_sha256": _sha256(source_bytes),
        "source_raw_sha256": raw_hash,
        "table_marker_detection": "completed" if raw_hash else "not_requested",
        "cleaned_file": cleaned_path.name,
        "cleaned_sha256": _sha256(cleaned_bytes),
        "frame_index": frame_index,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        **stats,
    }
    try:
        with cleaned_path.open("xb") as target:
            target.write(cleaned_bytes)
        with report_path.open("x", encoding="utf-8", newline="\n") as target:
            json.dump(payload, target, indent=2)
            target.write("\n")
    except Exception:
        cleaned_path.unlink(missing_ok=True)
        report_path.unlink(missing_ok=True)
        raise
    return payload


def verified_preprocessed_frame(source_path: Path) -> tuple[Path, dict[str, Any]] | None:
    """Return a cleanup artifact only when both source and output hashes still match."""
    source_path = source_path.resolve(strict=True)
    cleaned_path = source_path.with_name(source_path.stem + "_cleaned.ply")
    report_path = cleaned_path.with_suffix(".json")
    if not cleaned_path.exists() and not report_path.exists():
        return None
    if not cleaned_path.is_file() or not report_path.is_file():
        raise ValueError(f"{source_path.name} has an incomplete preprocessed-frame pair")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        source_hash = _sha256(source_path.read_bytes())
        cleaned_hash = _sha256(cleaned_path.read_bytes())
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{source_path.name} has an unreadable cleanup report") from exc
    if (report.get("format") != "cadyx-preprocessed-scan-frame-v1"
            or report.get("state") != "completed"
            or report.get("source_file") != source_path.name
            or report.get("source_sha256") != source_hash
            or report.get("cleaned_file") != cleaned_path.name
            or report.get("cleaned_sha256") != cleaned_hash):
        raise ValueError(f"{source_path.name} cleanup artifact does not match its original source")
    return cleaned_path, report


def _read_completed_rows(pose_log: Path) -> list[dict[str, str]]:
    if not pose_log.is_file():
        return []
    try:
        with pose_log.open(encoding="utf-8-sig", newline="") as source:
            return [row for row in csv.DictReader(source) if row.get("event") == "capture_completed"]
    except (OSError, csv.Error, UnicodeError):
        return []


def _write_aggregate_report(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    for attempt in range(10):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 9:
                temporary.unlink(missing_ok=True)
                raise
            time.sleep(0.05)


def watch_capture(pose_log: Path, run_directory: Path, run_id: str, expected_frame_count: int,
                  stop_signal: Path, report_path: Path, *, max_workers: int = 4,
                  poll_seconds: float = 0.1, calibration_path: Path | None = None,
                  depth_scale: float | None = None) -> dict[str, Any]:
    """Launch frame cleanup as soon as each capture-completed row is flushed."""
    run_directory = run_directory.resolve(strict=True)
    pose_log = pose_log.resolve()
    stop_signal = stop_signal.resolve()
    report_path = report_path.resolve()
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("run id must use revopoint_YYYYMMDDTHHMMSSZ")
    if not 1 <= expected_frame_count <= 360 or not 1 <= max_workers <= 16:
        raise ValueError("expected frame count or preprocessing worker limit is invalid")
    if not 0.01 <= poll_seconds <= 2.0:
        raise ValueError("pose-log polling interval must be between 0.01 and 2 seconds")
    if (calibration_path is None) != (depth_scale is None):
        raise ValueError("watch mode requires both calibration path and depth scale for marker removal")
    if calibration_path is not None:
        calibration_path = calibration_path.resolve(strict=True)
        if calibration_path.stat().st_size != 64 or depth_scale is None or not math.isfinite(depth_scale) or depth_scale <= 0:
            raise ValueError("watch mode received invalid marker-removal calibration")
    if report_path.exists():
        raise FileExistsError("refusing to overwrite the scan preprocessing report")

    pending: dict[int, Path] = {}
    active: dict[int, subprocess.Popen[str]] = {}
    reports: dict[int, dict[str, Any]] = {}
    errors: dict[int, str] = {}
    stop_signal_seen = False
    script_path = Path(__file__).resolve()
    aggregate: dict[str, Any] = {
        "format": "cadyx-scan-preprocessing-v1", "run_id": run_id,
        "timing": "during_capture", "method_order": list(_METHODS),
        "expected_frame_count": expected_frame_count, "worker_limit": max_workers,
        "state": "running", "frames": [],
    }
    _write_aggregate_report(report_path, aggregate)

    while True:
        for row in _read_completed_rows(pose_log):
            try:
                index = int(row["step_index"])
            except (KeyError, ValueError, TypeError):
                continue
            if not 0 <= index < expected_frame_count or index in pending or index in active or index in reports or index in errors:
                continue
            expected_stems = {f"{run_id}_pose_{index:03d}", f"{run_id}_pose_pose_{index:03d}"}
            command_prefix = Path(row.get("command", "")).resolve()
            if command_prefix.parent != run_directory or command_prefix.name not in expected_stems:
                errors[index] = "capture log contains a frame path outside its series or an unexpected frame name"
                continue
            source_path = command_prefix.with_suffix(".ply")
            if source_path.is_file():
                pending[index] = source_path

        stop_signal_seen = stop_signal_seen or stop_signal.exists()
        for index, process in list(active.items()):
            if process.poll() is None:
                continue
            output, _ = process.communicate()
            source_path = next((path for path in run_directory.glob(f"{run_id}_pose*{index:03d}_cleaned.json")), None)
            if process.returncode != 0:
                errors[index] = (output or f"frame worker exited with code {process.returncode}").strip()[-2000:]
            elif source_path is None:
                errors[index] = "frame worker completed without a cleanup report"
            else:
                try:
                    frame_report = json.loads(source_path.read_text(encoding="utf-8"))
                    if frame_report.get("frame_index") != index or frame_report.get("state") != "completed":
                        raise ValueError("cleanup report does not match its frame index or state")
                    reports[index] = frame_report
                except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                    errors[index] = f"invalid cleanup report: {exc}"
            del active[index]

        while pending and len(active) < max_workers:
            index = min(pending)
            source_path = pending.pop(index)
            cleaned_path = source_path.with_name(source_path.stem + "_cleaned.ply")
            frame_report_path = cleaned_path.with_suffix(".json")
            command = [sys.executable, str(script_path), "--input-ply", str(source_path),
                       "--cleaned-ply", str(cleaned_path), "--report-file", str(frame_report_path),
                       "--frame-index", str(index), "--timing", "during_capture"]
            if calibration_path is not None and depth_scale is not None:
                command.extend(("--raw-file", str(source_path.with_suffix(".raw")),
                                "--calibration-file", str(calibration_path),
                                "--depth-scale", format(depth_scale, ".12g")))
            try:
                active[index] = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                  text=True, encoding="utf-8", errors="replace",
                                                  creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
            except OSError as exc:
                errors[index] = f"could not start per-frame cleanup: {exc}"

        aggregate["frames"] = [reports[i] for i in sorted(reports)]
        aggregate["failed_frames"] = [{"frame_index": i, "error": errors[i]} for i in sorted(errors)]
        aggregate["cleaned_frame_count"] = len(reports)
        aggregate["failed_frame_count"] = len(errors)
        aggregate["detected_table_marker_count"] = sum(report.get("detected_table_markers", 0) for report in reports.values())
        aggregate["removed_table_marker_points"] = sum(report.get("removed_table_marker_points", 0) for report in reports.values())
        if stop_signal_seen and not pending and not active:
            aggregate["state"] = ("failed" if errors else "completed"
                                  if len(reports) == expected_frame_count else "partial")
            _write_aggregate_report(report_path, aggregate)
            return aggregate
        _write_aggregate_report(report_path, aggregate)
        time.sleep(poll_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch-pose-log", type=Path)
    parser.add_argument("--run-directory", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--expected-frame-count", type=int)
    parser.add_argument("--stop-signal", type=Path)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--input-ply", type=Path)
    parser.add_argument("--cleaned-ply", type=Path)
    parser.add_argument("--report-file", type=Path, required=True)
    parser.add_argument("--frame-index", type=int)
    parser.add_argument("--timing", choices=("during_capture", "after_capture_before_registration"),
                        default="after_capture_before_registration")
    parser.add_argument("--raw-file", type=Path)
    parser.add_argument("--calibration-file", type=Path)
    parser.add_argument("--depth-scale", type=float)
    args = parser.parse_args()
    if args.watch_pose_log is not None:
        if (args.run_directory is None or args.run_id is None or args.expected_frame_count is None
                or args.stop_signal is None or args.input_ply is not None or args.cleaned_ply is not None):
            parser.error("watch mode requires --run-directory, --run-id, --expected-frame-count, --stop-signal, and --report-file")
        try:
            report = watch_capture(args.watch_pose_log, args.run_directory, args.run_id,
                                   args.expected_frame_count, args.stop_signal, args.report_file,
                                   max_workers=args.max_workers, calibration_path=args.calibration_file,
                                   depth_scale=args.depth_scale)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"capture preprocessing worker failed: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"state": report["state"],
                          "cleaned_frame_count": report["cleaned_frame_count"],
                          "failed_frame_count": report["failed_frame_count"]}, sort_keys=True))
        return 1 if report["state"] == "failed" else 0
    if args.input_ply is None or args.cleaned_ply is None or args.frame_index is None:
        parser.error("per-frame mode requires --input-ply, --cleaned-ply, and --frame-index")
    try:
        report = preprocess_frame_file(args.input_ply, args.cleaned_ply, args.report_file,
                                       args.frame_index, timing=args.timing, raw_path=args.raw_file,
                                       calibration_path=args.calibration_file, depth_scale=args.depth_scale)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"frame preprocessing failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"frame_index": report["frame_index"],
                      "input_points": report["source_points"],
                      "output_points": report["cleaned_points"],
                      "removed_points": report["removed_points"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
