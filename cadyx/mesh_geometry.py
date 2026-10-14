"""Compact, validated storage for editable triangle meshes in documents."""

from __future__ import annotations

import base64
import hashlib
import math
import struct
import sys
import zlib
from array import array
from typing import Any

from .errors import ValidationError


_MESH_KERNEL = "cadyx-mesh-v1"
_MESH_KIND = "triangle_mesh"
_MESH_ENCODING = "zlib-f32-u32-le-v1"
_HEADER = struct.Struct("<4s6If")
_MAX_VERTICES = 300_000
_MAX_TRIANGLES = 100_000


def geometry_from_native_buffer(buffer: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    """Convert the native scan bridge result to document-safe geometry and metadata."""
    if len(buffer) < _HEADER.size:
        raise ValidationError("Native scan processor returned a truncated mesh.")
    magic, version, vertex_count, triangle_count, source_kind, input_vertices, input_triangles, voxel_size = (
        _HEADER.unpack_from(buffer)
    )
    if magic != b"CYSM" or version != 1 or source_kind not in {1, 2}:
        raise ValidationError("Native scan processor returned an unsupported mesh format.")
    payload_size = vertex_count * 12 + triangle_count * 12
    if len(buffer) != _HEADER.size + payload_size:
        raise ValidationError("Native scan processor returned inconsistent mesh counts.")
    if not math.isfinite(voxel_size) or voxel_size <= 0:
        raise ValidationError("Native scan processor returned an invalid voxel size.")
    if input_vertices <= 0 or vertex_count < 3 or triangle_count < 1:
        raise ValidationError("Native scan processor returned an empty mesh.")
    if vertex_count > _MAX_VERTICES or triangle_count > _MAX_TRIANGLES:
        raise ValidationError("Native scan mesh exceeds the supported document size.")

    packed = zlib.compress(memoryview(buffer)[_HEADER.size :], level=6)
    geometry = {
        "kernel": _MESH_KERNEL,
        "kind": _MESH_KIND,
        "encoding": _MESH_ENCODING,
        "vertex_count": vertex_count,
        "triangle_count": triangle_count,
        "sha256": hashlib.sha256(packed).hexdigest(),
        "data": base64.b64encode(packed).decode("ascii"),
    }
    validate_mesh_geometry(geometry)
    return geometry, {
        "source_kind": "triangle_mesh" if source_kind == 1 else "point_cloud",
        "input_vertices": input_vertices,
        "input_triangles": input_triangles,
        "output_vertices": vertex_count,
        "output_triangles": triangle_count,
        "effective_voxel_size": float(voxel_size),
    }


def _decode_payload(geometry: dict[str, Any]) -> tuple[array, array]:
    expected_keys = {
        "kernel", "kind", "encoding", "vertex_count", "triangle_count", "sha256", "data",
    }
    if not isinstance(geometry, dict) or set(geometry) != expected_keys:
        raise ValidationError("Geometry does not match the supported triangle mesh format.")
    if (geometry.get("kernel") != _MESH_KERNEL or geometry.get("kind") != _MESH_KIND or
            geometry.get("encoding") != _MESH_ENCODING):
        raise ValidationError("Geometry does not match the supported triangle mesh format.")

    vertex_count = geometry.get("vertex_count")
    triangle_count = geometry.get("triangle_count")
    if (type(vertex_count) is not int or type(triangle_count) is not int or
            vertex_count < 3 or vertex_count > _MAX_VERTICES or
            triangle_count < 1 or triangle_count > _MAX_TRIANGLES):
        raise ValidationError("Triangle mesh counts are missing or invalid.")
    checksum = geometry.get("sha256")
    encoded = geometry.get("data")
    if (not isinstance(checksum, str) or len(checksum) != 64 or
            not isinstance(encoded, str) or len(encoded) > 8_000_000):
        raise ValidationError("Triangle mesh data is missing or invalid.")
    try:
        compressed = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValidationError("Triangle mesh data is not valid Base64.") from exc
    if hashlib.sha256(compressed).hexdigest() != checksum:
        raise ValidationError("Triangle mesh checksum does not match its data.")

    expected_size = vertex_count * 12 + triangle_count * 12
    try:
        inflater = zlib.decompressobj()
        payload = inflater.decompress(compressed, expected_size + 1)
    except zlib.error as exc:
        raise ValidationError("Triangle mesh compressed data is invalid.") from exc
    if (len(payload) != expected_size or not inflater.eof or inflater.unused_data or
            inflater.unconsumed_tail):
        raise ValidationError("Triangle mesh data size does not match its counts.")

    positions = array("f")
    payload_view = memoryview(payload)
    positions.frombytes(payload_view[: vertex_count * 12])
    triangles = array("I")
    triangles.frombytes(payload_view[vertex_count * 12 :])
    if sys.byteorder != "little":
        positions.byteswap()
        triangles.byteswap()
    if len(positions) != vertex_count * 3 or len(triangles) != triangle_count * 3:
        raise ValidationError("Triangle mesh data is not aligned to its counts.")
    if not all(math.isfinite(value) for value in positions):
        raise ValidationError("Triangle mesh contains a non-finite vertex.")
    for offset in range(0, len(triangles), 3):
        first = triangles[offset]
        second = triangles[offset + 1]
        third = triangles[offset + 2]
        if (first >= vertex_count or second >= vertex_count or third >= vertex_count or
                first == second or second == third or first == third):
            raise ValidationError("Triangle mesh contains an invalid face index.")
    return positions, triangles


def validate_mesh_geometry(geometry: dict[str, Any]) -> None:
    """Validate packed triangle mesh geometry without exposing native objects."""
    _decode_payload(geometry)


def tessellate_mesh_geometry(geometry: dict[str, Any]) -> dict[str, list[int] | list[float]]:
    positions, triangles = _decode_payload(geometry)
    return {"positions": positions.tolist(), "triangles": triangles.tolist()}


def mesh_ply_bytes(geometry: dict[str, Any]) -> bytes:
    """Encode validated editable mesh geometry as a binary little-endian PLY."""
    positions, triangles = _decode_payload(geometry)
    if sys.byteorder != "little":
        positions.byteswap()
        triangles.byteswap()
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        f"element vertex {geometry['vertex_count']}\n"
        "property float x\n"
        "property float y\n"
        "property float z\n"
        f"element face {geometry['triangle_count']}\n"
        "property list uchar uint vertex_indices\n"
        "end_header\n"
    ).encode("ascii")
    faces = bytearray(geometry["triangle_count"] * 13)
    triangle_bytes = memoryview(triangles).cast("B")
    for face_index, offset in enumerate(range(0, len(triangles), 3)):
        face_offset = face_index * 13
        faces[face_offset] = 3
        source_offset = offset * triangles.itemsize
        faces[face_offset + 1 : face_offset + 13] = triangle_bytes[source_offset : source_offset + 12]
    return header + positions.tobytes() + faces

