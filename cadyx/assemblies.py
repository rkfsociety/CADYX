"""Same-document, versioned assembly graph; no copies of persisted geometry."""
from __future__ import annotations

import math
from typing import Any

from .errors import ValidationError

ASSEMBLY_VERSION = 6
IDENTITY_ROTATION = [1.0, 0.0, 0.0, 0.0]
IDENTITY_TRANSLATION = [0.0, 0.0, 0.0]


def empty_assembly() -> dict[str, Any]:
    return {"version": ASSEMBLY_VERSION, "units": "mm",
            "parts": [], "subassemblies": [], "instances": [], "mates": []}


def _name(record: dict[str, Any], label: str) -> None:
    value = record.get("name")
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 120:
        raise ValidationError(f"{label} name must contain 1–120 nonblank characters.")


def validate_material(material: Any) -> None:
    """A part may be unassigned, but measured mass requires stated provenance."""
    if material is None:
        return
    if not isinstance(material, dict) or set(material) != {
        "name", "density_kg_m3", "source"
    }:
        raise ValidationError("Part material needs name, density_kg_m3 and source.")
    for key in ("name", "source"):
        value = material[key]
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 256:
            raise ValidationError(f"Material {key} requires 1–256 nonblank characters.")
    density = material["density_kg_m3"]
    if (type(density) not in (int, float) or not math.isfinite(density)
            or density <= 0 or density > 1e9):
        raise ValidationError("Material density_kg_m3 must be finite and positive (at most 1e9).")


def _vector(value: Any, size: int, label: str) -> None:
    if (not isinstance(value, list) or len(value) != size
            or any(type(number) not in (int, float) or not math.isfinite(number)
                   for number in value)):
        raise ValidationError(f"{label} must have {size} finite numeric components.")


def validate_transform(translation: Any, rotation: Any) -> None:
    _vector(translation, 3, "Assembly translation")
    _vector(rotation, 4, "Assembly quaternion")
    norm = math.sqrt(sum(float(value) ** 2 for value in rotation))
    if not math.isfinite(norm) or abs(norm - 1.0) > 1e-6:
        raise ValidationError("Assembly quaternion must be unit length (w, x, y, z).")


def validate_assembly(graph: Any, objects: list[dict[str, Any]]) -> None:
    if not isinstance(graph, dict) or set(graph) != {
        "version", "units", "parts", "subassemblies", "instances", "mates"
    }:
        raise ValidationError("Assembly graph must have version, units, parts, subassemblies and instances.")
    if type(graph["version"]) is not int or graph["version"] != ASSEMBLY_VERSION:
        raise ValidationError("Unsupported assembly graph version.")
    if graph["units"] != "mm":
        raise ValidationError("Assembly placement units must be mm.")
    for key in ("parts", "subassemblies", "instances", "mates"):
        if not isinstance(graph[key], list):
            raise ValidationError(f"Assembly {key} must be a list.")
    bodies = {item["id"] for item in objects if item.get("type") == "body"}
    components: set[str] = set()
    bound_bodies: set[str] = set()
    for part in graph["parts"]:
        if not isinstance(part, dict) or set(part) != {"id", "name", "body_id", "material"}:
            raise ValidationError("Assembly part record has invalid fields.")
        _name(part, "Part")
        validate_material(part["material"])
        ident = part["id"]
        body_id = part["body_id"]
        if not isinstance(ident, str) or not ident.startswith("part:") or not ident[5:] or ident in components:
            raise ValidationError("Invalid or duplicate part ID.")
        if not isinstance(body_id, str) or body_id not in bodies or body_id in bound_bodies:
            raise ValidationError("Assembly part must reference a distinct existing body.")
        components.add(ident)
        bound_bodies.add(body_id)
    subassemblies: set[str] = set()
    for sub in graph["subassemblies"]:
        if not isinstance(sub, dict) or set(sub) != {"id", "name"}:
            raise ValidationError("Subassembly record has invalid fields.")
        _name(sub, "Subassembly")
        ident = sub["id"]
        if not isinstance(ident, str) or not ident.startswith("subassembly:") or not ident[12:] or ident in components:
            raise ValidationError("Invalid or duplicate subassembly ID.")
        components.add(ident)
        subassemblies.add(ident)
    instance_ids: set[str] = set()
    edges: dict[str, set[str]] = {sub: set() for sub in subassemblies}
    for instance in graph["instances"]:
        if not isinstance(instance, dict) or set(instance) != {
            "id", "name", "parent_id", "target_id", "translation", "rotation",
            "grounded", "visible",
        }:
            raise ValidationError("Assembly instance record has invalid fields.")
        if type(instance["grounded"]) is not bool or type(instance["visible"]) is not bool:
            raise ValidationError("Assembly instance grounded and visible fields must be boolean.")
        _name(instance, "Instance")
        ident = instance["id"]
        if not isinstance(ident, str) or not ident.startswith("instance:") or not ident[9:] or ident in instance_ids:
            raise ValidationError("Invalid or duplicate instance ID.")
        instance_ids.add(ident)
        parent = instance["parent_id"]
        target = instance["target_id"]
        if parent is not None and (not isinstance(parent, str) or parent not in subassemblies):
            raise ValidationError("Instance parent must be root or an existing subassembly.")
        if not isinstance(target, str) or target not in components:
            raise ValidationError("Instance target part or subassembly does not exist.")
        validate_transform(instance["translation"], instance["rotation"])
        if parent is not None and target in subassemblies:
            edges[parent].add(target)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise ValidationError("Cyclic subassembly references are not permitted.")
        if node in visited:
            return
        visiting.add(node)
        for child in sorted(edges[node]):
            visit(child)
        visiting.remove(node)
        visited.add(node)

    for node in sorted(subassemblies):
        visit(node)

    from .assembly_mates import inspect_assembly_mates
    inspect_assembly_mates(graph)


def _rotate(rotation: list[float], point: list[float]) -> list[float]:
    w, x, y, z = rotation
    dot = x * point[0] + y * point[1] + z * point[2]
    cross = [y * point[2] - z * point[1],
             z * point[0] - x * point[2],
             x * point[1] - y * point[0]]
    norm = x * x + y * y + z * z
    return [(w * w - norm) * point[i] + 2 * dot * u + 2 * w * cross[i]
            for i, u in enumerate((x, y, z))]


def transform_point(point: list[float], translation: list[float],
                    rotation: list[float]) -> list[float]:
    oriented = _rotate(rotation, point)
    return [oriented[i] + translation[i] for i in range(3)]


def _multiply(a: list[float], b: list[float]) -> list[float]:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return [aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw]


def flatten_assembly(graph: dict[str, Any], *, limit: int = 4096) -> list[dict[str, Any]]:
    """Flatten nested rigid placements for the read-only scene."""
    parts = {part["id"]: part for part in graph["parts"]}
    children: dict[str | None, list[dict[str, Any]]] = {}
    for instance in graph["instances"]:
        children.setdefault(instance["parent_id"], []).append(instance)
    leaves: list[dict[str, Any]] = []

    def walk(parent: str | None, path: tuple[str, ...],
             translation: list[float], rotation: list[float], depth: int,
             visible: bool = True) -> None:
        if depth > 32:
            raise ValidationError("Assembly nesting exceeds 32 viewport levels.")
        for instance in children.get(parent, []):
            instance_visible = visible and instance["visible"]
            if not instance_visible:
                continue
            next_path = (*path, instance["id"])
            next_translation = transform_point(instance["translation"], translation, rotation)
            next_rotation = _multiply(rotation, instance["rotation"])
            target = instance["target_id"]
            if target in parts:
                if len(leaves) >= limit:
                    raise ValidationError("Assembly exceeds 4096 displayed part instances.")
                leaves.append({"id": "/".join(next_path), "instance_id": instance["id"],
                               "instance_path": list(next_path), "part_id": target,
                               "body_id": parts[target]["body_id"], "name": instance["name"],
                               "translation": next_translation, "rotation": next_rotation})
            else:
                walk(target, next_path, next_translation, next_rotation, depth + 1,
                     instance_visible)

    walk(None, (), IDENTITY_TRANSLATION, IDENTITY_ROTATION, 0)
    return leaves
