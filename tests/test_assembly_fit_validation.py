"""Fail-closed numeric validation for assembly fit without a native kernel."""

from __future__ import annotations

import unittest

from cadyx.assembly_fit import _classify, _samples
from cadyx.errors import ValidationError


class AssemblyFitNumericValidationTests(unittest.TestCase):
    def _metrics(self):
        return {
            "minimum_distance": 2.0,
            "deflection": 0.01,
            "intersection_volume": 0.0,
            "inner_solution": False,
            "solution_count": 1,
            "point_on_first": [0.0, 0.0, 0.0],
            "point_on_second": [2.0, 0.0, 0.0],
        }

    def test_finite_joint_coordinates_remain_supported(self):
        samples = [{"joint_values": [{"mate_id": "m", "angle_deg": -45, "offset_mm": 3.5}]}]
        self.assertEqual(_samples(samples, {"mates": [{"id": "m"}]}), samples)

    def test_unbounded_joint_integers_raise_validation_error(self):
        for field in ("angle_deg", "offset_mm"):
            for number in (10**400, -(10**400)):
                with self.subTest(field=field, sign=number > 0):
                    sample = [{"joint_values": [{"mate_id": "m", field: number}]}]
                    with self.assertRaises(ValidationError):
                        _samples(sample, {"mates": [{"id": "m"}]})

    def test_valid_metrics_still_classify_as_separate(self):
        result = _classify(self._metrics(), 0.5, 0.01)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["interference_status"], "separate")

    def test_unbounded_kernel_metrics_raise_validation_error(self):
        for field in ("minimum_distance", "deflection", "intersection_volume"):
            with self.subTest(field=field):
                measured = self._metrics()
                measured[field] = 10**400
                with self.assertRaises(ValidationError):
                    _classify(measured, 0.5, 0.01)

    def test_unbounded_witness_coordinates_raise_validation_error(self):
        for field in ("point_on_first", "point_on_second"):
            with self.subTest(field=field):
                measured = self._metrics()
                measured[field][0] = 10**400
                with self.assertRaises(ValidationError):
                    _classify(measured, 0.5, 0.01)

    def test_large_integer_metric_sum_fails_closed(self):
        measured = self._metrics()
        measured["minimum_distance"] = 10**308
        measured["deflection"] = 10**308
        with self.assertRaisesRegex(ValidationError, "overflowing"):
            _classify(measured, 0.5, 0.01)

    def test_large_finite_deflection_does_not_overflow_cube(self):
        measured = self._metrics()
        measured["minimum_distance"] = 1e104
        measured["deflection"] = 1e103
        self.assertEqual(_classify(measured, 0.5, 0.01)["status"], "pass")

    def test_distance_interval_overflow_is_not_reported_as_infinity(self):
        measured = self._metrics()
        measured["minimum_distance"] = 1e308
        measured["deflection"] = 1e308
        with self.assertRaisesRegex(ValidationError, "overflowing"):
            _classify(measured, 0.5, 0.01)


if __name__ == "__main__":
    unittest.main()
