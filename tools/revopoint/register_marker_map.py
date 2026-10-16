#!/usr/bin/env python3
"""Exploratorily register rotating RGB marker trajectories to an MKF point map."""

from __future__ import annotations

import argparse
import csv
import json
import math
import pathlib
import random
import statistics
from typing import Sequence


def read_csv(path: pathlib.Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def eigen_symmetric(matrix: list[list[float]]) -> tuple[list[float], list[list[float]]]:
    """Jacobi eigensolver for the small covariance matrices used here."""
    size = len(matrix)
    values = [row[:] for row in matrix]
    vectors = [[float(i == j) for j in range(size)] for i in range(size)]
    for _ in range(100 * size * size):
        p, q = max(((i, j) for i in range(size) for j in range(i + 1, size)),
                   key=lambda pair: abs(values[pair[0]][pair[1]]), default=(0, 0))
        if p == q or abs(values[p][q]) < 1e-12:
            break
        angle = 0.5 * math.atan2(2.0 * values[p][q], values[q][q] - values[p][p])
        cosine, sine = math.cos(angle), math.sin(angle)
        app, aqq, apq = values[p][p], values[q][q], values[p][q]
        for k in range(size):
            if k in (p, q):
                continue
            vkp, vkq = values[k][p], values[k][q]
            values[k][p] = values[p][k] = cosine * vkp - sine * vkq
            values[k][q] = values[q][k] = sine * vkp + cosine * vkq
        values[p][p] = cosine * cosine * app - 2.0 * sine * cosine * apq + sine * sine * aqq
        values[q][q] = sine * sine * app + 2.0 * sine * cosine * apq + cosine * cosine * aqq
        values[p][q] = values[q][p] = 0.0
        for k in range(size):
            vkp, vkq = vectors[k][p], vectors[k][q]
            vectors[k][p] = cosine * vkp - sine * vkq
            vectors[k][q] = sine * vkp + cosine * vkq
    order = sorted(range(size), key=lambda index: values[index][index], reverse=True)
    return ([max(0.0, values[index][index]) for index in order],
            [[vectors[row][index] for index in order] for row in range(size)])


def project_pca(points: Sequence[Sequence[float]], dimensions: int = 2
                ) -> tuple[list[list[float]], list[float]]:
    center = [statistics.mean(point[axis] for point in points)
              for axis in range(len(points[0]))]
    centered = [[value - center[axis] for axis, value in enumerate(point)] for point in points]
    covariance = [[sum(point[i] * point[j] for point in centered) / len(points)
                   for j in range(len(center))] for i in range(len(center))]
    eigenvalues, eigenvectors = eigen_symmetric(covariance)
    projected = [[sum(point[axis] * eigenvectors[axis][component]
                      for axis in range(len(center)))
                  for component in range(dimensions)] for point in centered]
    singular = [math.sqrt(value * len(points)) for value in eigenvalues]
    return projected, singular


def solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    size = len(vector)
    augmented = [matrix[row][:] + [vector[row]] for row in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("Affine fit is singular for these marker correspondences.")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        scale = augmented[column][column]
        augmented[column] = [value / scale for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [value - factor * item
                              for value, item in zip(augmented[row], augmented[column])]
    return [augmented[row][-1] for row in range(size)]


def fit_affine(source: Sequence[Sequence[float]], target: Sequence[Sequence[float]],
               pairs: Sequence[tuple[int, int]]) -> list[list[float]]:
    normal = [[0.0] * 3 for _ in range(3)]
    right = [[0.0] * 3 for _ in range(2)]
    for source_index, target_index in pairs:
        x, y = source[source_index]
        row = (x, y, 1.0)
        for i in range(3):
            for j in range(3):
                normal[i][j] += row[i] * row[j]
            for axis in range(2):
                right[axis][i] += row[i] * target[target_index][axis]
    coefficients = [solve(normal, right[axis]) for axis in range(2)]
    return [[coefficients[axis][column] for axis in range(2)] for column in range(3)]


def transform(point: Sequence[float], affine: Sequence[Sequence[float]]) -> list[float]:
    return [point[0] * affine[0][axis] + point[1] * affine[1][axis] + affine[2][axis]
            for axis in range(2)]


def distances(source: Sequence[Sequence[float]], target: Sequence[Sequence[float]]) -> list[list[float]]:
    return [[math.dist(a, b) for b in target] for a in source]


def mutual_matches(matrix: Sequence[Sequence[float]], threshold: float
                   ) -> list[tuple[int, int, float]]:
    if not matrix or not matrix[0]:
        return []
    nearest_target = [min(range(len(matrix[row])), key=lambda index: matrix[row][index])
                      for row in range(len(matrix))]
    nearest_source = [min(range(len(matrix)), key=lambda index: matrix[index][column])
                      for column in range(len(matrix[0]))]
    result = []
    for source_index, target_index in enumerate(nearest_target):
        error = matrix[source_index][target_index]
        if nearest_source[target_index] == source_index and error <= threshold:
            result.append((source_index, target_index, error))
    return result


def fit_similarity(source: Sequence[Sequence[float]], target: Sequence[Sequence[float]],
                   source_a: int, source_b: int, target_a: int, target_b: int,
                   reflection: int) -> list[list[float]] | None:
    sx = source[source_b][0] - source[source_a][0]
    sy = reflection * (source[source_b][1] - source[source_a][1])
    tx = target[target_b][0] - target[target_a][0]
    ty = target[target_b][1] - target[target_a][1]
    source_length = math.hypot(sx, sy)
    target_length = math.hypot(tx, ty)
    if source_length < 1e-9 or target_length < 1e-9:
        return None
    scale = target_length / source_length
    cosine = (sx * tx + sy * ty) / (source_length * target_length)
    sine = (sx * ty - sy * tx) / (source_length * target_length)
    a00, a01 = scale * cosine, -scale * sine * reflection
    a10, a11 = scale * sine, scale * cosine * reflection
    offset_x = target[target_a][0] - a00 * source[source_a][0] - a01 * source[source_a][1]
    offset_y = target[target_a][1] - a10 * source[source_a][0] - a11 * source[source_a][1]
    return [[a00, a10], [a01, a11], [offset_x, offset_y]]


def ransac(source: Sequence[Sequence[float]], target: Sequence[Sequence[float]],
           threshold: float, iterations: int, seed: int
           ) -> tuple[list[tuple[int, int, float]], list[list[float]]]:
    rng = random.Random(seed)
    best_pairs: list[tuple[int, int, float]] = []
    best_error = math.inf
    for _ in range(iterations):
        source_a, source_b = rng.sample(range(len(source)), 2)
        target_a, target_b = rng.sample(range(len(target)), 2)
        for reflection in (1, -1):
            affine = fit_similarity(source, target, source_a, source_b,
                                    target_a, target_b, reflection)
            if affine is None:
                continue
            projected = [transform(point, affine) for point in source]
            found = mutual_matches(distances(projected, target), threshold)
            error = statistics.median(pair[2] for pair in found) if found else math.inf
            if len(found) > len(best_pairs) or len(found) == len(best_pairs) and error < best_error:
                best_pairs, best_error = found, error
    if len(best_pairs) < 4:
        raise ValueError(f"Only {len(best_pairs)} orientation-free matches found; at least four are needed.")
    return best_pairs, fit_affine(source, target, [(a, b) for a, b, _ in best_pairs])


def association(source: Sequence[Sequence[float]], target: Sequence[Sequence[float]],
                threshold: float, iterations: int, seed: int
                ) -> tuple[list[tuple[int, int, float]], list[list[float]], list[float | None]]:
    pairs, affine = ransac(source, target, threshold, iterations, seed)
    for _ in range(5):
        projected = [transform(point, affine) for point in source]
        refined = mutual_matches(distances(projected, target), threshold)
        if len(refined) < 4 or [(a, b) for a, b, _ in refined] == [(a, b) for a, b, _ in pairs]:
            break
        pairs = refined
        affine = fit_affine(source, target, [(a, b) for a, b, _ in pairs])
    projected = [transform(point, affine) for point in source]
    pairs = mutual_matches(distances(projected, target), threshold)
    if len(pairs) < 4:
        raise ValueError(f"Refinement retained only {len(pairs)} matches; at least four are needed.")
    loo_errors = []
    for source_index, target_index, _ in pairs:
        training = [(a, b) for a, b, _ in pairs if a != source_index]
        if len(training) < 4:
            loo_errors.append(None)
            continue
        loo_affine = fit_affine(source, target, training)
        loo_errors.append(math.dist(transform(source[source_index], loo_affine),
                                    target[target_index]))
    return pairs, affine, loo_errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=pathlib.Path, help="Folder containing marker_trajectories.csv")
    parser.add_argument("--map", type=pathlib.Path, default=pathlib.Path("framesmall_points.csv"),
                        help="MKF point-map CSV (default: ./framesmall_points.csv)")
    parser.add_argument("--threshold-px", type=float, default=35.0,
                        help="RANSAC and final feature-plane residual limit (default: 35)")
    parser.add_argument("--iterations", type=int, default=40000,
                        help="Deterministic RANSAC hypotheses (default: 40000)")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    points = read_csv(args.map)
    map_xyz = [[float(row[key]) for key in ("x", "y", "z")] for row in points]
    map_xy, map_singular = project_pca(map_xyz)
    trajectory_rows = read_csv(args.capture / "marker_trajectories.csv")
    candidates = []
    for row in trajectory_rows:
        if row["classification"] != "smooth_rotating_candidate":
            continue
        span = float(row["angle_span_degrees"])
        fit_rms = float(row["fit_rms_px"])
        observations = int(row["observations"])
        if span < 100.0 or fit_rms >= 15.0 or observations < 70:
            continue
        coefficients = [float(row[key]) for key in ("cx_cos", "cy_cos", "cx_sin", "cy_sin")]
        candidates.append({"track_id": int(row["track_id"]), "span": span,
                           "fit_rms": fit_rms, "observations": observations,
                           "coefficients": coefficients})
    if len(candidates) < 4:
        raise ValueError(f"Only {len(candidates)} long-span trajectory candidates; at least four are needed.")

    feature_points, feature_singular = project_pca([row["coefficients"] for row in candidates])
    pairs, affine, loo_errors = association(map_xy, feature_points, args.threshold_px,
                                             args.iterations, args.seed)
    distances_px = [error for _, _, error in pairs]
    valid_loo_errors = [value for value in loo_errors if value is not None and math.isfinite(value)]
    map_dimensions = len(points)
    detections = []
    loo_by_pair = {(source_index, target_index): loo
                   for (source_index, target_index, _), loo in zip(pairs, loo_errors)}
    for source_index, target_index, error in pairs:
        row = candidates[target_index]
        loo = loo_by_pair[source_index, target_index]
        detections.append({
            "track_id": row["track_id"], "mkf_id": source_index + 1,
            "feature_residual_px": round(error, 3),
            "leave_one_out_error_px": round(loo, 3) if loo is not None and math.isfinite(loo) else None,
            "angle_span_degrees": round(row["span"], 3),
            "trajectory_fit_rms_px": round(row["fit_rms"], 3),
            "observations": row["observations"],
            "status": "candidate",
        })
    detections.sort(key=lambda row: (row["mkf_id"], row["track_id"]))
    csv_path = args.capture / "marker_map_registration.csv"
    json_path = args.capture / "marker_map_registration.json"
    if csv_path.exists() or json_path.exists():
        raise FileExistsError("Marker-map registration outputs already exist; preserve them and choose another capture folder.")
    with csv_path.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=(
            "track_id", "mkf_id", "feature_residual_px", "leave_one_out_error_px",
            "angle_span_degrees", "trajectory_fit_rms_px", "observations", "status"))
        writer.writeheader()
        writer.writerows(detections)
    summary = {
        "format": "revopoint-marker-map-registration-experimental-v1",
        "map_file": str(args.map), "map_points": map_dimensions,
        "candidate_tracks": len(candidates), "matched_candidates": len(detections),
        "threshold_px": args.threshold_px, "ransac_iterations": args.iterations,
        "affine_feature_residual_rms_px": round(math.sqrt(sum(error * error for error in distances_px)
                                                    / len(distances_px)), 3),
        "leave_one_out_rms_px": (round(math.sqrt(sum(value * value for value in valid_loo_errors)
                                         / len(valid_loo_errors)), 3) if valid_loo_errors else None),
        "map_plane_singular_values": [round(value, 6) for value in map_singular],
        "trajectory_feature_singular_values": [round(value, 6) for value in feature_singular],
        "affine_map_to_feature": [[round(value, 9) for value in row] for row in affine],
        "matches": detections,
        "limits": [
            "All matches are candidates generated from thresholded RGB detections and greedy trajectories.",
            "The point-map coordinate units and camera metric scale are not established.",
            "A leave-one-out affine error is internal fit validation, not independent identity confirmation.",
            "No object point cloud is fused or corrected by this registration output.",
        ],
    }
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: summary[key] for key in (
        "map_points", "candidate_tracks", "matched_candidates",
        "affine_feature_residual_rms_px", "leave_one_out_rms_px", "matches", "limits")},
        indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
