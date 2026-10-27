"""Reproject a saved DepthCam Y16 frame without reconnecting to the camera."""

from __future__ import annotations

import argparse
import json
import math
import struct
from pathlib import Path


WIDTH = 640
HEIGHT = 400
RAW_BYTES = WIDTH * HEIGHT * 2
MIN_DEPTH_MM = 100.0
MAX_DEPTH_MM = 250.0


def reproject_y16(raw: bytes, q: tuple[float, ...], depth_scale: float) -> list[tuple[float, float, float]]:
    if len(raw) != RAW_BYTES:
        raise ValueError(f"Y16 frame must be exactly {RAW_BYTES} bytes.")
    if len(q) != 16 or not all(math.isfinite(value) for value in q):
        raise ValueError("Q calibration must contain 16 finite values.")
    if not math.isfinite(depth_scale) or depth_scale <= 0:
        raise ValueError("Depth scale must be positive and finite.")

    points: list[tuple[float, float, float]] = []
    for y in range(HEIGHT):
        row = y * WIDTH * 2
        for x in range(WIDTH):
            pixel = struct.unpack_from("<H", raw, row + x * 2)[0]
            depth = pixel * depth_scale
            if not MIN_DEPTH_MM <= depth <= MAX_DEPTH_MM:
                continue

            ray_x = q[0] * x + q[1] * y + q[2] * depth + q[3]
            ray_y = q[4] * x + q[5] * y + q[6] * depth + q[7]
            ray_z = q[8] * x + q[9] * y + q[10] * depth + q[11]
            if not math.isfinite(ray_z) or abs(ray_z) < 1e-12:
                continue

            point = (ray_x * depth / ray_z, -ray_y * depth / ray_z, -depth)
            if all(math.isfinite(value) for value in point):
                points.append(point)
    return points


def reproject_files(raw_path: Path, q_path: Path, output_prefix: Path, depth_scale: float) -> tuple[Path, Path]:
    raw_path = raw_path.resolve(strict=True)
    q_path = q_path.resolve(strict=True)
    if not output_prefix.parent.is_dir():
        raise ValueError("Output directory must be an existing folder; reproject_y16 does not create projects or folders.")

    ply_path = output_prefix.with_suffix(".ply")
    metadata_path = output_prefix.with_suffix(".json")
    if ply_path.exists() or metadata_path.exists():
        raise FileExistsError("Refusing to overwrite an existing PLY or metadata file.")

    raw = raw_path.read_bytes()
    q_bytes = q_path.read_bytes()
    if len(q_bytes) != 64:
        raise ValueError("Q calibration must be exactly 64 bytes (16 float32 values).")
    q = struct.unpack("<16f", q_bytes)
    points = reproject_y16(raw, q, depth_scale)
    if len(points) < 100:
        raise ValueError("Fewer than 100 valid points were reconstructed; no output was published.")

    ply_path.write_text(
        "ply\nformat ascii 1.0\n"
        "comment Q-ray reprojection with uint16 depth scaled to millimeters\n"
        f"comment depth_scale_mm_per_raw_unit={depth_scale:.12g} (direct UVC mapping provisional)\n"
        f"element vertex {len(points)}\n"
        "property float x\nproperty float y\nproperty float z\nend_header\n"
        + "".join(f"{x:.6f} {y:.6f} {z:.6f}\n" for x, y, z in points),
        encoding="ascii",
        newline="\n",
    )
    metadata_path.write_text(
        json.dumps(
            {
                "camera": "DepthCam",
                "width": WIDTH,
                "height": HEIGHT,
                "raw_format": "Y16 little-endian",
                "raw_file": raw_path.name,
                "ply_file": ply_path.name,
                "calibration_file": q_path.name,
                "projection_method": "Q_ray_scaled_to_depth",
                "depth_scale_mm_per_raw_unit": depth_scale,
                "depth_scale_status": "configured_candidate; direct_UVC_mapping_provisional",
                "point_count": len(points),
                "coordinates_unit": "millimeters_candidate_from_Revo_Scan_depth_scale",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return ply_path, metadata_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-file", type=Path, required=True)
    parser.add_argument("--q-file", type=Path, required=True)
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--depth-scale", type=float, required=True, help="millimeters per uint16 sample")
    args = parser.parse_args()
    ply_path, metadata_path = reproject_files(args.raw_file, args.q_file, args.output_prefix, args.depth_scale)
    print(f"Point cloud: {ply_path}")
    print(f"Metadata: {metadata_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
