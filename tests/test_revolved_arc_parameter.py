from __future__ import annotations

import tempfile
import unittest

from cadyx.core import CadyxCore
from cadyx.models import Document


class RevolvedArcParameterTests(unittest.TestCase):
    def test_outer_diameter_change_and_exact_restore_rebuilds_ellipse_arcs(self):
        with tempfile.TemporaryDirectory() as directory:
            core = CadyxCore(directory)
            project_id = core.create_project("Revolved arc parameter")["project"]["id"]
            lease_id = core.acquire_lease(project_id, "agent", "arc regression")["lease_id"]

            def execute(command):
                return core.execute_command(project_id, lease_id, command)["history_entry"]["result"]

            sketch_id = execute({"type": "create_sketch", "plane": "XZ"})["sketch_id"]
            execute({"type": "add_sketch_line", "sketch_id": sketch_id,
                     "start": {"x": 0.0, "y": 0.0}, "end": {"x": 2.0, "y": 0.0}})
            execute({"type": "add_sketch_arc_center", "sketch_id": sketch_id,
                     "center": {"x": 0.0, "y": 0.0},
                     "start": {"x": 2.0, "y": 0.0}, "end": {"x": 0.0, "y": 2.0}})
            execute({"type": "add_sketch_line", "sketch_id": sketch_id,
                     "start": {"x": 0.0, "y": 2.0}, "end": {"x": 0.0, "y": 0.0}})
            revolved = execute({"type": "revolve_sketch", "sketch_id": sketch_id,
                                "angle_degrees": 360.0})
            initial_volume = revolved["geometry"]["volume"]

            execute({"type": "set_parameter", "feature_id": revolved["feature_id"],
                     "name": "outer_diameter", "value": 3.6})
            shrunk = Document.from_dict(core.inspect_project(project_id)["document"])
            shrink_arc = next(item for item in shrunk.objects
                              if item.get("sketch_id") == sketch_id and item.get("type") == "ellipse")
            self.assertAlmostEqual(shrink_arc["major_radius"], 2.0, places=9)
            self.assertAlmostEqual(shrink_arc["minor_radius"], 1.8, places=9)
            shrunk_body = next(item for item in shrunk.objects
                               if item.get("id") == revolved["body_id"])
            self.assertEqual(sum(segment[0] == 2 for segment in
                                 shrunk_body["geometry"]["profile_segments"]), 1)

            restored = execute({"type": "set_parameter", "feature_id": revolved["feature_id"],
                                "name": "outer_diameter", "value": 4.0})
            self.assertAlmostEqual(restored["geometry"]["volume"], initial_volume, places=8)
            final_document = Document.from_dict(core.inspect_project(project_id)["document"])
            feature = next(item for item in final_document.objects
                           if item.get("id") == revolved["feature_id"])
            self.assertEqual(feature["parameters"]["outer_diameter"], 4.0)
            final_arc = next(item for item in final_document.objects
                             if item.get("sketch_id") == sketch_id and item.get("type") == "ellipse")
            self.assertAlmostEqual(final_arc["major_radius"], 2.0, places=9)
            self.assertAlmostEqual(final_arc["minor_radius"], 2.0, places=9)
            core.release_lease(project_id, lease_id)
