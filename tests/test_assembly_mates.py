"""Pure assembly mating, graph migration, and Core inspection regressions.

Run locally and in CI; these cases use only a primitive fixture.
"""
from __future__ import annotations

import copy
import math
import tempfile
import unittest

from cadyx.assembly_mates import inspect_assembly_mates
from cadyx.migrations import DOCUMENT_VERSION
from cadyx.commands import CommandSystem
from cadyx.core import CadyxCore
from cadyx.errors import ValidationError
from cadyx.kernel import PrimitiveKernelAdapter
from cadyx.migrations import migrate_document
from cadyx.models import Document


class AssemblyMateTests(unittest.TestCase):
    def setUp(self):
        self.document = Document.create()
        self.commands = CommandSystem(PrimitiveKernelAdapter())
        body = self.run_command("create_box", width=1, height=2, depth=3)["body_id"]
        part = self.run_command("create_assembly_part", body_id=body)["part_id"]
        self.a = self.run_command("add_assembly_instance", target_id=part, name="A")["instance_id"]
        self.b = self.run_command("add_assembly_instance", target_id=part, name="B",
                                  translation=[8, 3, 1])["instance_id"]
        self.c = self.run_command("add_assembly_instance", target_id=part, name="C",
                                  translation=[1, 4, 0])["instance_id"]

    def run_command(self, command_type, **fields):
        result = self.commands.execute(self.document, {"type": command_type, **fields})
        self.document.validate()
        return result

    def placement(self, instance_id):
        return next(item for item in self.document.assembly["instances"] if item["id"] == instance_id)

    def test_fixed_chain_and_consistent_redundant_cycle(self):
        self.assertEqual(inspect_assembly_mates(self.document.assembly)["status"],
                         "underconstrained")
        self.run_command("add_assembly_mate", kind="fixed", a_instance_id=self.a,
                         b_instance_id=self.b)
        self.run_command("add_assembly_mate", kind="fixed", a_instance_id=self.b,
                         b_instance_id=self.c)
        result = self.run_command("add_assembly_mate", kind="fixed", a_instance_id=self.a,
                                  b_instance_id=self.c)["kinematics"]
        self.assertEqual(result["internal_dof"], 0)
        self.assertEqual(len(result["redundant_mate_ids"]), 1)
        self.assertEqual(result["status"], "overconstrained")
        self.assertEqual(self.placement(self.a)["translation"],
                         self.placement(self.c)["translation"])

    def test_revolute_slider_coaxial_and_cylindrical_coordinates(self):
        mate_id = self.run_command("add_assembly_mate", kind="revolute",
                                   a_instance_id=self.a, b_instance_id=self.b)["mate_id"]
        report = self.run_command("set_assembly_mate", mate_id=mate_id,
                                  angle_deg=90)["kinematics"]
        self.assertEqual(report["internal_dof"], 1)
        self.assertAlmostEqual(abs(self.placement(self.b)["rotation"][0]), math.sqrt(0.5))
        self.run_command("remove_assembly_mate", mate_id=mate_id)
        mate_id = self.run_command("add_assembly_mate", kind="slider",
                                   a_instance_id=self.a, b_instance_id=self.b)["mate_id"]
        self.run_command("set_assembly_mate", mate_id=mate_id, offset_mm=12)
        self.assertAlmostEqual(self.placement(self.b)["translation"][2], 12)
        self.run_command("remove_assembly_mate", mate_id=mate_id)
        mate_id = self.run_command("add_assembly_mate", kind="cylindrical",
                                   a_instance_id=self.a, b_instance_id=self.b,
                                   angle_deg=180, offset_mm=4)["mate_id"]
        self.assertAlmostEqual(self.placement(self.b)["translation"][2], 4)
        self.assertEqual(inspect_assembly_mates(self.document.assembly)["internal_dof"], 2)
        self.run_command("remove_assembly_mate", mate_id=mate_id)
        self.run_command("add_assembly_mate", kind="coaxial",
                         a_instance_id=self.a, b_instance_id=self.b, offset_mm=-2)
        self.assertAlmostEqual(self.placement(self.b)["translation"][2], -2)

    def test_local_frames_and_coincident_orientation(self):
        local_frame = {"translation": [1, 0, 0], "rotation": [1, 0, 0, 0]}
        self.run_command("add_assembly_mate", kind="coincident", a_instance_id=self.a,
                         b_instance_id=self.b, frame_a=local_frame,
                         orientation=[math.sqrt(0.5), 0, math.sqrt(0.5), 0])
        self.assertAlmostEqual(self.placement(self.b)["translation"][0], 1)
        self.assertEqual(inspect_assembly_mates(self.document.assembly)["internal_dof"], 3)

    def test_invalid_joint_parameters_and_cross_parent(self):
        for kind in ("hinge", [], None):
            with self.assertRaises(ValidationError):
                self.run_command("add_assembly_mate", kind=kind,
                                 a_instance_id=self.a, b_instance_id=self.b)
            self.document.assembly["mates"].clear()  # Pure facade has no transaction.
        sub = self.run_command("create_subassembly", name="Sub")["subassembly_id"]
        nested = self.run_command("add_assembly_instance",
                                  target_id=self.document.assembly["parts"][0]["id"],
                                  parent_id=sub)["instance_id"]
        with self.assertRaises(ValidationError):
            self.run_command("add_assembly_mate", kind="fixed", a_instance_id=self.a,
                             b_instance_id=nested)
        self.document.assembly["mates"].clear()

    def test_conflicting_closed_loop_rejected(self):
        self.run_command("add_assembly_mate", kind="fixed", a_instance_id=self.a,
                         b_instance_id=self.b)
        self.run_command("add_assembly_mate", kind="fixed", a_instance_id=self.b,
                         b_instance_id=self.c)
        before = copy.deepcopy(self.document.assembly)
        with self.assertRaisesRegex(ValidationError, "Conflicting assembly mate loop"):
            self.run_command("add_assembly_mate", kind="slider", a_instance_id=self.a,
                             b_instance_id=self.c, offset_mm=10)
        self.document.assembly = before
        self.document.validate()

    def test_transform_anchors_part_and_removal_requires_detach(self):
        mate_id = self.run_command("add_assembly_mate", kind="fixed", a_instance_id=self.a,
                                   b_instance_id=self.b)["mate_id"]
        report = self.run_command("transform_assembly_instance", instance_id=self.b,
                                  translation=[7, 0, 0])["kinematics"]
        self.assertEqual(report["internal_dof"], 0)
        self.assertEqual(self.placement(self.a)["translation"], [7, 0, 0])
        with self.assertRaises(ValidationError):
            self.run_command("remove_assembly_instance", instance_id=self.b)
        self.run_command("remove_assembly_mate", mate_id=mate_id)
        self.run_command("remove_assembly_instance", instance_id=self.b)

    def test_joint_update_cannot_move_another_grounded_instance(self):
        mate_id = self.run_command("add_assembly_mate", kind="slider",
                                   a_instance_id=self.a, b_instance_id=self.b)["mate_id"]
        self.run_command("set_assembly_instance_properties", instance_id=self.a, grounded=True)
        self.run_command("set_assembly_instance_properties", instance_id=self.b, grounded=True)
        with self.assertRaisesRegex(ValidationError, "Conflicting grounded instance"):
            self.run_command("set_assembly_mate", mate_id=mate_id, offset_mm=5)

    def test_migrate_v9_preserves_existing_instances(self):
        old = self.document.to_dict()
        old["version"] = 9
        old["assembly"].pop("mates")
        for part in old["assembly"]["parts"]:
            part.pop("material")
        for instance in old["assembly"]["instances"]:
            instance.pop("grounded")
            instance.pop("visible")
        old["assembly"]["version"] = 1
        before = copy.deepcopy(old)
        upgraded = migrate_document(old)
        self.assertEqual(old, before)
        self.assertEqual(upgraded["version"], DOCUMENT_VERSION)
        self.assertEqual(upgraded["assembly"]["version"], 4)
        self.assertEqual(upgraded["assembly"]["mates"], [])
        self.assertEqual(Document.from_dict(upgraded).to_dict(), upgraded)

    def test_core_read_only_inspection_includes_saved_revision_and_dof(self):
        with tempfile.TemporaryDirectory() as directory:
            core = CadyxCore(directory, kernel=PrimitiveKernelAdapter())
            project_id = core.create_project("Mate inspection")["project"]["id"]
            lease_id = core.acquire_lease(project_id, "human", "mate-regression")["lease_id"]

            def execute(command):
                output = core.execute_command(project_id, lease_id, command)
                return output["history_entry"]["result"]

            body = execute({"type": "create_box", "width": 1,
                            "height": 1, "depth": 1})["body_id"]
            part = execute({"type": "create_assembly_part", "body_id": body})["part_id"]
            a = execute({"type": "add_assembly_instance", "target_id": part})["instance_id"]
            b = execute({"type": "add_assembly_instance", "target_id": part})["instance_id"]
            execute({"type": "add_assembly_mate", "kind": "fixed",
                     "a_instance_id": a, "b_instance_id": b})
            execute({"type": "set_assembly_instance_properties", "instance_id": b,
                     "grounded": True, "visible": False})
            snapshot = core.inspect_project(project_id)
            self.assertEqual(snapshot["assembly_kinematics"]["internal_dof"], 0)
            self.assertEqual(snapshot["assembly_kinematics"]["global_dof"], 0)
            self.assertEqual(len(snapshot["document"]["assembly"]["mates"]), 1)
            saved = {instance["id"]: instance
                     for instance in snapshot["document"]["assembly"]["instances"]}[b]
            self.assertTrue(saved["grounded"])
            self.assertFalse(saved["visible"])
            core.release_lease(project_id, lease_id)
