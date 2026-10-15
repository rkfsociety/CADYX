"""Addressed tests for isolated SK-054, SK-056 and SK-057 evaluators."""

import math
import unittest

from cadyx.sketch_curve_toolkit import (
    evaluate_archimedean_spiral,
    sample_coordinate_curve,
    sample_parametric_curve,
)


class SketchCurveToolkitTests(unittest.TestCase):
    def test_archimedean_spiral_radius_and_orientation(self):
        self.assertEqual(evaluate_archimedean_spiral((0, 0), 2, 4, 0), (2.0, 0.0))
        self.assertAlmostEqual(evaluate_archimedean_spiral((0, 0), 2, 4, math.tau)[0], 6.0)
        clockwise = evaluate_archimedean_spiral((0, 0), 2, 4, math.pi / 2, clockwise=True)
        self.assertAlmostEqual(clockwise[0], 0.0)
        self.assertAlmostEqual(clockwise[1], -3.0)

    def test_archimedean_spiral_rejects_negative_radius(self):
        with self.assertRaises(ValueError):
            evaluate_archimedean_spiral((0, 0), -1, 2, 0)

    def test_parametric_curve_samples_inclusive_uniform_domain(self):
        result = sample_parametric_curve(lambda t: t, lambda t: t*t, -1, 1, 3)
        self.assertEqual(result, ((-1.0, 1.0), (0.0, 0.0), (1.0, 1.0)))

    def test_parametric_curve_rejects_nonfinite_callback_result(self):
        with self.assertRaises(ValueError):
            sample_parametric_curve(lambda _t: math.inf, lambda t: t, 0, 1, 3)

    def test_coordinate_curve_interpolates_each_input_coordinate(self):
        coordinates = [(0, 0), (1, 2), (3, 1)]
        sampled = sample_coordinate_curve(coordinates, samples_per_span=4)
        self.assertEqual(len(sampled), 9)
        for index, point in enumerate(coordinates):
            self.assertEqual(sampled[index * 4], tuple(map(float, point)))
        self.assertEqual(sampled[-1], (3.0, 1.0))

    def test_closed_coordinate_curve_closes_and_rejects_open_input(self):
        points = [(0, 0), (2, 0), (1, 2), (0, 0)]
        sampled = sample_coordinate_curve(points, samples_per_span=3, closed=True)
        self.assertEqual(sampled[0], sampled[-1])
        with self.assertRaises(ValueError):
            sample_coordinate_curve(points[:-1], closed=True)


if __name__ == "__main__":
    unittest.main()
