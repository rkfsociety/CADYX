"""Fuse two ASCII PLY frames with a measured table angle and an explicit pose datum."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Iterable

from fuse_turntable_scan import build_spatial_grid, read_ascii_ply


def normalize_axis(axis: Iterable[float]) -> tuple[float, float, float]:
    values = tuple(float(value) for value in axis)
    if len(values) != 3 or not all(math.isfinite(value) for value in values):
        raise ValueError("rotation axis must contain three finite values")
    length = math.sqrt(sum(value * value for value in values))
    if length <= 1e-12:
        raise ValueError("rotation axis must be non-zero")
    return tuple(value / length for value in values)


def rotate_about_axis(point: tuple[float, float, float],
                      pivot: tuple[float, float, float],
                      axis: tuple[float, float, float],
                      angle_degrees: float) -> tuple[float, float, float]:
    """Apply a right-handed Rodrigues rotation around an arbitrary line."""
    angle = math.radians(angle_degrees)
    cosine, sine = math.cos(angle), math.sin(angle)
    v = tuple(point[i] - pivot[i] for i in range(3))
    cross = (axis[1] * v[2] - axis[2] * v[1],
             axis[2] * v[0] - axis[0] * v[2],
             axis[0] * v[1] - axis[1] * v[0])
    dot = sum(axis[i] * v[i] for i in range(3))
    return tuple(pivot[i] + v[i] * cosine + cross[i] * sine
                 + axis[i] * dot * (1.0 - cosine) for i in range(3))


def align_second_frame(points: list[tuple[float, float, float]],
                       table_angle_degrees: float,
                       pivot: tuple[float, float, float],
                       axis: tuple[float, float, float]
                       ) -> list[tuple[float, float, float]]:
    if not math.isfinite(table_angle_degrees):
        raise ValueError("table angle must be finite")
    return [rotate_about_axis(point, pivot, axis, -table_angle_degrees)
            for point in points]


def overlap_metrics(reference: list[tuple[float, float, float]],
                    aligned: list[tuple[float, float, float]],
                    sample_stride: int = 16) -> dict[str, object]:
    """Measure bidirectional nearest-neighbor overlap using a bounded spatial grid."""
    if sample_stride < 1:
        raise ValueError("sample_stride must be at least one")
    cell_size = 5.0
    radius2 = 5.0 ** 2

    def fractions(target: list[tuple[float, float, float]],
                  source: list[tuple[float, float, float]]) -> dict[str, float | int]:
        grid = build_spatial_grid(target, cell_size)
        sample = source[::sample_stride]
        hits = {threshold: 0 for threshold in (1.0, 2.0, 5.0)}
        for point in sample:
            cell = tuple(math.floor(value / cell_size) for value in point)
            best = radius2
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        for candidate in grid.get((cell[0] + dx, cell[1] + dy, cell[2] + dz), ()):
                            distance2 = sum((point[i] - candidate[i]) ** 2 for i in range(3))
                            if distance2 < best:
                                best = distance2
                                if best < 1.0:
                                    break
                        if best < 1.0:
                            break
                    if best < 1.0:
                        break
            for threshold in hits:
                if best < threshold * threshold:
                    hits[threshold] += 1
        return {
            "sample_count": len(sample),
            "within_1": hits[1.0] / len(sample),
            "within_2": hits[2.0] / len(sample),
            "within_5": hits[5.0] / len(sample),
        }

    return {
        "sample_stride": sample_stride,
        "threshold_units": "unverified Q coordinates",
        "reference_to_aligned_second": fractions(reference, aligned),
        "aligned_second_to_reference": fractions(aligned, reference),
    }


def voxel_average(points: Iterable[tuple[float, float, float]],
                  voxel_size: float) -> list[tuple[float, float, float]]:
    if not math.isfinite(voxel_size) or voxel_size <= 0:
        raise ValueError("voxel size must be positive and finite")
    cells: dict[tuple[int, int, int], list[float]] = {}

    def voxel_index(value: float) -> int:
        scaled = value / voxel_size
        nearest_boundary = round(scaled)
        # PLY coordinates are serialized to six decimal places; keep round-trip
        # noise from splitting a point exactly on a voxel boundary.
        if abs(scaled - nearest_boundary) <= 1e-5:
            scaled = float(nearest_boundary)
        return math.floor(scaled)

    for point in points:
        key = tuple(voxel_index(value) for value in point)
        bucket = cells.setdefault(key, [0.0, 0.0, 0.0, 0.0])
        bucket[0] += point[0]
        bucket[1] += point[1]
        bucket[2] += point[2]
        bucket[3] += 1.0
    return [(cell[0] / cell[3], cell[1] / cell[3], cell[2] / cell[3])
            for cell in cells.values()]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_ascii_ply(path: Path, points: list[tuple[float, float, float]], comment: str) -> None:
    with path.open("x", encoding="ascii", newline="\n") as output:
        output.write("ply\nformat ascii 1.0\n")
        output.write(f"comment {comment}\n")
        output.write(f"element vertex {len(points)}\n")
        output.write("property float x\nproperty float y\nproperty float z\nend_header\n")
        for point in points:
            output.write(f"{point[0]:.6f} {point[1]:.6f} {point[2]:.6f}\n")


def fuse_pair(first_path: Path, second_path: Path, output_path: Path,
              table_angle_degrees: float, axis_values: Iterable[float],
              pivot_values: Iterable[float], voxel_size: float = 0.5,
              sample_stride: int = 16) -> dict[str, object]:
    first_path, second_path, output_path = first_path.resolve(), second_path.resolve(), output_path.resolve()
    if first_path == second_path:
        raise ValueError("first and second frame must be different files")
    if not first_path.is_file() or not second_path.is_file():
        raise FileNotFoundError("both source PLY frames must exist")
    if output_path.suffix.lower() != ".ply":
        raise ValueError("output path must end in .ply")
    metadata_path = output_path.with_suffix(".json")
    if output_path.exists() or metadata_path.exists():
        raise FileExistsError("refusing to overwrite an existing fused result")
    axis = normalize_axis(axis_values)
    pivot = tuple(float(value) for value in pivot_values)
    if len(pivot) != 3 or not all(math.isfinite(value) for value in pivot):
        raise ValueError("pivot must contain three finite values")
    if not math.isfinite(table_angle_degrees) or abs(table_angle_degrees) > 360.0:
        raise ValueError("table angle must be finite and within ±360 degrees")
    first = read_ascii_ply(first_path)
    second = read_ascii_ply(second_path)
    if not first or not second:
        raise ValueError("source PLY files must contain points")
    aligned = align_second_frame(second, table_angle_degrees, pivot, axis)
    overlap = overlap_metrics(first, aligned, sample_stride)
    fused = voxel_average((*first, *aligned), voxel_size)
    if not fused:
        raise ValueError("fusion produced no points")
    write_ascii_ply(output_path, fused, "two-frame fusion; see companion JSON for pose and calibration status")
    bounds_min = [min(point[i] for point in fused) for i in range(3)]
    bounds_max = [max(point[i] for point in fused) for i in range(3)]
    metadata: dict[str, object] = {
        "format": "cadyx-revopoint-pair-fusion-v1",
        "status": "provisional_fused_candidate",
        "sources": [
            {"path": first_path.name, "sha256": sha256(first_path), "points": len(first)},
            {"path": second_path.name, "sha256": sha256(second_path), "points": len(second)},
        ],
        "registration": {
            "measured_table_angle_degrees": table_angle_degrees,
            "second_frame_alignment_rotation_degrees": -table_angle_degrees,
            "axis": list(axis),
            "pivot_q": list(pivot),
            "axis_pivot_source": "previous marker-registered candidate; independently checked by this pair's overlap, not a certified mechanical calibration",
            "overlap": overlap,
        },
        "voxel_size_q": voxel_size,
        "output_points": len(fused),
        "bounds_q": [bounds_min, bounds_max],
        "scale_status": "provisional_unverified",
        "limitations": "Two views only. Q coordinates are not calibrated physical units; this is a partial surface cloud, not a complete watertight model.",
    }
    with metadata_path.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(metadata, output, indent=2)
        output.write("\n")
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description="Fuse two saved Revopoint PLY frames using a measured turntable angle and explicit 3D axis/pivot.")
    parser.add_argument("first_ply", type=Path)
    parser.add_argument("second_ply", type=Path)
    parser.add_argument("output_ply", type=Path)
    parser.add_argument("--table-angle-deg", type=float, required=True,
                        help="measured second-frame angle relative to the first frame")
    parser.add_argument("--axis", type=float, nargs=3, required=True, metavar=("X", "Y", "Z"))
    parser.add_argument("--pivot", type=float, nargs=3, required=True, metavar=("X", "Y", "Z"))
    parser.add_argument("--voxel-size", type=float, default=0.5)
    parser.add_argument("--sample-stride", type=int, default=16)
    args = parser.parse_args()
    try:
        print(json.dumps(fuse_pair(args.first_ply, args.second_ply, args.output_ply,
                                   args.table_angle_deg, args.axis, args.pivot,
                                   args.voxel_size, args.sample_stride), indent=2))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
