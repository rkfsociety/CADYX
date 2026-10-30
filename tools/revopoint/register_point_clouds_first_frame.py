"""Register a 360 Y16 PLY sequence by geometry into frame zero, then fuse points only."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np

REPOSITORY = Path(__file__).resolve().parents[2]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from tools.revopoint.preprocess_scan_frame import preprocess_points, verified_preprocessed_frame

_RUN_ID = re.compile(r"^revopoint_\d{8}T\d{6}Z$")


def transform_points_to_table_map(points: np.ndarray, frame: dict[str, Any],
                                 clip_below_table: float = -2.5) -> tuple[np.ndarray, int]:
    """Rebase first-frame points through its marker fit into the table-map XY frame."""
    point_array = np.asarray(points, dtype=np.float64)
    if point_array.ndim != 2 or point_array.shape[1] != 3 or not np.isfinite(point_array).all():
        raise ValueError("point cloud must be a finite Nx3 array")
    try:
        origin = np.asarray(frame["scanner_table_plane_origin"], dtype=np.float64)
        axes = frame["scanner_table_plane_axes"]
        basis = np.asarray([axes["x"], axes["y"], axes["z"]], dtype=np.float64)
        similarity = frame["map_similarity"]
        a, b, c, d, tx, ty, scale = (float(similarity[key]) for key in
                                      ("a", "b", "c", "d", "tx", "ty", "scale"))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("frame-zero marker transform is incomplete") from error
    if (origin.shape != (3,) or basis.shape != (3, 3)
            or not np.isfinite(origin).all() or not np.isfinite(basis).all()
            or not np.allclose(basis @ basis.T, np.eye(3), atol=1e-3)
            or np.linalg.det(basis) < 0.999
            or not all(math.isfinite(value) for value in (a, b, c, d, tx, ty, scale))
            or scale <= 0 or not math.isfinite(clip_below_table)):
        raise ValueError("frame-zero marker transform is not a finite right-handed orthonormal frame")

    local = (point_array - origin) @ basis.T
    mapped_z = local[:, 2] * scale
    keep = mapped_z >= clip_below_table
    mapped = np.column_stack((a * local[:, 0] + b * local[:, 1] + tx,
                              c * local[:, 0] + d * local[:, 1] + ty,
                              np.maximum(mapped_z, 0.0)))
    return mapped[keep], int(np.count_nonzero(~keep))


def preprocess_frame_points(points: np.ndarray, frame_index: int) -> tuple[np.ndarray, dict[str, Any]]:
    """In-memory fallback for legacy series without during-capture cleanup artifacts."""
    cleaned, report = preprocess_points(
        [tuple(float(value) for value in point) for point in points], frame_index)
    report["timing"] = "after_capture_before_registration"
    return np.asarray(cleaned, dtype=np.float64), report


def source_paths(project: Path, run_id: str) -> list[Path]:
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("run id must be revopoint_YYYYMMDDTHHMMSSZ")
    manifest_path = project / f"{run_id}_scan_360.json"
    if not manifest_path.exists() and project.name == run_id:
        manifest_path = project.parent / f"scan_support_{run_id}" / manifest_path.name
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        frame_count = manifest.get("frame_count", 12)
    except (OSError, ValueError, TypeError, json.JSONDecodeError, AttributeError):
        frame_count = 12
    if isinstance(frame_count, bool) or not isinstance(frame_count, int) or not 1 <= frame_count <= 360:
        raise ValueError("scan manifest frame_count must be an integer from 1 to 360")
    paths = []
    for index in range(frame_count):
        choices = (
            project / f"{run_id}_pose_{index:03d}.ply",
            project / f"{run_id}_pose_pose_{index:03d}.ply",
        )
        path = next((candidate for candidate in choices if candidate.is_file()), None)
        if path is None:
            raise FileNotFoundError(f"Missing PLY for captured pose {index}/{frame_count - 1}.")
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
                      voxel_size: float = 2.0, fused_voxel_size: float = 0.75,
                      table_alignment_report: Path | None = None) -> dict[str, Any]:
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

    alignment_path: Path | None = None
    alignment_data: dict[str, Any] | None = None
    frame0_alignment: dict[str, Any] | None = None
    if table_alignment_report is not None:
        alignment_path = table_alignment_report.resolve(strict=True)
        try:
            alignment_data = json.loads(alignment_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("table-alignment report is unreadable") from error
        if (not isinstance(alignment_data, dict)
                or alignment_data.get("run_id") != run_id
                or alignment_data.get("coordinate_frame") != "table_marker_map_center_xy"):
            raise ValueError("table-alignment report does not match this scan run and coordinate frame")
        frame0_alignment = next((frame for frame in alignment_data.get("frames", [])
                                 if isinstance(frame, dict) and frame.get("index") == 0), None)
        if not isinstance(frame0_alignment, dict) or frame0_alignment.get("status") != "aligned":
            raise ValueError("frame zero does not have a verified table-marker alignment")

    o3d.utility.random.seed(20261020)
    paths = source_paths(project, run_id)
    prepared = [verified_preprocessed_frame(path) for path in paths]
    if any(item is not None for item in prepared) and not all(item is not None for item in prepared):
        raise ValueError("only some source frames have verified preprocessed artifacts; refusing to mix inputs")
    clouds = []
    source_point_counts = []
    frame_cleanup = []
    for index, path in enumerate(paths):
        source_cloud = o3d.io.read_point_cloud(str(path))
        if len(source_cloud.points) == 0:
            raise ValueError(f"source cloud {path.name} contains no finite points")
        source_point_counts.append(len(source_cloud.points))
        if prepared[index] is not None:
            cleaned_path, cleanup = prepared[index]
            cleaned_cloud = o3d.io.read_point_cloud(str(cleaned_path))
            if len(cleaned_cloud.points) != cleanup["cleaned_points"]:
                raise ValueError(f"preprocessed frame {index} point count does not match its report")
            cleanup = {**cleanup, "frame_index": index}
        else:
            cleaned_points, cleanup = preprocess_frame_points(
                np.asarray(source_cloud.points, dtype=np.float64), index)
            cleaned_cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(cleaned_points))
        frame_cleanup.append(cleanup)
        clouds.append(cleaned_cloud)
    registration = o3d.pipelines.registration
    downsampled = [cloud.voxel_down_sample(voxel_size) for cloud in clouds]
    features = []
    for cloud in downsampled:
        cloud.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=voxel_size * 4, max_nn=40))
        features.append(registration.compute_fpfh_feature(
            cloud, o3d.geometry.KDTreeSearchParamHybrid(radius=voxel_size * 9, max_nn=100)))

    correspondence_distance = voxel_size * 2.5
    rmse_limit = voxel_size * 1.5 + 1e-3

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
    accumulated_pose = np.eye(4)
    pose_graph.nodes.append(registration.PoseGraphNode(accumulated_pose.copy()))
    accumulated_cloud = o3d.geometry.PointCloud(clouds[0])
    accumulated_cloud = accumulated_cloud.voxel_down_sample(voxel_size)
    accumulated_cloud.estimate_normals(
        o3d.geometry.KDTreeSearchParamHybrid(radius=voxel_size * 4, max_nn=40))
    adjacent_edges = []
    incremental_edges = []
    frame_count = len(clouds)
    for target_id in range(1, frame_count):
        source_id = target_id - 1
        adjacent_result, _ = pairwise(source_id, target_id)
        if adjacent_result.fitness < 0.35 or adjacent_result.inlier_rmse > rmse_limit:
            raise RuntimeError(f"weak adjacent alignment {source_id}->{target_id}: "
                               f"fitness={adjacent_result.fitness:.3f}, rmse={adjacent_result.inlier_rmse:.3f}")
        adjacent_transform = np.asarray(adjacent_result.transformation)
        initial_transform = accumulated_pose @ np.linalg.inv(adjacent_transform)
        incremental_result = registration.registration_icp(
            downsampled[target_id], accumulated_cloud, correspondence_distance,
            initial_transform,
            registration.TransformationEstimationPointToPlane(registration.TukeyLoss(1.0)))
        if incremental_result.fitness < 0.35 or incremental_result.inlier_rmse > rmse_limit:
            raise RuntimeError(f"weak incremental alignment {target_id} to accumulated frames: "
                               f"fitness={incremental_result.fitness:.3f}, "
                               f"rmse={incremental_result.inlier_rmse:.3f}")
        current_pose = np.asarray(incremental_result.transformation)
        transform = np.linalg.inv(current_pose) @ accumulated_pose
        information = registration.get_information_matrix_from_point_clouds(
            downsampled[source_id], downsampled[target_id], correspondence_distance, transform)
        pose_graph.nodes.append(registration.PoseGraphNode(current_pose.copy()))
        pose_graph.edges.append(registration.PoseGraphEdge(
            source_id, target_id, transform, information, uncertain=False))
        aligned_source = o3d.geometry.PointCloud(clouds[target_id])
        aligned_source.transform(current_pose)
        accumulated_cloud += aligned_source
        accumulated_cloud = accumulated_cloud.voxel_down_sample(voxel_size)
        accumulated_cloud.estimate_normals(
            o3d.geometry.KDTreeSearchParamHybrid(radius=voxel_size * 4, max_nn=40))
        adjacent_edges.append({"source": source_id, "target": target_id,
                               "fitness": float(adjacent_result.fitness),
                               "inlier_rmse": float(adjacent_result.inlier_rmse)})
        incremental_edges.append({"source": target_id, "target": "accumulated",
                                  "fitness": float(incremental_result.fitness),
                                  "inlier_rmse": float(incremental_result.inlier_rmse)})
        accumulated_pose = current_pose

    loop_edges = []
    for target_id in range(2, frame_count):
        result, information = pairwise(0, target_id)
        if result.fitness >= 0.35 and result.inlier_rmse <= voxel_size * 1.5:
            pose_graph.edges.append(registration.PoseGraphEdge(
                0, target_id, np.asarray(result.transformation), information, uncertain=True))
            loop_edges.append({"source": 0, "target": target_id,
                               "fitness": float(result.fitness),
                               "inlier_rmse": float(result.inlier_rmse)})

    optimization_option = registration.GlobalOptimizationOption(
        max_correspondence_distance=correspondence_distance,
        edge_prune_threshold=0.25,
        reference_node=0,
    )

    def optimize_pose_graph() -> None:
        registration.global_optimization(
            pose_graph,
            registration.GlobalOptimizationLevenbergMarquardt(),
            registration.GlobalOptimizationConvergenceCriteria(),
            optimization_option,
        )

    def transform_clouds() -> list:
        aligned_clouds = []
        for source_cloud, node in zip(clouds, pose_graph.nodes):
            aligned = o3d.geometry.PointCloud(source_cloud)
            aligned.transform(node.pose)
            aligned_clouds.append(aligned)
        return aligned_clouds

    optimize_pose_graph()
    registered = transform_clouds()
    endpoint_closure = registration.registration_icp(
        registered[-1], registered[0], correspondence_distance, np.eye(4),
        registration.TransformationEstimationPointToPoint(False))
    closure_refinement: dict[str, Any] = {
        "status": "rejected",
        "fitness": float(endpoint_closure.fitness),
        "inlier_rmse_q": float(endpoint_closure.inlier_rmse),
        "table_encoder_or_axis_used": False,
    }
    if (endpoint_closure.fitness >= 0.7
            and endpoint_closure.inlier_rmse <= voxel_size * 0.75):
        current_first_pose = np.asarray(pose_graph.nodes[0].pose)
        current_last_pose = np.asarray(pose_graph.nodes[-1].pose)
        end_to_start_world = np.asarray(endpoint_closure.transformation)
        first_to_last = (np.linalg.inv(current_last_pose)
                         @ np.linalg.inv(end_to_start_world)
                         @ current_first_pose)
        closure_information = registration.get_information_matrix_from_point_clouds(
            downsampled[0], downsampled[-1], correspondence_distance, first_to_last)
        pose_graph.edges.append(registration.PoseGraphEdge(
            0, frame_count - 1, first_to_last, closure_information, uncertain=False))
        optimize_pose_graph()
        registered = transform_clouds()
        closure_refinement["status"] = "applied"
        closure_refinement["edge"] = [0, frame_count - 1]

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
    bounds = np.asarray(fused.get_axis_aligned_bounding_box().get_box_points())
    registration_lower, registration_upper = bounds.min(axis=0), bounds.max(axis=0)
    overlap = [
        _overlap_in_bounds(np.asarray(registered[index].points),
                           np.asarray(registered[index + 1].points),
                           registration_lower, registration_upper)
        for index in range(frame_count - 1)
    ]
    closure = _overlap_in_bounds(np.asarray(registered[-1].points),
                                 np.asarray(registered[0].points),
                                 registration_lower, registration_upper)
    closure_icp = registration.registration_icp(
        registered[-1], registered[0], correspondence_distance, np.eye(4),
        registration.TransformationEstimationPointToPoint(False))
    correction = np.asarray(closure_icp.transformation)
    correction_rotation = float(np.degrees(np.arccos(np.clip(
        (np.trace(correction[:3, :3]) - 1.0) / 2.0, -1.0, 1.0))))
    coordinate_frame = "first captured point cloud"
    table_map_alignment: dict[str, Any] | None = None
    clipped_below_table = 0
    if frame0_alignment is not None and alignment_data is not None and alignment_path is not None:
        mapped_points, clipped_below_table = transform_points_to_table_map(
            np.asarray(fused.points), frame0_alignment,
        )
        if len(mapped_points) == 0:
            raise RuntimeError("frame-zero table transform clipped every fused point")
        fused.points = o3d.utility.Vector3dVector(mapped_points)
        coordinate_frame = "table_marker_map_center_xy"
        table_map_alignment = {
            "report_file": alignment_path.name,
            "report_sha256": hashlib.sha256(alignment_path.read_bytes()).hexdigest(),
            "frame_index": 0,
            "matched_marker_count": frame0_alignment["matched_marker_count"],
            "marker_median_residual_map_units": frame0_alignment["marker_median_residual_map_units"],
            "scanner_to_map_scale": frame0_alignment["scanner_to_map_scale"],
            "clipped_below_table_points": clipped_below_table,
        }
    output_bounds = np.asarray(fused.get_axis_aligned_bounding_box().get_box_points())
    output_lower, output_upper = output_bounds.min(axis=0), output_bounds.max(axis=0)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not o3d.io.write_point_cloud(str(output), fused, write_ascii=True, compressed=False):
        raise OSError(f"Open3D failed to write {output}")
    result: dict[str, Any] = {
        "format": "cadyx-geometric-point-cloud-fusion-v1",
        "status": "provisional",
        "run_id": run_id,
        "coordinate_frame": coordinate_frame,
        "registration_coordinate_frame": "first captured point cloud",
        "table_map_alignment": table_map_alignment,
        "registration": "adjacent FPFH/RANSAC seeding, incremental merge-to-accumulated robust ICP, pose-graph loop optimization",
        "table_encoder_or_axis_used": False,
        "preprocessing": {
            "order": ["isolation_filter", "remove_overlaps"],
            "applied_per_source_frame_before_registration": True,
            "until_both_report_zero_removals": True,
            "source_frame_timing": ("during_capture" if prepared and prepared[0] is not None
                                     else "after_capture_before_registration_legacy_fallback"),
            "frames": frame_cleanup,
        },
        "source_files": [path.name for path in paths],
        "registration_input_files": [
            (item[0].name if item is not None else path.name) for path, item in zip(paths, prepared)],
        "source_cloud_count": len(clouds),
        "source_point_counts": source_point_counts,
        "preprocessed_point_counts": [len(cloud.points) for cloud in clouds],
        "voxel_size_q": fused_voxel_size,
        "output_points": len(fused.points),
        "discarded_isolated_points": int(len(sampled.points) - len(fused.points)),
        "dominant_component_fraction": float(len(fused.points) / max(1, len(sampled.points))),
        "registration_dimensions_q": (registration_upper - registration_lower).tolist(),
        "output_dimensions": (output_upper - output_lower).tolist(),
        "output_coordinate_units": ("table map units; physical scale unverified"
                                    if table_map_alignment else "Q-derived units; physical scale unverified"),
        "adjacent_edges": adjacent_edges,
        "incremental_edges": incremental_edges,
        "loop_edges_to_first_frame": loop_edges,
        "adjacent_direct_overlap": overlap,
        "last_to_first_direct_overlap": closure,
        "last_to_first_local_correction": {
            "fitness": float(closure_icp.fitness),
            "inlier_rmse_q": float(closure_icp.inlier_rmse),
            "rotation_degrees": correction_rotation,
            "translation_q": correction[:3, 3].tolist(),
        },
        "endpoint_closure_refinement": closure_refinement,
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
    parser.add_argument("--table-alignment-report", type=Path,
                        help="rebase the fused first-frame cloud to table-map XY using frame zero's marker fit")
    args = parser.parse_args()
    try:
        print(json.dumps(register_sequence(args.project_directory, args.run_id, args.output_ply,
                                           args.voxel_size, args.fused_voxel_size,
                                           args.table_alignment_report), indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
