"""Analytic kinematics and indexing checks for simple planetary gearsets."""
from __future__ import annotations

import math
from typing import Any

from .errors import ValidationError


def solve_planetary_gearset(
    *,
    module: Any,
    sun_teeth: Any,
    planet_teeth: Any,
    ring_teeth: Any,
    planet_angles_degrees: Any,
    sun_rotation_degrees: Any = 0.0,
    planet_rotation_degrees: Any = None,
    ring_rotation_degrees: Any = 0.0,
    sun_speed_radians_per_second: Any,
    ring_speed_radians_per_second: Any,
) -> dict[str, Any]:
    """Validate pitch geometry/indexing and return ideal angular velocities.

    Tooth indexing uses the angular tooth-pitch phase shared by the sun and
    internal ring: moving a planet around the carrier by ``dtheta`` advances
    that phase by ``(sun_teeth + ring_teeth) * dtheta / 2pi`` teeth.
    """
    module_value = _positive(module, "module")
    sun = _positive_int(sun_teeth, "sun_teeth")
    planet = _positive_int(planet_teeth, "planet_teeth")
    ring = _positive_int(ring_teeth, "ring_teeth")
    angles = _angles(planet_angles_degrees, len(planet_angles_degrees)
                     if isinstance(planet_angles_degrees, list) else -1)
    sun_rotation = _finite(sun_rotation_degrees, "sun_rotation_degrees")
    ring_rotation = _finite(ring_rotation_degrees, "ring_rotation_degrees")
    rotations = ([0.0] * len(angles) if planet_rotation_degrees is None
                 else _angles(planet_rotation_degrees, len(angles)))
    sun_speed = _finite(sun_speed_radians_per_second, "sun_speed_radians_per_second")
    ring_speed = _finite(ring_speed_radians_per_second, "ring_speed_radians_per_second")

    if len(angles) < 2:
        raise ValidationError("A planetary gearset needs at least two planet positions.")
    if len({round(value % 360.0, 10) for value in angles}) != len(angles):
        raise ValidationError("Planet positions must be distinct.")

    try:
        sun_planet_center = module_value * (sun + planet) / 2.0
        ring_planet_center = module_value * (ring - planet) / 2.0
    except OverflowError as exc:
        raise ValidationError("Gear geometry exceeds the finite numeric range.") from exc
    if not all(math.isfinite(value) for value in (sun_planet_center, ring_planet_center)):
        raise ValidationError("Gear geometry exceeds the finite numeric range.")
    pitch_compatible = ring > planet and math.isclose(
        sun_planet_center, ring_planet_center, rel_tol=1e-12, abs_tol=1e-10,
    )

    ordered = sorted(value % 360.0 for value in angles)
    gaps = [ordered[(index + 1) % len(ordered)] - ordered[index]
            for index in range(len(ordered))]
    gaps[-1] += 360.0
    try:
        phase_teeth = [((sun + ring) * gap / 360.0) for gap in gaps]
    except OverflowError as exc:
        raise ValidationError("Gear indexing exceeds the finite numeric range.") from exc
    if not all(math.isfinite(value) for value in phase_teeth):
        raise ValidationError("Gear indexing exceeds the finite numeric range.")
    phase_errors = [abs(value - round(value)) for value in phase_teeth]
    indexing_compatible = all(error <= 1e-9 for error in phase_errors)
    try:
        external_phase = [
            sun * (angle - sun_rotation) / 360.0
            + planet * (angle + 180.0 - rotation) / 360.0
            for angle, rotation in zip(angles, rotations, strict=True)
        ]
        internal_phase = [
            ring * (angle - ring_rotation) / 360.0
            - planet * (angle + 180.0 - rotation) / 360.0
            for angle, rotation in zip(angles, rotations, strict=True)
        ]
    except OverflowError as exc:
        raise ValidationError("Gear tooth phases exceed the finite numeric range.") from exc
    if not all(math.isfinite(value) for value in (*external_phase, *internal_phase)):
        raise ValidationError("Gear tooth phases exceed the finite numeric range.")

    def half_tooth_phase_error(value: float) -> float:
        distance = abs((value % 1.0) - 0.5)
        return min(distance, 1.0 - distance)

    external_phase_errors = [half_tooth_phase_error(value) for value in external_phase]
    internal_phase_errors = [half_tooth_phase_error(value) for value in internal_phase]
    rest_mesh_phase_compatible = all(
        error <= 1e-9 for error in (*external_phase_errors, *internal_phase_errors)
    )

    try:
        carrier_speed = (sun * sun_speed + ring * ring_speed) / (sun + ring)
        planet_speed = carrier_speed - (sun / planet) * (sun_speed - carrier_speed)
    except OverflowError as exc:
        raise ValidationError("Gear angular velocities exceed the finite numeric range.") from exc
    if not math.isfinite(carrier_speed) or not math.isfinite(planet_speed):
        raise ValidationError("Gear angular velocities exceed the finite numeric range.")
    return {
        "status": "pass" if pitch_compatible and indexing_compatible and rest_mesh_phase_compatible else "fail",
        "pitch_geometry_compatible": pitch_compatible,
        "tooth_indexing_compatible": indexing_compatible,
        "rest_mesh_phase_compatible": rest_mesh_phase_compatible,
        "sun_planet_phase_teeth": external_phase,
        "sun_planet_phase_error_teeth": external_phase_errors,
        "ring_planet_phase_teeth": internal_phase,
        "ring_planet_phase_error_teeth": internal_phase_errors,
        "planet_layout_balanced": all(
            math.isclose(gap, 360.0 / len(gaps), rel_tol=0.0, abs_tol=1e-9)
            for gap in gaps
        ),
        "module": module_value,
        "sun_teeth": sun,
        "planet_teeth": planet,
        "ring_teeth": ring,
        "planet_count": len(angles),
        "planet_angles_degrees": angles,
        "center_distance_sun_planet": sun_planet_center,
        "center_distance_ring_planet": ring_planet_center,
        "carrier_speed_radians_per_second": carrier_speed,
        "planet_spin_speed_radians_per_second": planet_speed,
        "sun_speed_radians_per_second": sun_speed,
        "ring_speed_radians_per_second": ring_speed,
        "indexing_phase_teeth_per_gap": phase_teeth,
        "indexing_phase_error_teeth_per_gap": phase_errors,
        "equation": "(sun_teeth + ring_teeth) * planet_spacing / 360 = integer",
    }


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a finite number.")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValidationError(f"{name} must be a finite number.") from exc
    if not math.isfinite(result):
        raise ValidationError(f"{name} must be a finite number.")
    return result


def _positive(value: Any, name: str) -> float:
    result = _finite(value, name)
    if result <= 0:
        raise ValidationError(f"{name} must be positive.")
    return result


def _positive_int(value: Any, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValidationError(f"{name} must be a positive integer.")
    return value


def _angles(values: Any, count: int) -> list[float]:
    if not isinstance(values, list) or count < 0 or len(values) != count:
        raise ValidationError("planet_angles_degrees must be a list of finite angles.")
    return [_finite(value, "planet_angles_degrees") for value in values]
