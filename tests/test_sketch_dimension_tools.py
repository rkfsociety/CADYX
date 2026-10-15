from __future__ import annotations

import math
import unittest

from cadyx.errors import ValidationError
from cadyx.sketch_dimension_tools import (
    measure_arc_angle,
    measure_arc_length,
    measure_circle_diameter,
    measure_circle_radius,
    measure_line_length,
    measure_point_distance,
)


class SketchDimensionMeasurementTests(unittest.TestCase):
    def test_point_distance_is_euclidean_in_sketch_coordinate_units(self) -> None:
        self.assertEqual(measure_point_distance((0, 0), (3, 4)), 5)
        self.assertEqual(measure_point_distance((2, -7), (2, -7)), 0)

    def test_line_length_measures_the_finite_segment_endpoints(self) -> None:
        self.assertEqual(measure_line_length((-2, 1), (1, 5)), 5)
        for end in ((0, 0), (1e-8, 0)):
            with self.subTest(end=end), self.assertRaises(ValidationError):
                measure_line_length((0, 0), end)

    def test_circle_radius_and_diameter_use_the_stored_radius(self) -> None:
        self.assertEqual(measure_circle_radius(2.5), 2.5)
        self.assertEqual(measure_circle_diameter(2.5), 5)
        with self.assertRaises(ValidationError):
            measure_circle_radius(0)
        with self.assertRaises(ValidationError):
            measure_circle_diameter(float("inf"))
        with self.assertRaises(ValidationError):
            measure_circle_diameter(float.fromhex("0x1.fffffffffffffp+1023"))

    def test_arc_angle_preserves_signed_document_radians(self) -> None:
        self.assertEqual(measure_arc_angle(-math.pi / 3), -math.pi / 3)
        self.assertEqual(measure_arc_angle(math.pi / 3), math.pi / 3)

    def test_arc_length_is_exact_radius_times_absolute_sweep(self) -> None:
        expected = 3 * math.pi / 2
        self.assertEqual(measure_arc_length(3, math.pi / 2), expected)
        self.assertEqual(measure_arc_length(3, -math.pi / 2), expected)

    def test_malformed_points_are_rejected(self) -> None:
        malformed = (None, "12", (1,), (1, 2, 3), (True, 0), (math.nan, 0),
                     (math.inf, 0), (10**10000, 0))
        for point in malformed:
            with self.subTest(point=type(point).__name__), self.assertRaises(ValidationError):
                measure_point_distance(point, (0, 0))

    def test_malformed_measurement_scalars_are_rejected(self) -> None:
        invalid_radii = (True, "2", None, 0, -1, math.nan, math.inf, 10**10000)
        for radius in invalid_radii:
            with self.subTest(radius=type(radius).__name__), self.assertRaises(ValidationError):
                measure_circle_radius(radius)

        invalid_sweeps = (True, "1", None, 0, 1e-8, math.tau, -math.tau,
                          math.nan, math.inf, 10**10000)
        for sweep in invalid_sweeps:
            with self.subTest(sweep=type(sweep).__name__), self.assertRaises(ValidationError):
                measure_arc_angle(sweep)

    def test_measurements_reject_non_finite_results(self) -> None:
        with self.assertRaises(ValidationError):
            measure_point_distance((-1e308, 0), (1e308, 0))
        with self.assertRaises(ValidationError):
            measure_arc_length(1e308, math.pi)


if __name__ == "__main__":
    unittest.main()
