from __future__ import annotations

import unittest

from cadyx.errors import ValidationError
from cadyx.sketch_transform_tools import (
    duplicate_geometry,
    geometry_dependency_closure,
    rotate_geometry,
    scale_geometry,
    translate_geometry,
)


def _point(object_id: str, x: float, y: float) -> dict:
    return {"id": object_id, "type": "point", "sketch_id": "sketch:1", "x": x, "y": y}


class SketchTransformToolsTests(unittest.TestCase):
    def test_translation_includes_line_point_dependencies_and_preserves_ids(self) -> None:
        objects = [_point("point:1", 1, 2), _point("point:2", 4, 2),
                   {"id": "line:1", "type": "line", "sketch_id": "sketch:1",
                    "start_id": "point:1", "end_id": "point:2", "extent": "segment"}]
        closure = geometry_dependency_closure(objects, ["line:1"])
        self.assertEqual([item["id"] for item in closure], ["point:1", "point:2", "line:1"])
        moved = translate_geometry(objects, ["line:1"], 5, -3)
        by_id = {item["id"]: item for item in moved}
        self.assertEqual((by_id["point:1"]["x"], by_id["point:1"]["y"]), (6, -1))
        self.assertEqual((by_id["point:2"]["x"], by_id["point:2"]["y"]), (9, -1))
        self.assertEqual((by_id["line:1"]["start_id"], by_id["line:1"]["end_id"]),
                         ("point:1", "point:2"))
        self.assertEqual(objects[0]["x"], 1, "input records are not mutated")

    def test_duplicate_remaps_all_dependency_ids_and_keeps_source_unmodified(self) -> None:
        objects = [_point("point:1", 0, 0), _point("point:2", 2, 0),
                   {"id": "line:1", "type": "line", "sketch_id": "sketch:1",
                    "start_id": "point:1", "end_id": "point:2", "extent": "segment"}]
        copied = duplicate_geometry(objects, ["line:1"], dx=3)
        by_id = {item["id"]: item for item in copied}
        copied_line = next(item for item in copied if item["type"] == "line")
        self.assertTrue(all(item["id"] not in {source["id"] for source in objects} for item in copied))
        self.assertEqual(by_id[copied_line["start_id"]]["x"], 3)
        self.assertEqual(by_id[copied_line["end_id"]]["x"], 5)
        self.assertNotEqual(copied_line["start_id"], "point:1")
        self.assertEqual(objects[0]["x"], 0)

    def test_rotation_transforms_arc_geometry_and_keeps_signed_sweep(self) -> None:
        objects = [_point("point:1", 1, 0), _point("point:2", 0, 1), _point("point:3", 0, 0),
                   {"id": "circle:1", "type": "circle", "sketch_id": "sketch:1",
                    "center_id": "point:3", "radius": 1, "start_id": "point:1", "end_id": "point:2",
                    "start_angle_radians": 0, "sweep_radians": 1.5707963267948966}]
        rotated = rotate_geometry(objects, ["circle:1"], center_x=0, center_y=0,
                                  angle_radians=1.5707963267948966)
        by_id = {item["id"]: item for item in rotated}
        self.assertAlmostEqual(by_id["point:1"]["x"], 0, places=12)
        self.assertAlmostEqual(by_id["point:1"]["y"], 1, places=12)
        arc = by_id["circle:1"]
        self.assertAlmostEqual(arc["start_angle_radians"], 1.5707963267948966)
        self.assertAlmostEqual(arc["sweep_radians"], 1.5707963267948966)

    def test_uniform_scale_updates_circle_ellipse_and_spline_geometry(self) -> None:
        objects = [_point("point:1", 2, 0), _point("point:2", 0, 0), _point("point:3", 0, 2),
                   {"id": "circle:1", "type": "circle", "sketch_id": "sketch:1",
                    "center_id": "point:2", "radius": 2},
                   {"id": "ellipse:1", "type": "ellipse", "sketch_id": "sketch:1",
                    "center_x": 1, "center_y": 2, "major_radius": 4, "minor_radius": 2,
                    "rotation_radians": 0.25},
                   {"id": "spline:1", "type": "spline", "sketch_id": "sketch:1",
                    "point_ids": ["point:1", "point:2", "point:3"]}]
        scaled = scale_geometry(objects, ["circle:1", "ellipse:1", "spline:1"],
                                center_x=0, center_y=0, factor=0.5)
        by_id = {item["id"]: item for item in scaled}
        self.assertEqual(by_id["circle:1"]["radius"], 1)
        self.assertEqual((by_id["ellipse:1"]["center_x"], by_id["ellipse:1"]["center_y"]), (0.5, 1))
        self.assertEqual((by_id["ellipse:1"]["major_radius"], by_id["ellipse:1"]["minor_radius"]), (2, 1))
        self.assertEqual(by_id["ellipse:1"]["rotation_radians"], 0.25)
        self.assertEqual((by_id["point:1"]["x"], by_id["point:1"]["y"]), (1, 0))

    def test_malformed_records_and_non_finite_transforms_are_rejected(self) -> None:
        point = _point("point:1", 0, 0)
        for objects, ids in [([], ["line:1"]), ([{**point, "type": "constraint"}], ["point:1"]),
                             ([{**point, "x": float("nan")}], ["point:1"]),
                             ([{**point, "sketch_id": "sketch:2"}, _point("point:2", 1, 0)], ["point:1"]),
                             ([point], [])]:
            with self.subTest(objects=objects, ids=ids), self.assertRaises(ValidationError):
                translate_geometry(objects, ids, 1, 1)

        line_objects = [_point("point:1", 0, 0),
                        {"id": "line:1", "type": "line", "sketch_id": "sketch:1",
                         "start_id": "point:1", "end_id": "point:missing"}]
        with self.assertRaises(ValidationError):
            duplicate_geometry(line_objects, ["line:1"])
        with self.assertRaises(ValidationError):
            scale_geometry([point], ["point:1"], center_x=0, center_y=0, factor=0)
        with self.assertRaises(ValidationError):
            translate_geometry([_point("point:1", 1e308, 0)], ["point:1"], 1e308, 0)
        huge_circle = [_point("point:1", 0, 0),
                       {"id": "circle:1", "type": "circle", "sketch_id": "sketch:1",
                        "center_id": "point:1", "radius": 1e308}]
        with self.assertRaises(ValidationError):
            scale_geometry(huge_circle, ["circle:1"], center_x=0, center_y=0, factor=2)


if __name__ == "__main__":
    unittest.main()
