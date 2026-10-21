"""Regression scenarios for read-only assembly instance interference screening.

Intentionally not run locally; covered by the Windows native CI lane.
"""
from __future__ import annotations

import unittest

from cadyx.assembly_fit import evaluate_assembly_fit
from cadyx.commands import CommandSystem
from cadyx.errors import ValidationError
from cadyx.kernel import OCCTKernelAdapter
from cadyx.models import Document


class AssemblyFitTests(unittest.TestCase):
    def setUp(self):
        self.kernel = OCCTKernelAdapter()
        self.commands = CommandSystem(self.kernel)
        self.document = Document.create()
        body = self.cmd("create_box", width=1, height=1, depth=1)["body_id"]
        part = self.cmd("create_assembly_part", body_id=body)["part_id"]
        self.a = self.cmd("add_assembly_instance", target_id=part, name="A")["instance_id"]
        self.b = self.cmd("add_assembly_instance", target_id=part, name="B",
                          translation=[0, 0, 3])["instance_id"]

    def cmd(self, command_type, **fields):
        result = self.commands.execute(self.document, {"type": command_type, **fields})
        self.document.validate()
        return result

    def fit(self, *, minimum=0.5, pairs=None, samples=None):
        return evaluate_assembly_fit(self.document, self.kernel, pairs, samples,
                                     minimum, 0.01)

    def test_distinct_instances_of_one_shared_part_have_exact_gap(self):
        result = self.fit()
        self.assertEqual(result["status"], "pass")
        item = result["samples"][0]["pairs"][0]
        self.assertEqual(item["body_ids"][0], item["body_ids"][1])
        self.assertAlmostEqual(item["measured_distance"], 2, places=5)
        self.assertEqual(item["interference_status"], "separate")
        self.assertFalse(result["continuous_motion_certified"])

    def test_saved_and_sampled_slider_pose_do_not_mutate_assembly(self):
        mate = self.cmd("add_assembly_mate", kind="slider", a_instance_id=self.a,
                        b_instance_id=self.b, offset_mm=3)["mate_id"]
        before = self.document.to_dict()
        result = self.fit(samples=[
            {"joint_values": [{"mate_id": mate, "offset_mm": 0}]},
            {"joint_values": [{"mate_id": mate, "offset_mm": 5}]},
        ])
        self.assertEqual([s["status"] for s in result["samples"]], ["pass", "fail", "pass"])
        self.assertEqual(result["status"], "fail")
        self.assertGreater(result["samples"][1]["pairs"][0]["intersection_volume"], 0)
        self.assertEqual(self.document.to_dict(), before)

    def test_rejects_invalid_pairs_and_joint_coordinates(self):
        with self.assertRaises(ValidationError):
            self.fit(pairs=[[self.a, self.a]])
        with self.assertRaises(ValidationError):
            self.fit(samples=[{"joint_values": [{"mate_id": "missing", "angle_deg": 45}]}])
        with self.assertRaises(ValidationError):
            self.fit(samples=[{"joint_values": [{"mate_id": "missing", "offset_mm": float("nan")}]}])
