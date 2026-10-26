from __future__ import annotations

import math
import unittest

from cadyx.commands import CommandSystem
from cadyx.kernel import OCCTKernelAdapter
from cadyx.models import Document


class GroupedSketchFeatureTests(unittest.TestCase):
    def test_disjoint_circle_pockets_share_one_feature_and_rebuild(self) -> None:
        system = CommandSystem(OCCTKernelAdapter())
        document = Document.create()
        base = system.execute(document, {"type": "create_box", "name": "Block",
                                         "width": 10, "height": 10, "depth": 2})
        sketch = system.execute(document, {"type": "create_sketch", "name": "Two bores",
                                           "plane": "XY", "section_normal_axis": "Z",
                                           "section_offset": 0})
        system.execute(document, {"type": "add_sketch_geometry_batch",
                                  "sketch_id": sketch["sketch_id"], "elements": [
                                      {"type": "circle", "x": 3, "y": 5, "radius": 1},
                                      {"type": "circle", "x": 7, "y": 5, "radius": 1},
                                  ]})
        groups = [{"outer_index": 0, "inner_indices": []},
                  {"outer_index": 1, "inner_indices": []}]
        pocket = system.execute(document, {"type": "pocket_sketch", "body_id": base["body_id"],
                                          "sketch_id": sketch["sketch_id"], "depth": 2,
                                          "profile_groups": groups, "name": "Two through bores"})
        self.assertAlmostEqual(pocket["geometry"]["volume"], 200 - 4 * math.pi, delta=1e-6)
        self.assertEqual(len([item for item in document.objects if item.get("operation") == "pocket_sketch"]), 1)

        system.execute(document, {"type": "set_parameter", "feature_id": pocket["feature_id"],
                                  "name": "depth", "value": 1})
        rebuilt = document.find_object(pocket["body_id"], "body")
        self.assertAlmostEqual(rebuilt["geometry"]["volume"], 200 - 2 * math.pi, delta=1e-6)
        self.assertEqual(rebuilt["geometry"], document.find_object(pocket["feature_id"], "feature")["geometry"])
        document.validate()

    def test_near_tangent_inner_contour_is_kept_analytic(self) -> None:
        system = CommandSystem(OCCTKernelAdapter())
        document = Document.create()
        base = system.execute(document, {"type": "create_box", "name": "Block",
                                         "width": 10, "height": 10, "depth": 2})
        sketch = system.execute(document, {"type": "create_sketch", "name": "Tangent pocket",
                                           "plane": "XY", "section_normal_axis": "Z",
                                           "section_offset": 0})
        system.execute(document, {"type": "add_sketch_geometry_batch",
                                  "sketch_id": sketch["sketch_id"], "elements": [
                                      {"type": "rectangle", "x": 2, "y": 2, "width": 4, "height": 4},
                                      {"type": "circle", "x": 3.001, "y": 3.001, "radius": 1},
                                  ]})
        result = system.execute(document, {"type": "pocket_sketch", "body_id": base["body_id"],
                                           "sketch_id": sketch["sketch_id"], "depth": 1,
                                           "profile_groups": [{"outer_index": 0, "inner_indices": [1]}]})
        self.assertAlmostEqual(result["geometry"]["volume"], 200 - (16 - math.pi), delta=1e-6)
        system.kernel.validate(result["geometry"])


if __name__ == "__main__":
    unittest.main()
