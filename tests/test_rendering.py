import math
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cadyx.core import CadyxCore
from cadyx.errors import ValidationError
from cadyx.rendering import render_scene


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
        with patch("cadyx.core.render_scene", side_effect=AssertionError("scene should be cached")):
            repeated = self.core.render_project(self.project)
        self.assertEqual(repeated["objects"][0]["positions"][0], 0,
                         "Cached scenes are returned as isolated copies")

        self.command({"type": "set_parameter", "feature_id": result["feature_id"],
                      "name": "width", "value": 4})
        with patch("cadyx.core.render_scene", wraps=render_scene) as render:
            changed = self.core.render_project(self.project)
        self.assertEqual(render.call_count, 1, "A document revision change rebuilds the scene")
        self.assertEqual(changed["bounds"]["max"], [4, 3, 5])

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
