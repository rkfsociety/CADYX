#!/usr/bin/env python3
"""Parse the observed Revopoint MKF marker-point export layout."""

import argparse
import csv
import math
import pathlib
import struct
import statistics


HEADER = struct.Struct("<4I")
RECORD = struct.Struct("<2i2f6d")


def parse(path):
    data = path.read_bytes()
    if len(data) < HEADER.size:
        raise ValueError("file is shorter than the observed 16-byte header")
    header = HEADER.unpack_from(data)
    version, field_1, field_2, count = header
    expected = HEADER.size + count * RECORD.size
    if len(data) != expected:
        raise ValueError(f"header declares {count} records ({expected} bytes), file has {len(data)} bytes")
    if count == 0:
        raise ValueError("file contains no points")

    points = []
    for index in range(count):
        fields = RECORD.unpack_from(data, HEADER.size + index * RECORD.size)
        meta_a, meta_b, scalar_a, scalar_b = fields[:4]
        values = fields[4:]
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"record {index} contains non-finite coordinates or normals")
        x, y, z, nx, ny, nz = values
        normal_length = math.sqrt(nx * nx + ny * ny + nz * nz)
        points.append({
            "id": index + 1,
            "meta_a": meta_a,
            "meta_b": meta_b,
            "scalar_a": scalar_a,
            "scalar_b": scalar_b,
            "x": x,
            "y": y,
            "z": z,
            "nx": nx,
            "ny": ny,
            "nz": nz,
            "normal_length": normal_length,
        })
    return header, points


def write_csv(path, header, points):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=(
            "id", "meta_a", "meta_b", "scalar_a", "scalar_b",
            "x", "y", "z", "nx", "ny", "nz", "normal_length",
        ))
        writer.writeheader()
        writer.writerows(points)


def write_ply(path, points):
    with path.open("w", encoding="ascii", newline="\n") as stream:
        stream.write("ply\nformat ascii 1.0\n")
        stream.write("comment parsed from observed framesmall.mkf record layout; coordinate units not established\n")
        stream.write(f"element vertex {len(points)}\n")
        for name in ("x", "y", "z", "nx", "ny", "nz"):
            stream.write(f"property float {name}\n")
        stream.write("end_header\n")
        for point in points:
            stream.write(" ".join(f"{point[key]:.9g}" for key in ("x", "y", "z", "nx", "ny", "nz")) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=pathlib.Path)
    parser.add_argument("--csv", type=pathlib.Path, required=True)
    parser.add_argument("--ply", type=pathlib.Path, required=True)
    args = parser.parse_args()
    header, points = parse(args.source)
    write_csv(args.csv, header, points)
    write_ply(args.ply, points)

    mean = [statistics.mean(point[key] for point in points) for key in ("x", "y", "z")]
    normal = [statistics.mean(point[key] for point in points) for key in ("nx", "ny", "nz")]
    length = math.sqrt(sum(value * value for value in normal))
    normal = [value / length for value in normal]
    residuals = [sum((point[key] - center) * normal[i] for i, (key, center) in enumerate(zip(("x", "y", "z"), mean))) for point in points]
    nearest = []
    for index, point in enumerate(points):
        distances = [math.dist((point["x"], point["y"], point["z"]),
                               (other["x"], other["y"], other["z"]))
                     for other_index, other in enumerate(points) if other_index != index]
        nearest.append(min(distances))
    print(f"header: version={header[0]}, fields=({header[1]}, {header[2]}), records={header[3]}")
    print(f"coordinate bounds: x={min(p['x'] for p in points):.3f}..{max(p['x'] for p in points):.3f}, "
          f"y={min(p['y'] for p in points):.3f}..{max(p['y'] for p in points):.3f}, "
          f"z={min(p['z'] for p in points):.3f}..{max(p['z'] for p in points):.3f}")
    print(f"normal lengths: {min(p['normal_length'] for p in points):.6f}..{max(p['normal_length'] for p in points):.6f}; "
          f"mean plane residual={statistics.mean(residuals):.6f}, rms={math.sqrt(statistics.mean(r*r for r in residuals)):.6f}")
    print(f"nearest-neighbor distance: median={statistics.median(nearest):.3f}, "
          f"range={min(nearest):.3f}..{max(nearest):.3f} (units not established)")
    print(f"wrote {args.csv} and {args.ply}")


if __name__ == "__main__":
    main()
