from __future__ import annotations

import math
import tempfile
import unittest

from cadyx.core import CadyxCore
from cadyx.models import Document


class ZNormalLoftTests(unittest.TestCase):
    def test_editable_xy_sections_build_and_rebuild_along_z(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            core = CadyxCore(directory)
            project_id = core.create_project("Z loft regression")["project"]["id"]
            core.acquire_lease(project_id, "agent", "z-loft-test")
            document = Document.create()
            sketches, point_ids = [], []
            for z, radius in ((0.0, 2.0), (5.0, 3.0), (10.0, 2.0)):
                sketch_id = core.commands.execute(
                    document, {"type": "create_sketch", "plane": "XY"}, project_id=project_id,
                )["sketch_id"]
                result = core.commands.execute(document, {
                    "type": "add_sketch_polyline", "sketch_id": sketch_id,
                    "points": [{"x": radius * math.cos(math.tau * i / 24),
                                "y": radius * math.sin(math.tau * i / 24)} for i in range(24)],
                    "closed": True,
                }, project_id=project_id)
                next(obj for obj in document.objects if obj.get("id") == sketch_id).update(
                    section_normal_axis="Z", section_offset=z,
                )
                sketches.append(sketch_id)
                point_ids.append(result["point_ids"])
            loft = core.commands.execute(document, {
                "type": "loft_sketches", "sketch_ids": sketches, "name": "Лофт по Z",
            }, project_id=project_id)
            geometry = loft["geometry"]
            self.assertEqual(geometry["section_axis"], "Z")
            bounds = core.kernel.metrics(geometry)["bounds"]
            self.assertAlmostEqual(bounds["min"][2], 0.0, places=5)
            self.assertAlmostEqual(bounds["max"][2], 10.0, places=5)
            old_volume = geometry["volume"]
            point = next(obj for obj in document.objects if obj.get("id") == point_ids[1][0])
            point["x"] *= 1.2
            core.commands._rebuild_changed_features(document, {sketches[1]})
            body = next(obj for obj in document.objects if obj.get("id") == loft["body_id"])
            self.assertNotEqual(body["geometry"]["volume"], old_volume)
            document.validate()
            core._validate_geometry(document)


if __name__ == "__main__":
    unittest.main()
