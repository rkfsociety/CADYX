from __future__ import annotations
import tempfile
import unittest
from cadyx.core import CadyxCore
from cadyx.kernel import OCCTKernelAdapter
from cadyx.native_kernel import NativeKernel
from cadyx.thread_profiles import thread_tool
from cadyx.errors import ValidationError

PROFILE = {"type":"trapezoidal","minor_diameter":8.45,"crest_width":0.25,"phase_degrees":260.532}

class ProfiledThreadTests(unittest.TestCase):
    def test_native_profiled_thread_is_one_valid_clipped_solid(self):
        kernel = OCCTKernelAdapter()
        geometry = thread_tool(kernel, major_diameter=10, pitch=1.8, length=3.6,
                               handedness="left", thread_profile=PROFILE)
        kernel.validate(geometry)
        topology = NativeKernel().geometry_graph(geometry, topology=True)
        self.assertTrue(topology["brep_valid"])
        self.assertEqual(topology["topology"]["solid_count"], 1)
        self.assertAlmostEqual(topology["bounds"]["min"][2], 0, places=6)
        self.assertAlmostEqual(topology["bounds"]["max"][2], 3.6, places=6)
        self.assertGreater(geometry["volume"], 3.14159 * (8.45/2)**2 * 3.6)
        self.assertLess(geometry["volume"], 3.1416 * 25 * 3.6)

    def test_custom_threaded_hole_keeps_profile_and_rebuilds_upstream(self):
        with tempfile.TemporaryDirectory() as directory:
            core = CadyxCore(directory)
            project = core.create_project("Custom thread")["project"]["id"]
            lease = core.acquire_lease(project, "agent", "test")["lease_id"]
            def command(value):
                return core.execute_command(project, lease, value)["history_entry"]["result"]
            base = command({"type":"create_box","width":20,"height":20,"depth":6})
            result = command({"type":"threaded_hole","body_id":base["body_id"],
                "origin":[10,10,1],"axis":[0,0,1],"major_diameter":10,"pitch":1.8,
                "length":3.6,"handedness":"left","thread_profile":PROFILE})
            feature = next(item for item in core.inspect_project(project)["document"]["objects"]
                           if item.get("id") == result["feature_id"])
            self.assertEqual(feature["parameters"]["thread_profile"], PROFILE)
            command({"type":"set_parameter","feature_id":base["feature_id"],"name":"depth","value":7})
            core.release_lease(project,lease)

    def test_invalid_profile_rejected_before_native_execution(self):
        for changes in ({"minor_diameter":11}, {"crest_width":1.8}, {"phase_degrees":True}):
            with self.assertRaises(ValidationError):
                thread_tool(OCCTKernelAdapter(), major_diameter=10, pitch=1.8,length=3.6,
                            thread_profile={**PROFILE,**changes})
