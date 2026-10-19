"""Versioned, JSON-safe scene contract shared by browser and agent clients."""

import math
import hashlib
import json
import struct
import threading
import zlib
from array import array
from collections import OrderedDict

from .kernel import GeometryKernel
from .models import Document
from .errors import ValidationError
from .assemblies import flatten_assembly, transform_point
from .sketches import spline_sample_points


_INSTANCE_TRANSFORM_CACHE_MAX_ENTRIES = 128
_INSTANCE_TRANSFORM_CACHE_MAX_BYTES = 64 * 1024 * 1024
_instance_transform_cache: OrderedDict[tuple[str, str, tuple[float, ...], tuple[float, ...]], tuple[list[float], int]] = OrderedDict()
_instance_transform_cache_bytes = 0
_instance_transform_cache_lock = threading.RLock()


def render_scene(document: Document, kernel: GeometryKernel) -> dict:
    from .agent_authoring import filter_consumed_sources

    source_bodies = {item['id']: item for item in document.objects if item.get('type') == 'body'}
    placements = flatten_assembly(document.assembly)
    placed_source_ids = {item['body_id'] for item in placements}
    document = filter_consumed_sources(document)
    objects = []
    by_id = {item["id"]: item for item in document.objects}
    consumed_bodies = {
        source_id
        for item in document.objects
        if item.get("type") == "feature"
        for source_id in ((item.get("base_body_id"), item.get("tool_body_id"))
                          if item.get("operation") in {"fuse_bodies", "cut_bodies"}
                          else (item.get("source_body_id"),)
                          if item.get("operation") in {
                              "fillet_edges", "resize_imported_thread", "transform_imported_brep",
                              "linear_pattern_body", "circular_pattern_body", "hole", "boss",
                              "pad_sketch_to_body", "chamfer_edges",
                          } else ())
    }
    feature_sketches = {
        source_id
        for item in document.objects
        if item.get("type") == "feature"
        for source_id in (
            [item.get("source_sketch_id")] if item.get("source_sketch_id")
            else [item["parameters"]["sketch_id"]] if item.get("operation") in {"pocket_sketch", "rib_from_sketch"}
            and isinstance(item.get("parameters", {}).get("sketch_id"), str)
            else item.get("source_sketch_ids", [])
        )
        if item.get("operation") in {"extrude_sketch", "revolve_sketch", "sweep_sketch", "loft_sketches",
                                      "pad_sketch_to_body", "pocket_sketch", "rib_from_sketch"}
    }
    feature_by_body = {item.get("body_id"): item for item in document.objects
                       if item.get("type") == "feature" and item.get("body_id")}
    feature_face_cache = {}

    def selected_feature_faces(body: dict, mesh: dict) -> set[int]:
        feature = feature_by_body.get(body["id"])
        feature_id = feature.get("id") if feature else None
        if feature_id in feature_face_cache:
            return feature_face_cache[feature_id]
        face_ids = mesh.get("selected_face_indices")
        if not face_ids and feature and feature.get("operation") in {
            "fillet_edges", "variable_fillet_edges", "chamfer_edges",
        }:
            source = by_id.get(feature.get("source_body_id"))
            parameters = feature.get("parameters", {})
            if source and source.get("geometry"):
                if feature["operation"] == "fillet_edges":
                    derived = kernel.create_fillet(source["geometry"],
                        edge_indices=parameters.get("edge_indices", []), radius=parameters.get("radius"))
                else:
                    from .advanced_features import _advanced_native
                    operation, values = (4, [parameters.get("start_radius"), parameters.get("end_radius")]) \
                        if feature["operation"] == "variable_fillet_edges" \
                        else (2, [parameters.get("distance")])
                    derived = _advanced_native(source["geometry"], operation, values,
                                               parameters.get("edge_indices", []))
                face_ids = kernel.tessellate(derived).get("selected_face_indices", [])
        result = {index for index in face_ids or [] if type(index) is int and index >= 0}
        if feature_id:
            feature_face_cache[feature_id] = result
        return result
    bounds_min = [math.inf, math.inf, math.inf]
    bounds_max = [-math.inf, -math.inf, -math.inf]
    has_coordinates = False

    def sketch_position(sketch_id: str, x: float, y: float) -> list[float]:
        sketch = by_id[sketch_id]
        section_axis = sketch.get("section_normal_axis")
        section_offset = sketch.get("section_offset")
        if section_axis == "X" and isinstance(section_offset, (int, float)):
            return [section_offset, x, y]
        if section_axis == "Y" and isinstance(section_offset, (int, float)):
            return [x, section_offset, y]
        if section_axis == "Z" and isinstance(section_offset, (int, float)):
            return [x, y, section_offset]
        return [x, 0, y] if sketch["plane"] == "XZ" else [x, y, 0]

    for item in document.objects:
        kind = item["type"]
        if kind == "body" and item["id"] in placed_source_ids:
            continue
        if kind == "body" and item.get("visible", True) is False:
            continue
        if kind == "body" and item["id"] in consumed_bodies:
            geometry = item.get("geometry")
            feature_id = next(iter(item.get("feature_ids", [])), None)
            if geometry is None or not feature_id:
                continue
            mesh = kernel.tessellate(geometry)
            selected_faces = selected_feature_faces(item, mesh)
            ranges = [face_range for face_range in mesh.get("face_triangle_ranges", [])
                      if face_range["face_index"] in selected_faces]
            if not ranges:
                continue
            remap, overlay_positions, overlay_triangles = {}, [], []
            for face_range in ranges:
                for triangle_index in range(face_range["start"], face_range["end"]):
                    for source_index in mesh["triangles"][triangle_index * 3:triangle_index * 3 + 3]:
                        target_index = remap.get(source_index)
                        if target_index is None:
                            target_index = len(overlay_positions) // 3
                            remap[source_index] = target_index
                            overlay_positions.extend(mesh["positions"][source_index * 3:source_index * 3 + 3])
                        overlay_triangles.append(target_index)
            if overlay_triangles:
                objects.append({"object_id": item["id"], "type": "feature_overlay",
                                "positions": overlay_positions, "triangles": overlay_triangles,
                                "lines": [], "points": [],
                                "feature_face_ranges": [{"feature_id": feature_id, "start": 0,
                                                         "end": len(overlay_triangles) // 3}]})
            continue
        if kind in {"point", "line", "circle", "ellipse", "spline"} and item.get("sketch_id") in feature_sketches:
            continue
        positions, triangles, lines, points = [], [], [], []
        feature_face_ranges = []
        if kind == "body" and item.get("geometry") is not None:
            mesh = kernel.tessellate(item["geometry"])
            positions, triangles = mesh["positions"], mesh["triangles"]
            selected_faces = selected_feature_faces(item, mesh)
            feature_id = next(iter(item.get("feature_ids", [])), None)
            feature_face_ranges = [
                {"feature_id": feature_id, "start": face_range["start"], "end": face_range["end"]}
                for face_range in mesh.get("face_triangle_ranges", [])
                if feature_id and face_range["face_index"] in selected_faces
            ]
            edge_vertex_offset = len(positions) // 3
            positions.extend(mesh.get("edge_positions", []))
            lines = [edge_vertex_offset + index for index in mesh.get("edge_lines", [])]
        elif kind == "point":
            positions = sketch_position(item["sketch_id"], item["x"], item["y"])
            points = [0]
        elif kind == "line":
            for key in ("start_id", "end_id"):
                point = by_id[item[key]]
                positions.extend(sketch_position(item["sketch_id"], point["x"], point["y"]))
            lines = [0, 1]
        elif kind == "circle":
            center = by_id[item["center_id"]]
            sweep = item.get("sweep_radians", math.tau)
            steps = max(1, math.ceil(64 * abs(sweep) / math.tau))
            for index in range(steps + (0 if sweep == math.tau else 1)):
                angle = math.atan2(by_id[item["start_id"]]["y"] - center["y"],
                                   by_id[item["start_id"]]["x"] - center["x"]) + sweep * index / steps \
                    if "sweep_radians" in item else math.tau * index / steps
                positions.extend(sketch_position(
                    item["sketch_id"], center["x"] + item["radius"] * math.cos(angle),
                    center["y"] + item["radius"] * math.sin(angle),
                ))
            for index in range(steps):
                lines.extend([index, index + 1 if "sweep_radians" in item else (index + 1) % steps])
        elif kind == "ellipse":
            rotation = item["rotation_radians"]
            cosine, sine = math.cos(rotation), math.sin(rotation)
            is_arc = "sweep_radians" in item
            sweep = item["sweep_radians"] if is_arc else math.tau
            steps = max(1, math.ceil(128 * abs(sweep) / math.tau))
            for index in range(steps + 1 if is_arc else steps):
                angle = item["start_angle_radians"] + sweep * index / steps if is_arc else math.tau * index / steps
                x = item["center_x"] + item["major_radius"] * math.cos(angle) * cosine - item["minor_radius"] * math.sin(angle) * sine
                y = item["center_y"] + item["major_radius"] * math.cos(angle) * sine + item["minor_radius"] * math.sin(angle) * cosine
                positions.extend(sketch_position(item["sketch_id"], x, y))
            for index in range(steps):
                lines.extend([index, index + 1 if is_arc else (index + 1) % steps])
        elif kind == "spline":
            sampled = spline_sample_points(item, by_id)
            for point in sampled:
                positions.extend(sketch_position(item["sketch_id"], point["x"], point["y"]))
            lines = [index for step in range(len(sampled) - 1) for index in (step, step + 1)]
        else:
            continue
        for offset in range(0, len(positions), 3):
            has_coordinates = True
            for axis in range(3):
                value = positions[offset + axis]
                if not math.isfinite(value):
                    raise ValidationError("Geometry exceeds the finite viewport coordinate range.")
                if value < bounds_min[axis]:
                    bounds_min[axis] = value
                if value > bounds_max[axis]:
                    bounds_max[axis] = value
        scene_item = {"object_id": item["id"], "type": kind,
                      "positions": positions, "triangles": triangles,
                      "lines": lines, "points": points}
        if feature_face_ranges:
            scene_item["feature_face_ranges"] = feature_face_ranges
        if kind in {"point", "line", "spline"}:
            scene_item["construction"] = item.get("construction", False)
        if kind == "line":
            scene_item["line_modes"] = [item.get("extent", "segment")]
        objects.append(scene_item)
    # Tessellate each source B-rep once per scene; instance placements never
    # become stored geometry or independently editable native solids.
    instance_meshes: dict[str, dict] = {}
    for placement in placements:
        source = source_bodies[placement["body_id"]]
        if source.get("visible", True) is False or source.get("geometry") is None:
            continue
        body_id = placement["body_id"]
        if body_id not in instance_meshes:
            instance_meshes[body_id] = kernel.tessellate(source["geometry"])
        mesh = instance_meshes[body_id]
        original_positions = list(mesh["positions"]) + list(mesh.get("edge_positions", []))
        descriptor_hash = hashlib.sha256(json.dumps(
            source["geometry"], sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode("ascii")).hexdigest()
        transform_key = ("occt-viewport-v1-linear-0.01-display-mesh-v1", descriptor_hash,
                         tuple(placement["translation"]), tuple(placement["rotation"]))
        global _instance_transform_cache_bytes
        with _instance_transform_cache_lock:
            cached_positions = _instance_transform_cache.get(transform_key)
            if cached_positions is not None:
                _instance_transform_cache.move_to_end(transform_key)
                positions = list(cached_positions[0])
            else:
                positions = []
        if cached_positions is None:
            for offset in range(0, len(original_positions), 3):
                point = transform_point(original_positions[offset:offset + 3],
                                        placement["translation"], placement["rotation"])
                positions.extend(point)
            size = len(positions) * 8
            if size <= _INSTANCE_TRANSFORM_CACHE_MAX_BYTES:
                with _instance_transform_cache_lock:
                    previous = _instance_transform_cache.pop(transform_key, None)
                    if previous is not None:
                        _instance_transform_cache_bytes -= previous[1]
                    while (_instance_transform_cache and
                           (_instance_transform_cache_bytes + size > _INSTANCE_TRANSFORM_CACHE_MAX_BYTES or
                            len(_instance_transform_cache) >= _INSTANCE_TRANSFORM_CACHE_MAX_ENTRIES)):
                        _, (_, removed_size) = _instance_transform_cache.popitem(last=False)
                        _instance_transform_cache_bytes -= removed_size
                    _instance_transform_cache[transform_key] = (list(positions), size)
                    _instance_transform_cache_bytes += size
        for offset in range(0, len(positions), 3):
            point = positions[offset:offset + 3]
            has_coordinates = True
            for axis, value in enumerate(point):
                if not math.isfinite(value):
                    raise ValidationError("Placed assembly geometry exceeds the viewport numeric range.")
                bounds_min[axis] = min(bounds_min[axis], value)
                bounds_max[axis] = max(bounds_max[axis], value)
        edge_offset = len(mesh["positions"]) // 3
        objects.append({
            "object_id": placement["id"], "type": "body",
            "name": placement["name"], "instance_id": placement["instance_id"],
            "instance_path": placement["instance_path"], "part_id": placement["part_id"],
            "source_body_id": body_id,
            "positions": positions, "triangles": mesh["triangles"],
            "lines": [edge_offset + index for index in mesh.get("edge_lines", [])],
            "points": [],
        })
    bounds = None
    if has_coordinates:
        bounds = {"min": bounds_min, "max": bounds_max}
        if not math.isfinite(math.hypot(*(high - low for low, high in zip(bounds_min, bounds_max)))):
            raise ValidationError("Scene extent exceeds the finite viewport coordinate range.")
    return {"version": 1, "revision": document.revision, "coordinate_system": "XYZ",
            "projection": "orthographic", "bounds": bounds, "objects": objects}


def render_scene_png(scene: dict, *, width: int = 960, height: int = 720, margin: int = 56) -> bytes:
    """Rasterize the shared scene for direct visual inspection by API clients."""
    right = (1 / math.sqrt(2), 1 / math.sqrt(2), 0)
    up = (-1 / math.sqrt(6), 1 / math.sqrt(6), 2 / math.sqrt(6))
    toward_camera = (1 / math.sqrt(3), -1 / math.sqrt(3), 1 / math.sqrt(3))
    projected_objects = []
    min_x = min_y = math.inf
    max_x = max_y = -math.inf
    has_projected = False

    for item in scene["objects"]:
        if item.get("type") == "feature_overlay":
            continue
        projected_vertices = []
        positions = item["positions"]
        for index in range(0, len(positions), 3):
            x, y, z = positions[index], positions[index + 1], positions[index + 2]
            point = (
                right[0] * x + right[1] * y + right[2] * z,
                up[0] * x + up[1] * y + up[2] * z,
                toward_camera[0] * x + toward_camera[1] * y + toward_camera[2] * z,
            )
            projected_vertices.append(point)
            has_projected = True
            if point[0] < min_x:
                min_x = point[0]
            if point[0] > max_x:
                max_x = point[0]
            if point[1] < min_y:
                min_y = point[1]
            if point[1] > max_y:
                max_y = point[1]
        projected_objects.append((item, positions, projected_vertices))

    if has_projected:
        scale = min((width - 2 * margin) / max(max_x - min_x, 1e-9),
                    (height - 2 * margin) / max(max_y - min_y, 1e-9))
        center_x, center_y = (min_x + max_x) / 2, (min_y + max_y) / 2
    else:
        scale, center_x, center_y = 1, 0, 0

    def screen(point: tuple[float, float, float]) -> tuple[float, float, float]:
        return (width / 2 + (point[0] - center_x) * scale,
                height / 2 - (point[1] - center_y) * scale, point[2])

    # Reuse the per-object projection lists for screen coordinates. Keeping
    # separate world, projected, and screen tuple arrays multiplies peak memory
    # for dense scan meshes.
    for _, _, projected_vertices in projected_objects:
        for index, point in enumerate(projected_vertices):
            projected_vertices[index] = screen(point)

    pixels = bytearray(bytes((20, 25, 28)) * (width * height))
    grid_color = bytes((39, 47, 52))
    grid_row = grid_color * width
    for y in range(0, height, 28):
        row = y * width * 3
        pixels[row:row + width * 3] = grid_row
    for x in range(0, width, 28):
        for y in range(height):
            index = (y * width + x) * 3
            pixels[index:index + 3] = grid_color

    depth_buffer = array("d", [-math.inf]) * (width * height)

    def edge(a: tuple[float, float, float], b: tuple[float, float, float], x: float, y: float) -> float:
        return (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0])

    def put_pixel(x: int, y: int, depth: float, color: bytes) -> None:
        if not (0 <= x < width and 0 <= y < height):
            return
        pixel_index = y * width + x
        if depth + 1e-7 < depth_buffer[pixel_index]:
            return
        depth_buffer[pixel_index] = depth
        byte_index = pixel_index * 3
        pixels[byte_index:byte_index + 3] = color

    def draw_segment(start: tuple[float, float, float], end: tuple[float, float, float],
                     color: bytes, thickness: int = 2) -> None:
        steps = max(1, round(max(abs(end[0] - start[0]), abs(end[1] - start[1]))))
        radius = thickness // 2
        for step in range(steps + 1):
            ratio = step / steps
            x = round(start[0] + (end[0] - start[0]) * ratio)
            y = round(start[1] + (end[1] - start[1]) * ratio)
            depth = start[2] + (end[2] - start[2]) * ratio
            for delta_y in range(-radius, radius + 1):
                for delta_x in range(-radius, radius + 1):
                    put_pixel(x + delta_x, y + delta_y, depth, color)

    def draw_dashed_segment(start: tuple[float, float, float], end: tuple[float, float, float],
                            color: bytes, dash: int = 8, gap: int = 5) -> None:
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        if length <= 0:
            return
        direction = tuple((end[index] - start[index]) / length for index in range(3))
        cursor = 0.0
        while cursor < length:
            finish = min(length, cursor + dash)
            draw_segment(
                tuple(start[index] + direction[index] * cursor for index in range(3)),
                tuple(start[index] + direction[index] * finish for index in range(3)),
                color,
            )
            cursor = finish + gap

    light = (0.38, -0.52, 0.76)
    light_length = math.sqrt(sum(value * value for value in light))
    light = tuple(value / light_length for value in light)
    base_color = (142, 185, 94)

    for item, positions, screen_vertices in projected_objects:
        if item["type"] != "body":
            continue
        indices = item["triangles"]
        for offset in range(0, len(indices), 3):
            i0, i1, i2 = indices[offset], indices[offset + 1], indices[offset + 2]
            a, b, c = i0 * 3, i1 * 3, i2 * 3
            ab_x = positions[b] - positions[a]
            ab_y = positions[b + 1] - positions[a + 1]
            ab_z = positions[b + 2] - positions[a + 2]
            ac_x = positions[c] - positions[a]
            ac_y = positions[c + 1] - positions[a + 1]
            ac_z = positions[c + 2] - positions[a + 2]
            normal_x = ab_y * ac_z - ab_z * ac_y
            normal_y = ab_z * ac_x - ab_x * ac_z
            normal_z = ab_x * ac_y - ab_y * ac_x
            normal_length = math.sqrt(normal_x * normal_x + normal_y * normal_y + normal_z * normal_z) or 1
            diffuse = max(0, (normal_x / normal_length) * light[0]
                         + (normal_y / normal_length) * light[1]
                         + (normal_z / normal_length) * light[2])
            shade = 0.48 + 0.52 * diffuse
            color = bytes(round(channel * shade) for channel in base_color)
            points = (screen_vertices[i0], screen_vertices[i1], screen_vertices[i2])
            area = edge(points[0], points[1], points[2][0], points[2][1])
            if abs(area) < 1e-9:
                continue
            inverse_area = 1 / area
            min_pixel_x = max(0, math.floor(min(point[0] for point in points)))
            max_pixel_x = min(width - 1, math.ceil(max(point[0] for point in points)))
            min_pixel_y = max(0, math.floor(min(point[1] for point in points)))
            max_pixel_y = min(height - 1, math.ceil(max(point[1] for point in points)))
            sample_x, sample_y = min_pixel_x + 0.5, min_pixel_y + 0.5
            row_w0 = edge(points[1], points[2], sample_x, sample_y) * inverse_area
            row_w1 = edge(points[2], points[0], sample_x, sample_y) * inverse_area
            row_w2 = 1 - row_w0 - row_w1
            w0_step_x = -(points[2][1] - points[1][1]) * inverse_area
            w1_step_x = -(points[0][1] - points[2][1]) * inverse_area
            w0_step_y = (points[2][0] - points[1][0]) * inverse_area
            w1_step_y = (points[0][0] - points[2][0]) * inverse_area
            w2_step_x = -w0_step_x - w1_step_x
            w2_step_y = -w0_step_y - w1_step_y
            for y in range(min_pixel_y, max_pixel_y + 1):
                w0, w1, w2 = row_w0, row_w1, row_w2
                pixel_index = y * width + min_pixel_x
                for x in range(min_pixel_x, max_pixel_x + 1):
                    if min(w0, w1, w2) >= -1e-7:
                        depth = points[0][2] * w0 + points[1][2] * w1 + points[2][2] * w2
                        if depth + 1e-7 >= depth_buffer[pixel_index]:
                            depth_buffer[pixel_index] = depth
                            byte_index = pixel_index * 3
                            pixels[byte_index:byte_index + 3] = color
                    pixel_index += 1
                    w0 += w0_step_x
                    w1 += w1_step_x
                    w2 += w2_step_x
                row_w0 += w0_step_y
                row_w1 += w1_step_y
                row_w2 += w2_step_y

    line_color = bytes((167, 216, 115))
    point_color = bytes((245, 202, 114))
    for item, _, screen_vertices in projected_objects:
        for index in range(0, len(item["lines"]), 2):
            start = screen_vertices[item["lines"][index]]
            end = screen_vertices[item["lines"][index + 1]]
            line_index = index // 2
            mode = item.get("line_modes", ["segment"] * (len(item["lines"]) // 2))[line_index]
            if mode == "infinite":
                dx, dy = end[0] - start[0], end[1] - start[1]
                length = math.hypot(dx, dy)
                if length > 0:
                    half = math.hypot(width, height) * 1.5
                    middle = tuple((start[axis] + end[axis]) / 2 for axis in range(3))
                    direction = (dx / length, dy / length, 0.0)
                    start = tuple(middle[axis] - direction[axis] * half for axis in range(3))
                    end = tuple(middle[axis] + direction[axis] * half for axis in range(3))
            if item.get("construction") or mode == "centerline":
                draw_dashed_segment(start, end, line_color)
            else:
                draw_segment(start, end, line_color)
        for vertex in item["points"]:
            x, y, _ = screen_vertices[vertex]
            if item.get("construction"):
                for angle_index in range(32):
                    angle = math.tau * angle_index / 32
                    next_angle = math.tau * (angle_index + 1) / 32
                    draw_segment((x + 4 * math.cos(angle), y + 4 * math.sin(angle), math.inf),
                                 (x + 4 * math.cos(next_angle), y + 4 * math.sin(next_angle), math.inf),
                                 point_color)
                continue
            for delta_y in range(-4, 5):
                for delta_x in range(-4, 5):
                    if delta_x * delta_x + delta_y * delta_y <= 16:
                        put_pixel(round(x) + delta_x, round(y) + delta_y, math.inf, point_color)

    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)
        raw.extend(pixels[y * stride:(y + 1) * stride])

    def chunk(kind: bytes, payload: bytes) -> bytes:
        data = kind + payload
        return struct.pack(">I", len(payload)) + data + struct.pack(">I", zlib.crc32(data) & 0xffffffff)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png_signature = bytes.fromhex("89504e470d0a1a0a")
    return png_signature + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
