"""Deterministic full-mesh cleanup and boundary-preserving Taubin smoothing."""
from __future__ import annotations

import math
import struct
import sys
from array import array
from collections import Counter
from typing import Any

from .errors import ValidationError
from .mesh_geometry import geometry_from_native_buffer, tessellate_mesh_geometry


def normalize_mesh_parameters(method: Any, parameters: Any) -> dict[str, Any]:
    fields = {"clean": set(), "orient": set(), "repair_orientation": set(), "remove_small_components": {"min_triangles"},
              "smooth": {"iterations", "strength"}}
    if not isinstance(method, str) or method not in fields or not isinstance(parameters, dict) or set(parameters) != fields[method]:
        raise ValidationError("Mesh method and its exact parameters are required.")
    p = dict(parameters)
    if method == "remove_small_components" and (type(p["min_triangles"]) is not int or not 1 <= p["min_triangles"] <= 100000):
        raise ValidationError("min_triangles must be an integer from 1 to 100000.")
    if method == "smooth":
        if type(p["iterations"]) is not int or not 1 <= p["iterations"] <= 10:
            raise ValidationError("Smoothing iterations must be an integer from 1 to 10.")
        if type(p["strength"]) not in (int, float) or not math.isfinite(p["strength"]) or not 0 < p["strength"] <= .5:
            raise ValidationError("Smoothing strength must be positive and at most 0.5.")
    return p


def _normal(vertices, face):
    a, b, c = (vertices[i] for i in face)
    u = [b[i] - a[i] for i in range(3)]
    v = [c[i] - a[i] for i in range(3)]
    return (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])


def _topology(vertices, faces):
    parents = list(range(len(faces)))
    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    edges = {}
    for fi, (a, b, c) in enumerate(faces):
        for x, y in ((a, b), (b, c), (c, a)):
            key = (min(x, y), max(x, y))
            entries = edges.setdefault(key, [])
            if entries:
                parents[root(fi)] = root(entries[0][0])
            entries.append((fi, x, y))
    groups = {}
    for fi in range(len(faces)):
        groups.setdefault(root(fi), []).append(fi)
    boundary = {v for edge, entries in edges.items() if len(entries) == 1 for v in edge}
    stats = {"vertices": len(vertices), "triangles": len(faces), "triangle_count": len(faces), "components": len(groups),
             "largest_component_triangles": max(map(len, groups.values()), default=0),
             "boundary_edges": sum(len(e) == 1 for e in edges.values()),
             "nonmanifold_edges": sum(len(e) > 2 for e in edges.values()),
             "inconsistent_edges": sum(len(e) == 2 and e[0][1:] == e[1][1:] for e in edges.values()),
             "degenerate_triangles": sum(not any(_normal(vertices, f)) for f in faces),
             "duplicate_triangles": sum(n - 1 for n in Counter(tuple(sorted(f)) for f in faces).values()),
             "bounds": {"min": [min(v[a] for v in vertices) for a in range(3)],
                        "max": [max(v[a] for v in vertices) for a in range(3)]}}
    return stats, list(groups.values()), boundary


def process_mesh_geometry(geometry: dict[str, Any], method: str, parameters: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    p = normalize_mesh_parameters(method, parameters)
    if geometry.get("kind") != "triangle_mesh":
        raise ValidationError("Mesh processing requires a triangle mesh, not points or a B-rep.")
    mesh = tessellate_mesh_geometry(geometry)
    if len(mesh["triangles"]) > 600000:
        raise ValidationError("Mesh processing supports at most 200000 triangles.", code="RESOURCE_LIMIT")
    vertices = [tuple(mesh["positions"][i:i+3]) for i in range(0, len(mesh["positions"]), 3)]
    faces = [tuple(mesh["triangles"][i:i+3]) for i in range(0, len(mesh["triangles"]), 3)]
    before, groups, boundary = _topology(vertices, faces)
    displacement = 0.
    if method == "clean":
        unique, remap, lookup = [], [], {}
        for point in vertices:
            if point not in lookup:
                lookup[point] = len(unique)
                unique.append(point)
            remap.append(lookup[point])
        vertices = unique
        seen, cleaned = set(), []
        for face in faces:
            mapped = tuple(remap[i] for i in face)
            key = tuple(sorted(mapped))
            if len(set(mapped)) == 3 and key not in seen and any(_normal(vertices, mapped)):
                seen.add(key)
                cleaned.append(mapped)
        faces = cleaned
    elif method == "remove_small_components":
        keep = {i for group in groups if len(group) >= p["min_triangles"] for i in group}
        faces = [face for i, face in enumerate(faces) if i in keep]
    elif method in {"orient", "repair_orientation"}:
        if before["nonmanifold_edges"]:
            raise ValidationError("Orienting requires edge-manifold triangles.")
        edges, adjacent = {}, [[] for _ in faces]
        for fi, face in enumerate(faces):
            for a, b in zip(face, (face[1], face[2], face[0])):
                key = (min(a, b), max(a, b))
                if key in edges:
                    other, start, end = edges[key]
                    same = (a, b) == (start, end)
                    adjacent[fi].append((other, same))
                    adjacent[other].append((fi, same))
                else:
                    edges[key] = (fi, a, b)
        flips, cut_faces = {}, set()
        for seed in range(len(faces)):
            if seed in flips:
                continue
            flips[seed], stack = False, [seed]
            while stack:
                current = stack.pop()
                for other, same in adjacent[current]:
                    desired = flips[current] != same
                    if other in flips:
                        if flips[other] != desired:
                            if method == "orient":
                                raise ValidationError("Mesh is non-orientable; no consistent winding exists.")
                            cut_faces.add(max(current, other))
                    else:
                        flips[other] = desired
                        stack.append(other)
        faces = [(a, c, b) if flips[i] else (a, b, c) for i, (a, b, c) in enumerate(faces) if i not in cut_faces]
    else:
        if before["nonmanifold_edges"] or before["degenerate_triangles"] or before["inconsistent_edges"]:
            raise ValidationError("Smoothing requires non-degenerate, consistently oriented edge-manifold triangles.")
        neighbors = [set() for _ in vertices]
        for a, b, c in faces:
            neighbors[a].update((b, c))
            neighbors[b].update((a, c))
            neighbors[c].update((a, b))
        original = vertices[:]
        normals = [_normal(vertices, f) for f in faces]
        for _ in range(p["iterations"]):
            for factor in (p["strength"], -p["strength"] * 1.05):
                candidate = [point if i in boundary or not neighbors[i] else
                             tuple(point[a] + factor * (math.fsum(vertices[j][a] for j in sorted(neighbors[i])) / len(neighbors[i]) - point[a])
                                   for a in range(3)) for i, point in enumerate(vertices)]
                if any(sum(a*b for a, b in zip(normal, _normal(candidate, f))) <= 0 for normal, f in zip(normals, faces)):
                    raise ValidationError("Smoothing would invert or collapse a triangle; use a smaller strength.")
                vertices = candidate
        displacement = max(math.dist(a, b) for a, b in zip(original, vertices))
    if not faces:
        raise ValidationError("Mesh processing would remove every triangle; no result was saved.")
    used = sorted({i for f in faces for i in f})
    remap = {old: new for new, old in enumerate(used)}
    vertices = [vertices[i] for i in used]
    faces = [tuple(remap[i] for i in f) for f in faces]
    positions = array("f", (v for point in vertices for v in point))
    indices = array("I", (i for f in faces for i in f))
    if sys.byteorder != "little":
        positions.byteswap()
        indices.byteswap()
    encoded = struct.pack("<4s6If", b"CYSM", 1, len(vertices), len(faces), 1, before["vertices"], before["triangles"], 1.)
    result, _ = geometry_from_native_buffer(encoded + positions.tobytes() + indices.tobytes())
    actual = tessellate_mesh_geometry(result)
    stored = [tuple(actual["positions"][i:i+3]) for i in range(0, len(actual["positions"]), 3)]
    after, _, _ = _topology(stored, faces)
    if method == "smooth":
        if any(sum(a*b for a, b in zip(normal, _normal(stored, f))) <= 0 for normal, f in zip(normals, faces)):
            raise ValidationError("Persisted smoothing result would invert a triangle.")
        displacement = max(math.dist(original[old], point) for old, point in zip(used, stored))
    if after["degenerate_triangles"] > before["degenerate_triangles"] or after["nonmanifold_edges"] > before["nonmanifold_edges"]:
        raise ValidationError("Processing introduced degenerate triangles or non-manifold edges.")
    return result, {"before": before, "after": after, "max_vertex_displacement": displacement,
                    "boundary_vertices_pinned": len(boundary) if method == "smooth" else 0,
                    "removed_triangles": before["triangles"] - after["triangles"],
                    "self_intersections": "untested", "scale_status": "unverified"}
