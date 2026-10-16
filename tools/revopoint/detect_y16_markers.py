#!/usr/bin/env python3
"""Find connected non-zero regions in a little-endian Revopoint Y16 frame."""

import argparse
import csv
import pathlib
import struct
import zlib


def png_chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload +
            struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def write_preview(path, values, width, height, components):
    nonzero = [value for value in values if value]
    low, high = min(nonzero), max(nonzero)
    pixels = bytearray()
    for y in range(height):
        pixels.append(0)
        for x in range(width):
            value = values[y * width + x]
            gray = 0 if value == 0 else min(255, (value - low) * 255 // max(1, high - low))
            pixels.extend((gray, gray, gray))

    def put(x, y, color):
        if 0 <= x < width and 0 <= y < height:
            offset = y * (width * 3 + 1) + 1 + x * 3
            pixels[offset:offset + 3] = bytes(color)

    for component in components:
        x0, y0, x1, y1 = component["bbox"]
        for x in range(x0, x1 + 1):
            put(x, y0, (255, 0, 0))
            put(x, y1, (255, 0, 0))
        for y in range(y0, y1 + 1):
            put(x0, y, (255, 0, 0))
            put(x1, y, (255, 0, 0))
        cx, cy = component["center"]
        for delta in range(-3, 4):
            put(round(cx) + delta, round(cy), (0, 255, 0))
            put(round(cx), round(cy) + delta, (0, 255, 0))

    raw = bytes(pixels)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    image = (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", header) +
             png_chunk(b"IDAT", zlib.compress(raw)) + png_chunk(b"IEND", b""))
    pathlib.Path(path).write_bytes(image)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=pathlib.Path, help="Y16 little-endian frame or one frame from a burst")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=400)
    parser.add_argument("--threshold", type=int, default=1, help="minimum Y16 value to consider valid")
    parser.add_argument("--min-area", type=int, default=20)
    parser.add_argument("--max-area", type=int, default=500, help="reject broad non-marker regions")
    parser.add_argument("--csv", type=pathlib.Path, required=True)
    parser.add_argument("--preview", type=pathlib.Path)
    args = parser.parse_args()

    raw = args.raw.read_bytes()
    count = args.width * args.height
    if len(raw) != count * 2:
        parser.error(f"expected one {args.width}x{args.height} Y16 frame ({count * 2} bytes), got {len(raw)}")
    if not 0 <= args.threshold <= 65535 or args.min_area < 1 or args.max_area < args.min_area:
        parser.error("threshold must be 0..65535 and area limits must be positive and ordered")
    values = struct.unpack(f"<{count}H", raw)
    mask = bytearray(value >= args.threshold and value != 0 for value in values)
    seen = bytearray(count)
    width, height = args.width, args.height
    components = []

    for seed, active in enumerate(mask):
        if not active or seen[seed]:
            continue
        queue = [seed]
        seen[seed] = 1
        area = perimeter = 0
        min_x, min_y, max_x, max_y = width, height, -1, -1
        sum_x = sum_y = sum_value = 0
        cursor = 0
        while cursor < len(queue):
            index = queue[cursor]
            cursor += 1
            x, y = index % width, index // width
            value = values[index]
            area += 1
            sum_x += x
            sum_y += y
            sum_value += value
            min_x, min_y = min(min_x, x), min(min_y, y)
            max_x, max_y = max(max_x, x), max(max_y, y)
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if nx < 0 or nx >= width or ny < 0 or ny >= height:
                    perimeter += 1
                    continue
                neighbor = ny * width + nx
                if not mask[neighbor]:
                    perimeter += 1
                elif not seen[neighbor]:
                    seen[neighbor] = 1
                    queue.append(neighbor)

        box_width, box_height = max_x - min_x + 1, max_y - min_y + 1
        aspect = min(box_width, box_height) / max(box_width, box_height)
        circularity = 4.0 * 3.141592653589793 * area / (perimeter * perimeter)
        if (area < args.min_area or area > args.max_area or max(box_width, box_height) > 100 or
                aspect < 0.25 or circularity < 0.25 or circularity > 1.35):
            continue
        components.append({
            "area": area,
            "bbox": (min_x, min_y, max_x, max_y),
            "center": (sum_x / area, sum_y / area),
            "mean_value": sum_value / area,
            "aspect": aspect,
            "circularity": circularity,
        })

    components.sort(key=lambda component: (component["center"][1], component["center"][0]))
    with args.csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("id", "cx_px", "cy_px", "area_px", "bbox_x", "bbox_y",
                         "bbox_width", "bbox_height", "aspect", "circularity", "mean_y16"))
        for marker_id, component in enumerate(components, 1):
            x0, y0, x1, y1 = component["bbox"]
            cx, cy = component["center"]
            writer.writerow((marker_id, f"{cx:.3f}", f"{cy:.3f}", component["area"],
                             x0, y0, x1 - x0 + 1, y1 - y0 + 1,
                             f"{component['aspect']:.3f}", f"{component['circularity']:.3f}",
                             f"{component['mean_value']:.1f}"))
    if args.preview:
        write_preview(args.preview, values, width, height, components)
    print(f"Detected {len(components)} Y16 connected-region candidates; "
          f"non-zero pixels={sum(mask)}, threshold={args.threshold}.")


if __name__ == "__main__":
    main()
