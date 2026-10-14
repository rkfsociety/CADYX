"""Versioned, JSON-safe scene contract shared by browser and agent clients."""

import math
import struct
import zlib
from array import array

from .kernel import GeometryKernel
from .models import Document
from .errors import ValidationError


def render_scene(document: Document, kernel: GeometryKernel) -> dict:
    objects = []
    by_id = {item["id"]: item for item in document.objects}
    for item in document.objects:
        kind = item["type"]
        positions, triangles, lines, points = [], [], [], []
        if kind == "body" and item.get("geometry") is not None:
            mesh = kernel.tessellate(item["geometry"])
            positions, triangles = mesh["positions"], mesh["triangles"]
        elif kind == "point":
            positions = [item["x"], item["y"], 0]
            points = [0]
        elif kind == "line":
            for key in ("start_id", "end_id"):
                point = by_id[item[key]]
                positions.extend([point["x"], point["y"], 0])
            lines = [0, 1]
        elif kind == "circle":
            center = by_id[item["center_id"]]
            for index in range(64):
                angle = math.tau * index / 64
                positions.extend([center["x"] + item["radius"] * math.cos(angle),
                                  center["y"] + item["radius"] * math.sin(angle), 0])
                lines.extend([index, (index + 1) % 64])
        else:
            continue
        if not all(math.isfinite(value) for value in positions):
            raise ValidationError("Geometry exceeds the finite viewport coordinate range.")
        objects.append({"object_id": item["id"], "type": kind,
                        "positions": positions, "triangles": triangles,
                        "lines": lines, "points": points})
    coordinates = [item["positions"] for item in objects]
    bounds = None
    if coordinates:
        bounds = {"min": [min(min(values[axis::3]) for values in coordinates) for axis in range(3)],
                  "max": [max(max(values[axis::3]) for values in coordinates) for axis in range(3)]}
        if not math.isfinite(math.hypot(*(high - low for low, high in zip(bounds["min"], bounds["max"])))):
            raise ValidationError("Scene extent exceeds the finite viewport coordinate range.")
    return {"version": 1, "revision": document.revision, "coordinate_system": "XYZ",
            "projection": "orthographic", "bounds": bounds, "objects": objects}


def render_scene_png(scene: dict) -> bytes:
    """Rasterize the shared scene for direct visual inspection by API clients."""
    width, height, margin = 960, 720, 56
    right = (1 / math.sqrt(2), 1 / math.sqrt(2), 0)
    up = (-1 / math.sqrt(6), 1 / math.sqrt(6), 2 / math.sqrt(6))
    toward_camera = (1 / math.sqrt(3), -1 / math.sqrt(3), 1 / math.sqrt(3))
    projected_objects = []
    all_projected = []

    for item in scene["objects"]:
        world_vertices = []
        projected_vertices = []
        positions = item["positions"]
        for index in range(0, len(positions), 3):
            xyz = tuple(positions[index:index + 3])
            world_vertices.append(xyz)
            point = (
                sum(axis * value for axis, value in zip(right, xyz)),
                sum(axis * value for axis, value in zip(up, xyz)),
                sum(axis * value for axis, value in zip(toward_camera, xyz)),
            )
            projected_vertices.append(point)
            all_projected.append(point)
        projected_objects.append((item, world_vertices, projected_vertices))

    if all_projected:
        min_x = min(point[0] for point in all_projected)
        max_x = max(point[0] for point in all_projected)
        min_y = min(point[1] for point in all_projected)
        max_y = max(point[1] for point in all_projected)
        scale = min((width - 2 * margin) / max(max_x - min_x, 1e-9),
                    (height - 2 * margin) / max(max_y - min_y, 1e-9))
        center_x, center_y = (min_x + max_x) / 2, (min_y + max_y) / 2
    else:
        scale, center_x, center_y = 1, 0, 0

    def screen(point: tuple[float, float, float]) -> tuple[float, float, float]:
        return (width / 2 + (point[0] - center_x) * scale,
                height / 2 - (point[1] - center_y) * scale, point[2])

    pixels = bytearray(bytes((20, 25, 28)) * (width * height))
    grid_color = bytes((39, 47, 52))
    for y in range(0, height, 28):
        row = y * width * 3
        for x in range(width):
            index = row + x * 3
            pixels[index:index + 3] = grid_color
    for x in range(0, width, 28):
        for y in range(height):
            index = (y * width + x) * 3
            pixels[index:index + 3] = grid_color

    depth_buffer = array("d", [-math.inf]) * (width * height)

    def edge(a: tuple[float, float, float], b: tuple[float, float, float], x: float, y: float) -> float:
        return (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0])

    def put_pixel(x: int, y: int, depth: float, color: tuple[int, int, int]) -> None:
        if not (0 <= x < width and 0 <= y < height):
            return
        pixel_index = y * width + x
        if depth + 1e-7 < depth_buffer[pixel_index]:
            return
        depth_buffer[pixel_index] = depth
        byte_index = pixel_index * 3
        pixels[byte_index:byte_index + 3] = bytes(color)

    def draw_segment(start: tuple[float, float, float], end: tuple[float, float, float],
                     color: tuple[int, int, int], thickness: int = 2) -> None:
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

    light = (0.38, -0.52, 0.76)
    light_length = math.sqrt(sum(value * value for value in light))
    light = tuple(value / light_length for value in light)
    base_color = (142, 185, 94)

    for item, world_vertices, projected_vertices in projected_objects:
        if item["type"] != "body":
            continue
        indices = item["triangles"]
        for offset in range(0, len(indices), 3):
            triangle = indices[offset:offset + 3]
            a, b, c = (world_vertices[index] for index in triangle)
            ab = tuple(b[axis] - a[axis] for axis in range(3))
            ac = tuple(c[axis] - a[axis] for axis in range(3))
            normal = (ab[1] * ac[2] - ab[2] * ac[1],
                      ab[2] * ac[0] - ab[0] * ac[2],
                      ab[0] * ac[1] - ab[1] * ac[0])
            normal_length = math.sqrt(sum(value * value for value in normal)) or 1
            diffuse = max(0, sum((normal[axis] / normal_length) * light[axis] for axis in range(3)))
            shade = 0.48 + 0.52 * diffuse
            color = tuple(round(channel * shade) for channel in base_color)
            points = [screen(projected_vertices[index]) for index in triangle]
            area = edge(points[0], points[1], points[2][0], points[2][1])
            if abs(area) < 1e-9:
                continue
            min_x = max(0, math.floor(min(point[0] for point in points)))
            max_x = min(width - 1, math.ceil(max(point[0] for point in points)))
            min_y = max(0, math.floor(min(point[1] for point in points)))
            max_y = min(height - 1, math.ceil(max(point[1] for point in points)))
            for y in range(min_y, max_y + 1):
                for x in range(min_x, max_x + 1):
                    sample_x, sample_y = x + 0.5, y + 0.5
                    w0 = edge(points[1], points[2], sample_x, sample_y) / area
                    w1 = edge(points[2], points[0], sample_x, sample_y) / area
                    w2 = 1 - w0 - w1
                    if min(w0, w1, w2) < -1e-7:
                        continue
                    depth = sum(points[index][2] * weight for index, weight in enumerate((w0, w1, w2)))
                    put_pixel(x, y, depth, color)

    for item, _, projected_vertices in projected_objects:
        for index in range(0, len(item["lines"]), 2):
            start, end = (screen(projected_vertices[vertex]) for vertex in item["lines"][index:index + 2])
            draw_segment(start, end, (167, 216, 115))
        for vertex in item["points"]:
            x, y, _ = screen(projected_vertices[vertex])
            for delta_y in range(-4, 5):
                for delta_x in range(-4, 5):
                    if delta_x * delta_x + delta_y * delta_y <= 16:
                        put_pixel(round(x) + delta_x, round(y) + delta_y, math.inf, (245, 202, 114))

    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)
        raw.extend(pixels[y * stride:(y + 1) * stride])

    def chunk(kind: bytes, payload: bytes) -> bytes:
        data = kind + payload
        return struct.pack(">I", len(payload)) + data + struct.pack(">I", zlib.crc32(data) & 0xffffffff)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
