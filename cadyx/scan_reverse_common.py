"""Common validated mesh helpers for CADYX scan reverse engineering."""
from __future__ import annotations

import base64
import hashlib
import math
import sys
import zlib
from array import array
from typing import Any, Iterable

from .errors import ValidationError
from .mesh_geometry import validate_mesh_geometry

_EPS = 1e-12

def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a finite number.")
    result = float(value)
    if not math.isfinite(result):
        raise ValidationError(f"{name} must be a finite number.")
    return result

def _positive(value: Any, name: str) -> float:
    result = _finite(value, name)
    if result <= 0:
        raise ValidationError(f"{name} must be positive.")
    return result

def _vector3(value: Any, name: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValidationError(f"{name} must be a three-component XYZ array.")
    return [_finite(item, name) for item in value]

def _dot(a: Iterable[float], b: Iterable[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))

def _cross(a: list[float], b: list[float]) -> list[float]:
    return [a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]

def _norm(vector: Iterable[float]) -> float:
    return math.sqrt(sum(value * value for value in vector))

def _unit(vector: list[float], name: str) -> list[float]:
    length = _norm(vector)
    if not math.isfinite(length) or length <= _EPS:
        raise ValidationError(f"{name} must be non-zero.")
    return [value / length for value in vector]

def _bounds(positions: list[float]) -> dict[str, list[float]]:
    if len(positions) < 9 or len(positions) % 3:
        raise ValidationError("Scan mesh does not contain valid XYZ positions.")
    low = [math.inf, math.inf, math.inf]
    high = [-math.inf, -math.inf, -math.inf]
    for offset in range(0, len(positions), 3):
        for axis in range(3):
            value = positions[offset + axis]
            if not math.isfinite(value):
                raise ValidationError("Scan mesh contains a non-finite coordinate.")
            low[axis] = min(low[axis], value)
            high[axis] = max(high[axis], value)
    return {"min": low, "max": high}

def _span(bounds: dict[str, list[float]]) -> list[float]:
    return [bounds["max"][axis] - bounds["min"][axis] for axis in range(3)]

def _pack_mesh(positions: list[float], triangles: list[int]) -> dict[str, Any]:
    if len(positions) % 3 or len(triangles) % 3:
        raise ValidationError("Triangle mesh buffers are not aligned.")
    vertex_count, triangle_count = len(positions) // 3, len(triangles) // 3
    if not 3 <= vertex_count <= 300_000 or not 1 <= triangle_count <= 100_000:
        raise ValidationError("Transformed scan exceeds the cadyx-mesh-v1 size limits.")
    native_positions = array("f", positions)
    native_triangles = array("I", triangles)
    if sys.byteorder != "little":
        native_positions.byteswap()
        native_triangles.byteswap()
    payload = native_positions.tobytes() + native_triangles.tobytes()
    packed = zlib.compress(payload, level=6)
    geometry = {
        "kernel": "cadyx-mesh-v1",
        "kind": "triangle_mesh",
        "encoding": "zlib-f32-u32-le-v1",
        "vertex_count": vertex_count,
        "triangle_count": triangle_count,
        "sha256": hashlib.sha256(packed).hexdigest(),
        "data": base64.b64encode(packed).decode("ascii"),
    }
    validate_mesh_geometry(geometry)
    return geometry

def _sample_points(positions: list[float], maximum: int) -> list[list[float]]:
    count = len(positions) // 3
    if count <= maximum:
        indices = range(count)
    else:
        indices = sorted({min(count - 1, (index * count) // maximum) for index in range(maximum)})
    return [[positions[index * 3], positions[index * 3 + 1], positions[index * 3 + 2]] for index in indices]
