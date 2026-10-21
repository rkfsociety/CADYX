"""Color an all-frame Y16 turntable cloud from unique, time-matched RGB frames."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import struct
import sys
from pathlib import Path
from typing import Any

import numpy as np

REPOSITORY = Path(__file__).resolve().parents[2]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from tools.revopoint.reproject_y16 import reproject_y16

RGB_WIDTH = 1280
RGB_HEIGHT = 800
DEPTH_CALIBRATION_WIDTH = 640
DEPTH_CALIBRATION_HEIGHT = 400
PAIR_TOLERANCE_MS = 15.0
VISIBILITY_EPSILON_MAP_UNITS = 0.75
COLOR_SAMPLE_VOXEL_MAP_UNITS = 1.5
COLOR_ASSOCIATION_RADIUS_MAP_UNITS = 2.0


def parse_rgb_calibration(extrinsic_bytes: bytes, projection_bytes: bytes) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Decode the active MINI camera files using the empirically checked LC-to-RGB layout."""
    if len(extrinsic_bytes) != 48 or len(projection_bytes) != 40:
        raise ValueError("RGB calibration requires 48-byte LC_RT.bin and 40-byte Prgb.bin files.")
    extrinsic = np.asarray(struct.unpack("<12f", extrinsic_bytes), dtype=np.float64)
    projection = np.asarray(struct.unpack("<10f", projection_bytes), dtype=np.float64)
    if not np.isfinite(extrinsic).all() or not np.isfinite(projection).all():
        raise ValueError("RGB calibration contains non-finite values.")
    rotation = extrinsic[:9].reshape(3, 3)
    translation = extrinsic[9:12]
    if (abs(float(np.linalg.det(rotation)) - 1.0) > 1e-3
            or float(np.linalg.norm(rotation @ rotation.T - np.eye(3))) > 1e-3):
        raise ValueError("LC_RT.bin does not contain a proper rigid rotation in its first nine float32 values.")
    fx, fy, cx, cy = projection[1], projection[5], projection[3], projection[6]
    if min(fx, fy) <= 0 or not all(math.isfinite(value) for value in (fx, fy, cx, cy)):
        raise ValueError("Prgb.bin has invalid focal lengths or principal point.")
    intrinsics = np.asarray([[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]])
    return rotation, translation, intrinsics


def project_depth_points_to_rgb(points: np.ndarray, rotation: np.ndarray, translation: np.ndarray,
                                intrinsics: np.ndarray, width: int = RGB_WIDTH,
                                height: int = RGB_HEIGHT) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Project the Q-reprojected right-handed depth points into the RGB camera image."""
    points = np.asarray(points, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3:
        raise ValueError("Depth points must be an Nx3 array.")
    # reproject_y16 stores camera points as X-right, Y-up, Z-away (negative).
    camera_points = points * np.asarray([1.0, -1.0, -1.0])
    rgb_points = (rotation @ camera_points.T).T + translation
    z = rgb_points[:, 2]
    scale_x = width / DEPTH_CALIBRATION_WIDTH
    scale_y = height / DEPTH_CALIBRATION_HEIGHT
    u = (intrinsics[0, 0] * rgb_points[:, 0] / z + intrinsics[0, 2]) * scale_x
    v = (intrinsics[1, 1] * rgb_points[:, 1] / z + intrinsics[1, 2]) * scale_y
    return u, v, z


def visible_projected_points(u: np.ndarray, v: np.ndarray, z: np.ndarray,
                             width: int, height: int,
                             epsilon: float = VISIBILITY_EPSILON_MAP_UNITS) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Use a nearest-depth raster to reject depth points hidden from the offset RGB camera."""
    u = np.asarray(u, dtype=np.float64)
    v = np.asarray(v, dtype=np.float64)
    z = np.asarray(z, dtype=np.float64)
    valid = (np.isfinite(u) & np.isfinite(v) & np.isfinite(z) & (z > 0)
             & (u >= 0) & (u < width) & (v >= 0) & (v < height))
    indices = np.flatnonzero(valid)
    if not len(indices):
        return indices, np.empty((0,), dtype=np.int32), np.empty((0,), dtype=np.int32)
    x = np.rint(u[indices]).astype(np.int32)
    y = np.rint(v[indices]).astype(np.int32)
    inside = (x >= 0) & (x < width) & (y >= 0) & (y < height)
    indices, x, y = indices[inside], x[inside], y[inside]
    pixel = y * width + x
    z_buffer = np.full(width * height, np.inf, dtype=np.float64)
    np.minimum.at(z_buffer, pixel, z[indices])
    visible = z[indices] <= z_buffer[pixel] + epsilon
    return indices[visible], x[visible], y[visible]


def _load_unique_pairs(run_dir: Path) -> dict[str, tuple[int, float]]:
    result: dict[str, tuple[int, float]] = {}
    used_rgb: set[int] = set()
    with (run_dir / "depth_rgb_pairs.csv").open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            if not row.get("rgb_frame") or not row.get("delta_ms"):
                continue
            rgb_index, delta = int(row["rgb_frame"]), float(row["delta_ms"])
            if delta > PAIR_TOLERANCE_MS:
                continue
            if row["depth_frame"] in result or rgb_index in used_rgb:
                raise ValueError("Depth/RGB pair table contains duplicate assignments.")
            result[row["depth_frame"]] = (rgb_index, delta)
            used_rgb.add(rgb_index)
    if not result:
        raise ValueError("No unique depth/RGB pairs fall within the 15 ms colorization window.")
    return result


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def colorize_continuous_scan(run_directory: Path, source_cloud_path: Path,
                             registration_report_path: Path, output_path: Path,
                             update_manifest: bool = True) -> dict[str, Any]:
    import open3d as o3d
    from scipy.spatial import cKDTree

    run_dir = run_directory.resolve(strict=True)
    run_id = run_dir.name
    manifest_path = run_dir / "capture_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    registration_report = json.loads(registration_report_path.read_text(encoding="utf-8"))
    source_cloud_path = source_cloud_path.resolve(strict=True)
    output_path = output_path.resolve()
    report_path = output_path.with_suffix(".json")
    if output_path.exists() or report_path.exists():
        raise FileExistsError("Refusing to overwrite an existing colorized cloud or report.")
    if registration_report.get("run_id") != run_id:
        raise ValueError("Registration report does not belong to this scan series.")
    frame_poses = registration_report.get("frame_poses")
    if not isinstance(frame_poses, list) or len(frame_poses) != int(registration_report.get("source_frame_count", 0)):
        raise ValueError("Registration report is missing one optimized pose per captured depth frame.")
    pose_by_file: dict[str, np.ndarray] = {}
    for frame in frame_poses:
        matrix = np.asarray(frame.get("scanner_to_map"), dtype=np.float64)
        if (matrix.shape != (4, 4) or not np.isfinite(matrix).all()
                or not np.allclose(matrix[3], [0.0, 0.0, 0.0, 1.0], atol=1e-7)):
            raise ValueError("Registration report contains an invalid scanner-to-map matrix.")
        pose_by_file[str(frame["source_file"])] = matrix

    extrinsic_path = run_dir / "LC_RT.bin"
    projection_path = run_dir / "Prgb.bin"
    rotation, translation, intrinsics = parse_rgb_calibration(
        extrinsic_path.read_bytes(), projection_path.read_bytes(),
    )
    q_bytes = (run_dir / "Q.bin").read_bytes()
    if len(q_bytes) != 64:
        raise ValueError("Run-local Q.bin is missing or invalid.")
    q = struct.unpack("<16f", q_bytes)
    depth_scale = float(json.loads((run_dir / "depth_scale.json").read_text(encoding="utf-8"))[
        "depth_scale_mm_per_raw_unit"])
    if not math.isfinite(depth_scale) or depth_scale <= 0:
        raise ValueError("Run-local depth scale must be positive and finite.")
    rgb_calibration_sha256 = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (extrinsic_path, projection_path)
    }
    pairs = _load_unique_pairs(run_dir)
    cloud = o3d.io.read_point_cloud(str(source_cloud_path))
    target_points = np.asarray(cloud.points, dtype=np.float64)
    if not len(target_points):
        raise ValueError("The all-frame source cloud has no points.")
    tree = cKDTree(target_points)
    color_sums = np.zeros((len(target_points), 3), dtype=np.float64)
    color_views = np.zeros(len(target_points), dtype=np.uint16)
    color_candidates = 0
    occlusion_rejected = 0
    rgb_width = rgb_height = None
    deltas = []

    with (run_dir / "depth_frames.csv").open(encoding="utf-8-sig", newline="") as source:
        depth_rows = list(csv.DictReader(source))
    for depth_row in depth_rows:
        source_name = depth_row["file"]
        pair = pairs.get(source_name)
        if pair is None:
            continue
        if source_name not in pose_by_file:
            raise ValueError(f"Registration report has no pose for {source_name}.")
        rgb_index, delta_ms = pair
        rgb_path = run_dir / "rgb_frames" / f"rgb_{rgb_index:04d}.jpg"
        if not rgb_path.is_file():
            raise FileNotFoundError(f"Paired RGB image is missing: {rgb_path.name}")
        raw_path = run_dir / "depth_frames" / source_name
        if not raw_path.is_file():
            raise FileNotFoundError(f"Paired depth frame is missing: {source_name}")
        raw = raw_path.read_bytes()
        if len(raw) != 640 * 400 * 2:
            raise ValueError(f"Depth frame has the wrong byte length: {source_name}")
        points = np.asarray(reproject_y16(raw, q, depth_scale), dtype=np.float64)
        camera_to_rgb = (rotation @ (points * np.asarray([1.0, -1.0, -1.0])).T).T + translation
        z = camera_to_rgb[:, 2]

        image = np.asarray(o3d.io.read_image(str(rgb_path)))
        if image.ndim != 3 or image.shape[2] < 3:
            raise ValueError(f"Paired RGB image is not a 3-channel image: {rgb_path.name}")
        height, width = int(image.shape[0]), int(image.shape[1])
        if rgb_width is None:
            rgb_width, rgb_height = width, height
        elif (width, height) != (rgb_width, rgb_height):
            raise ValueError("RGB frame dimensions changed during the scan series.")
        if (width % DEPTH_CALIBRATION_WIDTH or height % DEPTH_CALIBRATION_HEIGHT
                or width // DEPTH_CALIBRATION_WIDTH != height // DEPTH_CALIBRATION_HEIGHT):
            raise ValueError("RGB image dimensions do not match the saved 640x400 Prgb calibration scale.")
        scale_x = width / DEPTH_CALIBRATION_WIDTH
        scale_y = height / DEPTH_CALIBRATION_HEIGHT
        u = (intrinsics[0, 0] * camera_to_rgb[:, 0] / z + intrinsics[0, 2]) * scale_x
        v = (intrinsics[1, 1] * camera_to_rgb[:, 1] / z + intrinsics[1, 2]) * scale_y
        visible_indices, pixel_x, pixel_y = visible_projected_points(u, v, z, width, height)
        occlusion_rejected += int(len(points) - len(visible_indices))
        if not len(visible_indices):
            continue

        # Decode the same RGB point samples back through their captured image pixels.
        rgb_values = image[pixel_y, pixel_x, :3].astype(np.float64) / 255.0
        scanner_to_map = pose_by_file[source_name]
        world_points = (scanner_to_map[:3, :3] @ points[visible_indices].T).T + scanner_to_map[:3, 3]
        sample_cloud = o3d.geometry.PointCloud()
        sample_cloud.points = o3d.utility.Vector3dVector(world_points)
        sample_cloud.colors = o3d.utility.Vector3dVector(rgb_values)
        sample_cloud = sample_cloud.voxel_down_sample(COLOR_SAMPLE_VOXEL_MAP_UNITS)
        sample_points = np.asarray(sample_cloud.points, dtype=np.float64)
        sample_colors = np.asarray(sample_cloud.colors, dtype=np.float64)
        if not len(sample_points):
            continue

        distances, target_indices = tree.query(
            sample_points, k=1, distance_upper_bound=COLOR_ASSOCIATION_RADIUS_MAP_UNITS,
            workers=-1,
        )
        accepted = np.isfinite(distances) & (target_indices < len(target_points))
        if not accepted.any():
            deltas.append(delta_ms)
            continue
        color_candidates += int(accepted.sum())
        frame_sums = np.zeros((len(target_points), 3), dtype=np.float64)
        frame_counts = np.zeros(len(target_points), dtype=np.uint16)
        np.add.at(frame_sums, target_indices[accepted], sample_colors[accepted])
        np.add.at(frame_counts, target_indices[accepted], 1)
        observed = frame_counts > 0
        color_sums[observed] += frame_sums[observed] / frame_counts[observed, None]
        color_views[observed] += 1
        deltas.append(delta_ms)

    if not deltas:
        raise ValueError("None of the paired RGB frames produced visible color samples on the fused cloud.")
    colored_mask = color_views > 0
    colors = np.full((len(target_points), 3), 0.5, dtype=np.float64)
    colors[colored_mask] = color_sums[colored_mask] / color_views[colored_mask, None]
    cloud.colors = o3d.utility.Vector3dVector(colors)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not o3d.io.write_point_cloud(str(output_path), cloud, write_ascii=False, compressed=False):
        raise OSError(f"Open3D failed to write the colorized point cloud: {output_path}")

    report = {
        "format": "cadyx-continuous-rgb-color-mapping-v1",
        "status": "provisional",
        "run_id": run_id,
        "source_geometry": source_cloud_path.name,
        "output_cloud": output_path.name,
        "output_points": int(len(target_points)),
        "geometry_preserved": True,
        "colorized_vertices": int(colored_mask.sum()),
        "uncolored_vertices": int((~colored_mask).sum()),
        "color_coverage": float(colored_mask.mean()),
        "vertices_observed_in_multiple_views": int((color_views > 1).sum()),
        "paired_depth_frames": len(deltas),
        "rgb_frames_used": len(deltas),
        "rgb_pair_tolerance_ms": PAIR_TOLERANCE_MS,
        "rgb_pair_delta_median_ms": float(np.median(deltas)),
        "rgb_pair_delta_max_ms": float(np.max(deltas)),
        "color_sample_voxel_map_units": COLOR_SAMPLE_VOXEL_MAP_UNITS,
        "color_association_radius_map_units": COLOR_ASSOCIATION_RADIUS_MAP_UNITS,
        "color_candidates_associated": color_candidates,
        "not_visible_or_out_of_frame_depth_points": occlusion_rejected,
        "camera_transform": "LC_RT applied from depth-left camera coordinates to RGB camera coordinates; depth Y/Z signs converted to OpenCV camera axes",
        "rgb_intrinsics": {
            "fx": float(intrinsics[0, 0]), "fy": float(intrinsics[1, 1]),
            "cx": float(intrinsics[0, 2]), "cy": float(intrinsics[1, 2]),
            "calibration_resolution": [DEPTH_CALIBRATION_WIDTH, DEPTH_CALIBRATION_HEIGHT],
            "captured_resolution": [rgb_width, rgb_height],
        },
        "rgb_calibration_sha256": rgb_calibration_sha256,
        "calibration_validation": "projection was checked against paired table-marker image positions at multiple turntable poses; vendor file semantics remain undocumented in this tool",
        "limitations": "depth scale remains provisional; no RGB lens-distortion coefficients are applied; uncolored vertices are neutral gray",
        "capture_manifest_updated": update_manifest,
    }
    _atomic_json(report_path, report)
    if update_manifest:
        manifest["colorized_candidate"] = output_path.name
        manifest["color_mapping_report"] = report_path.name
        manifest["color_mapping"] = {
            "status": "provisional",
            "method": "paired RGB images projected through LC_RT and Prgb, then associated with the fused full-frame geometry",
            "paired_depth_frames": len(deltas),
            "rgb_frames_used": len(deltas),
            "colorized_vertices": int(colored_mask.sum()),
            "total_vertices": int(len(target_points)),
            "coverage": float(colored_mask.mean()),
            "report": report_path.name,
        }
        _atomic_json(manifest_path, manifest)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("source_cloud", type=Path)
    parser.add_argument("registration_report", type=Path)
    parser.add_argument("output_cloud", type=Path)
    parser.add_argument("--no-update-manifest", action="store_true",
                        help="write a comparison candidate without changing the project capture manifest")
    args = parser.parse_args()
    try:
        print(json.dumps(colorize_continuous_scan(
            args.run_directory, args.source_cloud, args.registration_report, args.output_cloud,
            update_manifest=not args.no_update_manifest,
        ), indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"error: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
