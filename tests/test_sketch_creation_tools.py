from __future__ import annotations

import tempfile
import unittest

from cadyx.core import CadyxCore
from cadyx.kernel import PrimitiveKernelAdapter


class IndividualSketchCreationToolTests(unittest.TestCase):
    """Each test starts with its own empty sketch and exercises one named tool."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.core = CadyxCore(self.temp.name, kernel=PrimitiveKernelAdapter())
        self.project_id = self.core.create_project("Individual sketch tool")["project"]["id"]
        self.lease_id = self.core.acquire_lease(self.project_id, "agent", "individual-tool-test")["lease_id"]
        self.sketch_id = self.command({"type": "create_sketch"})["sketch_id"]

    def command(self, value: dict) -> dict:
        return self.core.execute_command(self.project_id, self.lease_id, value)["history_entry"]["result"]

    def objects(self) -> dict[str, dict]:
        return {item["id"]: item for item in self.core.inspect_project(self.project_id)["document"]["objects"]}

    def test_rectangle_two_corners(self) -> None:
        result = self.command({"type": "add_sketch_rectangle_two_corners", "sketch_id": self.sketch_id,
                              "x1": 5, "y1": 7, "x2": 1, "y2": 2})
        self.assertEqual(len(result["line_ids"]), 4)
        self.assertEqual(len(result["point_ids"]), 4)

    def test_rectangle_center(self) -> None:
        result = self.command({"type": "add_sketch_rectangle_center", "sketch_id": self.sketch_id,
                              "center_x": 4, "center_y": -2, "width": 6, "height": 4})
        objects = self.objects()
        self.assertEqual({(objects[item]["x"], objects[item]["y"]) for item in result["point_ids"]},
                         {(1, -4), (7, -4), (7, 0), (1, 0)})

    def test_rectangle_three_points(self) -> None:
        result = self.command({"type": "add_sketch_rectangle_three_points", "sketch_id": self.sketch_id,
                              "points": [{"x": 0, "y": 4}, {"x": 0, "y": 0}, {"x": 3, "y": 0}]})
        objects = self.objects()
        self.assertEqual({(objects[item]["x"], objects[item]["y"]) for item in result["point_ids"]},
                         {(0, 4), (0, 0), (3, 0), (3, 4)})

    def test_rotated_rectangle(self) -> None:
        result = self.command({"type": "add_sketch_rotated_rectangle", "sketch_id": self.sketch_id,
                              "center_x": 10, "center_y": 20, "width": 4, "height": 2,
                              "angle_degrees": 90})
        objects = self.objects()
        corners = {(round(objects[item]["x"], 8), round(objects[item]["y"], 8)) for item in result["point_ids"]}
        self.assertEqual(corners, {(11, 18), (11, 22), (9, 22), (9, 18)})

    def test_parallelogram(self) -> None:
        result = self.command({"type": "add_sketch_parallelogram", "sketch_id": self.sketch_id,
                              "points": [{"x": 1, "y": 4}, {"x": 0, "y": 0}, {"x": 5, "y": 1}]})
        objects = self.objects()
        self.assertIn((6, 5), {(objects[item]["x"], objects[item]["y"]) for item in result["point_ids"]})

    def test_regular_polygon(self) -> None:
        result = self.command({"type": "add_sketch_regular_polygon", "sketch_id": self.sketch_id,
                              "center_x": 3, "center_y": 4, "side_count": 5, "radius": 2,
                              "radius_mode": "circumscribed", "rotation_degrees": 30})
        self.assertEqual(len(result["point_ids"]), 5)
        self.assertEqual(len(result["line_ids"]), 5)

    def test_circle_center_radius(self) -> None:
        result = self.command({"type": "add_sketch_circle", "sketch_id": self.sketch_id,
                              "x": 2, "y": 3, "radius": 4})
        self.assertEqual(self.objects()[result["circle_id"]]["radius"], 4)

    def test_circle_center_diameter(self) -> None:
        result = self.command({"type": "add_sketch_circle_diameter", "sketch_id": self.sketch_id,
                              "x": -2, "y": 1, "diameter": 8})
        circle = self.objects()[result["circle_id"]]
        self.assertEqual(circle["radius"], 4)

    def test_circle_two_points(self) -> None:
        result = self.command({"type": "add_sketch_circle_two_points", "sketch_id": self.sketch_id,
                              "first": {"x": 0, "y": 0}, "second": {"x": 4, "y": 0}})
        objects = self.objects()
        circle = objects[result["circle_id"]]
        center = objects[circle["center_id"]]
        self.assertEqual((center["x"], center["y"], circle["radius"]), (2, 0, 2))

    def test_circle_three_points(self) -> None:
        result = self.command({"type": "add_sketch_circle_three_points", "sketch_id": self.sketch_id,
                              "first": {"x": 100, "y": 100}, "second": {"x": 102, "y": 100},
                              "third": {"x": 100, "y": 102}})
        objects = self.objects()
        circle = objects[result["circle_id"]]
        center = objects[circle["center_id"]]
        self.assertAlmostEqual(center["x"], 101)
        self.assertAlmostEqual(center["y"], 101)
        self.assertAlmostEqual(circle["radius"], 2**0.5)
