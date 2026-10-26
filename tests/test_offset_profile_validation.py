from __future__ import annotations

import math
import tempfile
import unittest

from cadyx.core import CadyxCore
from cadyx.models import Document


class OffsetProfileValidationTests(unittest.TestCase):
    def test_offset_xy_extrusion_matches_its_source_sketch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            core = CadyxCore(directory)
            document = Document.create()
            sketch = core.commands.execute(document, {
                "type": "create_sketch", "name": "Смещённый профиль",
                "plane": "XY", "section_normal_axis": "Z", "section_offset": -91,
            }, project_id="offset-profile-test")["sketch_id"]
            core.commands.execute(document, {
                "type": "add_sketch_polyline", "sketch_id": sketch, "closed": True,
                "points": [
                    {"x": 0, "y": 0}, {"x": 4, "y": 0},
                    {"x": 4, "y": 3}, {"x": 0, "y": 3},
                ],
            }, project_id="offset-profile-test")
            core.commands.execute(document, {
                "type": "extrude_sketch", "sketch_id": sketch,
                "name": "Смещённое тело", "depth": 2,
            }, project_id="offset-profile-test")

            document.validate()
            core._validate_geometry(document)
            body = next(item for item in document.objects if item.get("type") == "body"
                        and item.get("name") == "Смещённое тело")
            bounds = core.kernel.metrics(body["geometry"])["bounds"]
            self.assertAlmostEqual(bounds["min"][2], -91.0, delta=1e-6)
            self.assertAlmostEqual(bounds["max"][2], -89.0, delta=1e-6)


    def test_editing_major_arc_sweep_as_two_minor_arcs_restores_extrusion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            core = CadyxCore(directory)
            document = Document.create()
            project_id = "editable-arc-test"
            sketch = core.commands.execute(document, {
                "type": "create_sketch", "name": "Профиль с выемкой", "plane": "XY",
            }, project_id=project_id)["sketch_id"]
            core.commands.execute(document, {
                "type": "add_sketch_line_chain", "sketch_id": sketch,
                "points": [
                    {"x": 0, "y": 0}, {"x": 40, "y": 0},
                    {"x": 40, "y": 30}, {"x": 26, "y": 30},
                    {"x": 26, "y": 23},
                ],
            }, project_id=project_id)
            major_arc = core.commands.execute(document, {
                "type": "add_sketch_arc_center", "sketch_id": sketch,
                "center": {"x": 20, "y": 15},
                "start": {"x": 26, "y": 23}, "end": {"x": 14, "y": 23},
                "clockwise": True,
            }, project_id=project_id)
            core.commands.execute(document, {
                "type": "add_sketch_line_chain", "sketch_id": sketch,
                "points": [
                    {"x": 14, "y": 23}, {"x": 14, "y": 30},
                    {"x": 0, "y": 30}, {"x": 0, "y": 0},
                ],
            }, project_id=project_id)

            core.commands.execute(document, {
                "type": "update_sketch_elements",
                "updates": [
                    {"object_id": major_arc["point_ids"][2],
                     "changes": {"x": 20, "y": 5}},
                    {"object_id": major_arc["arc_id"],
                     "changes": {"sweep_radians": -2.498091544796509}},
                ],
            }, project_id=project_id)
            core.commands.execute(document, {
                "type": "add_sketch_arc_center", "sketch_id": sketch,
                "center": {"x": 20, "y": 15},
                "start": {"x": 20, "y": 5}, "end": {"x": 14, "y": 23},
                "clockwise": True,
            }, project_id=project_id)
            result = core.commands.execute(document, {
                "type": "extrude_sketch", "sketch_id": sketch,
                "name": "Проверочная деталь", "depth": 2,
            }, project_id=project_id)

            document.validate()
            core._validate_geometry(document)
            self.assertGreater(result["geometry"]["volume"], 0)


    def test_circular_arc_sweep_accepts_a_named_parameter_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            core = CadyxCore(directory)
            document = Document.create()
            project_id = "arc-sweep-binding-test"
            core.commands.execute(document, {
                "type": "create_model_parameter", "name": "ArcSweep",
                "value": -1.5707963267948966, "unit": "unitless",
            }, project_id=project_id)
            sketch = core.commands.execute(document, {
                "type": "create_sketch", "name": "Параметр дуги", "plane": "XY",
            }, project_id=project_id)["sketch_id"]
            arc = core.commands.execute(document, {
                "type": "add_sketch_arc_center", "sketch_id": sketch,
                "center": {"x": 0, "y": 0},
                "start": {"x": 5, "y": 0}, "end": {"x": 0, "y": -5},
                "clockwise": True,
            }, project_id=project_id)
            core.commands.execute(document, {
                "type": "bind_model_parameter", "object_id": arc["arc_id"],
                "path": "sweep_radians", "expression": "ArcSweep",
            }, project_id=project_id)

            document.validate()
            saved_arc = next(item for item in document.objects if item["id"] == arc["arc_id"])
            self.assertEqual(saved_arc["sweep_radians"], -1.5707963267948966)
            self.assertEqual(document.parameter_bindings[-1]["path"], "sweep_radians")

    def test_two_notch_cap_profile_extrudes_with_and_without_mounting_holes(self) -> None:
        for include_holes in (False, True):
            with self.subTest(include_holes=include_holes), tempfile.TemporaryDirectory() as directory:
                core = CadyxCore(directory)
                document = Document.create()
                project_id = "two-notch-profile-test"
                sketch = core.commands.execute(document, {
                    "type": "create_sketch", "name": "Крышка с выемками", "plane": "XY",
                }, project_id=project_id)["sketch_id"]

                def chain(points):
                    core.commands.execute(document, {
                        "type": "add_sketch_line_chain", "sketch_id": sketch,
                        "points": [{"x": x, "y": y} for x, y in points],
                    }, project_id=project_id)

                def arc(center, start, end):
                    core.commands.execute(document, {
                        "type": "add_sketch_arc_center", "sketch_id": sketch,
                        "center": {"x": center[0], "y": center[1]},
                        "start": {"x": start[0], "y": start[1]},
                        "end": {"x": end[0], "y": end[1]}, "clockwise": True,
                    }, project_id=project_id)

                chord_y = 19.5 + math.sqrt(51)
                chain([(0, 0), (0, 34), (-11.375, 34), (-11.375, chord_y)])
                arc((-18.375, 19.5), (-11.375, chord_y), (-18.375, 9.5))
                arc((-18.375, 19.5), (-18.375, 9.5), (-25.375, chord_y))
                chain([(-25.375, chord_y), (-25.375, 34), (-60.625, 34), (-60.625, chord_y)])
                arc((-67.625, 19.5), (-60.625, chord_y), (-67.625, 9.5))
                arc((-67.625, 19.5), (-67.625, 9.5), (-74.625, chord_y))
                chain([(-74.625, chord_y), (-74.625, 34), (-86, 34), (-86, 0), (0, 0)])
                if include_holes:
                    for x in (-78.475, -56.775, -29.225, -7.525):
                        for y in (8.65, 30.35):
                            core.commands.execute(document, {
                                "type": "add_sketch_circle", "sketch_id": sketch,
                                "x": x, "y": y, "radius": 1.65,
                            }, project_id=project_id)

                feature = core.commands.execute(document, {
                    "type": "extrude_sketch", "sketch_id": sketch,
                    "name": "Проверочная крышка", "depth": 2,
                }, project_id=project_id)
                document.validate()
                core._validate_geometry(document)
                self.assertGreater(feature["geometry"]["volume"], 0)


if __name__ == "__main__":
    unittest.main()
