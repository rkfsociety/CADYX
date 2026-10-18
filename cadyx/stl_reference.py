"""Bounded local STL reference import; triangle meshes remain reference geometry."""
from __future__ import annotations

import hashlib
import math
import struct
from pathlib import Path
from uuid import uuid4

from .errors import ValidationError
from .mesh_geometry import geometry_from_native_buffer, mesh_bounds

MAX_BYTES = 64 * 1024 * 1024
CAPABILITIES = ({
    "type": "import_stl",
    "description": "Read a local STL reference without changing it; preserves source coordinates and units remain unspecified.",
    "required": ["source_path", "expected_sha256"],
    "optional": ["name"],
    "constraints": ["Regular non-symlink local .stl, <=64 MiB, exact source SHA-256 required. Output is a reference mesh, not an editable B-rep."],
}, {
    "type": "set_body_visibility",
    "description": "Change body visibility without removing geometry or its references.",
    "required": ["body_id", "visible"], "optional": [],
    "constraints": ["visible must be a boolean; the body remains available for analysis."],
})
ACTIVITY_ACTIONS = {"import_stl": "Импорт эталона STL", "set_body_visibility": "Изменяет видимость тела"}

def _validate_triangle_count(count: int) -> None:
    if type(count) is not int or count < 1:
        raise ValidationError("STL triangle count must be positive.")

def parse_stl(data: bytes):
    """Parse binary (including solid-prefixed headers) or strict ASCII STL."""
    if not data or len(data) > MAX_BYTES:
        raise ValidationError("STL must contain between 1 byte and 64 MiB.")
    if len(data) >= 84 and 84 + 50 * struct.unpack_from("<I", data, 80)[0] == len(data):
        count = struct.unpack_from("<I", data, 80)[0]
        _validate_triangle_count(count)
        facets = [record[3:12] for record in struct.iter_unpack("<12fH", data[84:])]
    else:
        try:
            lines = [line.strip().split() for line in data.decode("ascii").splitlines() if line.strip()]
            if not lines or lines[0][0] != "solid":
                raise ValueError()
            facets = []
            index = 1
            while index < len(lines) and lines[index][0] != "endsolid":
                if len(lines[index]) != 5 or lines[index][:2] != ["facet", "normal"]:
                    raise ValueError()
                if lines[index + 1] != ["outer", "loop"]:
                    raise ValueError()
                values = []
                for line in lines[index + 2:index + 5]:
                    if len(line) != 4 or line[0] != "vertex":
                        raise ValueError()
                    values.extend(float(value) for value in line[1:])
                if len(values) != 9 or lines[index + 5] != ["endloop"] or lines[index + 6] != ["endfacet"]:
                    raise ValueError()
                facets.append(values)
                _validate_triangle_count(len(facets))
                index += 7
            if index != len(lines) - 1 or lines[index][0] != "endsolid" or not facets:
                raise ValueError()
        except (UnicodeError, ValueError, IndexError) as exc:
            raise ValidationError("STL is malformed or truncated.") from exc
    vertices, triangles, lookup = [], [], {}
    for facet in facets:
        face = []
        for offset in (0, 3, 6):
            vertex = tuple(facet[offset:offset + 3])
            if not all(math.isfinite(value) for value in vertex):
                raise ValidationError("STL contains non-finite coordinates.")
            if vertex not in lookup:
                lookup[vertex] = len(vertices)
                vertices.append(vertex)
            face.append(lookup[vertex])
        if len(set(face)) != 3:
            raise ValidationError("STL contains a degenerate triangle.")
        a, b, c = [vertices[i] for i in face]
        u, v = [b[i] - a[i] for i in range(3)], [c[i] - a[i] for i in range(3)]
        cross = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]]
        if not any(value != 0 for value in cross):
            raise ValidationError("STL contains a zero-area triangle.")
        triangles.append(face)
    try:
        buffer = struct.pack("<4s6If", b"CYSM", 1, len(vertices), len(triangles), 1, len(vertices), len(triangles), 1.0)
        buffer += b"".join(struct.pack("<3f", *vertex) for vertex in vertices)
        buffer += b"".join(struct.pack("<3I", *face) for face in triangles)
    except (OverflowError, struct.error) as exc:
        raise ValidationError("STL coordinates exceed float32 storage.") from exc
    geometry, stats = geometry_from_native_buffer(buffer)
    stats.pop("effective_voxel_size", None)
    return geometry, {**stats, "bounds": mesh_bounds(geometry), "units": "unitless"}

def _execute(self, document, command, *, progress=None, project_id=None):
    if command.get("type") == "set_body_visibility":
        if type(command.get("visible")) is not bool:
            raise ValidationError("visible must be a boolean.")
        body = next((item for item in document.objects if item.get("id") == command.get("body_id") and item.get("type") == "body"), None)
        if body is None:
            raise ValidationError("Body was not found.")
        body["visible"] = command["visible"]
        return {"body_id": body["id"], "visible": body["visible"]}
    value, expected = command.get("source_path"), command.get("expected_sha256")
    if not isinstance(value, str) or not isinstance(expected, str) or len(expected) != 64:
        raise ValidationError("STL import requires a local path and source SHA-256.")
    source = Path(value)
    if not source.is_absolute() or source.suffix.casefold() != ".stl" or source.is_symlink() or not source.is_file():
        raise ValidationError("STL source must be an absolute regular non-symlink .stl file.")
    name = command.get("name", source.stem)
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        raise ValidationError("STL name must contain 1 to 120 characters.")
    if progress:
        progress({"phase": "read_source"})
    with source.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValidationError("STL source changed since inspection.")
    geometry, stats = parse_stl(data)
    body_id, feature_id = f"body:{uuid4()}", f"feature:{uuid4()}"
    document.objects.extend([
        {"id": body_id, "type": "body", "name": name.strip(), "feature_ids": [feature_id], "geometry": geometry},
        {"id": feature_id, "type": "feature", "operation": "import_stl", "body_id": body_id,
         "source": {"file": source.name, "sha256": expected}, "parameters": {}, "stats": stats},
    ])
    return {"body_id": body_id, "feature_id": feature_id, **stats}
