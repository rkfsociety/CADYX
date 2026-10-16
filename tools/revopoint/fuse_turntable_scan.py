"""Fuse ASCII PLY views captured by scan_360.ps1 using turntable angles."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Iterable


def read_ascii_ply(path: Path) -> list[tuple[float, float, float]]:
    with path.open("r", encoding="ascii") as source:
        if source.readline().strip() != "ply":
            raise ValueError(f"Not a PLY file: {path}")
        vertex_count: int | None = None
        properties: list[str] = []
        is_ascii = False
        for line in source:
            fields = line.split()
            if not fields:
                continue
            if fields[0] == "format":
                is_ascii = len(fields) >= 2 and fields[1] == "ascii"
            elif fields[0] == "element" and len(fields) == 3 and fields[1] == "vertex":
                vertex_count = int(fields[2])
            elif fields[0] == "property" and vertex_count is not None and len(fields) == 3:
                properties.append(fields[2])
            elif fields[0] == "end_header":
                break
        else:
            raise ValueError(f"PLY header has no end_header: {path}")
        if not is_ascii:
            raise ValueError(f"Only ASCII PLY point clouds are supported: {path}")
        if vertex_count is None or not all(name in properties for name in ("x", "y", "z")):
            raise ValueError(f"PLY has no xyz vertex fields: {path}")
        columns = tuple(properties.index(name) for name in ("x", "y", "z"))
        points: list[tuple[float, float, float]] = []
        for row_index in range(vertex_count):
            line = source.readline()
            if not line:
                raise ValueError(f"PLY ended after {row_index} of {vertex_count} vertices: {path}")
            fields = line.split()
            try:
                point = tuple(float(fields[column]) for column in columns)
            except (IndexError, ValueError) as error:
                raise ValueError(f"Invalid xyz vertex {row_index} in {path}") from error
            if all(math.isfinite(value) for value in point):
                points.append(point)
    return points


def rotate_to_zero(point: tuple[float, float, float], angle_degrees: float,
                   pivot_x: float, pivot_y: float, rotation_sign: int) -> tuple[float, float, float]:
    angle = math.radians(rotation_sign * angle_degrees)
    cosine, sine = math.cos(angle), math.sin(angle)
    x, y, z = point
    dx, dy = x - pivot_x, y - pivot_y
    return pivot_x + cosine * dx - sine * dy, pivot_y + sine * dx + cosine * dy, z


def build_spatial_grid(points: Iterable[tuple[float, float, float]], cell_size: float
                       ) -> dict[tuple[int, int, int], list[tuple[float, float, float]]]:
    grid: dict[tuple[int, int, int], list[tuple[float, float, float]]] = {}
    for point in points:
        cell = tuple(math.floor(value / cell_size) for value in point)
        grid.setdefault(cell, []).append(point)
    return grid


def matches_within(point: tuple[float, float, float], grid: dict,
                   cell_size: float, radius_squared: float) -> bool:
    cell = tuple(math.floor(value / cell_size) for value in point)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for candidate in grid.get((cell[0] + dx, cell[1] + dy, cell[2] + dz), ()):
                    distance_squared = sum((point[axis] - candidate[axis]) ** 2 for axis in range(3))
                    if distance_squared < radius_squared:
                        return True
    return False


def score_pivot(pairs: list[tuple[dict, list[tuple[float, float, float]], float]],
                pivot_x: float, pivot_y: float, rotation_sign: int,
                cell_size: float) -> tuple[float, float]:
    within_two: list[float] = []
    within_five: list[float] = []
    for target_grid, source_points, angle_delta in pairs:
        for point in source_points:
            aligned = rotate_to_zero(point, angle_delta, pivot_x, pivot_y, rotation_sign)
            hit_two = matches_within(aligned, target_grid, cell_size, 2.0 ** 2)
            hit_five = hit_two or matches_within(aligned, target_grid, cell_size, 5.0 ** 2)
            within_two.append(float(hit_two))
            within_five.append(float(hit_five))
    return statistics.fmean(within_two), statistics.fmean(within_five)


def estimate_pivot(clouds: list[list[tuple[float, float, float]]],
                   angle_values: list[float], arguments: argparse.Namespace
                   ) -> tuple[float, float, dict[str, float]]:
    training: list[tuple[dict, list[tuple[float, float, float]], float]] = []
    validation: list[tuple[dict, list[tuple[float, float, float]], float]] = []
    for index in range(len(clouds) - 1):
        target = clouds[index][::arguments.match_stride]
        source = clouds[index + 1][::arguments.match_stride]
        if not target or not source:
            continue
        angle_delta = (angle_values[index + 1] - angle_values[index]) % 360.0
        pair = (build_spatial_grid(target, 5.0), source, angle_delta)
        (training if index % 2 == 0 else validation).append(pair)
    if len(training) < 2 or not validation:
        raise ValueError("Need at least four usable views to fit and validate the pivot.")

    median_x = statistics.median(point[0] for cloud in clouds for point in cloud[::arguments.match_stride])
    median_y = statistics.median(point[1] for cloud in clouds for point in cloud[::arguments.match_stride])

    radius = arguments.pivot_search_radius
    coarse = arguments.pivot_search_step
    xs = _grid_values(median_x - radius, median_x + radius, coarse)
    ys = _grid_values(median_y - radius, median_y + radius, coarse)
    best_x = best_y = 0.0
    best_score = (-1.0, -1.0)
    for pivot_x in xs:
        for pivot_y in ys:
            score = score_pivot(training, pivot_x, pivot_y, arguments.rotation_sign, 5.0)
            if score > best_score:
                best_x, best_y, best_score = pivot_x, pivot_y, score

    fine_step = min(1.0, coarse)
    for pivot_x in _grid_values(best_x - coarse, best_x + coarse, fine_step):
        for pivot_y in _grid_values(best_y - coarse, best_y + coarse, fine_step):
            score = score_pivot(training, pivot_x, pivot_y, arguments.rotation_sign, 5.0)
            if score > best_score:
                best_x, best_y, best_score = pivot_x, pivot_y, score

    train_score = score_pivot(training, best_x, best_y, arguments.rotation_sign, 5.0)
    validation_score = score_pivot(validation, best_x, best_y, arguments.rotation_sign, 5.0)
    scores = {
        "fit_pairs_mean_within_2_units": train_score[0],
        "fit_pairs_mean_within_5_units": train_score[1],
        "holdout_pairs_mean_within_2_units": validation_score[0],
        "holdout_pairs_mean_within_5_units": validation_score[1],
    }
    return best_x, best_y, scores


def _grid_values(start: float, end: float, step: float) -> list[float]:
    count = int(math.floor((end - start) / step + 1e-9))
    return [start + index * step for index in range(count + 1)]


def write_ply(path: Path, points: list[tuple[float, float, float]]) -> None:
    with path.open("x", encoding="ascii", newline="\n") as output:
        output.write("ply\nformat ascii 1.0\n")
        output.write("comment experimental turntable fusion; metric scale and axis calibration are unverified\n")
        output.write(f"element vertex {len(points)}\nproperty float x\nproperty float y\nproperty float z\nend_header\n")
        for point in points:
            output.write(f"{point[0]:.6f} {point[1]:.6f} {point[2]:.6f}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Estimate a Z-axis table pivot from held-out view overlaps and fuse scan_360 PLY views.")
    parser.add_argument("scan_folder", type=Path, help="Folder created by tools/revopoint/scan_360.ps1")
    parser.add_argument("--voxel-size", type=float, default=0.5, help="Voxel size in unverified Q coordinate units (default: 0.5)")
    parser.add_argument("--match-stride", type=int, default=512, help="Use every Nth PLY point for pivot fitting (default: 512)")
    parser.add_argument("--pivot-search-radius", type=float, default=60.0, help="XY search radius around the median cloud center (default: 60)")
    parser.add_argument("--pivot-search-step", type=float, default=10.0, help="Coarse XY search step; local refinement uses up to 1 unit (default: 10)")
    parser.add_argument("--rotation-sign", type=int, choices=(-1, 1), default=-1, help="Transform views back to the first pose with this Z rotation sign (default: -1)")
    parser.add_argument("--output-prefix", default="approx_fused_candidate", help="Output basename inside scan_folder")
    arguments = parser.parse_args()
    if arguments.voxel_size <= 0 or arguments.match_stride <= 0 or arguments.pivot_search_radius <= 0 or arguments.pivot_search_step <= 0:
        parser.error("voxel size, match stride, and pivot search values must be positive")
    if Path(arguments.output_prefix).name != arguments.output_prefix:
        parser.error("output prefix must be a basename")

    root = arguments.scan_folder.resolve()
    manifest_path = root / "scan_360.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    frames = manifest.get("frames")
    if not isinstance(frames, list) or len(frames) < 4:
        raise ValueError("scan_360.json must contain at least four frames")
    if not math.isfinite(arguments.voxel_size):
        raise ValueError("voxel size must be finite")

    clouds: list[list[tuple[float, float, float]]] = []
    angle_values: list[float] = []
    for frame in frames:
        reported = float(frame["reported_angle_degrees"])
        if not math.isfinite(reported):
            raise ValueError("manifest contains a non-finite reported table angle")
        angle_values.append((reported - float(frames[0]["reported_angle_degrees"])) % 360.0)
        cloud = read_ascii_ply(root / frame["ply_file"])
        if not cloud:
            raise ValueError(f"No finite points in {frame['ply_file']}")
        clouds.append(cloud)

    pivot_x, pivot_y, validation = estimate_pivot(clouds, angle_values, arguments)
    merged: list[tuple[float, float, float]] = []
    input_counts: list[int] = []
    for cloud, angle in zip(clouds, angle_values):
        input_counts.append(len(cloud))
        merged.extend(rotate_to_zero(point, angle, pivot_x, pivot_y, arguments.rotation_sign)
                      for point in cloud)

    voxel_points: dict[tuple[int, int, int], tuple[float, float, float]] = {}
    for point in merged:
        key = tuple(math.floor(value / arguments.voxel_size) for value in point)
        voxel_points.setdefault(key, point)
    fused = list(voxel_points.values())
    if not fused:
        raise ValueError("Fusion produced no points")

    output_ply = root / f"{arguments.output_prefix}.ply"
    output_json = root / f"{arguments.output_prefix}.json"
    if output_ply.exists() or output_json.exists():
        raise FileExistsError("Output already exists; choose a different --output-prefix")
    write_ply(output_ply, fused)
    bounds_min = [min(point[axis] for point in fused) for axis in range(3)]
    bounds_max = [max(point[axis] for point in fused) for axis in range(3)]
    result = {
        "status": "experimental_candidate_unvalidated",
        "source_manifest": manifest_path.name,
        "source_frame_count": len(clouds),
        "frame_angles_degrees": angle_values,
        "source_point_counts": input_counts,
        "input_points": len(merged),
        "output_points": len(fused),
        "axis_assumption": "camera Z axis",
        "rotation_sign": arguments.rotation_sign,
        "pivot_xyz_in_Q_coordinates": [pivot_x, pivot_y, 0.0],
        "pivot_fit_pairs": "even-index adjacent pairs",
        "pivot_validation_pairs": "odd-index adjacent pairs held out",
        "pivot_overlap_validation": validation,
        "voxel_size_in_unverified_Q_units": arguments.voxel_size,
        "bounds_min": bounds_min,
        "bounds_max": bounds_max,
        "limitations": "The disparity scale and physical units are unverified. Z-axis alignment assumes the camera Z axis matches the turntable axis. A single zero-tilt sweep does not cover occluded or underside surfaces; this point cloud is not a validated complete scan or mesh.",
    }
    output_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
