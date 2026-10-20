"""Register a 360 Y16 PLY sequence by geometry into frame zero, then fuse points only."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np

_RUN_ID = re.compile(r"^revopoint_\d{8}T\d{6}Z$")


def source_paths(project: Path, run_id: str) -> list[Path]:
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("run id must be revopoint_YYYYMMDDTHHMMSSZ")
    paths = []
    for index in range(12):
        choices = (
            project / f"{run_id}_pose_{index:03d}.ply",
            project / f"{run_id}_pose_pose_{index:03d}.ply",
        )
        path = next((candidate for candidate in choices if candidate.is_file()), None)
        if path is None:
            raise FileNotFoundError(f"Missing PLY for captured pose {index}/11.")
        paths.append(path)
    return paths


def _overlap_in_bounds(source: np.ndarray, target: np.ndarray,
                       lower: np.ndarray, upper: np.ndarray) -> dict[str, float | int]:
    from scipy.spatial import cKDTree

    margin = 5.0
    source = source[np.all((source >= lower - margin) & (source <= upper + margin), axis=1)]
    target = target[np.all((target >= lower - margin) & (target <= upper + margin), axis=1)]
    if not len(source) or not len(target):
        return {"source_points": int(len(source)), "target_points": int(len(target)),
                "source_to_target_within_5": 0.0, "target_to_source_within_5": 0.0}
    forward = cKDTree(target).query(source, k=1, workers=-1)[0]
    reverse = cKDTree(source).query(target, k=1, workers=-1)[0]
    return {
        "source_points": int(len(source)), "target_points": int(len(target)),
        "source_to_target_within_2": float(np.mean(forward < 2.0)),
        "target_to_source_within_2": float(np.mean(reverse < 2.0)),
        "source_to_target_within_5": float(np.mean(forward < 5.0)),
        "target_to_source_within_5": float(np.mean(reverse < 5.0)),
    }


def register_sequence(project: Path, run_id: str, output: Path,
                      voxel_size: float = 2.0, fused_voxel_size: float = 0.75) -> dict[str, Any]:
    import open3d as o3d

    project = project.resolve(strict=True)
    output = output.resolve()
    try:
        output.relative_to(project)
    except ValueError:
        pass
    else:
        raise ValueError("write the candidate outside the project, then upload it through the CADYX Agent API")
    metadata_path = output.with_suffix(".json")
    if output.exists() or metadata_path.exists():
        raise FileExistsError("refusing to overwrite a prior registration candidate")
    if not math.isfinite(voxel_size) or voxel_size <= 0:
        raise ValueError("voxel size must be positive and finite")
    if not math.isfinite(fused_voxel_size) or fused_voxel_size <= 0:
        raise ValueError("fused voxel size must be positive and finite")

    o3d.utility.random.seed(20261020)
    paths = source_paths(project, run_id)
    clouds = [o3d.io.read_point_cloud(str(path)) for path in paths]
    if any(len(cloud.points) == 0 for cloud in clouds):
        raise ValueError("a source cloud contains no finite points")
    registration = o3d.pipelines.registration
    downsampled = [cloud.voxel_down_sample(voxel_size) for cloud in clouds]
    features = []
    for cloud in downsampled:
        cloud.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=voxel_size * 4, max_nn=40))
        features.append(registration.compute_fpfh_feature(
            cloud, o3d.geometry.KDTreeSearchParamHybrid(radius=voxel_size * 9, max_nn=100)))

    correspondence_distance = voxel_size * 2.5

    def pairwise(source_id: int, target_id: int):
        global_result = registration.registration_ransac_based_on_feature_matching(
            downsampled[source_id], downsampled[target_id], features[source_id], features[target_id],
            True, correspondence_distance,
            registration.TransformationEstimationPointToPoint(False), 4,
            [registration.CorrespondenceCheckerBasedOnEdgeLength(0.9),
             registration.CorrespondenceCheckerBasedOnDistance(correspondence_distance)],
            registration.RANSACConvergenceCriteria(100_000, 0.999))
        result = registration.registration_icp(
            downsampled[source_id], downsampled[target_id], correspondence_distance,
            global_result.transformation,
            registration.TransformationEstimationPointToPlane(registration.TukeyLoss(1.0)))
        information = registration.get_information_matrix_from_point_clouds(
            downsampled[source_id], downsampled[target_id], correspondence_distance,
            result.transformation)
        return result, information

    pose_graph = registration.PoseGraph()
    odometry = np.eye(4)
    pose_graph.nodes.append(registration.PoseGraphNode(odometry.copy()))
    adjacent_edges = []
    for source_id in range(11):
        target_id = source_id + 1
        result, information = pairwise(source_id, target_id)
        if result.fitness < 0.35 or result.inlier_rmse > voxel_size * 1.5:
            raise RuntimeError(f"weak adjacent alignment {source_id}->{target_id}: "
                               f"fitness={result.fitness:.3f}, rmse={result.inlier_rmse:.3f}")
        transform = np.asarray(result.transformation)
        odometry = transform @ odometry
        pose_graph.nodes.append(registration.PoseGraphNode(np.linalg.inv(odometry)))
        pose_graph.edges.append(registration.PoseGraphEdge(
            source_id, target_id, transform, information, uncertain=False))
        adjacent_edges.append({"source": source_id, "target": target_id,
                               "fitness": float(result.fitness),
                               "inlier_rmse": float(result.inlier_rmse)})

    loop_edges = []
    for target_id in range(2, 12):
        result, information = pairwise(0, target_id)
        if result.fitness >= 0.35 and result.inlier_rmse <= voxel_size * 1.5:
            pose_graph.edges.append(registration.PoseGraphEdge(
                0, target_id, np.asarray(result.transformation), information, uncertain=True))
            loop_edges.append({"source": 0, "target": target_id,
                               "fitness": float(result.fitness),
                               "inlier_rmse": float(result.inlier_rmse)})

    registration.global_optimization(
        pose_graph,
        registration.GlobalOptimizationLevenbergMarquardt(),
        registration.GlobalOptimizationConvergenceCriteria(),
        registration.GlobalOptimizationOption(
            max_correspondence_distance=correspondence_distance,
            edge_prune_threshold=0.25,
            reference_node=0,
        ),
    )

    registered = []
    for cloud, node in zip(clouds, pose_graph.nodes):
        aligned = o3d.geometry.PointCloud(cloud)
        aligned.transform(node.pose)
        registered.append(aligned)
    merged = o3d.geometry.PointCloud()
    for cloud in registered:
        merged += cloud
    sampled = merged.voxel_down_sample(fused_voxel_size)
    labels = np.asarray(sampled.cluster_dbscan(eps=voxel_size * 1.5, min_points=12,
                                               print_progress=False))
    cluster_ids, cluster_sizes = np.unique(labels[labels >= 0], return_counts=True)
    if not len(cluster_ids):
        raise RuntimeError("fusion produced no connected point-cloud component")
    dominant = int(cluster_ids[int(np.argmax(cluster_sizes))])
    fused = sampled.select_by_index(np.flatnonzero(labels == dominant).tolist())
    output.parent.mkdir(parents=True, exist_ok=True)
    if not o3d.io.write_point_cloud(str(output), fused, write_ascii=True, compressed=False):
        raise OSError(f"Open3D failed to write {output}")

    bounds = np.asarray(fused.get_axis_aligned_bounding_box().get_box_points())
    lower, upper = bounds.min(axis=0), bounds.max(axis=0)
    overlap = [
        _overlap_in_bounds(np.asarray(registered[index].points),
                           np.asarray(registered[index + 1].points), lower, upper)
        for index in range(11)
    ]
    closure = _overlap_in_bounds(np.asarray(registered[-1].points),
                                 np.asarray(registered[0].points), lower, upper)
    closure_icp = registration.registration_icp(
        registered[-1], registered[0], correspondence_distance, np.eye(4),
        registration.TransformationEstimationPointToPoint(False))
    correction = np.asarray(closure_icp.transformation)
    correction_rotation = float(np.degrees(np.arccos(np.clip(
        (np.trace(correction[:3, :3]) - 1.0) / 2.0, -1.0, 1.0))))
    result: dict[str, Any] = {
        "format": "cadyx-geometric-point-cloud-fusion-v1",
        "status": "provisional",
        "run_id": run_id,
        "coordinate_frame": "first captured point cloud",
        "registration": "FPFH/RANSAC, robust point-to-plane ICP, pose-graph loop optimization",
        "table_encoder_or_axis_used": False,
        "source_files": [path.name for path in paths],
        "source_cloud_count": len(clouds),
        "source_point_counts": [len(cloud.points) for cloud in clouds],
        "voxel_size_q": fused_voxel_size,
        "output_points": len(fused.points),
        "discarded_isolated_points": int(len(sampled.points) - len(fused.points)),
        "dominant_component_fraction": float(len(fused.points) / max(1, len(sampled.points))),
        "dimensions_q": (upper - lower).tolist(),
        "adjacent_edges": adjacent_edges,
        "loop_edges_to_first_frame": loop_edges,
        "adjacent_direct_overlap": overlap,
        "last_to_first_direct_overlap": closure,
        "last_to_first_local_correction": {
            "fitness": float(closure_icp.fitness),
            "inlier_rmse_q": float(closure_icp.inlier_rmse),
            "rotation_degrees": correction_rotation,
            "translation_q": correction[:3, 3].tolist(),
        },
        "scale_status": "Q-derived scale is provisional and has not been calibrated against a reference dimension",
        "limitations": "point-only result; no mesh; inspect closure residual and surface coverage before treating the cloud as final",
    }
    metadata_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_directory", type=Path)
    parser.add_argument("run_id")
    parser.add_argument("output_ply", type=Path,
                        help="staging output outside the project; publish the reviewed PLY via the CADYX Agent API")
    parser.add_argument("--voxel-size", type=float, default=2.0,
                        help="Q-derived units used for registration (default: 2.0)")
    parser.add_argument("--fused-voxel-size", type=float, default=0.75,
                        help="Q-derived units used for point-only fusion (default: 0.75)")
    args = parser.parse_args()
    try:
        print(json.dumps(register_sequence(args.project_directory, args.run_id, args.output_ply,
                                           args.voxel_size, args.fused_voxel_size), indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
