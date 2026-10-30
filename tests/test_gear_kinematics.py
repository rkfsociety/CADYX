from __future__ import annotations

import unittest

from cadyx.errors import ValidationError
from cadyx.gear_kinematics import solve_planetary_gearset


class PlanetaryGearKinematicsTests(unittest.TestCase):
    def solve(self, *, teeth: tuple[int, int, int], angles: list[float], rotations: list[float] | None = None) -> dict:
        return solve_planetary_gearset(
            module=2.0,
            sun_teeth=teeth[0],
            planet_teeth=teeth[1],
            ring_teeth=teeth[2],
            planet_angles_degrees=angles,
            planet_rotation_degrees=rotations,
            sun_speed_radians_per_second=1.0,
            ring_speed_radians_per_second=0.0,
        )

    def test_balanced_18_18_54_train_has_expected_speeds(self) -> None:
        result = self.solve(teeth=(18, 18, 54), angles=[0.0, 120.0, 240.0], rotations=[10.0, 250.0, 130.0])
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["pitch_geometry_compatible"])
        self.assertTrue(result["tooth_indexing_compatible"])
        self.assertTrue(result["rest_mesh_phase_compatible"])
        self.assertTrue(result["planet_layout_balanced"])
        self.assertAlmostEqual(result["carrier_speed_radians_per_second"], 0.25)
        self.assertAlmostEqual(result["planet_spin_speed_radians_per_second"], -0.5)
        self.assertEqual(result["indexing_phase_teeth_per_gap"], [24.0, 24.0, 24.0])

    def test_requested_20_20_60_balanced_layout_has_phase_conflict(self) -> None:
        result = self.solve(teeth=(20, 20, 60), angles=[0.0, 120.0, 240.0])
        self.assertTrue(result["pitch_geometry_compatible"])
        self.assertFalse(result["tooth_indexing_compatible"])
        self.assertTrue(result["planet_layout_balanced"])
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["indexing_phase_teeth_per_gap"], [80 / 3, 80 / 3, 80 / 3])

    def test_unequal_phase_compatible_layout_is_reported_as_unbalanced(self) -> None:
        result = self.solve(teeth=(20, 20, 60), angles=[0.0, 117.0, 238.5], rotations=[9.0, 9.0, 0.0])
        self.assertTrue(result["pitch_geometry_compatible"])
        self.assertTrue(result["tooth_indexing_compatible"])
        self.assertTrue(result["rest_mesh_phase_compatible"])
        self.assertFalse(result["planet_layout_balanced"])
        self.assertEqual(result["status"], "pass")

    def test_extreme_numeric_inputs_raise_validation_errors(self) -> None:
        valid = dict(
            module=2.0,
            sun_teeth=18,
            planet_teeth=18,
            ring_teeth=54,
            planet_angles_degrees=[0.0, 120.0, 240.0],
            sun_speed_radians_per_second=1.0,
            ring_speed_radians_per_second=0.0,
        )
        cases = (
            {"module": 1e308},
            {"sun_teeth": 10 ** 1000},
            {"sun_rotation_degrees": 1e308},
            {"sun_rotation_degrees": 10 ** 1000},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(ValidationError, "finite"):
                    solve_planetary_gearset(**{**valid, **overrides})

    def test_finite_extreme_speeds_do_not_overflow_weighted_average(self) -> None:
        base = dict(module=2.0, sun_teeth=18, planet_teeth=18, ring_teeth=54,
                    planet_angles_degrees=[0.0, 120.0, 240.0])
        cases = (
            (1e308, 0.0, 2.5e307, -5e307),
            (0.0, 1e308, 7.5e307, 1.5e308),
            (1e308, 1e308, 1e308, 1e308),
            (1.0, -1e308, -7.5e307, -1.5e308),
        )
        for sun_speed, ring_speed, expected_carrier, expected_planet in cases:
            with self.subTest(sun_speed=sun_speed, ring_speed=ring_speed):
                result = solve_planetary_gearset(
                    **base, sun_speed_radians_per_second=sun_speed,
                    ring_speed_radians_per_second=ring_speed,
                )
                self.assertAlmostEqual(result["carrier_speed_radians_per_second"] / 1e308,
                                       expected_carrier / 1e308, places=12)
                self.assertAlmostEqual(result["planet_spin_speed_radians_per_second"] / 1e308,
                                       expected_planet / 1e308, places=12)

    def test_finite_planet_speed_survives_overflowing_difference(self) -> None:
        base = dict(module=2.0, sun_teeth=10, planet_teeth=100, ring_teeth=210,
                    planet_angles_degrees=[0.0, 180.0])
        for sun_speed, ring_speed, expected in (
            (-1.7e308, 1.5e308, 1.66e308),
            (1.7e308, -1.5e308, -1.66e308),
        ):
            with self.subTest(sun_speed=sun_speed, ring_speed=ring_speed):
                result = solve_planetary_gearset(
                    **base, sun_speed_radians_per_second=sun_speed,
                    ring_speed_radians_per_second=ring_speed,
                )
                self.assertAlmostEqual(
                    result["planet_spin_speed_radians_per_second"] / 1e308,
                    expected / 1e308, places=12,
                )

    def test_unrepresentable_planet_speed_still_raises(self) -> None:
        with self.assertRaisesRegex(ValidationError, "angular velocities"):
            solve_planetary_gearset(
                module=2.0, sun_teeth=18, planet_teeth=18, ring_teeth=54,
                planet_angles_degrees=[0.0, 120.0, 240.0],
                sun_speed_radians_per_second=-1.7e308,
                ring_speed_radians_per_second=1.5e308,
            )

    def test_invalid_input_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "distinct"):
            self.solve(teeth=(18, 18, 54), angles=[0.0, 0.0, 180.0])


if __name__ == "__main__":
    unittest.main()
