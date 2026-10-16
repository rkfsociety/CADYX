from __future__ import annotations

import math
import tempfile
import unittest

from cadyx.core import CadyxCore
from cadyx.errors import ValidationError
from cadyx.kernel import _loft_contour_signature, _match_loft_contours
from cadyx.models import Document


class ProfileFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.core = CadyxCore(self.temporary.name)
        self.project_id = self.core.create_project("Profile features")["project"]["id"]
        self.lease_id = self.core.acquire_lease(self.project_id, "agent", "profile-test")["lease_id"]

    def command(self, value: dict) -> dict:
        return self.core.execute_command(self.project_id, self.lease_id, value)["history_entry"]["result"]

    def test_ruled_shell_command_sews_six_patches_into_one_solid(self) -> None:
        def rail(start, end):
            return [list(start), list(end)]

        x, y, z = 4.0, 3.0, 2.0
        patches = [
            {"first": rail((0, 0, 0), (x, 0, 0)), "second": rail((0, y, 0), (x, y, 0))},
            {"first": rail((0, 0, z), (x, 0, z)), "second": rail((0, y, z), (x, y, z))},
            {"first": rail((0, 0, 0), (x, 0, 0)), "second": rail((0, 0, z), (x, 0, z))},
            {"first": rail((0, y, 0), (x, y, 0)), "second": rail((0, y, z), (x, y, z))},
            {"first": rail((0, 0, 0), (0, y, 0)), "second": rail((0, 0, z), (0, y, z))},
            {"first": rail((x, 0, 0), (x, y, 0)), "second": rail((x, 0, z), (x, y, z))},
        ]
        result = self.command({"type": "create_ruled_shell", "name": "Sewn ruled fixture",
                               "patches": patches, "sewing_tolerance": 1e-3})
        self.assertEqual(result["geometry"]["kind"], "ruled_shell")
        self.assertAlmostEqual(result["geometry"]["volume"], 24.0, delta=1e-6)
        topology = self.core.kernel.topology(result["geometry"])
        self.assertTrue(topology["brep_valid"])
        self.assertEqual(topology["topology"]["solid_count"], 1)
        for actual, expected in zip(topology["bounds"]["min"], [0.0, 0.0, 0.0], strict=True):
            self.assertAlmostEqual(actual, expected, delta=1e-6)
        for actual, expected in zip(topology["bounds"]["max"], [x, y, z], strict=True):
            self.assertAlmostEqual(actual, expected, delta=1e-6)

    def closed_sketch(self, name: str, points: list[tuple[float, float]], plane: str = "XZ") -> tuple[str, list[str]]:
        sketch_id = self.command({"type": "create_sketch", "name": name, "plane": plane})["sketch_id"]
        point_ids = [self.command({"type": "add_sketch_element", "sketch_id": sketch_id,
                                   "element_type": "point", "x": x, "y": y})["object_id"]
                     for x, y in points]
        for index, point_id in enumerate(point_ids):
            self.command({"type": "add_sketch_element", "sketch_id": sketch_id,
                          "element_type": "line", "start_id": point_id,
                          "end_id": point_ids[(index + 1) % len(point_ids)]})
        return sketch_id, point_ids

    def test_revolution_and_extrusion_make_editable_valid_breps(self) -> None:
        bowl_sketch, bowl_points = self.closed_sketch(
            "Сечение чаши", [(0, 0), (32, 0), (32, 80), (28, 80), (28, 5), (0, 5)],
        )
        bowl = self.command({"type": "revolve_sketch", "sketch_id": bowl_sketch,
                             "name": "Чаша", "angle_degrees": 360})
        geometry = bowl["geometry"]
        expected_volume = math.pi * (32**2 * 5 + (32**2 - 28**2) * 75)
        self.assertEqual(geometry["kernel"], "occt-v1")
        self.assertEqual(geometry["kind"], "revolved_profile")
        self.assertAlmostEqual(geometry["volume"], expected_volume, delta=0.1)
        self.assertEqual(len(self.core.kernel.tessellate(geometry)["triangles"]) % 3, 0)

        handle_sketch, _ = self.closed_sketch(
            "Контур ручки",
            [(31, 60), (42, 60), (52, 55), (60, 47), (65, 40), (60, 33),
             (52, 25), (42, 20), (31, 20), (31, 26), (42, 26), (50, 29),
             (57, 35), (59, 40), (57, 45), (50, 51), (42, 54), (31, 54)],
        )
        handle = self.command({"type": "extrude_sketch", "sketch_id": handle_sketch,
                               "name": "Ручка", "depth": 12})
        self.assertEqual(handle["geometry"]["kind"], "extruded_profile")
        self.assertGreater(handle["geometry"]["volume"], 0)

        document = self.core.inspect_project(self.project_id)["document"]
        self.assertEqual(document["version"], 8)
        self.assertEqual(Document.from_dict(document).to_dict(), document)
        features = [item for item in document["objects"] if item["type"] == "feature"]
        self.assertEqual([item["operation"] for item in features], ["revolve_sketch", "extrude_sketch"])
        self.assertEqual(features[0]["source_sketch_id"], bowl_sketch)
        self.assertEqual(self.core.render_project(self.project_id)["bounds"]["max"][2], 80)

        before_volume = geometry["volume"]
        self.command({"type": "update_sketch_elements", "updates": [
            {"object_id": bowl_points[1], "changes": {"x": 34}},
        ]})
        rebuilt = self.core.inspect_project(self.project_id)["document"]
        rebuilt_body = next(item for item in rebuilt["objects"] if item.get("id") == bowl["body_id"])
        self.assertGreater(rebuilt_body["geometry"]["volume"], before_volume)
        rebuilt_feature = next(item for item in rebuilt["objects"] if item.get("id") == bowl["feature_id"])
        self.assertEqual(rebuilt_feature["geometry"], rebuilt_body["geometry"])
        with self.assertRaises(ValidationError):
            self.command({"type": "remove_sketch_object", "object_id": bowl_sketch})

    def test_loft_is_editable_and_rebuilds_when_a_section_changes(self) -> None:
        section_ids = []
        section_points = []
        for y, radius, sample_count in ((-2.0, 2.0, 16), (0.0, 3.0, 24), (2.0, 2.0, 32)):
            sketch_id = self.command({"type": "create_sketch", "name": f"Сечение {y}",
                                      "plane": "XZ"})["sketch_id"]
            points = [(radius * math.cos(math.tau * index / sample_count),
                       radius * math.sin(math.tau * index / sample_count)) for index in range(sample_count)]
            result = self.command({"type": "add_sketch_polyline", "sketch_id": sketch_id,
                                   "points": [{"x": x, "y": z} for x, z in points], "closed": True})
            section_ids.append(sketch_id)
            section_points.append(result["point_ids"])
        document = self.core.inspect_project(self.project_id)["document"]
        sketch_offsets = dict(zip(section_ids, (-2.0, 0.0, 2.0), strict=True))
        for sketch in (item for item in document["objects"] if item["id"] in section_ids):
            sketch.update(section_normal_axis="Y", section_offset=sketch_offsets[sketch["id"]])
        from cadyx.models import Document as ModelDocument
        model_document = ModelDocument.from_dict(document)
        result = self.core.commands.execute(model_document, {"type": "loft_sketches", "sketch_ids": section_ids,
                                                            "name": "Проверочный loft"}, project_id=self.project_id)
        loft = result
        geometry = loft["geometry"]
        self.assertEqual(geometry["kind"], "lofted_profiles")
        self.assertGreater(geometry["volume"], 0)
        self.assertIsNone(self.core.kernel.validate(geometry))
        from cadyx.sketches import closed_profile
        legacy_sections = [closed_profile(model_document.objects, sketch_id)[1] for sketch_id in section_ids]
        legacy_geometry = self.core.kernel.create_lofted_profiles(
            legacy_sections, section_offsets=[-2.0, 0.0, 2.0], alignment="independent-start-v1",
        )
        legacy_geometry.pop("profile_alignment")
        loft_body = next(item for item in model_document.objects if item["id"] == loft["body_id"])
        loft_feature = next(item for item in model_document.objects if item["id"] == loft["feature_id"])
        loft_body["geometry"] = loft_feature["geometry"] = legacy_geometry
        self.core._validate_geometry(model_document)
        loft_body["geometry"] = loft_feature["geometry"] = geometry

        shifted_sections = []
        for y, radius, sample_count in ((-2.0, 2.0, 16), (0.0, 3.0, 24), (2.0, 2.0, 32)):
            sketch_id = self.core.commands.execute(model_document, {
                "type": "create_sketch", "name": f"Смещённое сечение {y}", "plane": "XZ",
            }, project_id=self.project_id)["sketch_id"]
            points = [(1.5 + radius * math.cos(math.tau * index / sample_count),
                       radius * math.sin(math.tau * index / sample_count))
                      for index in range(sample_count)]
            self.core.commands.execute(model_document, {
                "type": "add_sketch_polyline", "sketch_id": sketch_id,
                "points": [{"x": x, "y": z} for x, z in points], "closed": True,
            }, project_id=self.project_id)
            next(item for item in model_document.objects if item["id"] == sketch_id).update(
                section_normal_axis="Y", section_offset=y,
            )
            shifted_sections.append(sketch_id)
        shifted = self.core.commands.execute(model_document, {
            "type": "loft_sketches", "sketch_ids": shifted_sections, "name": "Смещённый loft",
        }, project_id=self.project_id)
        fused = self.core.commands.execute(model_document, {
            "type": "fuse_bodies", "base_body_id": loft["body_id"],
            "tool_body_id": shifted["body_id"], "name": "Объединённые loft-тела",
        }, project_id=self.project_id)
        self.assertEqual(fused["geometry"]["kind"], "fused_solids")
        self.assertGreater(fused["geometry"]["volume"], geometry["volume"])
        with self.assertRaises(ValidationError):
            self.core.commands.execute(model_document, {
                "type": "remove_sketch_object", "object_id": section_ids[0],
            }, project_id=self.project_id)
        model_document.validate()
        self.core._validate_geometry(model_document)
        old_volume = geometry["volume"]
        point = next(item for item in model_document.objects if item["id"] == section_points[1][0])
        point["x"] = 4.0
        self.core.commands._rebuild_changed_features(model_document, {section_ids[1]})
        body = next(item for item in model_document.objects if item["id"] == loft["body_id"])
        self.assertNotEqual(body["geometry"]["volume"], old_volume)
        fused_body = next(item for item in model_document.objects if item["id"] == fused["body_id"])
        self.assertNotEqual(fused_body["geometry"]["volume"], fused["geometry"]["volume"])
        model_document.validate()
        self.core._validate_geometry(model_document)

    def test_loft_aligns_section_vertices_when_the_perimeter_anchor_moves(self) -> None:
        base = [
            {"x": -3.0, "y": 0.0}, {"x": -1.0, "y": -2.0}, {"x": 2.0, "y": -1.5},
            {"x": 4.0, "y": 0.0}, {"x": 3.0, "y": 2.0}, {"x": 0.5, "y": 3.0},
            {"x": -2.0, "y": 2.5},
        ]
        moved = [
            {"x": point["x"] + 0.4, "y": point["y"] - 0.2}
            for point in base[2:] + base[:2]
        ]
        normalized = self.core.kernel.create_lofted_profiles(
            [base, moved], section_offsets=[0.0, 1.0],
        )
        first, second = normalized["sections"]
        self.assertEqual(len(first), len(second))
        from cadyx.kernel import _align_loft_profile
        aligned = _align_loft_profile(moved, base)
        self.assertLess(math.hypot(base[0]["x"] - aligned[0]["x"],
                                   base[0]["y"] - aligned[0]["y"]), 1.0)
        self.assertIsNone(self.core.kernel.validate(normalized))
        self.assertEqual(normalized["profile_alignment"], "minimum-displacement-v2")
        chained = self.core.kernel.create_lofted_profiles(
            [base, moved], section_offsets=[0.0, 1.0], alignment="minimum-displacement-v1",
        )
        self.assertEqual(chained["profile_alignment"], "minimum-displacement-v1")
        self.assertIsNone(self.core.kernel.validate(chained))
        legacy = self.core.kernel.create_lofted_profiles(
            [base, moved], section_offsets=[0.0, 1.0], alignment="independent-start-v1",
        )
        legacy.pop("profile_alignment")
        self.assertIsNone(self.core.kernel.validate(legacy))

    def test_multicontour_loft_fuses_split_and_merge_sections(self) -> None:
        def circle(cx, radius, samples=32):
            return [{"x": cx + radius * math.cos(math.tau * i / samples),
                     "y": radius * math.sin(math.tau * i / samples)} for i in range(samples)]

        geometry = self.core.kernel.create_multicontour_loft(
            [[circle(0.0, 10.0)], [circle(-5.0, 4.0), circle(5.0, 4.0)], [circle(0.0, 10.0)]],
            section_offsets=[0.0, 5.0, 10.0],
        )
        self.assertEqual(geometry["kind"], "multicontour_loft")
        self.assertIsNone(self.core.kernel.validate(geometry))
        topology = self.core.kernel.topology(geometry)
        self.assertTrue(topology["brep_valid"])
        self.assertEqual(topology["topology"]["solid_count"], 1)
        self.assertGreaterEqual(topology["bounds"]["min"][0], -10.000001)
        self.assertLessEqual(topology["bounds"]["max"][0], 10.000001)

    def test_multicontour_pairing_tracks_spatial_contours_over_area(self) -> None:
        def circle(cx, radius, samples=32):
            points = [{"x": cx + radius * math.cos(math.tau * i / samples),
                       "y": radius * math.sin(math.tau * i / samples)} for i in range(samples)]
            area, center_x, center_y, _ = _loft_contour_signature(points)
            return points, area, center_x, center_y

        left = [circle(0.0, 10.0), circle(100.0, 5.0)]
        right = [circle(4.0, 5.0), circle(96.0, 10.0)]
        self.assertEqual(_match_loft_contours(left, right), [(0, 0), (1, 1)])

    def test_update_loft_sections_persists_grouped_sketch_dependencies(self) -> None:
        def circle(cx, radius, samples=16):
            return [(cx + radius * math.cos(math.tau * i / samples),
                     radius * math.sin(math.tau * i / samples)) for i in range(samples)]

        sections = [
            (0.0, [(0.0, 10.0)]),
            (5.0, [(-5.0, 4.0), (5.0, 4.0)]),
            (10.0, [(0.0, 10.0)]),
        ]
        groups = []
        for offset, circles in sections:
            sketch_ids = []
            for center, radius in circles:
                sketch_id, _ = self.closed_sketch(
                    f"Сечение {offset}", circle(center, radius),
                )
                sketch_ids.append(sketch_id)
            groups.append(sketch_ids)

        document_data = self.core.inspect_project(self.project_id)["document"]
        for group, (offset, _) in zip(groups, sections, strict=True):
            sketch = next(item for item in document_data["objects"] if item["id"] == group[0])
            sketch.update(section_normal_axis="Y", section_offset=offset)
            for sketch_id in group[1:]:
                next(item for item in document_data["objects"] if item["id"] == sketch_id).update(
                    section_normal_axis="Y", section_offset=offset,
                )
        model_document = Document.from_dict(document_data)
        feature = self.core.commands.execute(model_document, {
            "type": "loft_sketches",
            "sketch_ids":[groups[0][0], groups[1][0], groups[2][0]],
            "name":"Editable split loft",
        }, project_id=self.project_id)
        result = self.core.commands.execute(model_document, {
            "type": "update_loft_sections", "feature_id": feature["feature_id"],
            "section_groups": groups,
        }, project_id=self.project_id)
        saved_feature = next(item for item in model_document.objects if item["id"] == feature["feature_id"])
        self.assertEqual(saved_feature["parameters"]["section_sketch_groups"], groups)
        self.assertEqual(result["geometry"]["kind"], "multicontour_loft")
        model_document.validate()
        self.core._validate_geometry(model_document)

    def test_rounded_rectangle_extrudes_from_arc_profile(self) -> None:
        sketch = self.command({"type": "create_sketch", "name": "Скруглённый профиль", "plane": "XY"})["sketch_id"]
        self.command({"type": "add_sketch_rounded_rectangle", "sketch_id": sketch,
                      "x": 0, "y": 0, "width": 20, "height": 10, "corner_radius": 2})
        result = self.command({"type": "extrude_sketch", "sketch_id": sketch, "depth": 3})
        self.assertEqual(result["geometry"]["kind"], "extruded_profile")
        self.assertGreater(result["geometry"]["volume"], 0)
        self.assertIsNone(self.core.kernel.validate(result["geometry"]))

    def test_exact_ellipse_sketch_extrudes_from_sampled_profile(self) -> None:
        sketch = self.command({"type": "create_sketch", "name": "Эллипс", "plane": "XY"})["sketch_id"]
        ellipse = self.command({"type": "add_sketch_ellipse_center", "sketch_id": sketch,
                                "center": {"x": 0, "y": 0}, "major_radius": 6,
                                "minor_radius": 3, "rotation_degrees": 35})
        result = self.command({"type": "extrude_sketch", "sketch_id": sketch, "depth": 4})
        expected_polygon_volume = math.pi * 6 * 3 * 4 * (128 * math.sin(math.tau / 128) / (2 * math.pi))
        self.assertAlmostEqual(result["geometry"]["volume"], expected_polygon_volume, delta=0.1)
        self.assertIsNone(self.core.kernel.validate(result["geometry"]))
        saved = self.core.inspect_project(self.project_id)["document"]
        self.assertTrue(any(item["id"] == ellipse["ellipse_id"] and item["type"] == "ellipse"
                            for item in saved["objects"]))

    def test_elliptical_arc_closes_with_lines_and_extrudes(self) -> None:
        sketch = self.command({"type": "create_sketch", "name": "Профиль с эллиптической дугой",
                              "plane": "XY"})["sketch_id"]
        arc = self.command({"type": "add_sketch_elliptical_arc", "sketch_id": sketch,
                            "center": {"x": 0, "y": 0}, "major_radius": 5,
                            "minor_radius": 3, "start_angle_degrees": 0, "sweep_degrees": 90})
        center = self.command({"type": "add_sketch_point", "sketch_id": sketch, "x": 0, "y": 0})["point_id"]
        start_id, end_id = arc["point_ids"]
        self.command({"type": "add_sketch_element", "sketch_id": sketch, "element_type": "line",
                      "start_id": end_id, "end_id": center})
        self.command({"type": "add_sketch_element", "sketch_id": sketch, "element_type": "line",
                      "start_id": center, "end_id": start_id})
        result = self.command({"type": "extrude_sketch", "sketch_id": sketch, "depth": 2})
        self.assertGreater(result["geometry"]["volume"], 0)
        self.assertIsNone(self.core.kernel.validate(result["geometry"]))

    def test_interpolating_spline_closes_with_lines_and_extrudes(self) -> None:
        sketch = self.command({"type": "create_sketch", "name": "Профиль со сплайном",
                               "plane": "XY"})["sketch_id"]
        spline = self.command({"type": "add_sketch_spline", "sketch_id": sketch,
                               "points": [{"x": 0, "y": 0}, {"x": 2, "y": -1},
                                          {"x": 4, "y": 0}]})
        start_id, end_id = spline["point_ids"][0], spline["point_ids"][-1]
        top = self.command({"type": "add_sketch_point", "sketch_id": sketch, "x": 2, "y": 3})["point_id"]
        self.command({"type": "add_sketch_element", "sketch_id": sketch, "element_type": "line",
                      "start_id": end_id, "end_id": top})
        self.command({"type": "add_sketch_element", "sketch_id": sketch, "element_type": "line",
                      "start_id": top, "end_id": start_id})
        result = self.command({"type": "extrude_sketch", "sketch_id": sketch, "depth": 2})
        self.assertGreater(result["geometry"]["volume"], 0)
        self.assertIsNone(self.core.kernel.validate(result["geometry"]))

    def test_interpolating_spline_is_a_sweep_path(self) -> None:
        sketch = self.command({"type": "create_sketch", "name": "Сплайновая траектория",
                               "plane": "XY"})["sketch_id"]
        self.command({"type": "add_sketch_spline", "sketch_id": sketch,
                      "points": [{"x": 0, "y": 0}, {"x": 4, "y": 3}, {"x": 8, "y": 0}]})
        result = self.command({"type": "sweep_sketch", "sketch_id": sketch, "radius": 0.5})
        self.assertGreater(result["geometry"]["volume"], 0)
        self.assertIsNone(self.core.kernel.validate(result["geometry"]))

    def test_control_point_spline_closes_a_profile_and_extrudes(self) -> None:
        sketch = self.command({"type": "create_sketch", "name": "Профиль с B-сплайном",
                               "plane": "XY"})["sketch_id"]
        spline = self.command({"type": "add_sketch_spline_control_points", "sketch_id": sketch,
                               "points": [{"x": 0, "y": 0}, {"x": 1, "y": -1},
                                          {"x": 3, "y": -1}, {"x": 4, "y": 0}]})
        start_id, end_id = spline["point_ids"][0], spline["point_ids"][-1]
        top = self.command({"type": "add_sketch_point", "sketch_id": sketch, "x": 2, "y": 3})["point_id"]
        self.command({"type": "add_sketch_element", "sketch_id": sketch, "element_type": "line",
                      "start_id": end_id, "end_id": top})
        self.command({"type": "add_sketch_element", "sketch_id": sketch, "element_type": "line",
                      "start_id": top, "end_id": start_id})
        result = self.command({"type": "extrude_sketch", "sketch_id": sketch, "depth": 2})
        self.assertGreater(result["geometry"]["volume"], 0)
        self.assertIsNone(self.core.kernel.validate(result["geometry"]))

    def test_revolution_rejects_open_axis_or_xy_profiles_atomically(self) -> None:
        sketch, point_ids = self.closed_sketch("Профиль", [(2, 0), (4, 0), (4, 8), (2, 8)])
        before = self.core.inspect_project(self.project_id)["document"]
        with self.assertRaises(ValidationError):
            self.command({"type": "revolve_sketch", "sketch_id": sketch})
        self.assertEqual(self.core.inspect_project(self.project_id)["document"], before)

        xz_sketch, _ = self.closed_sketch("Ось вращения", [(0, 0), (4, 0), (4, 8), (0, 8)])
        result = self.command({"type": "revolve_sketch", "sketch_id": xz_sketch,
                               "angle_degrees": 360})
        self.assertGreater(result["geometry"]["volume"], 0)
        self.assertEqual(len(point_ids), 4)

    def test_fused_mug_rebuilds_from_sources_and_renders_one_solid(self) -> None:
        cup_sketch, _ = self.closed_sketch(
            "Cup section", [(0, 0), (32, 0), (32, 80), (28, 80), (28, 5), (0, 5)],
        )
        cup = self.command({"type": "revolve_sketch", "sketch_id": cup_sketch,
                            "name": "Cup", "angle_degrees": 360})
        handle_points = [
            (31, 60), (42, 60), (52, 55), (60, 47), (65, 40), (60, 33),
            (52, 25), (42, 20), (31, 20), (31, 26), (42, 26), (50, 29),
            (57, 35), (59, 40), (57, 45), (50, 51), (42, 54), (31, 54),
        ]
        handle_sketch, handle_point_ids = self.closed_sketch("Handle", handle_points)
        handle = self.command({"type": "extrude_sketch", "sketch_id": handle_sketch,
                               "name": "Handle", "depth": 12, "centered": True})
        fused = self.command({"type": "fuse_bodies", "base_body_id": cup["body_id"],
                              "tool_body_id": handle["body_id"], "name": "Mug"})

        self.assertEqual(fused["geometry"]["kind"], "fused_profiles")
        self.assertGreater(fused["geometry"]["volume"], cup["geometry"]["volume"])
        self.assertLess(fused["geometry"]["volume"],
                        cup["geometry"]["volume"] + handle["geometry"]["volume"])
        handle_mesh = self.core.kernel.tessellate(handle["geometry"])
        self.assertAlmostEqual(min(handle_mesh["positions"][1::3]), -6)
        self.assertAlmostEqual(max(handle_mesh["positions"][1::3]), 6)

        document = self.core.inspect_project(self.project_id)["document"]
        rendered_bodies = [item["object_id"] for item in self.core.render_project(self.project_id)["objects"]
                           if item["type"] == "body"]
        self.assertEqual(rendered_bodies, [fused["body_id"]])
        self.assertEqual(Document.from_dict(document).to_dict(), document)

        before = fused["geometry"]["volume"]
        self.command({"type": "update_sketch_elements", "updates": [
            {"object_id": handle_point_ids[4], "changes": {"x": 66}},
        ]})
        rebuilt = self.core.inspect_project(self.project_id)["document"]
        rebuilt_body = next(item for item in rebuilt["objects"] if item.get("id") == fused["body_id"])
        rebuilt_feature = next(item for item in rebuilt["objects"] if item.get("id") == fused["feature_id"])
        rebuilt_handle = next(item for item in rebuilt["objects"] if item.get("id") == handle["body_id"])
        self.assertGreater(rebuilt_body["geometry"]["volume"], before)
        self.assertEqual(rebuilt_body["geometry"], rebuilt_feature["geometry"])
        self.assertEqual(rebuilt_body["geometry"]["tool"], rebuilt_handle["geometry"])
        exported = self.core.execute_command(
            self.project_id,
            self.lease_id,
            {"type": "export_brep", "body_id": fused["body_id"]},
            expected_revision=self.core.inspect_project(self.project_id)["document"]["revision"],
        )
        delivered = exported["history_entry"]["result"]
        step_check = delivered["verification"]["step_round_trip"]
        self.assertEqual(step_check["status"], "passed")
        self.assertEqual(step_check["source_solid_count"], 1)
        self.assertEqual(step_check["reimported_solid_count"], 1)

    def test_cut_feature_is_editable_rebuildable_and_hidden_as_one_result(self) -> None:
        base = self.command({"type": "create_box", "name": "Top", "width": 20, "height": 20, "depth": 10})
        # The box's depth is global Z; an XY profile creates a vertical cutter.
        sketch, _ = self.closed_sketch("Cutter", [(8, 8), (12, 8), (12, 12), (8, 12)], "XY")
        cutter = self.command({"type": "extrude_sketch", "sketch_id": sketch,
                               "name": "Through Cutter", "depth": 22, "centered": True})
        cut = self.command({"type": "cut_bodies", "base_body_id": base["body_id"],
                            "tool_body_id": cutter["body_id"], "name": "Top with Hole"})
        self.assertAlmostEqual(cut["geometry"]["volume"], 3840)
        self.assertEqual(cut["geometry"]["kind"], "cut_solids")
        self.assertEqual(self.core.kernel.topology(cut["geometry"])["topology"]["solid_count"], 1)
        rendered = [item["object_id"] for item in self.core.render_project(self.project_id)["objects"]
                    if item["type"] == "body"]
        self.assertEqual(rendered, [cut["body_id"]])
        document = self.core.inspect_project(self.project_id)["document"]
        self.assertEqual(Document.from_dict(document).to_dict(), document)
        self.assertEqual(next(item for item in document["objects"]
                              if item.get("id") == cut["feature_id"])["operation"], "cut_bodies")

        self.command({"type": "set_parameter", "feature_id": base["feature_id"],
                      "name": "width", "value": 21})
        rebuilt = self.core.inspect_project(self.project_id)["document"]
        result_body = next(item for item in rebuilt["objects"] if item["id"] == cut["body_id"])
        self.assertAlmostEqual(result_body["geometry"]["volume"], 4040)
        self.assertEqual(result_body["geometry"]["base"]["dimensions"]["width"], 21)

    def test_replacing_profile_rebuilds_feature_with_smooth_sampled_loop(self) -> None:
        original = [(31, 60), (42, 60), (52, 55), (60, 47), (65, 40), (60, 33),
                    (52, 25), (42, 20), (31, 20), (31, 26), (42, 26), (50, 29),
                    (57, 35), (59, 40), (57, 45), (50, 51), (42, 54), (31, 54)]
        sketch, _ = self.closed_sketch("Handle", original)
        handle = self.command({"type": "extrude_sketch", "sketch_id": sketch,
                               "name": "Handle", "depth": 12})
        points = []
        for index in range(33):
            angle = math.pi / 2 - math.pi * index / 32
            nx, nz = math.cos(angle) / 34, math.sin(angle) / 20
            length = math.hypot(nx, nz)
            points.append({"x": 31 + 34 * math.cos(angle) + 3 * nx / length,
                           "y": 40 + 20 * math.sin(angle) + 3 * nz / length})
        for index in range(32, -1, -1):
            angle = math.pi / 2 - math.pi * index / 32
            nx, nz = math.cos(angle) / 34, math.sin(angle) / 20
            length = math.hypot(nx, nz)
            points.append({"x": 31 + 34 * math.cos(angle) - 3 * nx / length,
                           "y": 40 + 20 * math.sin(angle) - 3 * nz / length})

        result = self.command({"type": "replace_sketch_profile", "sketch_id": sketch, "points": points})
        self.assertEqual(len(result["point_ids"]), 66)
        self.assertEqual(len(result["line_ids"]), 66)
        rebuilt = self.core.inspect_project(self.project_id)["document"]
        body = next(item for item in rebuilt["objects"] if item.get("id") == handle["body_id"])
        self.assertEqual(len(body["geometry"]["profile"]), 66)
        self.assertEqual(body["geometry"]["profile"], points)

    def test_sweep_creates_editable_bspline_pipe_and_rebuilds_from_its_path(self) -> None:
        sketch_id = self.command({"type": "create_sketch", "name": "Траектория ручки", "plane": "XZ"})["sketch_id"]
        points = [(28, 20), (34, 20), (44, 23), (53, 31), (59, 40), (58, 49), (51, 57), (40, 60), (30, 59)]
        self.command({"type": "replace_sketch_path", "sketch_id": sketch_id,
                      "points": [{"x": x, "y": z} for x, z in points]})
        document = self.core.inspect_project(self.project_id)["document"]
        path_ids = [item["id"] for item in document["objects"]
                    if item.get("type") == "point" and item.get("sketch_id") == sketch_id]
        handle = self.command({"type": "sweep_sketch", "sketch_id": sketch_id,
                               "name": "Трубчатая ручка", "radius": 3.5})
        self.assertEqual(handle["geometry"]["kind"], "swept_pipe")
        self.assertAlmostEqual(handle["geometry"]["path"][0]["x"], 28)
        topology = self.core.inspect_body_topology(self.project_id, handle["body_id"])
        self.assertGreater(topology["volume"], 0)
        self.assertIn("bspline", {edge["curve"] for edge in topology["edges"]})

        before = handle["geometry"]["volume"]
        self.command({"type": "update_sketch_elements", "updates": [
            {"object_id": path_ids[4], "changes": {"x": 61}},
        ]})
        rebuilt = self.core.inspect_project(self.project_id)["document"]
        body = next(item for item in rebuilt["objects"] if item.get("id") == handle["body_id"])
        feature = next(item for item in rebuilt["objects"] if item.get("id") == handle["feature_id"])
        self.assertNotEqual(body["geometry"]["volume"], before)
        self.assertEqual(body["geometry"], feature["geometry"])
        self.assertEqual(Document.from_dict(rebuilt).to_dict(), rebuilt)

    def test_fillet_uses_unique_topology_edges_and_hides_consumed_source(self) -> None:
        source = self.command({"type": "create_box", "name": "Заготовка", "width": 10,
                               "height": 12, "depth": 14})
        topology = self.core.inspect_body_topology(self.project_id, source["body_id"])
        self.assertEqual(len(topology["edges"]), 12)
        result = self.command({"type": "fillet_edges", "body_id": source["body_id"],
                               "edge_indices": [0], "radius": 1.0, "name": "Скруглённая заготовка"})
        self.assertEqual(result["geometry"]["kind"], "filleted_solid")
        self.assertLess(result["geometry"]["volume"], source["geometry"]["volume"])
        visible = [item["object_id"] for item in self.core.render_project(self.project_id)["objects"]
                   if item["type"] == "body"]
        self.assertEqual(visible, [result["body_id"]])

        before = result["geometry"]["volume"]
        adjusted = self.command({"type": "set_parameter", "feature_id": result["feature_id"],
                                 "name": "radius", "value": 1.5})
        self.assertLess(adjusted["geometry"]["volume"], before)


if __name__ == "__main__":
    unittest.main()
