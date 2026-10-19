from __future__ import annotations

import unittest

from cadyx.commands import CommandSystem
from cadyx.kernel import OCCTKernelAdapter
from cadyx.models import Document


class ArbitraryPlaneParameterRebuildTests(unittest.TestCase):
    def test_bound_depth_rebuilds_arbitrary_plane_extrusion_geometry(self):
        kernel = OCCTKernelAdapter()
        system = CommandSystem(kernel)
        document = Document.create()
        system.execute(document, {"type": "create_model_parameter", "name": "WallDepth",
                                  "value": 10, "unit": "mm"})
        extrusion = system.execute(document, {
            "type": "extrude_profile_on_plane",
            "profile": [{"x": 0, "y": 0}, {"x": 10, "y": 0},
                        {"x": 10, "y": 5}, {"x": 0, "y": 5}],
            "origin": [2, 3, 4], "normal": [0, 1, 0],
            "x_axis": [1, 0, 0], "depth": 10,
        })
        system.execute(document, {"type": "bind_model_parameter",
            "object_id": extrusion["feature_id"], "path": "parameters.depth",
            "expression": "WallDepth"})

        system.execute(document, {"type": "set_model_parameter",
                                  "name": "WallDepth", "value": 15})

        feature = next(item for item in document.objects
                       if item.get("id") == extrusion["feature_id"])
        body = next(item for item in document.objects
                    if item.get("id") == extrusion["body_id"])
        metrics = kernel.metrics(body["geometry"])
        self.assertEqual(feature["parameters"]["depth"], 15)
        self.assertAlmostEqual(body["geometry"]["volume"], 750, places=6)
        for actual, expected in zip(metrics["bounds"]["min"], [2, 3, -1], strict=True):
            self.assertAlmostEqual(actual, expected, places=6)
        for actual, expected in zip(metrics["bounds"]["max"], [12, 18, 4], strict=True):
            self.assertAlmostEqual(actual, expected, places=6)
        document.validate()


if __name__ == "__main__":
    unittest.main()
