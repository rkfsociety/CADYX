"""Focused document, rendering, and profile checks for persisted advanced splines."""

from __future__ import annotations

import math
import tempfile
import unittest
from uuid import uuid4

from cadyx.core import CadyxCore
from cadyx.errors import ValidationError
from cadyx.models import Document
from cadyx.rendering import render_scene
from cadyx.sketch_curve_tools import evaluate_interpolating_spline
from cadyx.sketches import closed_profile, spline_sample_points


def make_spline_document(points: list[tuple[float, float]], *, mode: str,
                         spline_values: dict | None = None,
                         closing_lines: bool = False) -> tuple[Document, str]:
    document = Document.create()
    sketch_id = f"sketch:{uuid4()}"
    point_ids = [f"point:{uuid4()}" for _ in points]
    spline_id = f"spline:{uuid4()}"
    objects = ([{"id": sketch_id, "type": "sketch", "name": "Spline", "plane": "XY"}]
               + [{"id": point_id, "type": "point", "sketch_id": sketch_id, "x": x, "y": y}
                  for point_id, (x, y) in zip(point_ids, points)])
    if closing_lines:
        # These two lines close an open NURBS arc to the origin.
        origin_id = f"point:{uuid4()}"
        objects.append({"id": origin_id, "type": "point", "sketch_id": sketch_id, "x": 0.0, "y": 0.0})
        objects.extend([
            {"id": f"line:{uuid4()}", "type": "line", "sketch_id": sketch_id,
             "start_id": point_ids[-1], "end_id": origin_id},
            {"id": f"line:{uuid4()}", "type": "line", "sketch_id": sketch_id,
             "start_id": origin_id, "end_id": point_ids[0]},
        ])
    spline = {"id": spline_id, "type": "spline", "sketch_id": sketch_id,
              "point_ids": point_ids, "mode": mode}
    if spline_values:
        spline.update(spline_values)
    objects.append(spline)
    document.objects = objects
    return document, spline_id


class PersistedSketchCurveTests(unittest.TestCase):
    def test_nurbs_document_renders_rational_quarter_circle_and_closes_profile(self):
        weight = math.sqrt(0.5)
        document, spline_id = make_spline_document(
            [(1, 0), (1, 1), (0, 1)], mode="nurbs",
            spline_values={"degree": 2, "weights": [1, weight, 1], "knots": [0, 0, 0, 1, 1, 1]},
            closing_lines=True,
        )
        document.validate()
        points = {item["id"]: item for item in document.objects}
        sampled = spline_sample_points(points[spline_id], points)
        self.assertEqual(len(sampled), 25)
        self.assertEqual(sampled[0], {"x": 1.0, "y": 0.0})
        self.assertEqual(sampled[-1], {"x": 0.0, "y": 1.0})
        self.assertAlmostEqual(sampled[12]["x"], math.sqrt(0.5))
        self.assertAlmostEqual(sampled[12]["y"], math.sqrt(0.5))

        with tempfile.TemporaryDirectory() as directory:
            core = CadyxCore(directory)
            scene = render_scene(document, core.kernel)
            rendered = next(item for item in scene["objects"] if item["object_id"] == spline_id)
            self.assertEqual(len(rendered["positions"]), 25 * 3)
            self.assertEqual(rendered["positions"][:3], [1.0, 0.0, 0.0])
            self.assertEqual(rendered["positions"][-3:], [0.0, 1.0, 0.0])
            plane, profile = closed_profile(document.objects, document.objects[0]["id"])
            self.assertEqual(plane, "XY")
            self.assertGreater(len(profile), 25)
            geometry = core.kernel.create_extruded_profile(profile, plane=plane, depth=2)
            self.assertGreater(geometry["volume"], 0)
            self.assertIsNone(core.kernel.validate(geometry))

    def test_nurbs_rejects_invalid_weight_degree_and_knot_contracts(self):
        cases = (
            {"degree": True, "weights": [1, 1, 1], "knots": [0, 0, 0, 1, 1, 1]},
            {"degree": 2, "weights": [1, 1], "knots": [0, 0, 0, 1, 1, 1]},
            {"degree": 2, "weights": [1, 0, 1], "knots": [0, 0, 0, 1, 1, 1]},
            {"degree": 2, "weights": [1, float("inf"), 1], "knots": [0, 0, 0, 1, 1, 1]},
            {"degree": 2, "weights": [1, 1, 1], "knots": [0, 0, 0, 0, 1]},
            {"degree": 2, "weights": [1, 1, 1], "knots": [0, 0, 1, 0, 1, 1]},
            {"degree": 2, "weights": [1, 1, 1], "knots": [0, 0, 0, 0, 0, 0]},
            {"degree": 2, "weights": [1, 1, 1], "knots": [float("nan"), 0, 0, 1, 1, 1]},
        )
        for values in cases:
            with self.subTest(values=values):
                document, _ = make_spline_document([(0, 0), (1, 1), (2, 0)],
                                                   mode="nurbs", spline_values=values)
                with self.assertRaises(ValidationError):
                    document.validate()

    def test_nurbs_rejects_knot_multiplicity_above_degree_plus_one(self):
        points = [(0, 0), (1, 1), (2, 0), (3, 1), (4, 0)]
        document, _ = make_spline_document(
            points, mode="nurbs", spline_values={
                "degree": 2, "weights": [1] * 5,
                "knots": [0, 0, 0, 0.5, 0.5, 0.5, 0.5, 1],
            },
        )
        with self.assertRaises(ValidationError):
            document.validate()

    def test_closed_interpolating_spline_renders_and_extracts_a_profile(self):
        vertices = [(10, 0), (3, 9), (-8, 6), (-8, -6), (3, -9)]
        document, spline_id = make_spline_document(vertices, mode="closed")
        document.validate()
        by_id = {item["id"]: item for item in document.objects}
        sampled = spline_sample_points(by_id[spline_id], by_id)
        self.assertEqual(len(sampled), len(vertices) * 24 + 1)
        self.assertEqual(sampled[0], sampled[-1])
        for index, vertex in enumerate(vertices):
            self.assertEqual(sampled[index * 24], {"x": float(vertex[0]), "y": float(vertex[1])})

        with tempfile.TemporaryDirectory() as directory:
            core = CadyxCore(directory)
            scene = render_scene(document, core.kernel)
            rendered = next(item for item in scene["objects"] if item["object_id"] == spline_id)
            self.assertEqual(len(rendered["positions"]), len(sampled) * 3)
            self.assertEqual(len(rendered["lines"]), (len(sampled) - 1) * 2)
            self.assertEqual(rendered["positions"][:3], rendered["positions"][-3:])
            plane, profile = closed_profile(document.objects, document.objects[0]["id"])
            self.assertEqual(plane, "XY")
            self.assertEqual(profile, sampled[:-1])

    def test_closed_interpolating_spline_uses_centripetal_spacing(self):
        vertices = [(0, 0), (4, 0), (4, 1), (0, 3)]
        document, spline_id = make_spline_document(vertices, mode="closed")
        document.validate()
        by_id = {item["id"]: item for item in document.objects}
        sampled = spline_sample_points(by_id[spline_id], by_id)
        uniform_sample = evaluate_interpolating_spline(vertices + [vertices[0]], 0.5, closed=True)
        self.assertNotAlmostEqual(sampled[12]["x"], uniform_sample[0])
        self.assertNotAlmostEqual(sampled[12]["y"], uniform_sample[1])

    def test_closed_spline_rejects_duplicate_wrap_vertex_and_zero_area(self):
        document, _ = make_spline_document([(0, 0), (2, 0), (0, 2), (0, 0)], mode="closed")
        with self.assertRaises(ValidationError):
            document.validate()
        document, _ = make_spline_document([(0, 0), (1, 0), (2, 0)], mode="closed")
        with self.assertRaises(ValidationError):
            document.validate()


if __name__ == "__main__":
    unittest.main()
