import math
import unittest
from unittest.mock import patch

import cadyx  # installs extension layers
from cadyx.agent_authoring import _rotation_from_z, _workplane_rotation
from cadyx.commands import CommandSystem
from cadyx.models import Document


class FakeKernel:
    def __init__(self):
        self.thread = None
        self.cut = None

    def metrics(self, geometry):
        return {
            "bounds": {"min": [0.0, 0.0, 0.0], "max": [100.0, 60.0, 10.0]},
            "volume": geometry.get("volume", 1000.0),
        }

    def create_extruded_profile(self, profile, *, plane, depth, centered=False):
        if plane != "XY":
            raise AssertionError("work-plane authoring must start from local XY")
        return {
            "kernel": "fake",
            "kind": "extruded_profile",
            "plane": plane,
            "profile": profile,
            "depth": depth,
            "centered": centered,
            "volume": 20.0,
        }

    def create_metric_thread(self, *, major_diameter, pitch, length, handedness="right"):
        self.thread = {
            "major_diameter": major_diameter,
            "pitch": pitch,
            "length": length,
            "handedness": handedness,
        }
        return {"kernel": "fake", "kind": "metric_thread", "volume": 10.0}

    def cut_solids(self, base, tool):
        self.cut = (base, tool)
        return {
            "kernel": "fake",
            "kind": "cut_solids",
            "base": base,
            "tool": tool,
            "volume": base.get("volume", 100.0) - 1.0,
        }


class AgentAuthoringTests(unittest.TestCase):
    @staticmethod
    def seed_body(document):
        body_id = "body:00000000-0000-0000-0000-000000000020"
        feature_id = "feature:00000000-0000-0000-0000-000000000020"
        geometry = {
            "kernel": "fake",
            "kind": "box",
            "dimensions": {"width": 100.0, "height": 60.0, "depth": 10.0},
            "volume": 60000.0,
        }
        document.objects.extend(
            [
                {
                    "id": body_id,
                    "type": "body",
                    "name": "Seed",
                    "feature_ids": [feature_id],
                    "geometry": geometry,
                },
                {
                    "id": feature_id,
                    "type": "feature",
                    "body_id": body_id,
                    "operation": "seed",
                    "geometry": geometry,
                },
            ]
        )
        return body_id

    def test_through_hole_resolves_a_cutter_across_bounds(self):
        kernel = FakeKernel()
        system = CommandSystem(kernel)
        document = Document.create()
        body_id = self.seed_body(document)
        calls = []

        def cut(geometry, operation, values, indices=None):
            calls.append((operation, list(values)))
            return {"kernel": "fake", "kind": "cut", "volume": geometry["volume"] - 100.0}

        with patch("cadyx.agent_authoring._advanced_native", side_effect=cut):
            result = system.execute(
                document,
                {
                    "type": "through_hole",
                    "body_id": body_id,
                    "origin": [20.0, 20.0, 0.0],
                    "axis": [0.0, 0.0, 1.0],
                    "diameter": 8.0,
                },
            )

        self.assertTrue(result["body_id"].startswith("body:"))
        self.assertEqual(calls[0][0], 9)
        values = calls[0][1]
        self.assertLess(values[2], 0.0)
        self.assertGreater(values[7], math.dist([0, 0, 0], [100, 60, 10]))

    def test_counterbore_uses_main_and_larger_shallow_cuts(self):
        kernel = FakeKernel()
        system = CommandSystem(kernel)
        document = Document.create()
        body_id = self.seed_body(document)
        calls = []

        def cut(geometry, operation, values, indices=None):
            calls.append((operation, list(values)))
            return {"kernel": "fake", "kind": "cut", "volume": geometry["volume"] - 100.0}

        with patch("cadyx.agent_authoring._advanced_native", side_effect=cut):
            system.execute(
                document,
                {
                    "type": "counterbore_hole",
                    "body_id": body_id,
                    "origin": [10.0, 10.0, 0.0],
                    "axis": [0.0, 0.0, 1.0],
                    "hole_diameter": 6.0,
                    "counterbore_diameter": 12.0,
                    "counterbore_depth": 3.0,
                    "through": False,
                    "hole_depth": 9.0,
                },
            )

        self.assertEqual([call[0] for call in calls], [9, 9])
        self.assertEqual(calls[0][1][6:], [6.0, 9.0])
        self.assertEqual(calls[1][1][6:], [12.0, 3.0])

    def test_countersink_computes_depth_from_included_angle(self):
        kernel = FakeKernel()
        system = CommandSystem(kernel)
        document = Document.create()
        body_id = self.seed_body(document)
        native_calls = []

        def cylinder(geometry, operation, values, indices=None):
            return {"kernel": "fake", "kind": "cut", "volume": geometry["volume"] - 100.0}

        def feature(geometry, operation, values):
            native_calls.append((operation, list(values)))
            return {"kernel": "fake", "kind": "countersink", "volume": geometry["volume"] - 50.0}

        with (
            patch("cadyx.agent_authoring._advanced_native", side_effect=cylinder),
            patch("cadyx.agent_authoring._agent_native", side_effect=feature),
        ):
            system.execute(
                document,
                {
                    "type": "countersink_hole",
                    "body_id": body_id,
                    "origin": [10.0, 10.0, 0.0],
                    "axis": [0.0, 0.0, 1.0],
                    "hole_diameter": 6.0,
                    "countersink_diameter": 12.0,
                    "angle_degrees": 90.0,
                    "through": False,
                    "hole_depth": 9.0,
                },
            )

        self.assertEqual(native_calls[0][0], 1)
        values = native_calls[0][1]
        self.assertEqual(values[6:8], [12.0, 6.0])
        self.assertAlmostEqual(values[8], 3.0)

    def test_arbitrary_plane_extrusion_rotates_local_xy_and_translates(self):
        kernel = FakeKernel()
        system = CommandSystem(kernel)
        document = Document.create()
        native_calls = []

        def feature(geometry, operation, values):
            native_calls.append((operation, list(values)))
            return {"kernel": "fake", "kind": "transformed", "volume": geometry["volume"]}

        with patch("cadyx.agent_authoring._agent_native", side_effect=feature):
            result = system.execute(
                document,
                {
                    "type": "extrude_profile_on_plane",
                    "profile": [
                        {"x": 0.0, "y": 0.0},
                        {"x": 10.0, "y": 0.0},
                        {"x": 10.0, "y": 5.0},
                        {"x": 0.0, "y": 5.0},
                    ],
                    "origin": [10.0, 20.0, 30.0],
                    "normal": [0.0, 1.0, 0.0],
                    "x_axis": [1.0, 0.0, 0.0],
                    "depth": 4.0,
                },
            )

        self.assertTrue(result["body_id"].startswith("body:"))
        self.assertEqual(native_calls[0][0], 2)
        values = native_calls[0][1]
        self.assertAlmostEqual(abs(values[0]), 1.0)
        self.assertAlmostEqual(values[3], math.pi / 2.0)
        self.assertEqual(values[4:], [10.0, 20.0, 30.0])

    def test_threaded_hole_uses_metric_thread_as_boolean_tool(self):
        kernel = FakeKernel()
        system = CommandSystem(kernel)
        document = Document.create()
        body_id = self.seed_body(document)

        with patch(
            "cadyx.agent_authoring._agent_native",
            return_value={"kernel": "fake", "kind": "thread_tool", "volume": 10.0},
        ) as native:
            system.execute(
                document,
                {
                    "type": "threaded_hole",
                    "body_id": body_id,
                    "origin": [5.0, 5.0, 0.0],
                    "axis": [0.0, 0.0, 1.0],
                    "major_diameter": 10.0,
                    "pitch": 1.5,
                    "length": 12.0,
                },
            )

        self.assertEqual(kernel.thread["pitch"], 1.5)
        self.assertIsNotNone(kernel.cut)
        self.assertEqual(native.call_args.args[1], 2)

    def test_rotation_helpers_cover_opposite_axis_and_workplane(self):
        axis, angle, target = _rotation_from_z([0.0, 0.0, -2.0])
        self.assertEqual(axis, [1.0, 0.0, 0.0])
        self.assertAlmostEqual(angle, math.pi)
        self.assertEqual(target, [0.0, 0.0, -1.0])

        rotation_axis, angle, normal, local_x = _workplane_rotation(
            [0.0, 1.0, 0.0], [1.0, 0.0, 0.0]
        )
        self.assertAlmostEqual(abs(rotation_axis[0]), 1.0)
        self.assertAlmostEqual(angle, math.pi / 2.0)
        self.assertEqual(normal, [0.0, 1.0, 0.0])
        self.assertEqual(local_x, [1.0, 0.0, 0.0])


if __name__ == "__main__":
    unittest.main()
