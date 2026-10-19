"""Revision-bound physical properties for material-assigned assembly instances.

The OCCT kernel measures unit-density properties of each source solid once.
Placement transforms only these tensors, never the saved B-rep or document.
"""
from __future__ import annotations

import math
from typing import Any

from .assemblies import flatten_assembly, transform_point, validate_material
from .errors import ValidationError


def _checked_properties(value: Any, part_id: str) -> tuple[float, list[float], list[list[float]]]:
    if not isinstance(value, dict):
        raise ValidationError(f"Missing solid mass properties for {part_id}.")
    volume = value.get("volume")
    center = value.get("center_of_mass")
    tensor = value.get("inertia_unit_density")
    if (type(volume) not in (float, int) or not math.isfinite(volume) or volume <= 0
            or not isinstance(center, list) or len(center) != 3
            or not isinstance(tensor, list) or len(tensor) != 3
            or any(not isinstance(row, list) or len(row) != 3 for row in tensor)):
        raise ValidationError(f"Invalid OCCT volume, center or inertia for {part_id}.")
    entries = [*center, *(component for row in tensor for component in row)]
    if any(type(v) not in (float, int) or not math.isfinite(v) for v in entries):
        raise ValidationError(f"Non-finite OCCT physical properties for {part_id}.")
    for i in range(3):
        if tensor[i][i] < -1e-9 * max(1, abs(tensor[i][i])):
            raise ValidationError(f"Negative OCCT principal-axis inertia for {part_id}.")
        for j in range(i + 1, 3):
            if not math.isclose(tensor[i][j], tensor[j][i], rel_tol=1e-8, abs_tol=1e-8):
                raise ValidationError(f"Asymmetric OCCT inertia tensor for {part_id}.")
    return float(volume), [float(v) for v in center], [
        [float(component) for component in row] for row in tensor
    ]


def _rotation_matrix(quaternion: list[float]) -> list[list[float]]:
    norm = math.sqrt(sum(float(component) ** 2 for component in quaternion))
    if not math.isfinite(norm) or norm <= 0:
        raise ValidationError("Assembly instance has an invalid rotation.")
    unit = [float(component) / norm for component in quaternion]
    columns = [
        transform_point(axis, [0.0, 0.0, 0.0], unit)
        for axis in ([1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0])
    ]
    return [[columns[j][i] for j in range(3)] for i in range(3)]


def _rotate_inertia(tensor: list[list[float]], rotation: list[list[float]]) -> list[list[float]]:
    # R * I_com * R^T, inertia remains about the part's rotated COM.
    return [[math.fsum(rotation[i][a] * tensor[a][b] * rotation[j][b]
                       for a in range(3) for b in range(3))
             for j in range(3)] for i in range(3)]


def _ensure_finite(value: Any) -> None:
    if isinstance(value, (float, int)) and not math.isfinite(value):
        raise ValidationError("Assembly mass or inertia exceeds finite numeric limits.")
    if isinstance(value, list):
        for element in value:
            _ensure_finite(element)


def evaluate_assembly_mass_properties(document: Any, kernel: Any) -> dict[str, Any]:
    """Compute material-dependent kilograms and kg*mm^2 for saved millimeter geometry."""
    graph = document.assembly
    if graph["units"] != "mm":
        raise ValidationError("Mass properties require explicitly declared mm assembly geometry.")
    leaves = flatten_assembly(graph, limit=256)
    if not leaves:
        raise ValidationError("Mass properties require at least one placed assembly part.")
    by_part = {part["id"]: part for part in graph["parts"]}
    by_body = {obj["id"]: obj for obj in document.objects if obj.get("type") == "body"}
    distinct = sorted({leaf["part_id"] for leaf in leaves})
    if len(distinct) > 128:
        raise ValidationError("Mass properties support at most 128 distinct placed source parts.")
    part_results: dict[str, dict[str, Any]] = {}
    for part_id in distinct:
        part = by_part[part_id]
        material = part["material"]
        validate_material(material)
        if material is None:
            raise ValidationError(
                f"Part {part_id} has no density or provenance; set_assembly_part_material first."
            )
        geometry = by_body[part["body_id"]]["geometry"]
        if geometry.get("kernel") == "cadyx-mesh-v1":
            raise ValidationError(f"Part {part_id} is a mesh, not an exact closed B-rep solid.")
        properties = kernel.mass_properties(geometry)
        volume, center, inertia = _checked_properties(properties, part_id)
        density_kg_mm3 = float(material["density_kg_m3"]) * 1e-9
        mass = volume * density_kg_mm3
        local_inertia = [[entry * density_kg_mm3 for entry in row] for row in inertia]
        _ensure_finite([mass, local_inertia])
        if mass <= 0:
            raise ValidationError(f"Part {part_id} has a mass below representable precision.")
        part_results[part_id] = {
            "part_id": part_id, "body_id": part["body_id"], "name": part["name"],
            "material": dict(material), "volume_mm3": volume,
            "mass_kg": mass, "center_of_mass_mm": center,
            "inertia_kg_mm2": local_inertia,
        }

    instances = []
    for leaf in leaves:
        source = part_results[leaf["part_id"]]
        rot = _rotation_matrix(leaf["rotation"])
        # A normalized quaternion preserves orthogonality for the inertia rotation.
        qnorm = math.sqrt(sum(float(v) ** 2 for v in leaf["rotation"]))
        unit_q = [float(v) / qnorm for v in leaf["rotation"]]
        center = transform_point(source["center_of_mass_mm"], leaf["translation"], unit_q)
        tensor = _rotate_inertia(source["inertia_kg_mm2"], rot)
        _ensure_finite([center, tensor])
        instances.append({
            "instance_id": leaf["id"], "part_id": leaf["part_id"],
            "body_id": leaf["body_id"], "name": leaf["name"],
            "translation_mm": list(leaf["translation"]), "rotation_wxyz": list(leaf["rotation"]),
            "mass_kg": source["mass_kg"], "center_of_mass_mm": center,
            "inertia_kg_mm2": tensor,
        })
    try:
        total_mass = math.fsum(entry["mass_kg"] for entry in instances)
        if not math.isfinite(total_mass) or total_mass <= 0:
            raise ValidationError("Assembly aggregate mass is invalid.")
        center = [
            math.fsum(item["mass_kg"] * item["center_of_mass_mm"][axis]
                      for item in instances) / total_mass for axis in range(3)
        ]
        tensor = [[math.fsum(
            entry["inertia_kg_mm2"][i][j]
            + entry["mass_kg"] * (
                (sum((entry["center_of_mass_mm"][k] - center[k]) ** 2
                     for k in range(3)) if i == j else 0.0)
                - (entry["center_of_mass_mm"][i] - center[i])
                * (entry["center_of_mass_mm"][j] - center[j])
            )
            for entry in instances
        ) for j in range(3)] for i in range(3)]
    except OverflowError as exc:
        raise ValidationError("Assembly mass accumulation overflowed.") from exc
    _ensure_finite([total_mass, center, tensor])
    return {
        "status": "pass", "revision": document.revision,
        "method": "OCCT_BRepGProp_VolumeProperties",
        "units": {"length": "mm", "volume": "mm^3", "density": "kg/m^3",
                  "mass": "kg", "inertia": "kg*mm^2"},
        "scope": "placed_instances_additive_no_overlap_subtraction",
        "parts": [part_results[key] for key in distinct],
        "instances": instances,
        "assembly": {"mass_kg": total_mass, "center_of_mass_mm": center,
                     "inertia_kg_mm2": tensor, "instance_count": len(instances)},
    }
