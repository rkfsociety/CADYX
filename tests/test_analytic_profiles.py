from __future__ import annotations
import math
import unittest
from cadyx.errors import ValidationError
from cadyx.kernel import OCCTKernelAdapter
from cadyx.native_kernel import NativeKernel
from cadyx.profile_segments import segment_values, sketch_segments
from cadyx.sketches import closed_profile
from cadyx.models import Document
from cadyx.command_rebuild import RebuildCommands

def quarter(plane="XY"):
    return [
        {"id":"s","type":"sketch","plane":plane},
        {"id":"o","type":"point","sketch_id":"s","x":0.0,"y":0.0},
        {"id":"a","type":"point","sketch_id":"s","x":2.0,"y":0.0},
        {"id":"b","type":"point","sketch_id":"s","x":0.0,"y":2.0},
        {"id":"l1","type":"line","sketch_id":"s","start_id":"o","end_id":"a"},
        {"id":"l2","type":"line","sketch_id":"s","start_id":"b","end_id":"o"},
        {"id":"arc","type":"circle","sketch_id":"s","start_id":"a","end_id":"b",
         "center_id":"o","radius":2.0,"sweep_radians":math.pi/2},
    ]

class AnalyticProfileTests(unittest.TestCase):
    def test_extrusion_retains_one_cylindrical_face_and_exact_volume(self):
        objects = quarter()
        plane, profile = closed_profile(objects, "s")
        kernel = OCCTKernelAdapter()
        geometry = kernel.create_extruded_profile(profile, plane=plane, depth=3,
                                                  segments=sketch_segments(objects,"s"))
        self.assertAlmostEqual(geometry["volume"], math.pi*3, places=10)
        kernel.validate(geometry)
        topology = NativeKernel().geometry_graph(geometry, topology=True)
        self.assertEqual(topology["topology"]["face_count"], 5)
        self.assertEqual(sum(edge["curve"] == "circle" for edge in topology["edges"]), 2)

    def test_revolution_of_circular_arc_is_exact_hemisphere(self):
        objects = quarter("XZ")
        _, profile = closed_profile(objects, "s")
        geometry = OCCTKernelAdapter().create_revolved_profile(
            profile, angle_degrees=360, segments=sketch_segments(objects, "s"))
        self.assertAlmostEqual(geometry["volume"], 16*math.pi/3, places=9)
        OCCTKernelAdapter().validate(geometry)

    def test_rebuild_retains_arc_descriptor(self):
        document = Document.create()
        document.objects = quarter("XZ")
        engine = RebuildCommands()
        engine.kernel = OCCTKernelAdapter()
        feature = {"operation":"revolve_sketch", "source_sketch_id":"s"}
        a = engine._profile_geometry(document, feature, {"angle_degrees":360})
        b = engine._profile_geometry(document, feature, {"angle_degrees":180})
        self.assertEqual(a["profile_segments"], b["profile_segments"])
        self.assertAlmostEqual(a["volume"], b["volume"]*2, places=9)

    def test_elliptical_profile_arc_is_exact(self):
        objects = quarter()
        objects[2].update(x=3.0)
        objects[3].update(y=2.0)
        arc = objects[-1]
        arc.update(id="ellipse:00000000-0000-0000-0000-000000000001", type="ellipse",
                   center_x=0.0, center_y=0.0, major_radius=3.0, minor_radius=2.0,
                   rotation_radians=0.0, start_angle_radians=0.0)
        arc.pop("center_id")
        arc.pop("radius")
        plane, profile = closed_profile(objects, "s")
        segments = sketch_segments(objects, "s")
        self.assertEqual(sum(segment[0] == 2 for segment in segments), 1)
        geometry = OCCTKernelAdapter().create_extruded_profile(
            profile, plane=plane, depth=3.0, segments=segments)
        self.assertAlmostEqual(geometry["volume"], 4.5 * math.pi, places=9)
        OCCTKernelAdapter().validate(geometry)
        topology = NativeKernel().geometry_graph(geometry, topology=True)
        self.assertGreaterEqual(sum(edge["curve"] == "ellipse" for edge in topology["edges"]), 2)

    def test_disconnect_and_invalid_segment_are_rejected(self):
        valid = sketch_segments(quarter(), "s")
        for replacement in (float("nan"), True):
            broken = [segment[:] for segment in valid]
            broken[0][1] = replacement
            with self.assertRaises(ValidationError):
                segment_values(broken)
        broken = [segment[:] for segment in valid]
        broken[0][5] += 0.01
        with self.assertRaises(ValidationError):
            segment_values(broken)
        with self.assertRaises(ValidationError):
            segment_values([[], valid[0], valid[1]])

    def test_core_pad_creation_and_upstream_rebuild_use_exact_arcs(self):
        import tempfile
        from cadyx.core import CadyxCore
        with tempfile.TemporaryDirectory() as directory:
            core = CadyxCore(directory)
            project = core.create_project("Arc pad")["project"]["id"]
            lease = core.acquire_lease(project, "agent", "test")["lease_id"]
            def command(value):
                return core.execute_command(project, lease, value)["history_entry"]["result"]
            base = command({"type":"create_box","width":3,"height":3,"depth":1})["body_id"]
            sketch = command({"type":"create_sketch"})["sketch_id"]
            for start, end in (({"x":0,"y":0},{"x":2,"y":0}), ({"x":0,"y":2},{"x":0,"y":0})):
                command({"type":"add_sketch_line","sketch_id":sketch,"start":start,"end":end})
            command({"type":"add_sketch_arc_center","sketch_id":sketch,"center":{"x":0,"y":0},
                     "start":{"x":2,"y":0},"end":{"x":0,"y":2}})
            pad = command({"type":"pad_sketch_to_body","body_id":base,"sketch_id":sketch,"depth":3})
            self.assertIn("profile_segments", pad["geometry"]["tool"])
            command({"type":"set_parameter","feature_id":pad["feature_id"],"name":"depth","value":4})
            core.release_lease(project, lease)

    def test_pocket_profile_is_hidden_with_new_and_legacy_lineage(self):
        import tempfile
        from cadyx.core import CadyxCore
        from cadyx.rendering import render_scene
        with tempfile.TemporaryDirectory() as directory:
            core = CadyxCore(directory)
            project = core.create_project("Pocket lineage")["project"]["id"]
            lease = core.acquire_lease(project, "agent", "test")["lease_id"]
            def command(value):
                return core.execute_command(project, lease, value)["history_entry"]["result"]
            base = command({"type":"create_box","width":5,"height":5,"depth":5})["body_id"]
            sketch = command({"type":"create_sketch","plane":"XY","section_normal_axis":"Z",
                              "section_offset":2})["sketch_id"]
            command({"type":"add_sketch_rectangle","sketch_id":sketch,"x":1,"y":1,"width":2,"height":5})
            result = command({"type":"pocket_sketch","body_id":base,"sketch_id":sketch,"depth":2})
            document = Document.from_dict(core.inspect_project(project)["document"])
            feature = next(item for item in document.objects if item.get("id") == result["feature_id"])
            self.assertEqual(feature["source_sketch_id"], sketch)
            for legacy in (False, True):
                if legacy:
                    feature["source_sketch_id"] = None
                scene = render_scene(document, core.kernel)
                self.assertFalse(any(item.get("kind") in {"line","point"}
                                     for item in scene["objects"]))
            core.release_lease(project, lease)
