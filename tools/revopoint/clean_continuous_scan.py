"""Build a separate continuous-cloud candidate supported by multiple captured frames."""
from __future__ import annotations

import json
import math
import struct
from pathlib import Path
from typing import Any

import numpy as np

from tools.revopoint.reproject_y16 import reproject_y16_numpy


def support_mask(counts: np.ndarray, frame_count: int, fraction: float = 0.02) -> tuple[np.ndarray, int]:
    if frame_count < 2 or not math.isfinite(fraction) or not 0 < fraction <= 1:
        raise ValueError("Frame count and support fraction must be valid")
    threshold = max(2, math.ceil(frame_count * fraction))
    return counts >= threshold, threshold


def clean_continuous_scan(run_dir: Path, source: Path, report: dict[str, Any], output: Path,
                          *, voxel_size: float = 1.5, stop_path: Path | None = None) -> dict[str, Any]:
    import open3d as o3d

    run_dir = run_dir.resolve(strict=True)
    source = source.resolve(strict=True)
    output = output.resolve()
    if output.is_relative_to(run_dir) or output.exists() or output.with_suffix('.json').exists():
        raise ValueError("Cleaned output must be a new file outside the captured source series")
    if not math.isfinite(voxel_size) or voxel_size <= 0:
        raise ValueError("Support voxel size must be positive and finite")
    poses = report.get('frame_poses')
    if not isinstance(poses, list) or len(poses) != report.get('source_frame_count') or len(poses) < 2:
        raise ValueError("A registered pose is required for each captured frame")
    cloud = o3d.io.read_point_cloud(str(source))
    points = np.asarray(cloud.points)
    if not len(points) or not np.isfinite(points).all():
        raise ValueError("Source cloud is empty or invalid")
    origin = points.min(axis=0) - 2 * voxel_size
    dims = np.ceil((points.max(axis=0) - origin + 2 * voxel_size) / voxel_size).astype(np.int64) + 1
    cells = math.prod(int(value) for value in dims)
    if cells > 20_000_000:
        raise ValueError("Support grid exceeds its bounded memory limit")
    votes = np.zeros(cells, dtype=np.uint32)
    calibration = (run_dir / 'Q.bin').read_bytes()
    if len(calibration) != 64:
        raise ValueError("Run-local calibration is invalid")
    q = struct.unpack('<16f', calibration)
    scale = float(json.loads((run_dir / 'depth_scale.json').read_text(encoding='utf-8'))['depth_scale_mm_per_raw_unit'])
    for frame in poses:
        if stop_path is not None and stop_path.exists():
            raise RuntimeError("Continuous scan cancelled during cleanup; captured sources are preserved")
        name = frame.get('source_file')
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError("Invalid source frame name")
        transform = np.asarray(frame.get('scanner_to_output'), dtype=np.float64)
        if transform.shape != (4, 4) or not np.isfinite(transform).all():
            raise ValueError("Invalid registered frame transform")
        frame_points = reproject_y16_numpy((run_dir / 'depth_frames' / name).read_bytes(), q, scale)
        frame_points = frame_points @ transform[:3, :3].T + transform[:3, 3]
        index = np.floor((frame_points - origin) / voxel_size).astype(np.int64)
        index = index[((index >= 0) & (index < dims)).all(axis=1)]
        if len(index):
            votes[np.unique(np.ravel_multi_index(index.T, dims))] += 1
    index = np.floor((points - origin) / voxel_size).astype(np.int64)
    counts = votes[np.ravel_multi_index(index.T, dims)]
    mask, threshold = support_mask(counts, len(poses))
    cleaned = cloud.select_by_index(np.flatnonzero(mask).tolist())
    if not len(cleaned.points):
        raise ValueError("Frame-support cleanup would remove the entire cloud")
    translation = np.zeros(3, dtype=np.float64)
    if report.get('marker_anchor') is None:
        bounds = np.asarray(cleaned.points)
        lower, upper = bounds.min(axis=0), bounds.max(axis=0)
        translation = np.array([-(lower[0] + upper[0]) / 2, -(lower[1] + upper[1]) / 2, -lower[2]])
        cleaned.translate(translation)
    if not o3d.io.write_point_cloud(str(output), cleaned, write_ascii=False, compressed=False):
        raise OSError("Could not write the frame-support candidate")
    result = {'format': 'cadyx-continuous-frame-support-v1', 'status': 'provisional',
              'source_frame_count': len(poses), 'support_voxel_size': voxel_size,
              'minimum_support_frames': threshold, 'placement_translation': translation.tolist(), 'input_points': len(points),
              'output_points': len(cleaned.points), 'removed_points': int((~mask).sum()),
              'limitations': 'Frame support can remove thin or briefly visible features; compare with the preserved fused cloud.'}
    output.with_suffix('.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result
