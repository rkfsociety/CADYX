"""Analytic kinematics and indexing checks for simple planetary gearsets."""
from __future__ import annotations

import math
from fractions import Fraction
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
    # Compare circular gaps rather than rounded absolute angles: modulo on
    # negative subnormal angles can round to 360.0 and hide an overlap at 0.
    ordered_exact = sorted(Fraction(value) % 360 for value in angles)
    gaps_exact = [ordered_exact[(index + 1) % len(ordered_exact)] - ordered_exact[index]
                  for index in range(len(ordered_exact))]
    gaps_exact[-1] += 360
    if any(gap < Fraction(1, 10**10) for gap in gaps_exact):
        raise ValidationError("Planet positions must be distinct.")
    gaps = [float(gap) for gap in gaps_exact]

    try:
        # Keep the factor of 1/2 exact until conversion: multiplying first
        # can overflow even when the final pitch-center distance is finite.
        sun_planet_center = float(Fraction(module_value) * (sun + planet) / 2)
        ring_planet_center = float(Fraction(module_value) * (ring - planet) / 2)
    except OverflowError as exc:
        raise ValidationError("Gear geometry exceeds the finite numeric range.") from exc
    if not all(math.isfinite(value) for value in (sun_planet_center, ring_planet_center)):
        raise ValidationError("Gear geometry exceeds the finite numeric range.")
    # For a common module, pitch-center equality is exactly ring = sun + 2*planet.
    # Floating-point isclose can accept mismatched teeth at tiny modules or
    # large tooth counts, even when the centers are physically different.
    pitch_compatible = ring == sun + 2 * planet

    try:
        # Keep fractional tooth offsets exact: float rounding at large tooth
        # counts can otherwise turn an incompatible layout into a false pass.
        phase_teeth_exact = [gap * (sun + ring) / 360 for gap in gaps_exact]
        phase_teeth = [float(value) for value in phase_teeth_exact]
    except OverflowError as exc:
        raise ValidationError("Gear indexing exceeds the finite numeric range.") from exc
    if not all(math.isfinite(value) for value in phase_teeth):
        raise ValidationError("Gear indexing exceeds the finite numeric range.")
    phase_errors = [float(abs(value - round(value))) for value in phase_teeth_exact]
    indexing_compatible = all(error <= 1e-9 for error in phase_errors)
    try:
        # Exact tooth-pitch fractions preserve half-tooth offsets that would
        # vanish when large absolute phases are rounded to binary floats.
        external_phase_exact: list[Fraction] = []
        internal_phase_exact: list[Fraction] = []
        for angle, rotation in zip(angles, rotations, strict=True):
            planet_term = planet * (Fraction(angle) + 180 - Fraction(rotation)) / 360
            external_phase_exact.append(
                sun * (Fraction(angle) - Fraction(sun_rotation)) / 360 + planet_term
            )
            internal_phase_exact.append(
                ring * (Fraction(angle) - Fraction(ring_rotation)) / 360 - planet_term
            )
        external_phase = [float(value) for value in external_phase_exact]
        internal_phase = [float(value) for value in internal_phase_exact]
    except OverflowError as exc:
        raise ValidationError("Gear tooth phases exceed the finite numeric range.") from exc
    if not all(math.isfinite(value) for value in (*external_phase, *internal_phase)):
        raise ValidationError("Gear tooth phases exceed the finite numeric range.")

    def half_tooth_phase_error(value: Fraction) -> float:
        return float(abs(value % 1 - Fraction(1, 2)))

    external_phase_errors = [half_tooth_phase_error(value) for value in external_phase_exact]
    internal_phase_errors = [half_tooth_phase_error(value) for value in internal_phase_exact]
    rest_mesh_phase_compatible = all(
        error <= 1e-9 for error in (*external_phase_errors, *internal_phase_errors)
    )

    try:
        # Weighted terms avoid overflowing intermediate tooth-count products
        # when the resulting carrier velocity is still representable.
        total_teeth = sun + ring
        carrier_speed = math.fsum((sun_speed * (sun / total_teeth),
                                  ring_speed * (ring / total_teeth)))
        planet_speed = carrier_speed - (sun / planet) * (sun_speed - carrier_speed)
        if not math.isfinite(planet_speed) and math.isfinite(carrier_speed):
            # A finite planet speed can survive an overflowing speed difference.
            # Recompute only this exceptional path with exact float ratios.
            planet_speed = float((
                Fraction(sun_speed) * sun * (planet - ring)
                + Fraction(ring_speed) * ring * (sun + planet)
            ) / (planet * total_teeth))
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
