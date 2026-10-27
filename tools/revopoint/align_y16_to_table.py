"""Place captured Y16 point clouds in the table-marker map coordinate frame."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import struct
import sys
from pathlib import Path
from typing import Iterable

REPOSITORY = Path(__file__).resolve().parents[2]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from tools.revopoint.preprocess_scan_frame import verified_preprocessed_frame

try:
    from .reproject_y16 import HEIGHT, MAX_DEPTH_MM, MIN_DEPTH_MM, RAW_BYTES, WIDTH
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from reproject_y16 import HEIGHT, MAX_DEPTH_MM, MIN_DEPTH_MM, RAW_BYTES, WIDTH

_HEADER = struct.Struct("<4I")
_RECORD = struct.Struct("<2i2f6d")
_RUN_ID = re.compile(r"^revopoint_\d{8}T\d{6}Z$")

Vector = tuple[float, float, float]
Point2 = tuple[float, float]


def _dot(a: Iterable[float], b: Iterable[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _cross(a: Vector, b: Vector) -> Vector:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _normalize(vector: Iterable[float], label: str) -> Vector:
    values = tuple(vector)
    length = math.sqrt(_dot(values, values))
    if not math.isfinite(length) or length < 1e-10:
        raise ValueError(f"{label} is degenerate")
    return tuple(value / length for value in values)  # type: ignore[return-value]


def _add(a: Vector, b: Vector) -> Vector:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _subtract(a: Vector, b: Vector) -> Vector:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale(vector: Vector, factor: float) -> Vector:
    return (vector[0] * factor, vector[1] * factor, vector[2] * factor)


def _map_frame(data: bytes) -> dict:
    if len(data) < _HEADER.size:
        raise ValueError("table marker map is shorter than its header")
    version, _, _, count = _HEADER.unpack_from(data)
    if version != 1 or not 4 <= count <= 10000 or len(data) != _HEADER.size + count * _RECORD.size:
        raise ValueError("table marker map has an invalid header or record count")

    points: list[Vector] = []
    normals: list[Vector] = []
    for index in range(count):
        fields = _RECORD.unpack_from(data, _HEADER.size + index * _RECORD.size)
        point = tuple(fields[4:7])
        normal = _normalize(fields[7:10], f"map normal {index + 1}")
        points.append(point)  # type: ignore[arg-type]
        normals.append(normal)
    normal = _normalize(tuple(sum(n[axis] for n in normals) for axis in range(3)), "map plane normal")
    if any(_dot(normal, candidate) < 0.5 for candidate in normals):
        raise ValueError("table marker normals disagree")
    x_projection = (1 - normal[0] * normal[0], -normal[0] * normal[1], -normal[0] * normal[2])
    if math.sqrt(_dot(x_projection, x_projection)) < 1e-8:
        x_axis = _normalize((-normal[1] * normal[0], 1 - normal[1] * normal[1], -normal[1] * normal[2]),
                            "map X axis")
    else:
        x_axis = _normalize(x_projection, "map X axis")
    y_axis = _cross(normal, x_axis)
    mean = tuple(sum(point[axis] for point in points) / len(points) for axis in range(3))
    center_index = min(range(count), key=lambda index: math.hypot(
        _dot(_subtract(points[index], mean), x_axis), _dot(_subtract(points[index], mean), y_axis)))
    center = points[center_index]
    projected = [( _dot(_subtract(point, center), x_axis), _dot(_subtract(point, center), y_axis))
                 for point in points]
    nearest_spacings = [min(math.dist(point, other) for other_index, other in enumerate(projected)
                            if other_index != index) for index, point in enumerate(projected)]
    threshold = max(1.0, sorted(nearest_spacings)[len(nearest_spacings) // 2] * 0.25)
    return {"points": projected, "center_index": center_index, "center": center,
            "normal": normal, "x_axis": x_axis, "y_axis": y_axis,
            "match_threshold": threshold}


def _reproject_pixel(x: int, y: int, depth_value: int, q: tuple[float, ...], depth_scale: float) -> Vector | None:
    depth = depth_value * depth_scale
    if not MIN_DEPTH_MM <= depth <= MAX_DEPTH_MM:
        return None
    ray_x = q[0] * x + q[1] * y + q[2] * depth + q[3]
    ray_y = q[4] * x + q[5] * y + q[6] * depth + q[7]
    ray_z = q[8] * x + q[9] * y + q[10] * depth + q[11]
    if not math.isfinite(ray_z) or abs(ray_z) < 1e-12:
        return None
    point = (ray_x * depth / ray_z, -ray_y * depth / ray_z, -depth)
    return point if all(math.isfinite(value) for value in point) else None


def detect_marker_regions(raw: bytes, depth_scale: float) -> list[list[int]]:
    """Return accepted connected table-marker pixel regions from a Y16 frame."""
    if len(raw) != RAW_BYTES:
        raise ValueError(f"Y16 frame must be exactly {RAW_BYTES} bytes")
    if not math.isfinite(depth_scale) or depth_scale <= 0:
        raise ValueError("Depth scale must be positive and finite")
    values = struct.unpack(f"<{WIDTH * HEIGHT}H", raw)
    in_range = bytearray(MIN_DEPTH_MM <= value * depth_scale <= MAX_DEPTH_MM for value in values)
    seen = bytearray(WIDTH * HEIGHT)
    regions: list[list[int]] = []
    for seed in range(WIDTH * HEIGHT):
        if seen[seed] or not in_range[seed]:
            continue
        pixels = [seed]
        seen[seed] = 1
        min_x, max_x = WIDTH, 0
        min_y, max_y = HEIGHT, 0
        sum_x = sum_y = 0
        cursor = 0
        while cursor < len(pixels):
            index = pixels[cursor]
            x, y = index % WIDTH, index // WIDTH
            min_x, max_x = min(min_x, x), max(max_x, x)
            min_y, max_y = min(min_y, y), max(max_y, y)
            sum_x += x
            sum_y += y
            neighbors = (index - 1 if x else -1, index + 1 if x < WIDTH - 1 else -1,
                         index - WIDTH if y else -1, index + WIDTH if y < HEIGHT - 1 else -1)
            for neighbor in neighbors:
                if neighbor >= 0 and not seen[neighbor] and in_range[neighbor]:
                    seen[neighbor] = 1
                    pixels.append(neighbor)
            cursor += 1
        width, height = max_x - min_x + 1, max_y - min_y + 1
        if not (20 <= len(pixels) <= 1000 and 0.3 < width / height < 3
                and len(pixels) / (width * height) > 0.45):
            continue
        regions.append(pixels)
    return regions


def detect_marker_centers(raw: bytes, q: tuple[float, ...], depth_scale: float) -> list[Vector]:
    """Mirror the live-preview connected-region detector for a saved Y16 frame."""
    values = struct.unpack(f"<{WIDTH * HEIGHT}H", raw)
    centers: list[Vector] = []
    for pixels in detect_marker_regions(raw, depth_scale):
        center_x = sum(index % WIDTH for index in pixels) / len(pixels)
        center_y = sum(index // WIDTH for index in pixels) / len(pixels)
        pixel = min((index for index in pixels if index % 2 == 0),
                    key=lambda index: (index % WIDTH - center_x) ** 2 + (index // WIDTH - center_y) ** 2,
                    default=-1)
        if pixel < 0:
            continue
        point = _reproject_pixel(pixel % WIDTH, pixel // WIDTH, values[pixel], q, depth_scale)
        if point is not None:
            centers.append(point)
    if len(centers) < 4:
        raise ValueError(f"only {len(centers)} table marker regions were detected; at least four are required")
    return centers


def _smallest_eigenvector(covariance: list[list[float]]) -> Vector:
    matrix = [row[:] for row in covariance]
    vectors = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    for _ in range(32):
        p, q = max(((0, 1), (0, 2), (1, 2)), key=lambda pair: abs(matrix[pair[0]][pair[1]]))
        if abs(matrix[p][q]) < 1e-12:
            break
        angle = 0.5 * math.atan2(2 * matrix[p][q], matrix[q][q] - matrix[p][p])
        cosine, sine = math.cos(angle), math.sin(angle)
        for k in range(3):
            mkp, mkq = matrix[k][p], matrix[k][q]
            matrix[k][p], matrix[k][q] = cosine * mkp - sine * mkq, sine * mkp + cosine * mkq
        for k in range(3):
            mpk, mqk = matrix[p][k], matrix[q][k]
            matrix[p][k], matrix[q][k] = cosine * mpk - sine * mqk, sine * mpk + cosine * mqk
        for k in range(3):
            vkp, vkq = vectors[k][p], vectors[k][q]
            vectors[k][p], vectors[k][q] = cosine * vkp - sine * vkq, sine * vkp + cosine * vkq
    index = min(range(3), key=lambda axis: matrix[axis][axis])
    return _normalize(tuple(vectors[row][index] for row in range(3)), "detected table plane normal")


def _table_frame(markers: list[Vector], center_direction: Vector) -> dict:
    seed = random.Random(0xC4D1A)
    best: tuple[list[Vector], Vector, float] | None = None
    for _ in range(512):
        a, b, c = seed.sample(markers, 3)
        normal = _cross(_subtract(b, a), _subtract(c, a))
        length = math.sqrt(_dot(normal, normal))
        if length < 1e-8:
            continue
        normal = _scale(normal, 1 / length)
        distance = -_dot(normal, a)
        inliers = [point for point in markers if abs(_dot(normal, point) + distance) <= 2.5]
        score = len({(math.floor(point[0] / 10), math.floor(point[1] / 10)) for point in inliers})
        if len(inliers) >= 4 and (best is None or score > best[2]):
            best = (inliers, normal, float(score))
    if best is None:
        raise ValueError("could not fit a stable plane through four or more detected table markers")
    inliers = best[0]
    origin = tuple(sum(point[axis] for point in inliers) / len(inliers) for axis in range(3))
    covariance = [[sum((p[row] - origin[row]) * (p[column] - origin[column]) for p in inliers) / len(inliers)
                    for column in range(3)] for row in range(3)]
    z_axis = _smallest_eigenvector(covariance)
    away_from_camera = _scale(center_direction, -1)
    if _dot(z_axis, away_from_camera) < 0:
        z_axis = _scale(z_axis, -1)
    x_guess = (1.0, 0.0, 0.0)
    x_projection = _subtract(x_guess, _scale(z_axis, _dot(x_guess, z_axis)))
    if math.sqrt(_dot(x_projection, x_projection)) < 1e-8:
        x_guess = (0.0, 1.0, 0.0)
        x_projection = _subtract(x_guess, _scale(z_axis, _dot(x_guess, z_axis)))
    x_axis = _normalize(x_projection, "detected table X axis")
    y_axis = _cross(z_axis, x_axis)
    projected = []
    for point in markers:
        relative = _subtract(point, origin)
        projected.append((_dot(relative, x_axis), _dot(relative, y_axis)))
    return {"origin": origin, "x_axis": x_axis, "y_axis": y_axis, "z_axis": z_axis,
            "markers_2d": projected, "inlier_count": len(inliers)}


def _center_ray(q: tuple[float, ...]) -> Vector:
    x, y, depth = WIDTH / 2, HEIGHT / 2, 100.0
    ray_x = q[0] * x + q[1] * y + q[2] * depth + q[3]
    ray_y = q[4] * x + q[5] * y + q[6] * depth + q[7]
    ray_z = q[8] * x + q[9] * y + q[10] * depth + q[11]
    if abs(ray_z) < 1e-12 or not math.isfinite(ray_z):
        raise ValueError("scanner calibration has an invalid image-center ray")
    return _normalize((ray_x * depth / ray_z, -ray_y * depth / ray_z, -depth), "image-center ray")


def _from_pair(source_a: Point2, source_b: Point2, target_a: Point2, target_b: Point2,
               reflection: int) -> dict | None:
    sx, sy = source_b[0] - source_a[0], reflection * (source_b[1] - source_a[1])
    tx, ty = target_b[0] - target_a[0], target_b[1] - target_a[1]
    source_length, target_length = math.hypot(sx, sy), math.hypot(tx, ty)
    if min(source_length, target_length) < 1e-8:
        return None
    scale = target_length / source_length
    cosine = (sx * tx + sy * ty) / (source_length * target_length)
    sine = (sx * ty - sy * tx) / (source_length * target_length)
    a, b = scale * cosine, -scale * sine * reflection
    c, d = scale * sine, scale * cosine * reflection
    return {"a": a, "b": b, "c": c, "d": d,
            "tx": target_a[0] - a * source_a[0] - b * source_a[1],
            "ty": target_a[1] - c * source_a[0] - d * source_a[1], "scale": scale,
            "reflection": reflection}


def _apply2(point: Point2, transform: dict) -> Point2:
    return (transform["a"] * point[0] + transform["b"] * point[1] + transform["tx"],
            transform["c"] * point[0] + transform["d"] * point[1] + transform["ty"])


def _mutual_matches(source: list[Point2], target: list[Point2], transform: dict,
                    threshold: float) -> list[tuple[int, int, float]]:
    projected = [_apply2(point, transform) for point in source]
    nearest_target = [min(range(len(target)), key=lambda index: math.dist(point, target[index])) for point in projected]
    nearest_source = [min(range(len(projected)), key=lambda index: math.dist(point, projected[index])) for point in target]
    return [(source_index, target_index, math.dist(projected[source_index], target[target_index]))
            for source_index, target_index in enumerate(nearest_target)
            if nearest_source[target_index] == source_index
            and math.dist(projected[source_index], target[target_index]) <= threshold]


def _fit_many(source: list[Point2], target: list[Point2], pairs: list[tuple[int, int, float]], reflection: int) -> dict | None:
    source_center = (sum(source[i][0] for i, _, _ in pairs) / len(pairs),
                     sum(reflection * source[i][1] for i, _, _ in pairs) / len(pairs))
    target_center = (sum(target[j][0] for _, j, _ in pairs) / len(pairs),
                     sum(target[j][1] for _, j, _ in pairs) / len(pairs))
    dot = cross = norm = 0.0
    for source_index, target_index, _ in pairs:
        sx, sy = source[source_index][0] - source_center[0], reflection * source[source_index][1] - source_center[1]
        tx, ty = target[target_index][0] - target_center[0], target[target_index][1] - target_center[1]
        dot += sx * tx + sy * ty
        cross += sx * ty - sy * tx
        norm += sx * sx + sy * sy
    magnitude = math.hypot(dot, cross)
    if norm < 1e-10 or magnitude < 1e-10:
        return None
    scale = magnitude / norm
    cosine, sine = dot / magnitude, cross / magnitude
    a, b = scale * cosine, -scale * sine * reflection
    c, d = scale * sine, scale * cosine * reflection
    return {"a": a, "b": b, "c": c, "d": d,
            "tx": target_center[0] - a * source_center[0] - b * reflection * source_center[1],
            "ty": target_center[1] - c * source_center[0] - d * reflection * source_center[1],
            "scale": scale, "reflection": reflection}


def register_markers(source: list[Point2], target: list[Point2], threshold: float) -> dict:
    if len(source) < 4 or len(target) < 4:
        raise ValueError("at least four detected and reference markers are required")
    rng = random.Random(0x51F15E5D)
    best: tuple[list[tuple[int, int, float]], dict, float] | None = None
    for _ in range(1024):
        source_a, source_b = rng.sample(range(len(source)), 2)
        target_a, target_b = rng.sample(range(len(target)), 2)
        for reflection in (1, -1):
            transform = _from_pair(source[source_a], source[source_b], target[target_a], target[target_b], reflection)
            if transform is None:
                continue
            pairs = _mutual_matches(source, target, transform, threshold)
            if len(pairs) < 4:
                continue
            error = sum(pair[2] for pair in pairs) / len(pairs)
            if best is None or (len(pairs), -error) > (len(best[0]), -best[2]):
                best = (pairs, transform, error)
    if best is None:
        raise ValueError("table map matching is ambiguous or has fewer than four reliable correspondences")
    pairs, transform, _ = best
    for _ in range(5):
        refined = _fit_many(source, target, pairs, transform.get("reflection", 1))
        if refined is None:
            break
        matches = _mutual_matches(source, target, refined, threshold)
        if len(matches) < 4:
            break
        transform, pairs = refined, matches
    residuals = sorted(math.dist(_apply2(source[i], transform), target[j]) for i, j, _ in pairs)
    median = residuals[len(residuals) // 2]
    if median > threshold:
        raise ValueError(f"table map marker residual is too large ({median:.3f} map units)")
    target_spread = max((math.dist(target[a], target[b]) for i, a, _ in pairs
                         for _, b, _ in pairs if a != b), default=0.0)
    if target_spread < threshold * 2:
        raise ValueError("matched table markers do not span enough of the map to fix its center")
    matched_target = [target[target_index] for _, target_index, _ in pairs]
    triangle_area2 = max((abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
                          for index_a, a in enumerate(matched_target)
                          for index_b, b in enumerate(matched_target[index_a + 1:], index_a + 1)
                          for c in matched_target[index_b + 1:]), default=0.0)
    if triangle_area2 < target_spread * target_spread * 0.02:
        raise ValueError("matched table markers are nearly collinear and cannot fix the table-plane frame")
    return {**transform, "matches": len(pairs), "median_residual": median,
            "threshold": threshold}


def _read_ascii_ply(path: Path) -> tuple[list[Vector], int]:
    with path.open("r", encoding="ascii") as stream:
        if stream.readline().strip() != "ply":
            raise ValueError(f"not a PLY point cloud: {path.name}")
        vertex_count = None
        properties: list[str] = []
        ascii_format = False
        for line in stream:
            fields = line.split()
            if not fields:
                continue
            if fields[0] == "format":
                ascii_format = len(fields) > 1 and fields[1] == "ascii"
            elif fields[0] == "element" and len(fields) == 3 and fields[1] == "vertex":
                vertex_count = int(fields[2])
            elif fields[0] == "property" and vertex_count is not None:
                properties.append(fields[-1])
            elif fields[0] == "end_header":
                break
        if not ascii_format or vertex_count is None or not all(key in properties for key in ("x", "y", "z")):
            raise ValueError(f"{path.name} must be an ASCII PLY with xyz vertices")
        columns = tuple(properties.index(key) for key in ("x", "y", "z"))
        points = []
        for index in range(vertex_count):
            fields = stream.readline().split()
            if not fields:
                raise ValueError(f"{path.name} ended after {index} of {vertex_count} vertices")
            point = tuple(float(fields[column]) for column in columns)
            if not all(math.isfinite(value) for value in point):
                raise ValueError(f"{path.name} contains a non-finite point")
            points.append(point)  # type: ignore[arg-type]
    if len(points) < 3:
        raise ValueError(f"{path.name} contains fewer than three points")
    return points, vertex_count


def _write_aligned_ply(path: Path, points: list[Vector], source_name: str) -> None:
    with path.open("x", encoding="ascii", newline="\n") as stream:
        stream.write("ply\nformat ascii 1.0\n")
        stream.write(f"comment table marker map alignment; source={source_name}\n")
        stream.write("comment coordinates are in map units; physical scale is unverified\n")
        stream.write(f"element vertex {len(points)}\nproperty float x\nproperty float y\nproperty float z\nend_header\n")
        for point in points:
            stream.write(f"{point[0]:.6f} {point[1]:.6f} {point[2]:.6f}\n")


def align_run(project: Path, run_id: str, marker_map: Path, calibration_file: Path,
              depth_scale: float, frame_count: int = 12) -> dict:
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("run id must use revopoint_YYYYMMDDTHHMMSSZ")
    if isinstance(frame_count, bool) or not isinstance(frame_count, int) or not 1 <= frame_count <= 360:
        raise ValueError("frame count must be an integer from 1 to 360")
    if not math.isfinite(depth_scale) or depth_scale <= 0:
        raise ValueError("depth scale must be positive and finite")
    project = project.resolve(strict=True)
    marker_map, calibration_file = marker_map.resolve(strict=True), calibration_file.resolve(strict=True)
    q_data = calibration_file.read_bytes()
    if len(q_data) != 64:
        raise ValueError("scanner calibration must contain 16 float32 values")
    q = struct.unpack("<16f", q_data)
    if not all(math.isfinite(value) for value in q):
        raise ValueError("scanner calibration contains non-finite values")
    map_data = marker_map.read_bytes()
    table = _map_frame(map_data)
    stage = project / f".cadyx-{run_id}-table-alignment"
    result_path = project / f"{run_id}_table_alignment.json"
    if stage.exists() or result_path.exists():
        raise FileExistsError("table-alignment outputs already exist; refusing to overwrite")
    stage.mkdir()
    frames = []
    run_files = []
    try:
        for index in range(frame_count):
            source_stem = f"{run_id}_pose_{index:03d}"
            raw_path = project / f"{source_stem}.raw"
            ply_path = project / f"{source_stem}.ply"
            if not raw_path.is_file() or not ply_path.is_file():
                legacy_stem = f"{run_id}_pose_pose_{index:03d}"
                raw_path = project / f"{legacy_stem}.raw"
                ply_path = project / f"{legacy_stem}.ply"
            if not raw_path.is_file() or not ply_path.is_file():
                raise FileNotFoundError(f"capture frame {index} is missing its source RAW or PLY")
            preprocessed = verified_preprocessed_frame(ply_path)
            registration_ply = preprocessed[0] if preprocessed is not None else ply_path
            source_points, source_count = _read_ascii_ply(registration_ply)
            try:
                markers = detect_marker_centers(raw_path.read_bytes(), q, depth_scale)
                frame = _table_frame(markers, _center_ray(q))
                marker_transform = register_markers(
                    frame["markers_2d"], table["points"], table["match_threshold"],
                )
            except ValueError as error:
                frames.append({"index": index, "source_ply": registration_ply.name,
                               "original_source_ply": ply_path.name, "source_raw": raw_path.name,
                               "status": "deferred", "alignment_error": str(error),
                               "source_point_count": source_count})
                continue
            aligned_points = []
            clipped_below = 0
            for point in source_points:
                relative = _subtract(point, frame["origin"])
                x = _dot(relative, frame["x_axis"])
                y = _dot(relative, frame["y_axis"])
                z = _dot(relative, frame["z_axis"])
                mapped_x, mapped_y = _apply2((x, y), marker_transform)
                mapped_z = z * marker_transform["scale"]
                if mapped_z < -2.5:
                    clipped_below += 1
                    continue
                aligned_points.append((mapped_x, mapped_y, max(0.0, mapped_z)))
            output_name = f"{source_stem}_table_aligned.ply"
            _write_aligned_ply(stage / output_name, aligned_points, registration_ply.name)
            run_files.append((stage / output_name, project / output_name))
            frames.append({"index": index, "source_ply": registration_ply.name,
                           "original_source_ply": ply_path.name, "source_raw": raw_path.name,
                           "status": "aligned",
                           "table_aligned_ply": output_name, "source_point_count": source_count,
                           "output_point_count": len(aligned_points), "clipped_below_table": clipped_below,
                           "detected_marker_count": len(markers), "matched_marker_count": marker_transform["matches"],
                           "marker_median_residual_map_units": marker_transform["median_residual"],
                           "scanner_to_map_scale": marker_transform["scale"],
                           "scanner_table_plane_origin": list(frame["origin"]),
                           "scanner_table_plane_axes": {"x": list(frame["x_axis"]),
                                                        "y": list(frame["y_axis"]),
                                                        "z": list(frame["z_axis"])},
                           "map_similarity": {key: marker_transform[key]
                                              for key in ("a", "b", "c", "d", "tx", "ty", "scale", "reflection")}})
        if any(destination.exists() for _, destination in run_files):
            raise FileExistsError("a table-aligned frame already exists; refusing to overwrite")
        for source, destination in run_files:
            source.rename(destination)
        aligned_count = len(run_files)
        status = "aligned_provisional" if aligned_count == frame_count else "partial" if aligned_count else "unavailable"
        result = {"format": "cadyx-table-marker-alignment-v1", "algorithm_version": 1, "status": status,
                  "run_id": run_id, "coordinate_frame": "table_marker_map_center_xy",
                  "center_marker_map_record": table["center_index"] + 1,
                  "center_map_coordinates": list(table["center"]),
                  "table_plane_normal": list(table["normal"]),
                  "coordinate_units": "table map units; semantics and physical scale unverified",
                  "registration": "per-frame marker geometric correspondences; 2D similarity fit in detected table plane",
                  "aligned_frame_count": aligned_count,
                  "deferred_frame_count": frame_count - aligned_count,
                  "deferred_frame_policy": "preserve the source frame; determine its object datum later from cloud or mesh axes and planes",
                  "turntable_telemetry_used_for_alignment": False,
                  "marker_map_file": marker_map.name,
                  "marker_map_sha256": hashlib.sha256(map_data).hexdigest(),
                  "calibration_file": calibration_file.name,
                  "calibration_sha256": hashlib.sha256(q_data).hexdigest(),
                  "depth_scale_mm_per_raw_unit": depth_scale,
                  "depth_scale_status": "provisional_for_direct_UVC",
                  "frames": frames,
                  "limitations": "source RAW and PLY files are preserved; aligned outputs use map units, whose physical scale is unverified"}
        with result_path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
        return result
    finally:
        for child in stage.iterdir():
            child.unlink()
        stage.rmdir()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--marker-map", type=Path, required=True)
    parser.add_argument("--calibration-file", type=Path, required=True)
    parser.add_argument("--depth-scale", type=float, required=True)
    parser.add_argument("--frame-count", type=int, default=12)
    arguments = parser.parse_args()
    print(json.dumps(align_run(arguments.project, arguments.run_id, arguments.marker_map,
                               arguments.calibration_file, arguments.depth_scale,
                               arguments.frame_count), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
