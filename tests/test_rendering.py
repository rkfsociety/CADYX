import math
import struct
import unittest
import zlib
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cadyx.core import CadyxCore
from cadyx.errors import ValidationError
from cadyx.rendering import render_scene, render_scene_png


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

    def test_viewport_scene_cache_reuses_same_revision_and_invalidates_after_edit(self):
        result = self.command({"type": "create_box", "width": 2, "height": 3, "depth": 5})
        first = self.core.render_project(self.project)
        first["objects"][0]["positions"][0] = 999
        with patch("cadyx.core.render_scene", side_effect=AssertionError("scene should be cached")), \
                patch("cadyx.core.Document.from_dict", side_effect=AssertionError("document should not be copied on a cache hit")):
            repeated = self.core.render_project(self.project)
        self.assertEqual(repeated["objects"][0]["positions"][0], 0,
                         "Cached scenes are returned as isolated copies")

        self.command({"type": "set_parameter", "feature_id": result["feature_id"],
                      "name": "width", "value": 4})
        with patch("cadyx.core.render_scene", wraps=render_scene) as render:
            changed = self.core.render_project(self.project)
        self.assertEqual(render.call_count, 1, "A document revision change rebuilds the scene")
        self.assertEqual(changed["bounds"]["max"], [4, 3, 5])

    def test_scene_cache_hit_avoids_document_copy(self):
        first = self.core.render_project(self.project)
        with patch("cadyx.core.Document.from_dict", side_effect=AssertionError("cache hit should skip document copy")):
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

