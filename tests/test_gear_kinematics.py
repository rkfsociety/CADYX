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

    def test_invalid_input_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "distinct"):
            self.solve(teeth=(18, 18, 54), angles=[0.0, 0.0, 180.0])


if __name__ == "__main__":
    unittest.main()
