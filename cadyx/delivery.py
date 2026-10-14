"""Checks and stable identities for exported CAD delivery artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import struct
from typing import Any

from .errors import ValidationError

_STL_DEVIATION_SAMPLE_LIMIT = 64


def geometry_sha256(geometry: dict[str, Any]) -> str:
    try:
        encoded = json.dumps(
            geometry, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValidationError("Body geometry cannot be fingerprinted for export.") from exc
    return hashlib.sha256(encoded).hexdigest()


def compare_step_metrics(source: dict[str, Any], imported: dict[str, Any]) -> dict[str, Any]:
    source_topology = source.get("topology")
    imported_topology = imported.get("topology")
    if (source.get("brep_valid") is not True or imported.get("brep_valid") is not True
            or not isinstance(source_topology, dict) or source_topology.get("solid_count") != 1
            or not isinstance(imported_topology, dict) or imported_topology.get("solid_count") != 1):
        raise ValidationError("STEP round-trip must produce one valid closed solid.")

    source_volume, imported_volume = source.get("volume"), imported.get("volume")
    if (isinstance(source_volume, bool) or not isinstance(source_volume, (int, float))
            or isinstance(imported_volume, bool) or not isinstance(imported_volume, (int, float))
            or not math.isfinite(source_volume) or not math.isfinite(imported_volume)
            or source_volume <= 0 or imported_volume <= 0):
        raise ValidationError("STEP round-trip returned invalid volume metrics.")
    relative_volume_error = abs(imported_volume - source_volume) / source_volume
    source_bounds, imported_bounds = source.get("bounds"), imported.get("bounds")
    if not isinstance(source_bounds, dict) or not isinstance(imported_bounds, dict):
        raise ValidationError("STEP round-trip returned invalid bounds metrics.")
    bounds_max_deviation = 0.0
    for side in ("min", "max"):
        left, right = source_bounds.get(side), imported_bounds.get(side)
        if (not isinstance(left, list) or not isinstance(right, list) or len(left) != 3 or len(right) != 3
                or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                       for value in (*left, *right))):
            raise ValidationError("STEP round-trip returned invalid bounds coordinates.")
        bounds_max_deviation = max(
            bounds_max_deviation, *(abs(float(a) - float(b)) for a, b in zip(left, right, strict=True)),
        )
    passed = (math.isclose(source_volume, imported_volume, rel_tol=1e-8, abs_tol=1e-7)
              and bounds_max_deviation <= 1e-6)
    if not passed:
        raise ValidationError("STEP round-trip changed the source solid's volume or bounds beyond tolerance.")
    return {
        "status": "passed",
        "source_solid_count": source_topology["solid_count"],
        "reimported_solid_count": imported_topology["solid_count"],
        "source_valid": source["brep_valid"],
        "reimported_valid": imported["brep_valid"],
        "relative_volume_error": relative_volume_error,
        "bounds_max_deviation": bounds_max_deviation,
        "tolerances": {"volume_relative": 1e-8, "volume_absolute": 1e-7, "bounds_absolute": 1e-6},
        "source_metrics": source,
        "reimported_metrics": imported,
    }


def verify_binary_stl(data: bytes, *, source_volume: float, native_metrics: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, bytes) or len(data) < 84:
        raise ValidationError("OCCT returned a truncated binary STL export.")
    if len(data) > 64 * 1024 * 1024:
        raise ValidationError("STL output exceeds the 64 MB delivery limit.")
    triangle_count = struct.unpack_from("<I", data, 80)[0]
    if triangle_count == 0 or len(data) != 84 + triangle_count * 50:
        raise ValidationError("OCCT returned an invalid binary STL export.")
    if native_metrics.get("format") != "binary_stl" or native_metrics.get("triangle_count") != triangle_count:
        raise ValidationError("Native STL verification metrics do not match the exported file.")

    edge_states: dict[bytes, int] = {}
    signed_volume = 0.0
    for index in range(triangle_count):
        offset = 84 + index * 50
        values = struct.unpack_from("<12f", data, offset)
        if any(not math.isfinite(value) for value in values):
            raise ValidationError("Binary STL contains non-finite normals or vertices.")
        normal = values[:3]
        vertices = [
            tuple(0.0 if coordinate == 0.0 else float(coordinate) for coordinate in values[start:start + 3])
            for start in (3, 6, 9)
        ]
        vertex_keys = [struct.pack("<3f", *vertex) for vertex in vertices]
        a, b, c = vertices
        ab = tuple(b[axis] - a[axis] for axis in range(3))
        ac = tuple(c[axis] - a[axis] for axis in range(3))
        cross = (ab[1] * ac[2] - ab[2] * ac[1],
                 ab[2] * ac[0] - ab[0] * ac[2],
                 ab[0] * ac[1] - ab[1] * ac[0])
        area_twice = math.sqrt(sum(value * value for value in cross))
        normal_length = math.sqrt(sum(value * value for value in normal))
        if area_twice <= 1e-18 or normal_length <= 1e-18:
            raise ValidationError("Binary STL contains a degenerate triangle or missing facet normal.")
        if sum(cross[axis] * normal[axis] for axis in range(3)) <= 0:
            raise ValidationError("Binary STL facet normals do not agree with triangle orientation.")
        signed_volume += (a[0] * (b[1] * c[2] - b[2] * c[1])
                          + a[1] * (b[2] * c[0] - b[0] * c[2])
                          + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6.0
        for first, second in ((0, 1), (1, 2), (2, 0)):
            start, end = vertex_keys[first], vertex_keys[second]
            key = start + end if start < end else end + start
            direction = start < end
            state = edge_states.get(key, 0)
            if state == 0:
                edge_states[key] = 1 if direction else 2
            elif state == 1:
                edge_states[key] = 3 if not direction else 4
            elif state == 2:
                edge_states[key] = 3 if direction else 4
            else:
                edge_states[key] = 5

    watertight = bool(edge_states) and all(state in {3, 4} for state in edge_states.values())
    orientation_consistent = bool(edge_states) and all(state == 3 for state in edge_states.values())
    if not watertight or not orientation_consistent:
        raise ValidationError("Binary STL is not a watertight, consistently oriented triangle shell.")
    if not math.isfinite(signed_volume) or signed_volume <= 0:
        raise ValidationError("Binary STL has an invalid signed volume or inward global orientation.")
    if (isinstance(source_volume, bool) or not isinstance(source_volume, (int, float))
            or not math.isfinite(source_volume) or source_volume <= 0):
        raise ValidationError("Source B-rep volume is invalid for STL comparison.")
    relative_volume_error = abs(signed_volume - source_volume) / source_volume
    surface_sample = native_metrics.get("surface_deviation_sample")
    if (not isinstance(surface_sample, dict)
            or surface_sample.get("sample_count") != min(triangle_count, _STL_DEVIATION_SAMPLE_LIMIT)
            or not all(isinstance(surface_sample.get(key), (int, float))
                       and math.isfinite(surface_sample[key]) for key in ("max", "rms"))):
        raise ValidationError("Native STL surface-deviation sample is missing or invalid.")
    return {
        "status": "passed",
        "format": "binary_stl",
        "triangle_count": triangle_count,
        "watertight": watertight,
        "orientation_consistent": orientation_consistent,
        "mesh_volume": signed_volume,
        "relative_volume_error": relative_volume_error,
        "linear_deflection": native_metrics["linear_deflection"],
        "angular_deflection": native_metrics["angular_deflection"],
        "surface_deviation_sample": surface_sample,
    }
