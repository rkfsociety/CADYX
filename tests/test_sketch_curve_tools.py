"""Addressed mathematical tests for SK-034 through SK-042 curve evaluators."""

import math
import unittest

from cadyx.sketch_curve_tools import (
    evaluate_bezier,
    evaluate_bspline,
    evaluate_conic,
    evaluate_hyperbola,
    evaluate_interpolating_spline,
    evaluate_nurbs,
    evaluate_parabola,
    sample_conic_arc,
)


class SketchCurveToolsTests(unittest.TestCase):
    def test_bezier_endpoints_and_midpoint(self):
        controls = [(0, 0), (1, 2), (2, 0)]
        self.assertEqual(evaluate_bezier(controls, 0), (0.0, 0.0))
        self.assertEqual(evaluate_bezier(controls, 1), (2.0, 0.0))
        self.assertEqual(evaluate_bezier(controls, .5), (1.0, 1.0))

    def test_bspline_matches_quadratic_bezier(self):
        controls = [(0, 0), (1, 2), (2, 0)]
        self.assertEqual(evaluate_bspline(controls, 2, [0, 0, 0, 1, 1, 1], .5),
                         evaluate_bezier(controls, .5))
        self.assertEqual(evaluate_bspline(controls, 2, [0, 0, 0, 1, 1, 1], 1), (2.0, 0.0))

    def test_nurbs_quarter_circle(self):
        w = math.sqrt(.5)
        point = evaluate_nurbs([(1, 0), (1, 1), (0, 1)], [1, w, 1], 2,
                               [0, 0, 0, 1, 1, 1], .5)
        self.assertAlmostEqual(point[0], math.sqrt(.5))
        self.assertAlmostEqual(point[1], math.sqrt(.5))

    def test_interpolating_spline_hits_each_vertex(self):
        points = [(0, 0), (1, 2), (3, 1)]
        for index, expected in enumerate(points):
            self.assertEqual(evaluate_interpolating_spline(points, index), tuple(map(float, expected)))

    def test_closed_spline_wraps_and_interpolates_vertices(self):
        points = [(0, 0), (2, 0), (1, 2), (0, 0)]
        self.assertEqual(evaluate_interpolating_spline(points, 0, closed=True), (0.0, 0.0))
        self.assertEqual(evaluate_interpolating_spline(points, 3, closed=True), (0.0, 0.0))
        self.assertEqual(evaluate_interpolating_spline(points, 1, closed=True), (2.0, 0.0))

    def test_parabola_local_parameterization(self):
        self.assertEqual(evaluate_parabola((0, 0), (1, 0), 1, 2), (1.0, 2.0))
        with self.assertRaises(ValueError):
            evaluate_parabola((0, 0), (2, 0), 1, 2)

    def test_hyperbola_identity(self):
        self.assertEqual(evaluate_hyperbola((0, 0), (1, 0), 3, 4, 0), (3.0, 0.0))
        self.assertEqual(evaluate_hyperbola((0, 0), (1, 0), 3, 4, 0, branch=-1), (-3.0, 0.0))

    def test_conic_evaluation_and_arc_sampling(self):
        circle = (1, 0, 1, 0, 0, -1)
        self.assertEqual(evaluate_conic(circle, 0), (0.0, 1.0))
        arc = sample_conic_arc(circle, 0, 1, 3)
        self.assertEqual(arc[0], (0.0, 1.0))
        self.assertEqual(arc[-1], (1.0, 0.0))
        self.assertEqual(len(arc), 3)

    def test_invalid_domains_and_curve_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_bezier([(0, 0), (1, 1)], 1.1)
        with self.assertRaises(ValueError):
            evaluate_bspline([(0, 0), (1, 1)], 1, [0, 0, 1], .5)
        with self.assertRaises(ValueError):
            evaluate_nurbs([(0, 0), (1, 1)], [1, 0], 1, [0, 0, 1, 1], .5)
        with self.assertRaises(ValueError):
            evaluate_interpolating_spline([(0, 0), (1, 1), (2, 0)], 0, closed=True)
        with self.assertRaises(ValueError):
            evaluate_conic((1, 0, 1, 0, 0, -1), 2)


if __name__ == "__main__":
    unittest.main()
