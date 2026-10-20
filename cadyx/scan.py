"""Workspace PLY validation and editable mesh reconstruction workflow."""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import struct
import tempfile
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from .errors import ValidationError
from .mesh_geometry import geometry_from_native_buffer
from .native_scan import NativeScan


DEFAULT_SCAN_PARAMETERS = {
    "reconstruction_method": "poisson",
    "voxel_size": 0.75,
    "target_triangles": 30000,
    "outlier_neighbors": 20,
    "outlier_std_ratio": 2.0,
    "poisson_depth": 8,
    "poisson_density_quantile": 0.01,
}
_MAX_PLY_BYTES = 512 * 1024 * 1024
_MAX_PLY_HEADER_BYTES = 64 * 1024
_MAX_PLY_VERTICES = 2_000_000
_MAX_PLY_FACES = 5_000_000
_MAX_POINT_CLOUD_VERTICES = 300_000


@contextmanager
def _native_scan_source(source: Path, source_hash: str) -> Iterator[Path]:
    """Give the Windows Open3D reader an ASCII path when the workspace is Unicode."""
    if os.name != "nt" or source.as_posix().isascii():
        yield source
        return

    temp_roots = (
        Path(tempfile.gettempdir()),
        Path(__file__).resolve().parents[1] / ".runtime",
    )
    temp_root = next((root for root in temp_roots if root.as_posix().isascii()), None)
    if temp_root is None:
        raise ValidationError("Windows scan processing requires an ASCII temporary directory.")

    try:
        temp_root.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="cadyx-scan-", dir=temp_root)
    except OSError as exc:
        raise ValidationError(f"Could not prepare a temporary scan source: {exc}.") from exc

    with temporary as directory:
        native_source = Path(directory) / "source.ply"
        try:
            shutil.copyfile(source, native_source)
        except OSError as exc:
            raise ValidationError(f"Could not copy the scan for native processing: {exc}.") from exc
        if _sha256_file(native_source) != source_hash:
            raise ValidationError("Temporary scan copy failed its source-hash check.")
        if not native_source.as_posix().isascii():
            raise ValidationError("Temporary scan path is not ASCII; native processing was not started.")
        yield native_source


def normalize_scan_parameters(value: Any) -> dict[str, float | int | str]:
    if not isinstance(value, dict):
        raise ValidationError("Scan parameters must be an object.")
    unknown = set(value) - set(DEFAULT_SCAN_PARAMETERS)
    if unknown:
        raise ValidationError(f"Unsupported scan parameter: {sorted(unknown)[0]!r}.")
    result = dict(DEFAULT_SCAN_PARAMETERS)
    result.update(value)

    reconstruction_method = result["reconstruction_method"]
    if not isinstance(reconstruction_method, str) or reconstruction_method not in {"poisson", "ball_pivoting"}:
        raise ValidationError("reconstruction_method must be 'poisson' or 'ball_pivoting'.")

    voxel_size = result["voxel_size"]
    if (isinstance(voxel_size, bool) or not isinstance(voxel_size, (int, float)) or
            not math.isfinite(float(voxel_size)) or not 0.00001 <= float(voxel_size) <= 10000):
        raise ValidationError("voxel_size must be finite and between 0.00001 and 10000.")
    result["voxel_size"] = float(voxel_size)

    for name, low, high in (("target_triangles", 100, 100000),
                            ("outlier_neighbors", 4, 128),
                            ("poisson_depth", 5, 9)):
        number = result[name]
        if type(number) is not int or not low <= number <= high:
            raise ValidationError(f"{name} must be an integer between {low} and {high}.")

    outlier_std_ratio = result["outlier_std_ratio"]
    if (isinstance(outlier_std_ratio, bool) or not isinstance(outlier_std_ratio, (int, float)) or
            not math.isfinite(float(outlier_std_ratio)) or not 0.1 <= float(outlier_std_ratio) <= 5):
        raise ValidationError("outlier_std_ratio must be finite and between 0.1 and 5.")
    result["outlier_std_ratio"] = float(outlier_std_ratio)

    density_quantile = result["poisson_density_quantile"]
    if (isinstance(density_quantile, bool) or not isinstance(density_quantile, (int, float)) or
            not math.isfinite(float(density_quantile)) or not 0.0 <= float(density_quantile) <= 0.5):
        raise ValidationError("poisson_density_quantile must be finite and between 0 and 0.5.")
    result["poisson_density_quantile"] = float(density_quantile)
    return result


def _ply_counts(path: Path) -> tuple[int, int]:
    header = bytearray()
    try:
        with path.open("rb") as stream:
            while len(header) <= _MAX_PLY_HEADER_BYTES:
                line = stream.readline(4096)
                if not line:
                    break
                header.extend(line)
                if line.strip() == b"end_header":
                    break
    except OSError as exc:
        raise ValidationError(f"PLY source could not be read: {exc}.") from exc
    if len(header) > _MAX_PLY_HEADER_BYTES or not header.rstrip().endswith(b"end_header"):
        raise ValidationError("PLY header is missing, incomplete, or exceeds 64 KiB.")
    try:
        lines = header.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise ValidationError("PLY header must use ASCII encoding.") from exc
    if not lines or lines[0].strip() != "ply" or not any(line.startswith("format ") for line in lines):
        raise ValidationError("File does not contain a valid PLY header.")
    counts: dict[str, int] = {}
    for line in lines:
        fields = line.split()
        if len(fields) == 3 and fields[0] == "element" and fields[1] in {"vertex", "face"}:
            try:
                count = int(fields[2])
            except ValueError as exc:
                raise ValidationError("PLY element count is invalid.") from exc
            if count < 0:
                raise ValidationError("PLY element count cannot be negative.")
            counts[fields[1]] = count
    vertices = counts.get("vertex", 0)
    faces = counts.get("face", 0)
    if vertices < 3:
        raise ValidationError("PLY scan must contain at least three vertices.")
    if vertices > _MAX_PLY_VERTICES or faces > _MAX_PLY_FACES:
        raise ValidationError("PLY scan exceeds the 2M vertex or 5M face resource limit.",
                              code="RESOURCE_LIMIT", diagnostics={"category": "resource_limit",
                              "failed_precondition": "the PLY vertex and face counts must fit scan processing limits",
                              "measurements": {"vertices": vertices, "faces": faces,
                                               "maximum_vertices": 2_000_000, "maximum_faces": 5_000_000},
                              "correction_hint": "Use a smaller scan or reduce its mesh density before import."})
    return vertices, faces


def read_point_cloud_ply(path: str | Path) -> tuple[list[tuple[float, float, float]], str, dict[str, Any]]:
    """Read a direct point-only PLY for persistence in the server viewport."""
    source = Path(path).resolve(strict=True)
    if source.suffix.casefold() != ".ply" or not source.is_file():
        raise ValidationError("Point-cloud source must be a regular PLY file.")
    if source.stat().st_size > _MAX_PLY_BYTES:
        raise ValidationError("PLY source exceeds the 512 MiB processing limit.", code="RESOURCE_LIMIT")
    source_hash = _sha256_file(source)
    property_types = {
        "char": "b", "int8": "b", "uchar": "B", "uint8": "B",
        "short": "h", "int16": "h", "ushort": "H", "uint16": "H",
        "int": "i", "int32": "i", "uint": "I", "uint32": "I",
        "float": "f", "float32": "f", "double": "d", "float64": "d",
    }
    try:
        with source.open("rb") as stream:
            header = bytearray()
            while len(header) <= _MAX_PLY_HEADER_BYTES:
                line = stream.readline(4096)
                if not line:
                    break
                header.extend(line)
                if line.strip() == b"end_header":
                    break
            if len(header) > _MAX_PLY_HEADER_BYTES or not header.rstrip().endswith(b"end_header"):
                raise ValidationError("PLY header is missing, incomplete, or exceeds 64 KiB.")
            try:
                lines = header.decode("ascii").splitlines()
            except UnicodeDecodeError as exc:
                raise ValidationError("PLY header must use ASCII encoding.") from exc
            if not lines or lines[0].strip() != "ply":
                raise ValidationError("File does not contain a valid PLY header.")
            ply_format = None
            elements: list[tuple[str, int, list[tuple[str, str]]]] = []
            current: tuple[str, int, list[tuple[str, str]]] | None = None
            for line in lines[1:]:
                fields = line.split()
                if not fields or fields[0] in {"comment", "obj_info", "end_header"}:
                    continue
                if fields[0] == "format" and len(fields) == 3:
                    ply_format = fields[1]
                elif fields[0] == "element" and len(fields) == 3:
                    try:
                        count = int(fields[2])
                    except ValueError as exc:
                        raise ValidationError("PLY element count is invalid.") from exc
                    if count < 0:
                        raise ValidationError("PLY element count cannot be negative.")
                    current = (fields[1], count, [])
                    elements.append(current)
                elif fields[0] == "property":
                    if current is None or len(fields) != 3:
                        raise ValidationError("Point-cloud PLY properties must be scalar fields.")
                    if fields[1] not in property_types:
                        raise ValidationError(f"PLY property type is unsupported: {fields[1]!r}.")
                    current[2].append((fields[2], fields[1]))
            if ply_format not in {"ascii", "binary_little_endian", "binary_big_endian"}:
                raise ValidationError("Point-cloud PLY format must be ASCII or binary little/big endian.")
            if len(elements) != 1 or elements[0][0] != "vertex":
                raise ValidationError("Point-cloud import requires a point-only PLY with no faces or other elements.")
            _, vertex_count, properties = elements[0]
            if not 3 <= vertex_count <= _MAX_POINT_CLOUD_VERTICES:
                raise ValidationError("Point cloud must contain 3–300000 vertices.", code="RESOURCE_LIMIT")
            names = [name for name, _ in properties]
            if len(names) != len(set(names)) or not all(name in names for name in ("x", "y", "z")):
                raise ValidationError("Point-cloud PLY must contain unique x, y and z properties.")
            xyz_indices = tuple(names.index(axis) for axis in ("x", "y", "z"))
            points: list[tuple[float, float, float]] = []
            if ply_format == "ascii":
                for index in range(vertex_count):
                    row = stream.readline()
                    if not row:
                        raise ValidationError(f"PLY ended after {index} of {vertex_count} vertices.")
                    try:
                        fields = row.decode("ascii").split()
                        if len(fields) != len(properties):
                            raise ValueError("property count mismatch")
                        point = tuple(float(fields[column]) for column in xyz_indices)
                        points.append(point)
                    except (UnicodeDecodeError, ValueError, IndexError) as exc:
                        raise ValidationError(f"PLY vertex {index} is malformed.") from exc
            else:
                endian = "<" if ply_format == "binary_little_endian" else ">"
                record = struct.Struct(endian + "".join(property_types[prop_type] for _, prop_type in properties))
                if record.size * vertex_count > source.stat().st_size - len(header):
                    raise ValidationError("Binary PLY ended before all vertex records were read.")
                for index in range(vertex_count):
                    row = stream.read(record.size)
                    if len(row) != record.size:
                        raise ValidationError(f"PLY ended after {index} of {vertex_count} vertices.")
                    values = record.unpack(row)
                    point = tuple(float(values[column]) for column in xyz_indices)
                    points.append(point)
            for index, point in enumerate(points):
                if not all(math.isfinite(value) for value in point):
                    raise ValidationError(f"PLY vertex {index} contains a non-finite coordinate.")
    except OSError as exc:
        raise ValidationError(f"PLY source could not be read: {exc}.") from exc
    if _sha256_file(source) != source_hash:
        raise ValidationError("Source PLY changed while it was being read; no model change was saved.")
    bounds = {
        "min": [min(point[axis] for point in points) for axis in range(3)],
        "max": [max(point[axis] for point in points) for axis in range(3)],
    }
    return points, source_hash, {"point_count": vertex_count, "bounds": bounds}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class ScanProcessor:
    def __init__(self, native: NativeScan | None = None) -> None:
        self.native = native

    def reconstruct(
        self,
        path: str | Path,
        parameters: dict[str, Any],
        *,
        expected_sha256: str | None = None,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        if progress is not None:
            progress({"phase": "validate_source"})
        source = Path(path).resolve(strict=True)
        if source.suffix.casefold() != ".ply" or not source.is_file():
            raise ValidationError("Scan source must be a regular PLY file.")
        size = source.stat().st_size
        if size > _MAX_PLY_BYTES:
            raise ValidationError("PLY source exceeds the 512 MiB processing limit.", code="RESOURCE_LIMIT",
                                  diagnostics={"category": "resource_limit",
                                               "failed_precondition": "the source PLY size must stay within the scan import budget",
                                               "correction_hint": "Use a source PLY no larger than 512 MiB."})
        header_vertices, header_faces = _ply_counts(source)
        if progress is not None:
            progress({"phase": "hash_source", "source_kind": "triangle_mesh" if header_faces else "point_cloud"})
        source_hash = _sha256_file(source)
        if expected_sha256 is not None and source_hash != expected_sha256:
            raise ValidationError("Source PLY changed since import; import the updated scan as a new feature.")

        normalized = normalize_scan_parameters(parameters)
        if progress is not None:
            progress({"phase": "prepare_parameters", "parameters": normalized})
        native = self.native if self.native is not None else NativeScan()
        with _native_scan_source(source, source_hash) as native_source:
            result = native.process(
                native_source,
                normalized,
                source_has_faces=header_faces > 0,
                progress=progress,
            )
        if progress is not None:
            progress({"phase": "verify_source"})
        if _sha256_file(source) != source_hash:
            raise ValidationError("Source PLY changed while it was being processed; no model change was saved.")

        if progress is not None:
            progress({"phase": "validate_result"})
        geometry, metadata = geometry_from_native_buffer(result)
        if metadata["input_vertices"] != header_vertices:
            raise ValidationError("Open3D read a different vertex count than the PLY header declares.")
        if header_faces and metadata["input_triangles"] != header_faces:
            raise ValidationError("Open3D read a different face count than the PLY header declares.")
        metadata["processor_version"] = native.version
        metadata["reconstruction_method"] = normalized["reconstruction_method"]
        return geometry, metadata, source_hash
