from __future__ import annotations

import math
import unittest

from cadyx.errors import ValidationError
from cadyx.sketches import closed_profile, validate_sketches
from cadyx.sketch_slot_tools import (
    build_arc_slot_from_center,
    build_arc_slot_three_points,
    build_slot_by_centers,
    build_slot_by_total_length,
    build_straight_slot,
    build_slot_from_center,
    build_tangent_arc_slot,
    contour_to_sketch_objects,
)


class SketchSlotGeometryTests(unittest.TestCase):
    def assert_closed(self, contour: dict) -> None:
        edges = contour["edges"]
        self.assertTrue(contour["closed"])
        self.assertGreaterEqual(len(edges), 4)
        for current, following in zip(edges, edges[1:] + edges[:1]):
            self.assertAlmostEqual(current["end"][0], following["start"][0], places=7)
            self.assertAlmostEqual(current["end"][1], following["start"][1], places=7)

    def test_straight_slot_has_exact_caps_and_parallel_sides(self) -> None:
        shape = build_straight_slot((0, 0), (10, 0), 4)
        self.assert_closed(shape)
        self.assertEqual([edge["type"] for edge in shape["edges"]], ["line", "arc", "line", "arc"])
        self.assertAlmostEqual(shape["edges"][0]["start"][1], 2)
        self.assertAlmostEqual(shape["edges"][2]["start"][1], -2)
        self.assertAlmostEqual(shape["edges"][1]["radius"], 2)
        self.assertAlmostEqual(shape["edges"][1]["sweep_radians"], -math.pi)

    def test_center_and_total_length_variants(self) -> None:
        centered = build_slot_by_centers((-5, 2), (5, 2), 2)
        self.assert_closed(centered)
        total = build_slot_by_total_length((3, 4), 14, 4, 90)
        a, b = total["centerline"]["start"], total["centerline"]["end"]
        self.assertAlmostEqual(math.dist(a, b), 10)
        self.assertAlmostEqual((a[0] + b[0]) / 2, 3)
        self.assertAlmostEqual((a[1] + b[1]) / 2, 4)
        self.assert_closed(build_slot_from_center((0, 0), 10, 4))
        with self.assertRaises(ValidationError):
            build_slot_by_total_length((0, 0), 4, 4)

    def test_arc_slot_three_points_passes_through_middle_and_closes(self) -> None:
        shape = build_arc_slot_three_points((5, 0), (0, 5), (-5, 0), 2)
        self.assert_closed(shape)
        self.assertAlmostEqual(shape["centerline"]["radius"], 5)
        self.assertAlmostEqual(shape["centerline"]["sweep_radians"], math.pi)
        self.assertTrue(all(edge["type"] == "arc" for edge in shape["edges"]))
        self.assertAlmostEqual(shape["edges"][0]["radius"], 6)
        self.assertAlmostEqual(shape["edges"][2]["radius"], 4)
        translated = build_arc_slot_three_points((1e9 + 5, 1e9), (1e9, 1e9 + 5),
                                                 (1e9 - 5, 1e9), 2)
        self.assertAlmostEqual(translated["centerline"]["center"][0], 1e9, places=6)
        self.assertAlmostEqual(translated["centerline"]["center"][1], 1e9, places=6)

    def test_center_and_tangent_arc_variants(self) -> None:
        centered = build_arc_slot_from_center((1, 2), 8, 0, 120, 2)
        self.assert_closed(centered)
        tangent = build_tangent_arc_slot((0, 0), (1, 0), 8, 90, 2, side=1)
        self.assert_closed(tangent)
        self.assertAlmostEqual(tangent["centerline"]["center"][0], 0)
        self.assertAlmostEqual(tangent["centerline"]["center"][1], 8)
        self.assertAlmostEqual(tangent["centerline"]["start_angle_radians"], -math.pi / 2)

    def test_tangent_arc_direction_follows_side_and_signed_sweep(self) -> None:
        for side, sweep, expected_x in ((1, 90, 1), (1, -90, -1), (-1, 90, -1), (-1, -90, 1)):
            with self.subTest(side=side, sweep=sweep):
                shape = build_tangent_arc_slot((2, 3), (5, 0), 8, sweep, 2, side=side)
                centerline = shape["centerline"]
                center = centerline["center"]
                start_angle = centerline["start_angle_radians"]
                direction = 1 if sweep > 0 else -1
                tangent_x = direction * -math.sin(start_angle)
                tangent_y = direction * math.cos(start_angle)
                self.assertAlmostEqual(tangent_x, expected_x)
                self.assertAlmostEqual(tangent_y, 0)
                self.assertEqual(centerline["sweep_radians"] > 0, sweep > 0)

    def test_near_full_turn_rejects_overlapping_end_caps(self) -> None:
        with self.assertRaises(ValidationError):
            build_arc_slot_from_center((0, 0), 5, 0, 330, 8)
        with self.assertRaises(ValidationError):
            build_arc_slot_from_center((0, 0), 10, 0, -350, 2)
        self.assert_closed(build_arc_slot_from_center((0, 0), 10, 0, 340, 2))

        for side, sweep in ((1, 60), (-1, 60), (1, -60), (-1, -60)):
            result = build_tangent_arc_slot((2, 3), (3, 4), 7, sweep, 1, side=side)
            cx, cy = result["centerline"]["center"]
            rx, ry = 2 - cx, 3 - cy
            tangent_at_start = (-ry * math.copysign(1, sweep), rx * math.copysign(1, sweep))
            expected_sign = side * math.copysign(1, sweep)
            direction_dot = tangent_at_start[0] * 0.6 + tangent_at_start[1] * 0.8
            self.assertGreater(direction_dot * expected_sign, 0)

    def test_contours_convert_to_native_records_and_closed_profiles(self) -> None:
        sketch_id = "sketch:12345678-1234-4234-8234-123456789abc"
        sketch = {"id": sketch_id, "type": "sketch", "plane": "XY", "name": "Slot test"}
        contours = (
            ("SK-047", build_straight_slot((0, 0), (10, 0), 4)),
            ("SK-048", build_slot_by_centers((-5, 2), (5, 2), 2)),
            ("SK-049", build_slot_by_total_length((3, 4), 14, 4, 37)),
            ("SK-050", build_slot_from_center((1, -2), 10, 4, -45)),
            ("SK-051", build_arc_slot_three_points((5, 0), (0, 5), (-5, 0), 2)),
            ("SK-052+", build_arc_slot_from_center((0, 0), 8, 0, 120, 2)),
            ("SK-052-", build_arc_slot_from_center((1, 2), 8, 20, -120, 2)),
            ("SK-053+", build_tangent_arc_slot((0, 0), (1, 0), 8, 90, 2, side=1)),
            ("SK-053-", build_tangent_arc_slot((0, 0), (1, 0), 8, -90, 2, side=-1)),
        )
        for name, contour in contours:
            with self.subTest(tool=name, centerline=contour["centerline"]):
                objects = [sketch, *contour_to_sketch_objects(contour, sketch_id)]
                validate_sketches(objects)
                by_id = {item["id"]: item for item in objects}
                arc_center_ids = [item["center_id"] for item in objects
                                  if item["type"] == "circle" and "sweep_radians" in item]
                self.assertTrue(all(by_id[center_id].get("construction") is True
                                    for center_id in arc_center_ids))
                plane, profile = closed_profile(objects, sketch_id)
                self.assertEqual(plane, "XY")
                self.assertGreaterEqual(len(profile), 16)
                self.assertTrue(all(math.isfinite(p["x"]) and math.isfinite(p["y"]) for p in profile))
                area = sum(profile[i]["x"] * profile[(i + 1) % len(profile)]["y"]
                           - profile[(i + 1) % len(profile)]["x"] * profile[i]["y"]
                           for i in range(len(profile)))
                self.assertGreater(abs(area), 1e-6)

    def test_contour_conversion_rejects_invalid_arc_geometry(self) -> None:
        sketch_id = "sketch:12345678-1234-4234-8234-123456789abc"
        malformed = {
            "closed": True,
            "edges": [
                {"type": "arc", "center": (0, 0), "radius": 1,
                 "start_angle_radians": 0, "sweep_radians": math.pi / 2,
                 "start": (1, 0), "end": (0, 2)},
                {"type": "line", "start": (0, 2), "end": (-1, 0)},
                {"type": "line", "start": (-1, 0), "end": (1, 0)},
            ],
        }
        with self.assertRaises(ValidationError):
            contour_to_sketch_objects(malformed, sketch_id)

        malformed["edges"][0]["type"] = []
        with self.assertRaises(ValidationError):
            contour_to_sketch_objects(malformed, sketch_id)

    def test_degenerate_and_non_finite_inputs_are_rejected(self) -> None:
        invalid_calls = (
            lambda: build_straight_slot((0, 0), (0, 0), 2),
            lambda: build_straight_slot((0, 0), (1, 0), 0),
            lambda: build_slot_by_total_length((0, 0), 3, 4),
            lambda: build_slot_by_total_length((0, 0), 4, 4),
            lambda: build_arc_slot_three_points((0, 0), (1, 1), (2, 2), 1),
            lambda: build_arc_slot_from_center((0, 0), 1, 0, 90, 2),
            lambda: build_arc_slot_from_center((0, 0), 2, 0, 360, 1),
            lambda: build_arc_slot_from_center((0, 0), 5, 0, 330, 8),
            lambda: build_tangent_arc_slot((0, 0), (0, 0), 4, 90, 1),
            lambda: build_tangent_arc_slot((0, 0), (1, 0), 4, 90, 1, side=0),
            lambda: build_straight_slot((0, math.inf), (1, 0), 1),
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises(ValidationError):
                    call()


if __name__ == "__main__":
    unittest.main()
