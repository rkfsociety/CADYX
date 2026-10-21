"""Register every Y16 frame from one continuous encoder-tracked turn and fuse a point-only cloud."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

REPOSITORY = Path(__file__).resolve().parents[2]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from tools.revopoint.align_y16_to_table import _map_frame
from tools.revopoint.reproject_y16 import reproject_y16

_RUN_ID = re.compile(r"^revopoint_\d{8}T\d{6}Z$")


def marker_similarity_matrix(frame: dict[str, Any], transform: dict[str, Any]) -> np.ndarray:
    """Return the 3D uniform similarity placing one camera frame in the table map frame."""
    origin = np.asarray(frame["scanner_table_plane_origin"], dtype=np.float64)
    axes = frame["scanner_table_plane_axes"]
    x_axis = np.asarray(axes["x"], dtype=np.float64)
    y_axis = np.asarray(axes["y"], dtype=np.float64)
    z_axis = np.asarray(axes["z"], dtype=np.float64)
    scale = float(frame["scanner_to_map_scale"])
    matrix = np.eye(4, dtype=np.float64)
    matrix[0, :3] = float(transform["a"]) * x_axis + float(transform["b"]) * y_axis
    matrix[1, :3] = float(transform["c"]) * x_axis + float(transform["d"]) * y_axis
    matrix[2, :3] = scale * z_axis
    matrix[0, 3] = float(transform["tx"]) - matrix[0, :3] @ origin
    matrix[1, 3] = float(transform["ty"]) - matrix[1, :3] @ origin
    matrix[2, 3] = -matrix[2, :3] @ origin
    return matrix


def compose_camera_to_reference(previous_pose: np.ndarray, current_to_previous: np.ndarray) -> np.ndarray:
    """Compose rigid frame-to-frame odometry into the first-frame coordinate system."""
    return previous_pose @ current_to_previous


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    if not rows:
        raise ValueError(f"{path.name} contains no rows")
    return rows


def _read_marker_anchor(run_dir: Path) -> tuple[str, np.ndarray, dict[str, Any]]:
    run_id = run_dir.name
    report = json.loads((run_dir / f"{run_id}_table_alignment.json").read_text(encoding="utf-8"))
    selected_rows = _read_rows(run_dir / "selected_poses.csv")
    selected_by_pose = {int(row["pose_index"]): row for row in selected_rows}
    candidates = []
    for frame in report.get("frames", []):
        if frame.get("status") != "aligned":
            continue
        residual = float(frame.get("marker_median_residual_map_units", math.inf))
        matches = int(frame.get("matched_marker_count", 0))
        scale = float(frame.get("scanner_to_map_scale", 0.0))
        if matches >= 5 and residual <= 1.0 and 0.75 <= scale <= 1.25:
            candidates.append((residual, -matches, frame))
    if not candidates:
        raise ValueError("No marker frame has at least five matches, <=1 map-unit residual and a stable scale.")
    _, _, frame = min(candidates)
    pose_row = selected_by_pose[int(frame["index"])]
    source_raw = pose_row["file"]
    match = re.fullmatch(r"frame_(\d+)\.raw", source_raw)
    if not match:
        raise ValueError("Selected marker anchor does not refer to an archived continuous Y16 frame.")
    metadata = {"source_frame_index": int(match.group(1)), "selected_pose_index": int(frame["index"]),
                "matched_marker_count": int(frame["matched_marker_count"]),
                "median_residual_map_units": float(frame["marker_median_residual_map_units"]),
                "scale": float(frame["scanner_to_map_scale"])}
    return source_raw, marker_similarity_matrix(frame, frame["map_similarity"]), metadata


def _cloud(points: np.ndarray, o3d: Any) -> Any:
    cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points.astype(np.float64, copy=False)))
    return cloud


def fuse_all_frames(run_dir: Path, output: Path, voxel_size: float = 2.0,
                    fused_voxel_size: float = 0.75) -> dict[str, Any]:
    import open3d as o3d

    run_dir = run_dir.resolve(strict=True)
    run_id = run_dir.name
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("run directory must be named revopoint_YYYYMMDDTHHMMSSZ")
    output = output.resolve()
    if output.is_relative_to(run_dir):
        raise ValueError("write the fused candidate outside the source capture folder")
    metadata_path = output.with_suffix(".json")
    if output.exists() or metadata_path.exists():
        raise FileExistsError("refusing to overwrite an existing all-frame candidate")
    if not math.isfinite(voxel_size) or voxel_size <= 0 or not math.isfinite(fused_voxel_size) or fused_voxel_size <= 0:
        raise ValueError("voxel sizes must be finite and positive")

    rows = _read_rows(run_dir / "depth_frames.csv")
    if len(rows) < 12:
        raise ValueError("at least 12 continuous Y16 frames are required for a full-turn registration")
    source_paths = [run_dir / "depth_frames" / row["file"] for row in rows]
    if any(not path.is_file() for path in source_paths):
        raise FileNotFoundError("a recorded Y16 frame listed in depth_frames.csv is missing")
    for previous, current in zip(rows, rows[1:]):
        if current["index"] == previous["index"]:
            raise ValueError("duplicate Y16 frame sequence number")

    q_bytes = (run_dir / "Q.bin").read_bytes()
    if len(q_bytes) != 64:
        raise ValueError("run-local Q.bin is missing or invalid")
    q = struct.unpack("<16f", q_bytes)
    scale_data = json.loads((run_dir / "depth_scale.json").read_text(encoding="utf-8"))
    depth_scale = float(scale_data["depth_scale_mm_per_raw_unit"])
    alignment = json.loads((run_dir / f"{run_id}_table_alignment.json").read_text(encoding="utf-8"))
    table_map = (run_dir / "framesmall.mkf").read_bytes()
    map_frame = _map_frame(table_map)
    anchor_raw, anchor_to_map, anchor_report = _read_marker_anchor(run_dir)
    anchor_row = next(row for row in rows if row["file"] == anchor_raw)
    anchor_index = int(anchor_row["index"])
    selected_rows = _read_rows(run_dir / "selected_poses.csv")
    loop_indices = sorted({int(re.search(r"frame_(\d+)\.raw", row["file"]).group(1))
                           for row in selected_rows if re.search(r"frame_(\d+)\.raw", row["file"])})
    loop_indices = sorted(set(loop_indices) | {0, len(rows) - 1})

    frame_clouds = []
    frame_counts = []
    downsampled: list[Any | None] = []
    odometry: list[np.ndarray] = []
    adjacent_edges = []
    adjacent_transforms: list[np.ndarray] = []
    adjacent_information: list[np.ndarray] = []
    registration = o3d.pipelines.registration
    correspondence_distance = voxel_size * 2.5
    first_down = None
    previous_down = None

    with tempfile.TemporaryDirectory(prefix=f"{run_id}_all_frames_") as temporary:
        temporary_path = Path(temporary)
        for index, raw_path in enumerate(source_paths):
            points = np.asarray(reproject_y16(raw_path.read_bytes(), q, depth_scale), dtype=np.float32)
            if len(points) < 100:
                raise ValueError(f"frame {index} contains fewer than 100 valid depth points")
            np.save(temporary_path / f"frame_{index:05d}.npy", points, allow_pickle=False)
            frame_counts.append(int(len(points)))
            current = _cloud(points, o3d).voxel_down_sample(voxel_size)
            if len(current.points) < 100:
                raise ValueError(f"frame {index} has too few points at registration voxel size {voxel_size}")
            current.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=voxel_size * 4, max_nn=40))
            if index == 0:
                first_down = current
                previous_down = current
                odometry.append(np.eye(4, dtype=np.float64))
                downsampled.append(current if index in loop_indices else None)
                continue

            result = registration.registration_icp(
                current, previous_down, correspondence_distance, np.eye(4),
                registration.TransformationEstimationPointToPlane(registration.TukeyLoss(1.0)),
            )
            if result.fitness < 0.25 or result.inlier_rmse > voxel_size * 1.5:
                raise RuntimeError(f"weak adjacent all-frame alignment {index - 1}->{index}: "
                                   f"fitness={result.fitness:.3f}, rmse={result.inlier_rmse:.3f}")
            transform = np.asarray(result.transformation)
            odometry.append(compose_camera_to_reference(odometry[-1], transform))
            information = registration.get_information_matrix_from_point_clouds(
                current, previous_down, correspondence_distance, transform)
            adjacent_transforms.append(transform)
            adjacent_information.append(information)
            adjacent_edges.append({"source": index, "target": index - 1,
                                   "fitness": float(result.fitness), "inlier_rmse": float(result.inlier_rmse)})
            pose_graph_node = current if index in loop_indices else None
            downsampled.append(pose_graph_node)
            previous_down = current

        if first_down is None or previous_down is None:
            raise RuntimeError("no point-cloud frames were prepared")

        pose_graph = registration.PoseGraph()
        for pose in odometry:
            pose_graph.nodes.append(registration.PoseGraphNode(pose.copy()))
        for index, edge in enumerate(adjacent_edges):
            source, target = edge["source"], edge["target"]
            transform = adjacent_transforms[index]
            information = adjacent_information[index]
            pose_graph.edges.append(registration.PoseGraphEdge(source, target, transform, information, uncertain=False))

        loop_edges = []
        anchor_clouds = {index: cloud for index, cloud in enumerate(downsampled) if cloud is not None}
        for source in sorted(set(loop_indices) | {len(rows) - 1}):
            if source == 0 or source not in anchor_clouds:
                continue
            candidate = registration.registration_icp(
                anchor_clouds[source], first_down, correspondence_distance, odometry[source],
                registration.TransformationEstimationPointToPlane(registration.TukeyLoss(1.0)),
            )
            if candidate.fitness >= 0.35 and candidate.inlier_rmse <= voxel_size * 1.5:
                information = registration.get_information_matrix_from_point_clouds(
                    anchor_clouds[source], first_down, correspondence_distance, candidate.transformation)
                pose_graph.edges.append(registration.PoseGraphEdge(
                    source, 0, np.asarray(candidate.transformation), information, uncertain=True))
                loop_edges.append({"source": source, "target": 0, "fitness": float(candidate.fitness),
                                   "inlier_rmse": float(candidate.inlier_rmse)})

        registration.global_optimization(
            pose_graph,
            registration.GlobalOptimizationLevenbergMarquardt(),
            registration.GlobalOptimizationConvergenceCriteria(),
            registration.GlobalOptimizationOption(max_correspondence_distance=correspondence_distance,
                                                   edge_prune_threshold=0.25, reference_node=0),
        )

        anchor_pose = np.asarray(pose_graph.nodes[anchor_index].pose)
        first_to_map = anchor_to_map @ np.linalg.inv(anchor_pose)
        fused_chunks = []
        for index, node in enumerate(pose_graph.nodes):
            points = np.load(temporary_path / f"frame_{index:05d}.npy", allow_pickle=False)
            cloud = _cloud(points, o3d)
            cloud.transform(node.pose)
            cloud.transform(first_to_map)
            reduced = cloud.voxel_down_sample(fused_voxel_size)
            fused_chunks.append(np.asarray(reduced.points, dtype=np.float32))
        all_fused_points = np.concatenate(fused_chunks, axis=0)
        fused = _cloud(all_fused_points, o3d).voxel_down_sample(fused_voxel_size)
        if not len(fused.points):
            raise RuntimeError("all-frame fusion produced no points")
        output.parent.mkdir(parents=True, exist_ok=True)
        if not o3d.io.write_point_cloud(str(output), fused, write_ascii=False, compressed=False):
            raise OSError(f"Open3D failed to write the fused point cloud: {output}")

        final_clouds = []
        for index in (0, len(rows) - 1):
            points = np.load(temporary_path / f"frame_{index:05d}.npy", allow_pickle=False)
            cloud = _cloud(points, o3d).voxel_down_sample(voxel_size)
            cloud.transform(pose_graph.nodes[index].pose)
            final_clouds.append(cloud)
        closure = registration.registration_icp(
            final_clouds[1], final_clouds[0], correspondence_distance, np.eye(4),
            registration.TransformationEstimationPointToPoint(False),
        )
        correction = np.asarray(closure.transformation)
        closure_rotation = float(np.degrees(np.arccos(np.clip((np.trace(correction[:3, :3]) - 1) / 2, -1, 1))))
        bounds = np.asarray(fused.get_axis_aligned_bounding_box().get_box_points())
        lower, upper = bounds.min(axis=0), bounds.max(axis=0)
        result: dict[str, Any] = {
            "format": "cadyx-continuous-all-frame-fusion-v1",
            "status": "provisional",
            "run_id": run_id,
            "coordinate_frame": "table_marker_map_center_xy",
            "coordinate_units": "table map units; physical scale remains unverified",
            "all_captured_frames_used": True,
            "source_frame_count": len(rows),
            "source_files": [path.name for path in source_paths],
            "source_point_counts": frame_counts,
            "registration": "adjacent robust point-to-plane ICP; selected 30-degree frames used only as loop-closure anchors",
            "table_encoder_used_for_frame_selection": True,
            "marker_anchor": anchor_report,
            "marker_map_sha256": __import__("hashlib").sha256(table_map).hexdigest(),
            "depth_scale_status": scale_data.get("status", "unverified"),
            "registration_voxel_size": voxel_size,
            "fusion_voxel_size": fused_voxel_size,
            "output_points": len(fused.points),
            "dimensions_map_units": (upper - lower).tolist(),
            "adjacent_edges": adjacent_edges,
            "loop_edges_to_first_frame": loop_edges,
            "last_to_first_closure": {
                "fitness": float(closure.fitness), "inlier_rmse": float(closure.inlier_rmse),
                "local_correction_degrees": closure_rotation,
                "translation_map_units": correction[:3, 3].tolist(),
            },
            "limitations": "point-only candidate; no mesh or RGB color projection; inspect surface coverage and closure before treating as final",
        }
        metadata_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("output_ply", type=Path)
    parser.add_argument("--voxel-size", type=float, default=2.0)
    parser.add_argument("--fused-voxel-size", type=float, default=0.75)
    args = parser.parse_args()
    try:
        print(json.dumps(fuse_all_frames(args.run_directory, args.output_ply,
                                         args.voxel_size, args.fused_voxel_size), indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"error: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
