"""Datum and scale transforms for CADYX scan meshes."""
from __future__ import annotations

import math
import statistics
from typing import Any

from .errors import ValidationError
from .mesh_geometry import tessellate_mesh_geometry
from .scan_reverse_common import _EPS, _bounds, _cross, _dot, _pack_mesh, _positive, _sample_points, _unit, _vector3

_ALLOWED_UNITS = {"unitless", "mm", "cm", "m", "in"}

def _jacobi_eigenvectors(matrix: list[list[float]]) -> tuple[list[float], list[list[float]]]:
    a = [row[:] for row in matrix]
    vectors = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    for _ in range(40):
        p, q = max(((0, 1), (0, 2), (1, 2)), key=lambda pair: abs(a[pair[0]][pair[1]]))
        if abs(a[p][q]) <= 1e-14 * max(1.0, abs(a[0][0]), abs(a[1][1]), abs(a[2][2])):
            break
        angle = 0.5 * math.atan2(2.0 * a[p][q], a[q][q] - a[p][p])
        cosine, sine = math.cos(angle), math.sin(angle)
        app, aqq, apq = a[p][p], a[q][q], a[p][q]
        a[p][p] = cosine * cosine * app - 2.0 * sine * cosine * apq + sine * sine * aqq
        a[q][q] = sine * sine * app + 2.0 * sine * cosine * apq + cosine * cosine * aqq
        a[p][q] = a[q][p] = 0.0
        for r in range(3):
            if r in {p, q}:
                continue
            arp, arq = a[r][p], a[r][q]
            a[r][p] = a[p][r] = cosine * arp - sine * arq
            a[r][q] = a[q][r] = sine * arp + cosine * arq
        for r in range(3):
            vrp, vrq = vectors[r][p], vectors[r][q]
            vectors[r][p] = cosine * vrp - sine * vrq
            vectors[r][q] = sine * vrp + cosine * vrq
    eigenvalues = [a[index][index] for index in range(3)]
    eigenvectors = [[vectors[row][column] for row in range(3)] for column in range(3)]
    order = sorted(range(3), key=lambda index: eigenvalues[index], reverse=True)
    return [eigenvalues[index] for index in order], [_unit(eigenvectors[index], "PCA axis") for index in order]

def _orient_axis(axis: list[float], points: list[list[float]], center: list[float]) -> list[float]:
    projections = [_dot([point[i] - center[i] for i in range(3)], axis) for point in points]
    third = sum(value * value * value for value in projections)
    spread = max((abs(value) for value in projections), default=0.0)
    if abs(third) > max(1e-15, spread ** 3 * 1e-9):
        return [-value for value in axis] if third < 0 else axis
    dominant = max(range(3), key=lambda index: abs(axis[index]))
    return [-value for value in axis] if axis[dominant] < 0 else axis

def _pca_basis(positions: list[float], long_axis: str) -> tuple[list[list[float]], list[float], list[float]]:
    points = _sample_points(positions, 20_000)
    center = [statistics.fmean(point[axis] for point in points) for axis in range(3)]
    covariance = [[0.0] * 3 for _ in range(3)]
    for point in points:
        delta = [point[axis] - center[axis] for axis in range(3)]
        for row in range(3):
            for column in range(row, 3):
                covariance[row][column] += delta[row] * delta[column]
    scale = max(1, len(points) - 1)
    for row in range(3):
        for column in range(row, 3):
            covariance[row][column] /= scale
            covariance[column][row] = covariance[row][column]
    values, principal = _jacobi_eigenvectors(covariance)
    principal = [_orient_axis(axis, points, center) for axis in principal]
    if values[0] <= 1e-18 or values[1] <= 1e-18:
        raise ValidationError("Scan PCA is degenerate; provide an explicit alignment basis.")
    first, second = principal[0], principal[1]
    if long_axis == "Y":
        ey, ex = first, second
        ez = _unit(_cross(ex, ey), "PCA Z axis")
    elif long_axis == "X":
        ex, ey = first, second
        ez = _unit(_cross(ex, ey), "PCA Z axis")
    elif long_axis == "Z":
        ez, ex = first, second
        ey = _unit(_cross(ez, ex), "PCA Y axis")
    else:
        raise ValidationError("long_axis must be X, Y, or Z.")
    basis = [_unit(ex, "X basis"), _unit(ey, "Y basis"), _unit(ez, "Z basis")]
    return basis, center, values

def _explicit_basis(value: Any) -> list[list[float]]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValidationError("basis must contain three XYZ axis vectors.")
    basis = [_unit(_vector3(row, "basis axis"), "basis axis") for row in value]
    if any(abs(_dot(basis[a], basis[b])) > 1e-6 for a, b in ((0, 1), (0, 2), (1, 2))):
        raise ValidationError("Explicit basis axes must be orthogonal.")
    determinant = _dot(_cross(basis[0], basis[1]), basis[2])
    if determinant < 0.999999:
        raise ValidationError("Explicit basis must be right-handed.")
    return basis

def _alignment_from_command(positions: list[float], command: dict[str, Any]) -> tuple[dict[str, Any], list[float]]:
    mode = command.get("mode", "pca")
    if mode not in {"pca", "explicit"}:
        raise ValidationError("align_scan mode must be 'pca' or 'explicit'.")
    if mode == "pca":
        long_axis = command.get("long_axis", "Y")
        if not isinstance(long_axis, str):
            raise ValidationError("long_axis must be X, Y, or Z.")
        basis, centroid, eigenvalues = _pca_basis(positions, long_axis.upper())
        center = command.get("center", True)
        if type(center) is not bool:
            raise ValidationError("center must be a boolean.")
        origin = _vector3(command["origin"], "origin") if "origin" in command else (centroid if center else [0.0, 0.0, 0.0])
    else:
        basis = _explicit_basis(command.get("basis"))
        origin = _vector3(command.get("origin", [0.0, 0.0, 0.0]), "origin")
        eigenvalues = []
        long_axis = None

    signs = command.get("axis_signs", [1, 1, 1])
    if (not isinstance(signs, list) or len(signs) != 3
            or any(type(value) is not int or value not in {-1, 1} for value in signs)
            or signs[0] * signs[1] * signs[2] != 1):
        raise ValidationError("axis_signs must contain three +/-1 values with a positive product.")
    basis = [[basis[row][column] * signs[row] for column in range(3)] for row in range(3)]
    scale = _positive(command.get("scale", 1.0), "scale")
    units = command.get("units", "unitless")
    if not isinstance(units, str) or units not in _ALLOWED_UNITS:
        raise ValidationError("units must be one of unitless, mm, cm, m, or in.")
    translation = _vector3(command.get("translation", [0.0, 0.0, 0.0]), "translation")
    record = {
        "version": 1,
        "mode": mode,
        "basis": basis,
        "origin": origin,
        "scale": scale,
        "units": units,
        "translation": translation,
        "axis_signs": signs,
    }
    if mode == "pca":
        record["long_axis"] = long_axis.upper()
        record["pca_eigenvalues"] = eigenvalues
    return record, origin

def _apply_alignment(geometry: dict[str, Any], record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, list[float]]]:
    mesh = tessellate_mesh_geometry(geometry)
    basis = record["basis"]
    origin, scale, translation = record["origin"], record["scale"], record["translation"]
    transformed: list[float] = []
    positions = mesh["positions"]
    for offset in range(0, len(positions), 3):
        delta = [positions[offset + axis] - origin[axis] for axis in range(3)]
        transformed.extend(scale * _dot(delta, basis[axis]) + translation[axis] for axis in range(3))
    return _pack_mesh(transformed, mesh["triangles"]), _bounds(transformed)
