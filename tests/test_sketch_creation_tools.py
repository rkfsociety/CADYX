from __future__ import annotations

import math
import tempfile
import unittest

from cadyx.core import CadyxCore
from cadyx.errors import ValidationError
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

    def test_ellipse_major_axis_and_minor_radius(self) -> None:
        result = self.command({"type": "add_sketch_ellipse", "sketch_id": self.sketch_id,
                               "major_axis_start": {"x": -3, "y": 2},
                               "major_axis_end": {"x": 5, "y": 2}, "minor_radius": 2})
        ellipse = self.objects()[result["ellipse_id"]]
        self.assertEqual((ellipse["center_x"], ellipse["center_y"]), (1, 2))
        self.assertEqual((ellipse["major_radius"], ellipse["minor_radius"]), (4, 2))
        self.assertEqual(ellipse["rotation_radians"], 0)

    def test_center_ellipse_with_rotation_and_validation(self) -> None:
        result = self.command({"type": "add_sketch_ellipse_center", "sketch_id": self.sketch_id,
                               "center": {"x": -2, "y": 3}, "major_radius": 5,
                               "minor_radius": 2, "rotation_degrees": 30})
        ellipse = self.objects()[result["ellipse_id"]]
        self.assertAlmostEqual(ellipse["rotation_radians"], math.pi / 6)
        with self.assertRaises(ValidationError):
            self.command({"type": "add_sketch_ellipse_center", "sketch_id": self.sketch_id,
                          "center": {"x": 0, "y": 0}, "major_radius": 2, "minor_radius": 3})

    def test_ellipse_can_be_mirrored_as_sketch_geometry(self) -> None:
        source = self.command({"type": "add_sketch_ellipse_center", "sketch_id": self.sketch_id,
                               "center": {"x": 2, "y": 3}, "major_radius": 5,
                               "minor_radius": 2, "rotation_degrees": 30})["ellipse_id"]
        result = self.command({"type": "mirror_sketch_elements", "sketch_id": self.sketch_id,
                               "object_ids": [source], "axis_start": {"x": 0, "y": 0},
                               "axis_end": {"x": 1, "y": 0}})
        self.assertEqual(len(result["ellipse_ids"]), 1)
        clone = self.objects()[result["ellipse_ids"][0]]
        self.assertAlmostEqual(clone["center_y"], -3)
        self.assertAlmostEqual(clone["rotation_radians"], -math.pi / 6)
        pattern = self.command({"type": "pattern_sketch_elements", "sketch_id": self.sketch_id,
                                "object_ids": [source], "mode": "circular", "count": 2,
                                "center_x": 0, "center_y": 0, "angle_step_degrees": 90})
        rotated = self.objects()[pattern["ellipse_ids"][0]]
        self.assertAlmostEqual(rotated["center_x"], -3)
        self.assertAlmostEqual(rotated["center_y"], 2)
        self.assertAlmostEqual(rotated["rotation_radians"], 2 * math.pi / 3)

    def test_ellipse_three_points_define_center_and_perpendicular_axes(self) -> None:
        result = self.command({"type": "add_sketch_ellipse_three_points", "sketch_id": self.sketch_id,
                               "center": {"x": 1, "y": -2},
                               "major_axis_endpoint": {"x": 5, "y": 1},
                               "minor_axis_endpoint": {"x": -0.5, "y": 0}})
        ellipse = self.objects()[result["ellipse_id"]]
        self.assertEqual((ellipse["center_x"], ellipse["center_y"]), (1, -2))
        self.assertEqual((ellipse["major_radius"], ellipse["minor_radius"]), (5, 2.5))
        self.assertAlmostEqual(ellipse["rotation_radians"], math.atan2(3, 4))
        before = len(self.objects())
        with self.assertRaises(ValidationError):
            self.command({"type": "add_sketch_ellipse_three_points", "sketch_id": self.sketch_id,
                          "center": {"x": 0, "y": 0},
                          "major_axis_endpoint": {"x": 4, "y": 0},
                          "minor_axis_endpoint": {"x": 1, "y": 2}})
        self.assertEqual(len(self.objects()), before)

    def test_circle_tangent_to_line_at_point(self) -> None:
        line = self.line_tool("add_sketch_line", start={"x": 0, "y": 0}, end={"x": 10, "y": 0})
        result = self.command({"type": "add_sketch_circle_tangent_to_line", "sketch_id": self.sketch_id,
                               "line_id": line["line_id"], "tangent_point": {"x": 4, "y": 0},
                               "radius": 2})
        objects = self.objects()
        circle = objects[result["circle_id"]]
        center = objects[circle["center_id"]]
        self.assertEqual((center["x"], center["y"], circle["radius"]), (4, 2, 2))
        self.assertEqual(result["circle_ids"], [result["circle_id"]])
        self.command({"type": "add_sketch_circle_tangent_to_line", "sketch_id": self.sketch_id,
                      "line_id": line["line_id"], "tangent_point": {"x": 4, "y": 0},
                      "radius": 2, "side": -1})
        diagonal = self.line_tool("add_sketch_line", start={"x": 0, "y": 0}, end={"x": 3, "y": 4})
        rotated = self.command({"type": "add_sketch_circle_tangent_to_line", "sketch_id": self.sketch_id,
                                "line_id": diagonal["line_id"], "tangent_point": {"x": 1.2, "y": 1.6},
                                "radius": 2})
        center = self.objects()[rotated["circle_id"]]["center_id"]
        center_point = self.objects()[center]
        self.assertAlmostEqual(center_point["x"], -0.4)
        self.assertAlmostEqual(center_point["y"], 2.8)
        for tangent_point in ({"x": 4, "y": 1}, {"x": 11, "y": 0}):
            with self.subTest(tangent_point=tangent_point), self.assertRaises(ValidationError):
                self.command({"type": "add_sketch_circle_tangent_to_line", "sketch_id": self.sketch_id,
                              "line_id": line["line_id"], "tangent_point": tangent_point,
                              "radius": 2})

    def test_circle_tangent_to_two_lines(self) -> None:
        horizontal = self.line_tool("add_sketch_line", start={"x": -10, "y": 0}, end={"x": 0, "y": 0})
        vertical = self.line_tool("add_sketch_line", start={"x": 0, "y": 0}, end={"x": 0, "y": 10})
        result = self.command({"type": "add_sketch_circle_tangent_to_two_lines", "sketch_id": self.sketch_id,
                              "first_line_id": horizontal["line_id"], "second_line_id": vertical["line_id"],
                              "radius": 2})
        objects = self.objects()
        circle = objects[result["circle_id"]]
        center = objects[circle["center_id"]]
        self.assertEqual((center["x"], center["y"], circle["radius"]), (-2, 2, 2))
        self.assertEqual(result["tangent_points"], [{"x": -2.0, "y": 0.0}, {"x": 0.0, "y": 2.0}])
        self.assertEqual(result["circle_ids"], [result["circle_id"]])

        parallel = self.line_tool("add_sketch_line", start={"x": -10, "y": 5}, end={"x": 0, "y": 5})
        with self.assertRaises(ValidationError):
            self.command({"type": "add_sketch_circle_tangent_to_two_lines", "sketch_id": self.sketch_id,
                          "first_line_id": horizontal["line_id"], "second_line_id": parallel["line_id"],
                          "radius": 2})

    def test_circle_tangent_to_three_lines(self) -> None:
        root_two = 2 ** 0.5
        horizontal = self.line_tool("add_sketch_line", start={"x": -10, "y": 0}, end={"x": 10, "y": 0})
        vertical = self.line_tool("add_sketch_line", start={"x": 0, "y": -10}, end={"x": 0, "y": 10})
        tangent = {"x": -2 - root_two, "y": 2 - root_two}
        diagonal = self.line_tool("add_sketch_line",
                                  start={"x": tangent["x"] - 10 / root_two,
                                         "y": tangent["y"] + 10 / root_two},
                                  end={"x": tangent["x"] + 10 / root_two,
                                       "y": tangent["y"] - 10 / root_two})
        result = self.command({"type": "add_sketch_circle_tangent_to_three_lines", "sketch_id": self.sketch_id,
                               "first_line_id": horizontal["line_id"],
                               "second_line_id": vertical["line_id"],
                               "third_line_id": diagonal["line_id"]})
        objects = self.objects()
        circle = objects[result["circle_id"]]
        center = objects[circle["center_id"]]
        self.assertAlmostEqual(center["x"], -2)
        self.assertAlmostEqual(center["y"], 2)
        self.assertAlmostEqual(circle["radius"], 2)
        self.assertEqual(len(result["tangent_points"]), 3)
        self.assertAlmostEqual(result["tangent_points"][0]["x"], -2)
        self.assertAlmostEqual(result["tangent_points"][1]["y"], 2)

        concurrent = self.line_tool("add_sketch_line", start={"x": -10, "y": -10}, end={"x": 10, "y": 10})
        with self.assertRaises(ValidationError):
            self.command({"type": "add_sketch_circle_tangent_to_three_lines", "sketch_id": self.sketch_id,
                          "first_line_id": horizontal["line_id"], "second_line_id": vertical["line_id"],
                          "third_line_id": concurrent["line_id"]})

    def test_arc_center_rejects_different_endpoint_radii(self) -> None:
        with self.assertRaises(ValidationError):
            self.command({"type": "add_sketch_arc_center", "sketch_id": self.sketch_id,
                          "center": {"x": 0, "y": 0}, "start": {"x": 5, "y": 0},
                          "end": {"x": 0, "y": 4}})

    def test_arc_center(self) -> None:
        result = self.command({"type": "add_sketch_arc_center", "sketch_id": self.sketch_id,
                               "center": {"x": 0, "y": 0}, "start": {"x": 5, "y": 0},
                               "end": {"x": 0, "y": 5}})
        arc = self.objects()[result["arc_id"]]
        self.assertAlmostEqual(arc["radius"], 5)
        self.assertAlmostEqual(arc["sweep_radians"], 3.141592653589793 / 2)
        clockwise = self.command({"type": "add_sketch_arc_center", "sketch_id": self.sketch_id,
                                  "center": {"x": 0, "y": 0}, "start": {"x": 5, "y": 0},
                                  "end": {"x": 0, "y": -5}, "clockwise": True})
        self.assertAlmostEqual(self.objects()[clockwise["arc_id"]]["sweep_radians"], -3.141592653589793 / 2)

    def test_arc_three_points(self) -> None:
        result = self.command({"type": "add_sketch_arc_three_points", "sketch_id": self.sketch_id,
                               "start": {"x": 1, "y": 0}, "through": {"x": 0, "y": 1},
                               "end": {"x": -1, "y": 0}})
        arc = self.objects()[result["arc_id"]]
        center = self.objects()[arc["center_id"]]
        self.assertAlmostEqual(center["x"], 0)
        self.assertAlmostEqual(center["y"], 0)
        self.assertAlmostEqual(arc["radius"], 1)
        self.assertAlmostEqual(arc["sweep_radians"], 3.141592653589793)
        self.assertEqual(result["through"], {"x": 0, "y": 1})

        clockwise = self.command({"type": "add_sketch_arc_three_points", "sketch_id": self.sketch_id,
                                  "start": {"x": 1, "y": 0}, "through": {"x": 0, "y": -1},
                                  "end": {"x": -1, "y": 0}})
        self.assertAlmostEqual(self.objects()[clockwise["arc_id"]]["sweep_radians"], -3.141592653589793)

    def test_arc_three_points_rejects_collinear_points(self) -> None:
        with self.assertRaises(ValidationError):
            self.command({"type": "add_sketch_arc_three_points", "sketch_id": self.sketch_id,
                          "start": {"x": 0, "y": 0}, "through": {"x": 1, "y": 1},
                          "end": {"x": 2, "y": 2}})

    def test_arc_start_center_end(self) -> None:
        result = self.command({"type": "add_sketch_arc_start_center_end", "sketch_id": self.sketch_id,
                               "start": {"x": 5, "y": 0}, "center": {"x": 0, "y": 0},
                               "end": {"x": 0, "y": 5}})
        self.assertAlmostEqual(self.objects()[result["arc_id"]]["sweep_radians"], 3.141592653589793 / 2)

    def test_arc_radius(self) -> None:
        minor = self.command({"type": "add_sketch_arc_radius", "sketch_id": self.sketch_id,
                              "start": {"x": 0, "y": 0}, "end": {"x": 4, "y": 0}, "radius": 3})
        minor_arc = self.objects()[minor["arc_id"]]
        self.assertAlmostEqual(minor_arc["radius"], 3)
        self.assertLess(abs(minor_arc["sweep_radians"]), 3.141592653589793)
        major = self.command({"type": "add_sketch_arc_radius", "sketch_id": self.sketch_id,
                              "start": {"x": 0, "y": 0}, "end": {"x": 4, "y": 0}, "radius": 3,
                              "large_arc": True, "clockwise": True})
        self.assertLess(self.objects()[major["arc_id"]]["sweep_radians"], -3.141592653589793)
        major_ccw = self.command({"type": "add_sketch_arc_radius", "sketch_id": self.sketch_id,
                                  "start": {"x": 0, "y": 0}, "end": {"x": 4, "y": 0}, "radius": 3,
                                  "large_arc": True})
        self.assertGreater(self.objects()[major_ccw["arc_id"]]["sweep_radians"], 3.141592653589793)
        minor_cw = self.command({"type": "add_sketch_arc_radius", "sketch_id": self.sketch_id,
                                 "start": {"x": 0, "y": 0}, "end": {"x": 4, "y": 0}, "radius": 3,
                                 "clockwise": True})
        self.assertGreater(self.objects()[minor_cw["arc_id"]]["sweep_radians"], -3.141592653589793)
        with self.assertRaises(ValidationError):
            self.command({"type": "add_sketch_arc_radius", "sketch_id": self.sketch_id,
                          "start": {"x": 0, "y": 0}, "end": {"x": 4, "y": 0}, "radius": 1.9})

    def test_arc_tangent(self) -> None:
        line = self.line_tool("add_sketch_line", start={"x": 0, "y": 0}, end={"x": 10, "y": 0})
        result = self.command({"type": "add_sketch_arc_tangent", "sketch_id": self.sketch_id,
                               "line_id": line["line_id"], "tangent_point": {"x": 5, "y": 0},
                               "radius": 2, "sweep_degrees": 90})
        arc = self.objects()[result["arc_id"]]
        center = self.objects()[arc["center_id"]]
        self.assertAlmostEqual(center["x"], 5)
        self.assertAlmostEqual(center["y"], 2)
        self.assertAlmostEqual(arc["sweep_radians"], 3.141592653589793 / 2)
        self.assertAlmostEqual(result["radius"], 2)
        with self.assertRaises(ValidationError):
            self.command({"type": "add_sketch_arc_tangent", "sketch_id": self.sketch_id,
                          "line_id": line["line_id"], "tangent_point": {"x": 5, "y": 1},
                          "radius": 2, "sweep_degrees": 90})
