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

    def line_tool(self, kind: str, *, start=None, end=None) -> dict:
        return self.command({"type": kind, "sketch_id": self.sketch_id,
                             "start": start or {"x": 0, "y": 0},
                             "end": end or {"x": 4, "y": 3}})

    def test_regular_point(self) -> None:
        result = self.command({"type": "add_sketch_point", "sketch_id": self.sketch_id, "x": 2, "y": -3})
        self.assertEqual(self.objects()[result["point_id"]]["x"], 2)
        self.assertNotIn("construction", self.objects()[result["point_id"]])

    def test_construction_point(self) -> None:
        result = self.command({"type": "add_sketch_construction_point", "sketch_id": self.sketch_id,
                              "x": -2, "y": 5})
        self.assertTrue(self.objects()[result["point_id"]]["construction"])

    def test_segment(self) -> None:
        result = self.line_tool("add_sketch_segment")
        objects = self.objects()
        self.assertEqual(len(result["line_ids"]), 1)
        self.assertEqual(objects[result["line_id"]].get("extent", "segment"), "segment")

    def test_line(self) -> None:
        result = self.line_tool("add_sketch_line")
        self.assertEqual(self.objects()[result["line_id"]].get("extent", "segment"), "segment")

    def test_infinite_line(self) -> None:
        result = self.line_tool("add_sketch_infinite_line")
        self.assertEqual(self.objects()[result["line_id"]]["extent"], "infinite")

    def test_centerline(self) -> None:
        result = self.line_tool("add_sketch_centerline")
        self.assertEqual(self.objects()[result["line_id"]]["extent"], "centerline")

    def test_construction_line(self) -> None:
        result = self.line_tool("add_sketch_construction_line")
        objects = self.objects()
        self.assertTrue(objects[result["line_id"]]["construction"])
        self.assertTrue(all(objects[point_id]["construction"] for point_id in result["point_ids"]))

    def test_open_polyline(self) -> None:
        result = self.command({"type": "add_sketch_polyline", "sketch_id": self.sketch_id,
                              "points": [{"x": 0, "y": 0}, {"x": 2, "y": 1}, {"x": 4, "y": 0}]})
        self.assertFalse(result["closed"])
        self.assertEqual(len(result["line_ids"]), 2)

    def test_line_chain(self) -> None:
        result = self.command({"type": "add_sketch_line_chain", "sketch_id": self.sketch_id,
                              "points": [{"x": 1, "y": 2}, {"x": 3, "y": 2}, {"x": 3, "y": 5}]})
        self.assertFalse(result["closed"])
        self.assertEqual(len(result["line_ids"]), 2)

    def test_rectangle_two_corners(self) -> None:
        result = self.command({"type": "add_sketch_rectangle_two_corners", "sketch_id": self.sketch_id,
                              "x1": 5, "y1": 7, "x2": 1, "y2": 2})
        self.assertEqual(len(result["line_ids"]), 4)
        self.assertEqual(len(result["point_ids"]), 4)

    def test_rounded_rectangle(self) -> None:
        result = self.command({"type": "add_sketch_rounded_rectangle", "sketch_id": self.sketch_id,
                              "x": 1, "y": 2, "width": 20, "height": 12, "corner_radius": 3})
        objects = self.objects()
        arcs = [objects[item] for item in result["circle_ids"]]
        self.assertEqual(len(result["line_ids"]), 4)
        self.assertTrue(result["closed"])
        self.assertTrue(all(arc["radius"] == 3 and abs(arc["sweep_radians"]) == 3.141592653589793 / 2
                            for arc in arcs))

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

    def test_inscribed_polygon(self) -> None:
        result = self.command({"type": "add_sketch_inscribed_polygon", "sketch_id": self.sketch_id,
                              "center_x": 0, "center_y": 0, "side_count": 4, "radius": 2})
        objects = self.objects()
        vertices = [objects[item] for item in result["point_ids"]]
        self.assertTrue(all(abs((item["x"] ** 2 + item["y"] ** 2) ** 0.5 - 2) < 1e-9
                            for item in vertices))

    def test_circumscribed_polygon(self) -> None:
        result = self.command({"type": "add_sketch_circumscribed_polygon", "sketch_id": self.sketch_id,
                              "center_x": 0, "center_y": 0, "side_count": 4, "radius": 2})
        objects = self.objects()
        vertices = [objects[item] for item in result["point_ids"]]
        self.assertTrue(all(abs(max(abs(item["x"]), abs(item["y"])) - 2 / (2**0.5 / 2)) < 1e-9
                            for item in vertices))

    def test_polygon_from_center(self) -> None:
        result = self.command({"type": "add_sketch_polygon_from_center", "sketch_id": self.sketch_id,
                              "center_x": 2, "center_y": -1,
                              "first_vertex": {"x": 2, "y": 4}, "side_count": 5})
        objects = self.objects()
        first = objects[result["point_ids"][0]]
        self.assertAlmostEqual(first["x"], 2)
        self.assertAlmostEqual(first["y"], 4)
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
