import copy
import math
import struct
import unittest
import zlib
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from cadyx.core import CadyxCore
from cadyx.errors import ValidationError
from cadyx.models import Document
from cadyx.rendering import render_scene, render_scene_png
from cadyx.sketches import closed_profile, spline_sample_points


class RenderingTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.core = CadyxCore(self.directory.name)
        self.project = self.core.create_project("Scene")["project"]["id"]
        self.lease = self.core.acquire_lease(self.project, "agent", "test")["lease_id"]

    def command(self, command):
        return self.core.execute_command(self.project, self.lease, command)["history_entry"]["result"]

    def test_mesh_winding_volume_bounds_and_read_only_scene(self):
        body = self.command({"type": "create_box", "width": 2, "height": 3, "depth": 5})["body_id"]
        path = self.core.workspace._project_dir(self.project) / "project.json"
        before = path.read_bytes()
        scene = self.core.render_project(self.project)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(scene["bounds"], {"min": [0, 0, 0], "max": [2, 3, 5]})
        self.assertEqual(scene["revision"], 1)
        mesh = scene["objects"][0]
        self.assertEqual(mesh["object_id"], body)
        self.assertEqual(len(mesh["triangles"]), 36)
        vertices = [mesh["positions"][i:i+3] for i in range(0, len(mesh["positions"]), 3)]
        volume = 0
        for index in range(0, len(mesh["triangles"]), 3):
            a, b, c = [vertices[i] for i in mesh["triangles"][index:index+3]]
            volume += (a[0]*(b[1]*c[2]-b[2]*c[1]) + a[1]*(b[2]*c[0]-b[0]*c[2]) + a[2]*(b[0]*c[1]-b[1]*c[0])) / 6
        self.assertAlmostEqual(volume, 30)

    def test_hole_feature_exposes_its_cylindrical_face_for_selection(self):
        base = self.command({"type": "create_box", "width": 20, "height": 20, "depth": 10})["body_id"]
        hole = self.command({"type": "hole", "body_id": base, "origin": [10, 10, 10],
                             "axis": [0, 0, -1], "diameter": 4, "depth": 6})

        scene = self.core.render_project(self.project)

        body = next(item for item in scene["objects"] if item["object_id"] == hole["body_id"])
        ranges = [item for item in body.get("feature_face_ranges", [])
                  if item["feature_id"] == hole["feature_id"]]
        self.assertTrue(ranges, "The hole operation must expose its generated cylindrical face range.")
        self.assertTrue(all(item["end"] > item["start"] for item in ranges))

    def test_project_point_cloud_is_present_in_server_scene_and_png(self):
        project_directory = self.core.workspace._project_dir(self.project)
        source = project_directory / "merged.ply"
        source.write_text(
            "ply\nformat ascii 1.0\nelement vertex 4\n"
            "property float x\nproperty float y\nproperty float z\nend_header\n"
            "0 0 0\n4 0 0\n0 4 0\n0 0 4\n", encoding="ascii")
        result = self.command({"type": "import_project_point_cloud", "file": source.name,
                               "name": "Unified scan"})
        scene = self.core.render_project(self.project)
        body = next(item for item in scene["objects"] if item["object_id"] == result["body_id"])
        self.assertEqual(body["triangles"], [])
        self.assertEqual(body["points"], [0, 1, 2, 3])
        self.assertEqual(body["positions"], [0.0, 0.0, 0.0, 4.0, 0.0, 0.0,
                                             0.0, 4.0, 0.0, 0.0, 0.0, 4.0])
        image = self.core.render_project_png(self.project)
        empty = render_scene_png({"objects": []})
        self.assertGreater(len(image), 100)
        self.assertNotEqual(image, empty)

    def test_hidden_imported_reference_is_not_in_viewport_or_scene_bounds(self):
        document = Document.create()
        body_id = f"body:{uuid4()}"
        document.objects = [{
            "id": body_id,
            "type": "body",
            "name": "Reference STEP",
            "feature_ids": [],
            "geometry": self.core.kernel.create_box(width=2, height=3, depth=5),
            "visible": False,
        }]

        scene = render_scene(document, self.core.kernel)

        self.assertEqual(scene["objects"], [])
        self.assertIsNone(scene["bounds"])

    def test_body_visibility_flag_must_be_boolean(self):
        document = Document.create()
        document.objects = [{
            "id": f"body:{uuid4()}",
            "type": "body",
            "name": "Reference STEP",
            "feature_ids": [],
            "geometry": self.core.kernel.create_box(width=2, height=3, depth=5),
            "visible": "false",
        }]

        with self.assertRaisesRegex(ValidationError, "Body visible must be a boolean"):
            document.validate()

    def test_pattern_source_body_is_hidden_from_the_scene(self):
        document = Document.create()
        source_id, result_id = f"body:{uuid4()}", f"body:{uuid4()}"
        feature_id = f"feature:{uuid4()}"
        geometry = self.core.kernel.create_box(width=2, height=3, depth=5)
        document.objects = [
            {"id": source_id, "type": "body", "name": "Seed", "feature_ids": [],
             "geometry": geometry},
            {"id": result_id, "type": "body", "name": "Pattern", "feature_ids": [feature_id],
             "geometry": geometry},
            {"id": feature_id, "type": "feature", "body_id": result_id,
             "source_body_id": source_id, "operation": "circular_pattern_body",
             "parameters": {"count": 10}, "geometry": geometry},
        ]

        scene = render_scene(document, self.core.kernel)

        self.assertEqual([item["object_id"] for item in scene["objects"]], [result_id])

    def test_viewport_scene_cache_reuses_same_revision_and_invalidates_after_edit(self):
        result = self.command({"type": "create_box", "width": 2, "height": 3, "depth": 5})
        first = self.core.render_project(self.project)
        first["objects"][0]["positions"][0] = 999
        with patch("cadyx.core_services.render_scene", side_effect=AssertionError("scene should be cached")), \
                patch("cadyx.core_services.Document.from_dict", side_effect=AssertionError("document should not be copied on a cache hit")):
            repeated = self.core.render_project(self.project)
        self.assertEqual(repeated["objects"][0]["positions"][0], 0,
                         "Cached scenes are returned as isolated copies")

        self.command({"type": "set_parameter", "feature_id": result["feature_id"],
                      "name": "width", "value": 4})
        with patch("cadyx.core_services.render_scene", wraps=render_scene) as render:
            changed = self.core.render_project(self.project)
        self.assertEqual(render.call_count, 1, "A document revision change rebuilds the scene")
        self.assertEqual(changed["bounds"]["max"], [4, 3, 5])

    def test_arc_slot_commands_render_all_analytic_profile_arcs(self):
        sketch_id = self.command({"type": "create_sketch"})["sketch_id"]
        line = self.command({"type": "add_sketch_line", "sketch_id": sketch_id,
                             "start": {"x": -10, "y": 0}, "end": {"x": 10, "y": 0}})
        results = (
            self.command({"type": "add_sketch_arc_slot_three_points", "sketch_id": sketch_id,
                          "start": {"x": 5, "y": 0}, "through": {"x": 0, "y": 5},
                          "end": {"x": -5, "y": 0}, "width": 2}),
            self.command({"type": "add_sketch_arc_slot_center", "sketch_id": sketch_id,
                          "center": {"x": 20, "y": 0}, "radius": 8,
                          "start_angle_degrees": 10, "sweep_degrees": -90, "width": 2}),
            self.command({"type": "add_sketch_arc_slot_tangent", "sketch_id": sketch_id,
                          "line_id": line["line_id"], "tangent_point": {"x": 0, "y": 0},
                          "radius": 8, "sweep_degrees": 90, "width": 2}),
        )
        scene = self.core.render_project(self.project)
        scene_by_id = {item["object_id"]: item for item in scene["objects"]}
        for result in results:
            self.assertTrue(all(scene_by_id[object_id]["positions"]
                                for object_id in result["object_ids"]))
            self.assertTrue(all(scene_by_id[arc_id]["type"] == "circle"
                                and len(scene_by_id[arc_id]["lines"]) > 1
                                for arc_id in result["circle_ids"]))

    def test_scene_cache_hit_avoids_document_copy(self):
        first = self.core.render_project(self.project)
        with patch("cadyx.core_services.Document.from_dict", side_effect=AssertionError("cache hit should skip document copy")):
            repeated = self.core.render_project(self.project)
        self.assertEqual(repeated, first)

    def test_png_rasterizer_fills_a_mesh(self):
        scene = {"objects": [{
            "type": "body",
            "positions": [0, 0, 0, 2, 0, 0, 2, 3, 0, 0, 3, 0, 0, 0, 5, 2, 0, 5, 2, 3, 5, 0, 3, 5],
            "triangles": [0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4,
                          1, 2, 6, 1, 6, 5, 2, 3, 7, 2, 7, 6, 3, 0, 4, 3, 4, 7],
            "lines": [], "points": [],
        }]}
        image = render_scene_png(scene)
        self.assertEqual(image[:8], b"\x89PNG\r\n\x1a\n")
        position = 8
        compressed = bytearray()
        while position < len(image):
            length = struct.unpack(">I", image[position:position + 4])[0]
            kind = image[position + 4:position + 8]
            payload = image[position + 8:position + 8 + length]
            if kind == b"IDAT":
                compressed.extend(payload)
            position += length + 12
        raw = zlib.decompress(compressed)
        width, height = struct.unpack(">II", image[16:24])
        self.assertEqual((width, height), (960, 720))
        pixel_offset = 360 * (width * 3 + 1) + 1 + 480 * 3
        self.assertNotEqual(raw[pixel_offset:pixel_offset + 3], bytes((20, 25, 28)))

    def test_thumbnail_png_uses_compact_16_9_card_aspect_ratio(self):
        image = render_scene_png({"objects": []}, width=480, height=270)
        self.assertEqual(struct.unpack(">II", image[16:24]), (480, 270))
        thumbnail, revision = self.core.render_project_thumbnail_png(self.project)
        self.assertEqual(struct.unpack(">II", thumbnail[16:24]), (480, 270))
        self.assertEqual(revision, 0)

    def test_empty_and_sketch_scene_and_parameter_refresh(self):
        self.assertIsNone(self.core.render_project(self.project)["bounds"])
        sketch = self.command({"type": "create_sketch"})["sketch_id"]
        points = [self.command({"type": "add_sketch_element", "sketch_id": sketch,
                              "element_type": "point", "x": x, "y": 1})["object_id"] for x in (0, 4)]
        self.command({"type": "add_sketch_element", "sketch_id": sketch, "element_type": "line",
                      "start_id": points[0], "end_id": points[1]})
        self.command({"type": "add_sketch_element", "sketch_id": sketch, "element_type": "circle",
                      "center_id": points[0], "radius": 2})
        scene = self.core.render_project(self.project)
        self.assertEqual([obj["type"] for obj in scene["objects"]], ["point", "point", "line", "circle"])
        self.assertEqual(scene["bounds"], {"min": [-2, -1, 0], "max": [4, 3, 0]})
        self.assertTrue(all(math.isfinite(v) for obj in scene["objects"] for v in obj["positions"]))
        result = self.command({"type": "create_box", "width": 2, "height": 3, "depth": 5})
        self.command({"type": "set_parameter", "feature_id": result["feature_id"], "name": "width", "value": 8})
        self.assertEqual(self.core.render_project(self.project)["bounds"]["max"], [8, 3, 5])

    def test_extracted_section_sketches_render_on_their_source_plane(self):
        sketch = self.command({"type": "create_sketch", "plane": "XZ"})["sketch_id"]
        self.command({"type": "add_sketch_polyline", "sketch_id": sketch,
                      "points": [{"x": 1, "y": 2}, {"x": 4, "y": 2}, {"x": 1, "y": 5}],
                      "closed": True})
        document = Document.from_dict(self.core.inspect_project(self.project)["document"])
        section = next(item for item in document.objects if item["id"] == sketch)
        section.update(section_normal_axis="Y", section_offset=-7.5, section_profile_index=0)

        scene = render_scene(document, self.core.kernel)
        positions = [item["positions"] for item in scene["objects"] if item["type"] == "line"]
        self.assertTrue(positions)
        self.assertTrue(all(values[index] == -7.5 for values in positions for index in (1, 4)))

    def test_sketch_scene_exposes_construction_and_infinite_line_modes(self):
        sketch = self.command({"type": "create_sketch"})["sketch_id"]
        self.command({"type": "add_sketch_construction_point", "sketch_id": sketch, "x": 2, "y": 3})
        infinite = self.command({"type": "add_sketch_infinite_line", "sketch_id": sketch,
                                 "start": {"x": 0, "y": 0}, "end": {"x": 4, "y": 2}})
        scene = self.core.render_project(self.project)
        by_id = {item["object_id"]: item for item in scene["objects"]}
        self.assertTrue(by_id[infinite["line_id"]]["construction"])
        self.assertEqual(by_id[infinite["line_id"]]["line_modes"], ["infinite"])
        self.assertTrue(all(by_id[point_id]["construction"] for point_id in infinite["point_ids"]))

    def test_sketch_scene_renders_partial_circle_as_open_arc(self):
        sketch = self.command({"type": "create_sketch"})["sketch_id"]
        result = self.command({"type": "add_sketch_rounded_rectangle", "sketch_id": sketch,
                               "x": 0, "y": 0, "width": 12, "height": 8, "corner_radius": 2})
        scene = self.core.render_project(self.project)
        by_id = {item["object_id"]: item for item in scene["objects"]}
        arc = by_id[result["circle_ids"][0]]
        self.assertEqual(len(arc["lines"]), 32)

    def test_center_defined_arc_renders_only_its_sweep(self):
        sketch = self.command({"type": "create_sketch"})["sketch_id"]
        result = self.command({"type": "add_sketch_arc_center", "sketch_id": sketch,
                               "center": {"x": 0, "y": 0}, "start": {"x": 5, "y": 0},
                               "end": {"x": 0, "y": 5}})
        scene = self.core.render_project(self.project)
        arc = next(item for item in scene["objects"] if item["object_id"] == result["arc_id"])
        self.assertEqual(len(arc["lines"]), 32)
        self.assertAlmostEqual(min(arc["positions"][0::3]), 0)
        self.assertAlmostEqual(max(arc["positions"][0::3]), 5)
        self.assertAlmostEqual(min(arc["positions"][1::3]), 0)
        self.assertAlmostEqual(max(arc["positions"][1::3]), 5)

    def test_ellipse_scene_is_a_closed_analytic_conic_polyline(self):
        sketch = self.command({"type": "create_sketch"})["sketch_id"]
        result = self.command({"type": "add_sketch_ellipse_center", "sketch_id": sketch,
                               "center": {"x": 2, "y": -1}, "major_radius": 5,
                               "minor_radius": 2, "rotation_degrees": 30})
        scene = self.core.render_project(self.project)
        ellipse = next(item for item in scene["objects"] if item["object_id"] == result["ellipse_id"])
        self.assertEqual(ellipse["type"], "ellipse")
        self.assertEqual(len(ellipse["positions"]), 128 * 3)
        self.assertEqual(len(ellipse["lines"]), 128 * 2)
        self.assertEqual(ellipse["lines"][-2:], [127, 0])

    def test_elliptical_arc_scene_renders_only_its_open_sweep(self):
        sketch = self.command({"type": "create_sketch"})["sketch_id"]
        result = self.command({"type": "add_sketch_elliptical_arc", "sketch_id": sketch,
                               "center": {"x": 0, "y": 0}, "major_radius": 5,
                               "minor_radius": 3, "start_angle_degrees": 0, "sweep_degrees": 90})
        scene = self.core.render_project(self.project)
        arc = next(item for item in scene["objects"] if item["object_id"] == result["ellipse_id"])
        self.assertEqual(len(arc["positions"]), 33 * 3)
        self.assertEqual(len(arc["lines"]), 32 * 2)
        self.assertEqual(arc["lines"][-2:], [31, 32])

    def test_spline_scene_renders_a_smooth_open_curve_through_its_points(self):
        sketch = self.command({"type": "create_sketch", "plane": "XZ"})["sketch_id"]
        result = self.command({"type": "add_sketch_spline", "sketch_id": sketch,
                               "points": [{"x": 0, "y": 1}, {"x": 2, "y": 4},
                                          {"x": 5, "y": 2}]})
        scene = self.core.render_project(self.project)
        spline = next(item for item in scene["objects"] if item["object_id"] == result["spline_id"])
        self.assertEqual(len(spline["positions"]), (2 * 24 + 1) * 3)
        self.assertEqual(len(spline["lines"]), 2 * 24 * 2)
        self.assertEqual(spline["positions"][:3], [0, 0, 1])
        self.assertEqual(spline["positions"][24 * 3:24 * 3 + 3], [2, 0, 4])
        self.assertEqual(spline["positions"][-3:], [5, 0, 2])

    def test_control_point_spline_scene_renders_a_clamped_bspline(self):
        sketch = self.command({"type": "create_sketch"})["sketch_id"]
        result = self.command({"type": "add_sketch_spline_control_points", "sketch_id": sketch,
                               "points": [{"x": 0, "y": 0}, {"x": 1, "y": 3},
                                          {"x": 3, "y": 4}, {"x": 5, "y": 0}]})
        scene = self.core.render_project(self.project)
        spline = next(item for item in scene["objects"] if item["object_id"] == result["spline_id"])
        middle = spline["positions"][12 * 3:12 * 3 + 3]
        self.assertAlmostEqual(middle[0], 2.125)
        self.assertAlmostEqual(middle[1], 2.625)

    def test_bezier_and_uniform_bspline_document_curves_render(self):
        for mode, points, expected_count in (
            ("bezier", [(0, 0), (1, 3), (3, 4), (5, 0)], 25),
            ("bspline", [(0, 0), (1, 3), (3, 4), (5, 2), (6, 0)], 49),
        ):
            with self.subTest(mode=mode):
                document = Document.create()
                sketch_id = f"sketch:{uuid4()}"
                point_ids = [f"point:{uuid4()}" for _ in points]
                spline_id = f"spline:{uuid4()}"
                document.objects = ([{"id": sketch_id, "type": "sketch", "name": "Curve", "plane": "XY"}]
                                    + [{"id": point_id, "type": "point", "sketch_id": sketch_id,
                                        "x": x, "y": y}
                                       for point_id, (x, y) in zip(point_ids, points)]
                                    + [{"id": f"line:{uuid4()}", "type": "line", "sketch_id": sketch_id,
                                        "start_id": point_ids[-1], "end_id": point_ids[-2]},
                                       {"id": f"line:{uuid4()}", "type": "line", "sketch_id": sketch_id,
                                        "start_id": point_ids[-2], "end_id": point_ids[0]}]
                                    + [{"id": spline_id, "type": "spline", "sketch_id": sketch_id,
                                        "point_ids": point_ids, "mode": mode}])
                document.validate()
                _, profile = closed_profile(document.objects, sketch_id)
                self.assertGreaterEqual(len(profile), expected_count)
                self.assertNotEqual(profile[0], profile[-1])
                scene = render_scene(document, self.core.kernel)
                curve = next(item for item in scene["objects"] if item["object_id"] == spline_id)
                self.assertEqual(len(curve["positions"]), expected_count * 3)
                self.assertEqual(len(curve["lines"]), (expected_count - 1) * 2)
                self.assertEqual(curve["positions"][:3], [float(points[0][0]), float(points[0][1]), 0.0])
                self.assertEqual(curve["positions"][-3:], [float(points[-1][0]), float(points[-1][1]), 0.0])
                midpoint_index = (expected_count - 1) // 2
                midpoint = curve["positions"][midpoint_index * 3:midpoint_index * 3 + 3]
                if mode == "bezier":
                    self.assertAlmostEqual(midpoint[0], 2.125)
                    self.assertAlmostEqual(midpoint[1], 2.625)
                else:
                    self.assertAlmostEqual(midpoint[0], 3.0)
                    self.assertAlmostEqual(midpoint[1], 3.25)

    def test_nurbs_document_curve_requires_valid_clamped_parameters_and_renders(self):
        control_points = [(0, 0), (1, 3), (3, 4), (5, 0)]
        document = Document.create()
        sketch_id = f"sketch:{uuid4()}"
        point_ids = [f"point:{uuid4()}" for _ in control_points]
        spline_id = f"spline:{uuid4()}"
        document.objects = ([{"id": sketch_id, "type": "sketch", "name": "NURBS", "plane": "XY"}]
                            + [{"id": point_id, "type": "point", "sketch_id": sketch_id,
                                "x": x, "y": y}
                               for point_id, (x, y) in zip(point_ids, control_points)]
                            + [{"id": f"line:{uuid4()}", "type": "line", "sketch_id": sketch_id,
                                "start_id": point_ids[-1], "end_id": point_ids[-2]},
                               {"id": f"line:{uuid4()}", "type": "line", "sketch_id": sketch_id,
                                "start_id": point_ids[-2], "end_id": point_ids[0]},
                               {"id": spline_id, "type": "spline", "sketch_id": sketch_id,
                                "point_ids": point_ids, "mode": "nurbs", "degree": 2,
                                "weights": [1.0, math.sqrt(.5), 1.0, 1.0],
                                "knots": [0.0, 0.0, 0.0, 1.0, 2.0, 2.0, 2.0]}])
        document.validate()

        _, profile = closed_profile(document.objects, sketch_id)
        self.assertGreater(len(profile), 25)
        scene = render_scene(document, self.core.kernel)
        curve = next(item for item in scene["objects"] if item["object_id"] == spline_id)
        self.assertEqual(len(curve["positions"]), 49 * 3)
        self.assertEqual(curve["positions"][:3], [0.0, 0.0, 0.0])
        self.assertEqual(curve["positions"][-3:], [5.0, 0.0, 0.0])

        invalid_variants = (
            lambda spline: spline.update(knots=[0.0, 0.0, 0.0, 1.0, 2.0, 3.0, 4.0]),
            lambda spline: spline.update(weights=[1.0, 0.0, 1.0, 1.0]),
            lambda spline: spline.update(knots=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
            lambda spline: spline.update(knots=[0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 2.0]),
        )
        for mutate in invalid_variants:
            corrupted = copy.deepcopy(document)
            spline = next(item for item in corrupted.objects if item["id"] == spline_id)
            mutate(spline)
            with self.subTest(spline=spline), self.assertRaises(ValidationError):
                corrupted.validate()

    def test_spline_sampling_rejects_invalid_sample_count(self):
        sketch = self.command({"type": "create_sketch"})["sketch_id"]
        result = self.command({"type": "add_sketch_spline", "sketch_id": sketch,
                               "points": [{"x": 0, "y": 0}, {"x": 1, "y": 2}, {"x": 2, "y": 0}]})
        document = Document.from_dict(self.core.inspect_project(self.project)["document"])
        by_id = {item["id"]: item for item in document.objects}
        spline = by_id[result["spline_id"]]
        for count in (0, -1, True, 1.5):
            with self.subTest(count=count), self.assertRaises(ValidationError):
                spline_sample_points(spline, by_id, samples_per_span=count)

    def test_unrenderable_sketch_is_rejected_without_changing_document(self):
        sketch = self.command({"type": "create_sketch"})["sketch_id"]
        center = self.command({"type": "add_sketch_element", "sketch_id": sketch,
                               "element_type": "point", "x": 1e308, "y": 0})["object_id"]
        self.command({"type": "add_sketch_element", "sketch_id": sketch, "element_type": "circle",
                      "center_id": center, "radius": 1e308})
        path = self.core.workspace._project_dir(self.project) / "project.json"
        before = path.read_bytes()
        with self.assertRaises(ValidationError):
            self.core.render_project(self.project)
        self.assertEqual(path.read_bytes(), before)

    def test_xz_sketch_is_rendered_in_the_vertical_profile_plane(self):
        sketch = self.command({"type": "create_sketch", "plane": "XZ", "name": "Section"})["sketch_id"]
        point = self.command({"type": "add_sketch_element", "sketch_id": sketch,
                              "element_type": "point", "x": 2, "y": 7})["object_id"]
        scene = self.core.render_project(self.project)
        rendered = next(item for item in scene["objects"] if item["object_id"] == point)
        self.assertEqual(rendered["positions"], [2, 0, 7])

    def test_sketch_is_hidden_after_its_profile_becomes_a_solid_feature(self):
        sketch = self.command({"type": "create_sketch", "plane": "XY"})["sketch_id"]
        self.command({"type": "add_sketch_polyline", "sketch_id": sketch, "closed": True,
                      "points": [{"x": 0, "y": 0}, {"x": 4, "y": 0},
                                 {"x": 4, "y": 4}, {"x": 0, "y": 4}]})
        self.command({"type": "extrude_sketch", "sketch_id": sketch, "depth": 2})

        scene = self.core.render_project(self.project)
        self.assertEqual([item["type"] for item in scene["objects"]], ["body"])

