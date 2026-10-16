#!/usr/bin/env python3
"""Join Revopoint RGB/Y16 timestamps to turntable poses and track bright candidates."""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import re
import subprocess
from datetime import datetime
from pathlib import Path
from statistics import mean, median


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def parse_utc(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def stream_rate(rows: list[dict[str, str]]) -> dict[str, float | int | None]:
    if len(rows) < 2:
        return {"frames": len(rows), "duration_seconds": 0.0, "arrival_fps": None,
                "mf_fps": None, "median_gap_ms": None, "p95_gap_ms": None}
    times = [int(row["utc_epoch_us"]) / 1_000_000 for row in rows]
    mf = [int(row["mf_timestamp_100ns"]) / 10_000_000 for row in rows]
    gaps = [(right - left) * 1000 for left, right in zip(times, times[1:])]
    duration = times[-1] - times[0]
    mf_duration = mf[-1] - mf[0]
    return {
        "frames": len(rows), "duration_seconds": duration,
        "arrival_fps": (len(rows) - 1) / duration if duration > 0 else None,
        "mf_fps": (len(rows) - 1) / mf_duration if mf_duration > 0 else None,
        "median_gap_ms": median(gaps), "p95_gap_ms": percentile(gaps, 0.95),
    }


def read_telemetry(path: Path) -> tuple[list[float], list[float], list[float], list[dict[str, str]]]:
    rows = read_csv(path)
    rows = [row for row in rows if row.get("event") != "command_sent" and
            row.get("angle_degrees") and row.get("tilt_degrees")]
    times = [parse_utc(row["utc"]) for row in rows]
    angles: list[float] = []
    for row in rows:
        raw = float(row["angle_degrees"])
        if not angles:
            angles.append(raw)
            continue
        delta = raw - float(rows[len(angles) - 1]["angle_degrees"])
        if delta > 180:
            delta -= 360
        elif delta < -180:
            delta += 360
        angles.append(angles[-1] + delta)
    tilts = [float(row["tilt_degrees"]) for row in rows]
    return times, angles, tilts, rows


def interpolate(times: list[float], values: list[float], at: float) -> float:
    if at <= times[0]:
        return values[0]
    if at >= times[-1]:
        return values[-1]
    right = bisect.bisect_right(times, at)
    left = right - 1
    ratio = (at - times[left]) / (times[right] - times[left])
    return values[left] + ratio * (values[right] - values[left])


def pair_streams(rgb: list[dict[str, str]], depth: list[dict[str, str]], tolerance_ms: float):
    rgb_times = [int(row["utc_epoch_us"]) / 1_000_000 for row in rgb]
    depth_times = [int(row["utc_epoch_us"]) / 1_000_000 for row in depth]
    rgb_to_depth: dict[int, int] = {}
    depth_to_rgb: dict[int, int] = {}
    offsets: list[float] = []
    i = j = 0
    tolerance = tolerance_ms / 1000
    while i < len(rgb) and j < len(depth):
        delta = depth_times[j] - rgb_times[i]
        if abs(delta) <= tolerance:
            rgb_to_depth[i] = j
            depth_to_rgb[j] = i
            offsets.append(delta * 1000)
            i += 1
            j += 1
        elif delta < 0:
            j += 1
        else:
            i += 1
    return rgb_to_depth, depth_to_rgb, offsets


def ensure_marker_candidates(folder: Path, threshold: int) -> int:
    detector = Path(__file__).resolve().parent / "build" / "DetectMarkers.exe"
    if not detector.is_file():
        raise FileNotFoundError(f"{detector} not found; run tools/revopoint/build.ps1 first.")
    frames = sorted(folder.glob("rgb_*.jpg"))
    missing = [frame for frame in frames
               if not (folder / f"{frame.stem}_candidates{threshold}.csv").exists()]
    if not frames:
        raise FileNotFoundError(f"No rgb_*.jpg frames were found in {folder}.")
    for index, frame in enumerate(missing, 1):
        output = folder / f"{frame.stem}_candidates{threshold}.csv"
        try:
            subprocess.run([
                str(detector), "--image", str(frame), "--out-csv", str(output),
                "--threshold", str(threshold),
            ], check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as error:
            raise RuntimeError(f"Marker detector failed on {frame.name}: {error.stderr.strip()}") from error
        if index % 100 == 0 or index == len(missing):
            print(f"Detected RGB candidates for {index}/{len(missing)} new frames.")
    return len(frames)


def detect_candidates(folder: Path, frame_index: int, threshold: int) -> list[dict[str, str]]:
    path = folder / f"rgb_{frame_index:04d}_candidates{threshold}.csv"
    return read_csv(path) if path.exists() else []


def write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def solve_three_by_three(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Solve a small 3x3 system with pivoting; used by the trajectory fit."""
    augmented = [row[:] + [value] for row, value in zip(matrix, vector)]
    for column in range(3):
        pivot = max(range(column, 3), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("Trajectory angle samples do not constrain a sinusoid fit.")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        scale = augmented[column][column]
        augmented[column] = [value / scale for value in augmented[column]]
        for row in range(3):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [value - factor * pivot_value
                              for value, pivot_value in zip(augmented[row], augmented[column])]
    return [augmented[row][3] for row in range(3)]


def fit_marker_trajectories(frame_candidates: list[tuple[float, list[dict[str, str]]]],
                            min_y: float, max_track_gap: int, min_area: int,
                            max_rms_px: float) -> list[dict[str, object]]:
    """Associate larger tabletop blobs, then retain smooth angle-driven paths as candidates."""
    tracks: list[dict[str, object]] = []
    next_track_id = 1
    for frame_index, (angle, candidates) in enumerate(frame_candidates):
        assigned: set[int] = set()
        eligible = [candidate for candidate in candidates
                    if float(candidate["cy_px"]) >= min_y and int(candidate["area_px"]) >= min_area]
        for candidate in sorted(eligible, key=lambda item: int(item["area_px"]), reverse=True):
            cx, cy, area = float(candidate["cx_px"]), float(candidate["cy_px"]), int(candidate["area_px"])
            matches: list[tuple[float, dict[str, object]]] = []
            for track in tracks:
                track_id = int(track["track_id"])
                if track_id in assigned:
                    continue
                gap = frame_index - int(track["last_frame"])
                if gap < 1 or gap > max_track_gap:
                    continue
                previous = track["observations"][-1]
                previous_area = int(previous["area_px"])
                area_ratio = max(area, previous_area) / max(1, min(area, previous_area))
                if area_ratio > 3.0:
                    continue
                distance = math.hypot(cx - float(previous["cx_px"]), cy - float(previous["cy_px"]))
                if distance > 16.0 * gap:
                    continue
                matches.append((distance + math.log(area_ratio) * 1.5, track))

            observation = {"frame_index": frame_index, "angle_degrees": angle,
                           "cx_px": cx, "cy_px": cy, "area_px": area}
            if matches:
                matches.sort(key=lambda item: item[0])
                track = matches[0][1]
                track["last_frame"] = frame_index
                track["observations"].append(observation)
                assigned.add(int(track["track_id"]))
            else:
                track_id = next_track_id
                next_track_id += 1
                tracks.append({"track_id": track_id, "last_frame": frame_index,
                               "observations": [observation]})
                assigned.add(track_id)

    result: list[dict[str, object]] = []
    for track in tracks:
        observations = track["observations"]
        angles = [float(row["angle_degrees"]) for row in observations]
        xs = [float(row["cx_px"]) for row in observations]
        ys = [float(row["cy_px"]) for row in observations]
        angle_span = max(angles) - min(angles)
        extent = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        coefficients = [0.0] * 6
        residuals = [0.0] * len(observations)
        fit_rms = None
        if len(observations) >= 3:
            trig = [(1.0, math.cos(math.radians(angle)), math.sin(math.radians(angle)))
                    for angle in angles]
            matrix = [[sum(row[i] * row[j] for row in trig) for j in range(3)] for i in range(3)]
            try:
                x_coefficients = solve_three_by_three(matrix,
                    [sum(row[i] * value for row, value in zip(trig, xs)) for i in range(3)])
                y_coefficients = solve_three_by_three(matrix,
                    [sum(row[i] * value for row, value in zip(trig, ys)) for i in range(3)])
                coefficients = x_coefficients + y_coefficients
                residuals = [math.hypot(x - sum(term * value for term, value in zip(row, x_coefficients)),
                                        y - sum(term * value for term, value in zip(row, y_coefficients)))
                             for row, x, y in zip(trig, xs, ys)]
                fit_rms = math.sqrt(mean(value * value for value in residuals))
            except ValueError:
                pass

        if len(observations) >= 10 and angle_span >= 45.0 and extent >= 8.0:
            classification = "smooth_rotating_candidate" if fit_rms is not None and fit_rms <= max_rms_px else "poor_sinusoid_fit"
        else:
            classification = "short_or_static"
        result.append({
            "track_id": track["track_id"], "classification": classification,
            "observations": len(observations), "first_frame": observations[0]["frame_index"],
            "last_frame": observations[-1]["frame_index"], "angle_span_degrees": f"{angle_span:.3f}",
            "mean_cx_px": f"{mean(xs):.3f}", "mean_cy_px": f"{mean(ys):.3f}",
            "image_extent_px": f"{extent:.3f}",
            "fit_rms_px": f"{fit_rms:.3f}" if fit_rms is not None else "",
            "fit_p95_px": f"{percentile(residuals, 0.95):.3f}" if fit_rms is not None else "",
            "cx_constant": f"{coefficients[0]:.6f}", "cx_cos": f"{coefficients[1]:.6f}",
            "cx_sin": f"{coefficients[2]:.6f}", "cy_constant": f"{coefficients[3]:.6f}",
            "cy_cos": f"{coefficients[4]:.6f}", "cy_sin": f"{coefficients[5]:.6f}",
        })
    return result


def analyze(folder: Path, min_y: float, pair_tolerance_ms: float, max_track_gap: int,
            suffix: str, threshold: int, trajectory_min_area: int,
            trajectory_max_rms_px: float) -> dict[str, object]:
    outputs = [folder / f"{stem}{suffix}{extension}" for stem, extension in (
        ("frame_pose_sync", ".csv"), ("marker_detections", ".csv"),
        ("marker_tracks", ".csv"), ("marker_trajectories", ".csv"),
        ("sweep_analysis", ".json"))]
    existing = [path.name for path in outputs if path.exists()]
    if existing:
        raise FileExistsError("Refusing to overwrite analysis output: " + ", ".join(existing))

    manifest = json.loads((folder / "capture_manifest.json").read_text(encoding="utf-8-sig"))
    rgb = read_csv(folder / "rgb_timestamps.csv")
    depth = read_csv(folder / "depth_timestamps.csv")
    ensure_marker_candidates(folder, threshold)
    table_path = folder / "turntable_sweep.csv"
    sweep_events = read_csv(table_path)
    table_times, table_angles, table_tilts, table_rows = read_telemetry(table_path)
    rgb_to_depth, depth_to_rgb, pair_offsets = pair_streams(rgb, depth, pair_tolerance_ms)

    sweep_start = next(parse_utc(row["utc"]) for row in sweep_events if row["event"] == "command_sent")
    sweep_end = next(parse_utc(row["utc"]) for row in reversed(sweep_events) if row["event"] == "target_reached")
    pose_rows: list[dict[str, object]] = []
    detections_out: list[dict[str, object]] = []
    track_out: list[dict[str, object]] = []
    tracks: list[dict[str, object]] = []
    next_track_id = 1
    rgb_counts: list[int] = []
    roi_counts: list[int] = []
    moving_counts: list[int] = []
    roi_frames_in_motion = 0
    frame_candidates: list[tuple[float, list[dict[str, str]]]] = []

    for index, timestamp in enumerate(rgb):
        at = int(timestamp["utc_epoch_us"]) / 1_000_000
        angle = interpolate(table_times, table_angles, at)
        tilt = interpolate(table_times, table_tilts, at)
        in_motion_window = sweep_start <= at <= sweep_end
        candidates = detect_candidates(folder, int(timestamp["index"]), threshold)
        frame_candidates.append((angle, candidates))
        rgb_counts.append(len(candidates))
        roi = [candidate for candidate in candidates if float(candidate["cy_px"]) >= min_y]
        roi_counts.append(len(roi))
        if in_motion_window:
            roi_frames_in_motion += 1
            moving_counts.append(len(roi))

        assigned_tracks: set[int] = set()
        for candidate in sorted(candidates, key=lambda item: int(item["area_px"]), reverse=True):
            cx, cy = float(candidate["cx_px"]), float(candidate["cy_px"])
            area = int(candidate["area_px"])
            match_candidates: list[tuple[float, dict[str, object], int]] = []
            for track in tracks:
                if int(track["track_id"]) in assigned_tracks:
                    continue
                gap = index - int(track["last_frame"])
                if gap < 1 or gap > max_track_gap:
                    continue
                last = track["observations"][-1]
                distance = math.hypot(cx - float(last["cx_px"]), cy - float(last["cy_px"]))
                if distance <= 16.0 * gap:
                    area_ratio = max(area, int(last["area_px"])) / max(1, min(area, int(last["area_px"])))
                    cost = distance + min(math.log(area_ratio), 3.0) * 1.5
                    match_candidates.append((cost, track, gap))
            track_id: int
            if match_candidates:
                match_candidates.sort(key=lambda item: item[0])
                _, selected, _ = match_candidates[0]
                if selected["last_frame"] == index:
                    selected = {}
                else:
                    track_id = int(selected["track_id"])
                    assigned_tracks.add(track_id)
                    selected["last_frame"] = index
                    selected["observations"].append({
                        "frame_index": index, "utc_epoch_us": int(timestamp["utc_epoch_us"]),
                        "angle_degrees": angle, "cx_px": cx, "cy_px": cy, "area_px": area,
                        "candidate_id": candidate["id"],
                    })
            else:
                selected = {}
            if not match_candidates or not selected:
                track_id = next_track_id
                next_track_id += 1
                assigned_tracks.add(track_id)
                observation = {
                    "frame_index": index, "utc_epoch_us": int(timestamp["utc_epoch_us"]),
                    "angle_degrees": angle, "cx_px": cx, "cy_px": cy, "area_px": area,
                    "candidate_id": candidate["id"],
                }
                tracks.append({"track_id": track_id, "first_frame": index,
                               "last_frame": index, "observations": [observation]})
            else:
                track_id = int(selected["track_id"])
            detections_out.append({
                "frame_index": index, "utc_epoch_us": timestamp["utc_epoch_us"],
                "table_angle_degrees": f"{angle:.3f}", "candidate_id": candidate["id"],
                "track_id": track_id, "cx_px": candidate["cx_px"], "cy_px": candidate["cy_px"],
                "area_px": candidate["area_px"], "on_table_roi": str(float(candidate["cy_px"]) >= min_y).lower(),
            })

    rgb_times = [int(row["utc_epoch_us"]) / 1_000_000 for row in rgb]
    depth_times = [int(row["utc_epoch_us"]) / 1_000_000 for row in depth]
    for stream, rows, other_map in (("rgb", rgb, rgb_to_depth), ("depth", depth, depth_to_rgb)):
        for index, timestamp in enumerate(rows):
            at = int(timestamp["utc_epoch_us"]) / 1_000_000
            other_index = other_map.get(index)
            other_rows = depth if stream == "rgb" else rgb
            delta_ms = None
            if other_index is not None:
                delta_ms = (int(other_rows[other_index]["utc_epoch_us"]) - int(timestamp["utc_epoch_us"])) / 1000
            candidates = rgb_counts[index] if stream == "rgb" else None
            roi_candidates = roi_counts[index] if stream == "rgb" else None
            pose_rows.append({
                "stream": stream, "frame_index": timestamp["index"],
                "utc_epoch_us": timestamp["utc_epoch_us"], "mf_timestamp_100ns": timestamp["mf_timestamp_100ns"],
                "table_angle_degrees": f"{interpolate(table_times, table_angles, at):.4f}",
                "tilt_degrees": f"{interpolate(table_times, table_tilts, at):.4f}",
                "in_rotation_window": str(sweep_start <= at <= sweep_end).lower(),
                "nearest_other_frame": other_rows[other_index]["index"] if other_index is not None else "",
                "other_minus_this_ms": f"{delta_ms:.3f}" if delta_ms is not None else "",
                "rgb_candidates": candidates if candidates is not None else "",
                "rgb_candidates_y_ge_min": roi_candidates if roi_candidates is not None else "",
            })

    for track in tracks:
        observations = track["observations"]
        xs = [float(row["cx_px"]) for row in observations]
        ys = [float(row["cy_px"]) for row in observations]
        angles = [float(row["angle_degrees"]) for row in observations]
        extent = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        angle_span = max(angles) - min(angles)
        if len(observations) >= 10 and extent < 3.0:
            classification = "stable_in_image"
        elif len(observations) >= 10 and extent >= 8.0 and angle_span >= 45.0:
            classification = "moving_candidate_track"
        else:
            classification = "ambiguous_or_short"
        track_out.append({
            "track_id": track["track_id"], "classification": classification,
            "observations": len(observations), "first_frame": track["first_frame"],
            "last_frame": track["last_frame"], "mean_cx_px": f"{mean(xs):.3f}",
            "mean_cy_px": f"{mean(ys):.3f}", "image_extent_px": f"{extent:.3f}",
            "table_angle_span_degrees": f"{angle_span:.3f}",
        })

    write_rows(outputs[0], ["stream", "frame_index", "utc_epoch_us", "mf_timestamp_100ns",
                           "table_angle_degrees", "tilt_degrees", "in_rotation_window",
                           "nearest_other_frame", "other_minus_this_ms", "rgb_candidates",
                           "rgb_candidates_y_ge_min"], pose_rows)
    write_rows(outputs[1], ["frame_index", "utc_epoch_us", "table_angle_degrees", "candidate_id",
                           "track_id", "cx_px", "cy_px", "area_px", "on_table_roi"], detections_out)
    write_rows(outputs[2], ["track_id", "classification", "observations", "first_frame", "last_frame",
                           "mean_cx_px", "mean_cy_px", "image_extent_px", "table_angle_span_degrees"], track_out)
    trajectory_rows = fit_marker_trajectories(frame_candidates, min_y, max_track_gap,
                                               trajectory_min_area, trajectory_max_rms_px)
    write_rows(outputs[3], ["track_id", "classification", "observations", "first_frame", "last_frame",
                           "angle_span_degrees", "mean_cx_px", "mean_cy_px", "image_extent_px",
                           "fit_rms_px", "fit_p95_px", "cx_constant", "cx_cos", "cx_sin",
                           "cy_constant", "cy_cos", "cy_sin"], trajectory_rows)

    depth_rate = stream_rate(depth)
    rgb_rate = stream_rate(rgb)
    moving_rgb_rows = [row for row, at in zip(rgb, rgb_times) if sweep_start <= at <= sweep_end]
    moving_depth_rows = [row for row, at in zip(depth, depth_times) if sweep_start <= at <= sweep_end]
    moving_pairs = sum(
        sweep_start <= rgb_times[rgb_index] <= sweep_end and
        sweep_start <= depth_times[depth_index] <= sweep_end
        for rgb_index, depth_index in rgb_to_depth.items()
    )
    summary: dict[str, object] = {
        "format": "revopoint-marker-sweep-analysis-v1",
        "capture_status": manifest["status"],
        "requested_fps": manifest["requested_fps"],
        "marker_threshold": threshold,
        "depth": depth_rate,
        "rgb": rgb_rate,
        "moving_window_seconds": sweep_end - sweep_start,
        "moving_depth_frames": sum(sweep_start <= value <= sweep_end for value in depth_times),
        "moving_rgb_frames": roi_frames_in_motion,
        "moving_depth_rate": stream_rate(moving_depth_rows),
        "moving_rgb_rate": stream_rate(moving_rgb_rows),
        "moving_rgb_depth_pairs_within_tolerance": moving_pairs,
        "turntable_angle_span_degrees": max(table_angles) - min(table_angles),
        "telemetry_samples": len(table_rows),
        "telemetry_median_gap_ms": median([(b - a) * 1000 for a, b in zip(table_times, table_times[1:])]) if len(table_times) > 1 else None,
        "rgb_depth_pairs_within_tolerance": len(pair_offsets),
        "pair_tolerance_ms": pair_tolerance_ms,
        "pair_offset_median_ms_depth_minus_rgb": median(pair_offsets) if pair_offsets else None,
        "pair_offset_abs_p95_ms": percentile([abs(value) for value in pair_offsets], 0.95),
        "rgb_candidate_count_mean": mean(rgb_counts) if rgb_counts else 0,
        "rgb_candidate_count_y_ge_min_mean": mean(roi_counts) if roi_counts else 0,
        "moving_rgb_roi_candidate_count_mean": mean(moving_counts) if moving_counts else 0,
        "roi_min_y_px": min_y,
        "tracking": {
            "method": f"greedy nearest-centroid association, max {max_track_gap}-frame gap, up to 16 px per frame",
            "track_count": len(track_out),
            "stable_in_image_tracks": sum(row["classification"] == "stable_in_image" for row in track_out),
            "moving_candidate_tracks": sum(row["classification"] == "moving_candidate_track" for row in track_out),
            "ambiguous_or_short_tracks": sum(row["classification"] == "ambiguous_or_short" for row in track_out),
            "limits": "Exploratory thresholded components; tracks are not verified reflector identities and are not matched to the MKF 3D map.",
        },
        "trajectory_analysis": {
            "method": f"area-filtered tabletop candidates, greedy centroid association with max {max_track_gap}-frame gap, then cx,cy = constant + cos(angle) + sin(angle) fit",
            "minimum_component_area_px": trajectory_min_area,
            "maximum_fit_rms_px": trajectory_max_rms_px,
            "minimum_observations": 10,
            "minimum_angle_span_degrees": 45,
            "minimum_image_extent_px": 8,
            "smooth_rotating_candidates": sum(row["classification"] == "smooth_rotating_candidate" for row in trajectory_rows),
            "limits": "Trajectory candidates are not confirmed marker identities and are not matched to MKF points.",
        },
        "outputs": [path.name for path in outputs],
    }
    outputs[4].write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture_dir", type=Path)
    parser.add_argument("--min-y-px", type=float, default=250)
    parser.add_argument("--pair-tolerance-ms", type=float, default=45)
    parser.add_argument("--max-track-gap", type=int, default=3)
    parser.add_argument("--suffix", default="", help="Optional safe suffix for separate analysis outputs.")
    parser.add_argument("--threshold", type=int, default=150, help="Grayscale threshold for the existing bright-component detector (1-254).")
    parser.add_argument("--trajectory-min-area", type=int, default=100,
                        help="Minimum bright-component area used by the angle-trajectory candidate tracker.")
    parser.add_argument("--trajectory-max-rms-px", type=float, default=25,
                        help="Maximum sinusoid-fit RMS in pixels for a smooth rotating candidate.")
    args = parser.parse_args()
    if (args.min_y_px < 0 or args.pair_tolerance_ms <= 0 or args.max_track_gap < 1 or
            not re.fullmatch(r"[A-Za-z0-9_-]*", args.suffix) or not 1 <= args.threshold <= 254 or
            args.trajectory_min_area < 1 or args.trajectory_max_rms_px <= 0):
        parser.error("ROI/timing values and threshold must be valid; suffix may contain only letters, digits, _ or -.")
    folder = args.capture_dir.resolve()
    if not folder.is_dir():
        parser.error("capture_dir must be an existing directory.")
    print(json.dumps(analyze(folder, args.min_y_px, args.pair_tolerance_ms,
                             args.max_track_gap, args.suffix, args.threshold,
                             args.trajectory_min_area, args.trajectory_max_rms_px), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
