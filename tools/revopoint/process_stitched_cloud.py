"""Process a stitched point cloud PLY with the CADYX cleanup cycle.

Runs as a detached worker: reads the full-resolution source PLY, applies the
mandatory cleanup cycle (isolation filter, then overlap removal) with default
parameters, and writes the processed PLY plus a JSON report into a private
staging directory. The Core verifies hashes and publishes the artifacts.

Progress is reported as JSON lines on stdout:
    {"phase": <phase>, "done": <n>, "total": <m>}
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

ISOLATION_PARAMETERS = {"rate": 15., "component_factor": 3.}
OVERLAP_PARAMETERS = {"distance": .56, "radius": 2., "tangential_distance": .1}
STAGES = (("isolation_filter", ISOLATION_PARAMETERS), ("remove_overlaps", OVERLAP_PARAMETERS))


def _point_cloud_ply_bytes(points: list[tuple[float, float, float]]) -> bytes:
    """Encode points as binary little-endian PLY, matching mesh_ply_bytes layout."""
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        f"element vertex {len(points)}\n"
        "property float x\n"
        "property float y\n"
        "property float z\n"
        "end_header\n"
    ).encode("ascii")
    payload = bytearray()
    for point in points:
        payload.extend(struct.pack("<fff", *point))
    return header + bytes(payload)


def _progress(phase: str, done: int, total: int) -> None:
    print(json.dumps({"phase": phase, "done": done, "total": total}, ensure_ascii=False), flush=True)


def _fail(exc: BaseException) -> int:
    print(json.dumps({"state": "failed", "error": str(exc)[:2000]}, ensure_ascii=False), flush=True)
    print(str(exc), file=sys.stderr, flush=True)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--filename", required=True)
    parser.add_argument("--staging", type=Path, required=True)
    args = parser.parse_args()

    try:
        from cadyx.point_cloud import process_points
        from cadyx.scan import _sha256_file, read_point_cloud_ply

        staging = args.staging
        staging.mkdir(parents=True, exist_ok=True)

        _progress("load", 0, len(STAGES) + 1)
        source = args.source.resolve(strict=True)
        if source.is_symlink() or source.suffix.casefold() != ".ply":
            raise ValueError("Обрабатывать можно только обычный PLY-файл облака точек.")
        source_hash = _sha256_file(source)
        points, digest, _ = read_point_cloud_ply(source, full_resolution=True)
        if digest != source_hash:
            raise ValueError("Исходное облако изменилось во время чтения; обработка отменена.")

        stages = []
        current = points
        for index, (method, parameters) in enumerate(STAGES):
            _progress(method, index + 1, len(STAGES) + 1)
            diagnostics: dict = {}
            result = process_points(current, method, dict(parameters), diagnostics=diagnostics)
            stages.append({
                "method": method,
                "parameters": parameters,
                "input_points": len(current),
                "output_points": len(result),
                "removed_points": len(current) - len(result),
                "diagnostics": diagnostics,
            })
            current = result

        _progress("write", len(STAGES) + 1, len(STAGES) + 1)
        output = staging / "processed.ply"
        output.write_bytes(_point_cloud_ply_bytes(current))
        output_hash = _sha256_file(output)

        report = {
            "format": "cadyx-cloud-processing-v1",
            "state": "completed",
            "source_file": args.filename,
            "source_sha256": source_hash,
            "input_points": len(points),
            "output_points": len(current),
            "removed_points": len(points) - len(current),
            "stages": stages,
            "output": {"file": "processed.ply", "sha256": output_hash},
            "coordinate_units": "source_unverified",
            "scale_status": "unverified",
            "quality": "provisional",
        }
        (staging / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        print(json.dumps({"state": "completed", "report": str(staging / "report.json")},
                         ensure_ascii=False), flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001 - every worker failure is reported to the watcher.
        return _fail(exc)


if __name__ == "__main__":
    sys.exit(main())
