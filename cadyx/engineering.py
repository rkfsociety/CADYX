"""Generated engineering profiles used by internal CADYX model commands."""

from __future__ import annotations

import math
from typing import Any

from .errors import ValidationError


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a number.")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValidationError(f"{name} must be finite.") from None
    if not math.isfinite(result):
        raise ValidationError(f"{name} must be finite.")
    return result


def involute_spur_gear_profile(
    *,
    module: Any,
    tooth_count: Any,
    pressure_angle_degrees: Any = 20.0,
    center_x: Any = 0.0,
    center_y: Any = 0.0,
    rotation_degrees: Any = 0.0,
    backlash: Any = 0.0,
) -> tuple[list[tuple[float, float]], dict[str, float | int]]:
    """Return a closed unshifted external spur-gear outline with involute flanks."""
    module_value = _finite_number(module, "module")
    if module_value <= 0:
        raise ValidationError("module must be finite and positive.")
    if type(tooth_count) is not int or tooth_count < 3:
        raise ValidationError("tooth_count must be an integer of at least 3.")

    pressure_degrees = _finite_number(pressure_angle_degrees, "pressure_angle_degrees")
    if not 5.0 <= pressure_degrees <= 45.0:
        raise ValidationError("pressure_angle_degrees must be from 5 to 45 degrees.")
    pressure = math.radians(pressure_degrees)
    minimum_teeth = math.ceil(2.0 / (math.sin(pressure) ** 2))
    if tooth_count < minimum_teeth:
        raise ValidationError(
            f"tooth_count must be at least {minimum_teeth} for this pressure angle to avoid rack undercut."
        )

    x_center = _finite_number(center_x, "center_x")
    y_center = _finite_number(center_y, "center_y")
    rotation = math.radians(_finite_number(rotation_degrees, "rotation_degrees"))
    backlash_value = _finite_number(backlash, "backlash")
    if backlash_value < 0 or backlash_value >= math.pi * module_value / 2:
        raise ValidationError("backlash must be non-negative and less than half the circular pitch.")

    try:
        pitch_radius = module_value * tooth_count / 2.0
        base_radius = pitch_radius * math.cos(pressure)
        tip_radius = module_value * (tooth_count / 2.0 + 1.0)
        root_radius = module_value * (tooth_count / 2.0 - 1.25)
    except OverflowError as exc:
        raise ValidationError("Gear dimensions must remain finite for this tooth count and module.") from exc
    if not all(math.isfinite(value) for value in (pitch_radius, base_radius, tip_radius, root_radius)):
        raise ValidationError("Gear dimensions must remain finite for this tooth count and module.")
    if root_radius <= 0 or tip_radius <= base_radius:
        raise ValidationError("Gear dimensions do not leave a usable involute tooth profile.")

    def involute(angle: float) -> float:
        return math.tan(angle) - angle

    start_radius = max(root_radius, base_radius)
    start_pressure = math.acos(min(1.0, base_radius / start_radius))
    tooth_half_angle = math.pi / (2.0 * tooth_count) - backlash_value / (module_value * tooth_count)
    start_half_angle = tooth_half_angle + involute(pressure) - involute(start_pressure)
    tip_pressure = math.acos(base_radius / tip_radius)
    tip_half_angle = tooth_half_angle + involute(pressure) - involute(tip_pressure)
    tooth_pitch_angle = 2.0 * math.pi / tooth_count
    root_gap_angle = tooth_pitch_angle - 2.0 * start_half_angle
    if start_half_angle <= 0 or tip_half_angle <= 0 or root_gap_angle <= 0:
        raise ValidationError("Gear dimensions close or cross the tooth spaces.")

    points: list[tuple[float, float]] = []

    def append_polar(radius: float, angle: float) -> None:
        point = (x_center + radius * math.cos(angle + rotation),
                 y_center + radius * math.sin(angle + rotation))
        if not points or math.hypot(point[0] - points[-1][0], point[1] - points[-1][1]) > 1e-7:
            points.append(point)

    def half_angle_at(radius: float) -> float:
        local_pressure = math.acos(min(1.0, base_radius / radius))
        return tooth_half_angle + involute(pressure) - involute(local_pressure)

    involute_steps = 12
    arc_step = math.radians(5.0)
    for tooth in range(tooth_count):
        center_angle = tooth_pitch_angle * tooth
        left_angle = center_angle - start_half_angle
        right_angle = center_angle + start_half_angle
        append_polar(root_radius, left_angle)

        start_roll = math.sqrt(max(0.0, (start_radius / base_radius) ** 2 - 1.0))
        tip_roll = math.sqrt((tip_radius / base_radius) ** 2 - 1.0)
        for step in range(1, involute_steps + 1):
            roll = start_roll + (tip_roll - start_roll) * step / involute_steps
            radius = base_radius * math.sqrt(1.0 + roll * roll)
            flank_angle = center_angle - half_angle_at(radius)
            append_polar(radius, flank_angle)

        tip_segments = max(1, math.ceil(2.0 * tip_half_angle / arc_step))
        for step in range(1, tip_segments + 1):
            angle = center_angle - tip_half_angle + 2.0 * tip_half_angle * step / tip_segments
            append_polar(tip_radius, angle)

        for step in range(involute_steps - 1, -1, -1):
            roll = start_roll + (tip_roll - start_roll) * step / involute_steps
            radius = base_radius * math.sqrt(1.0 + roll * roll)
            flank_angle = center_angle + half_angle_at(radius)
            append_polar(radius, flank_angle)
        append_polar(root_radius, right_angle)

        root_segments = max(1, math.ceil(root_gap_angle / arc_step))
        next_left = center_angle + tooth_pitch_angle - start_half_angle
        for step in range(1, root_segments):
            append_polar(root_radius, right_angle + (next_left - right_angle) * step / root_segments)

    if len(points) < 3:
        raise ValidationError("Generated gear profile must contain at least 3 points.")
    if any(not math.isfinite(value) for point in points for value in point):
        raise ValidationError("Generated gear coordinates must be finite.")

    parameters: dict[str, float | int] = {
        "module": module_value,
        "tooth_count": tooth_count,
        "pressure_angle_degrees": pressure_degrees,
        "center_x": x_center,
        "center_y": y_center,
        "rotation_degrees": math.degrees(rotation),
        "backlash": backlash_value,
        "pitch_radius": pitch_radius,
        "base_radius": base_radius,
        "tip_radius": tip_radius,
        "root_radius": root_radius,
    }
    return points, parameters
