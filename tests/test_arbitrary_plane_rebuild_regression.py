import unittest
from types import SimpleNamespace

from cadyx.commands import CommandSystem
from cadyx.kernel import OCCTKernelAdapter
from cadyx.models import Document
from cadyx.rebuild_regression import run_rebuild_regression


class ArbitraryPlaneRebuildRegressionTests(unittest.TestCase):
    def test_arbitrary_plane_root_part_passes_saved_cold_warm_rebuild(self):
        system = CommandSystem(OCCTKernelAdapter())
        document = Document.create()
        system.execute(document, {
            "type": "extrude_profile_on_plane",
            "profile": [{"x": 0, "y": 0}, {"x": 35, "y": 0}, {"x": 35, "y": 45}],
            "origin": [-20, 15, 10],
            "normal": [1, 0, 0],
            "x_axis": [0, 1, 0],
            "depth": 8,
            "name": "Parametric rib",
        })

        status, evidence = run_rebuild_regression(
            SimpleNamespace(kernel=system.kernel, commands=system),
            document, max_bodies=128, checkpoint=lambda: None,
        )

        self.assertEqual(status, "pass", evidence)
        self.assertEqual(evidence["compared_bodies"], 1)
        self.assertEqual(evidence["metric_difference_count"], 0)
        self.assertEqual(evidence["descriptor_difference_count"], 0)


if __name__ == "__main__":
    unittest.main()
