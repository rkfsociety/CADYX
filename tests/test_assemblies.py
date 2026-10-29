"""No-native-SDK assembly graph, migration and renderer regression cases."""
from __future__ import annotations

import copy
import math
import unittest

from cadyx.assemblies import flatten_assembly, transform_point
from cadyx.commands import CommandSystem
from cadyx.errors import ValidationError
from cadyx.kernel import PrimitiveKernelAdapter
from cadyx.migrations import DOCUMENT_VERSION, migrate_document
from cadyx.models import Document
from cadyx.rendering import render_scene


class AssemblyTests(unittest.TestCase):
    def setUp(self):
        self.kernel = PrimitiveKernelAdapter()
        self.commands = CommandSystem(self.kernel)
        self.document = Document.create()

    def cmd(self, kind, **fields):
        result = self.commands.execute(self.document, {"type": kind, **fields})
        self.document.validate()
        return result

    def test_v8_migration_preserves_source(self):
        old = {"id": self.document.id, "version": 8, "revision": 0,
               "objects": [], "model_parameters": {}, "parameter_bindings": []}
        before = copy.deepcopy(old)
        new = migrate_document(old)
        self.assertEqual(old, before)
        self.assertEqual(new["version"], DOCUMENT_VERSION)
        self.assertEqual(new["assembly"], {"version": 4, "units": "mm",
                                         "parts": [], "subassemblies": [], "instances": [], "mates": []})
        self.assertEqual(Document.from_dict(new).to_dict(), new)

    def test_nested_repeated_part_instances_and_render(self):
        body = self.cmd("create_box", width=2, height=3, depth=4)["body_id"]
        part = self.cmd("create_assembly_part", body_id=body)["part_id"]
        sub = self.cmd("create_subassembly", name="Pair")["subassembly_id"]
        self.cmd("add_assembly_instance", target_id=part, parent_id=sub,
                 translation=[2, 0, 0])
        self.cmd("add_assembly_instance", target_id=part, parent_id=sub,
                 translation=[0, 3, 0])
        root = self.cmd("add_assembly_instance", target_id=sub,
                        translation=[10, 0, 0],
                        rotation=[math.sqrt(0.5), 0, 0, math.sqrt(0.5)])
        placed = flatten_assembly(self.document.assembly)
        self.assertEqual(len(placed), 2)
        self.assertEqual({p["body_id"] for p in placed}, {body})
        self.assertTrue(all(p["instance_path"][0] == root["instance_id"] for p in placed))
        self.assertAlmostEqual(placed[0]["translation"][0], 10)
        self.assertAlmostEqual(placed[0]["translation"][1], 2)
        self.assertAlmostEqual(placed[1]["translation"][0], 7)
        scene = render_scene(self.document, self.kernel)
        objects = [obj for obj in scene["objects"] if "instance_path" in obj]
        self.assertEqual(len(objects), 2)
        self.assertEqual(len({obj["object_id"] for obj in objects}), 2)
        self.assertEqual({obj["source_body_id"] for obj in objects}, {body})
        self.assertTrue(scene["bounds"]["max"][0] >= 10)

    def test_cycles_and_invalid_transform_rejected(self):
        a = self.cmd("create_subassembly", name="A")["subassembly_id"]
        b = self.cmd("create_subassembly", name="B")["subassembly_id"]
        self.cmd("add_assembly_instance", target_id=b, parent_id=a)
        snapshot = copy.deepcopy(self.document.assembly)
        with self.assertRaises(ValidationError):
            self.cmd("add_assembly_instance", target_id=a, parent_id=b)
        self.document.assembly = snapshot
        body = self.cmd("create_box", width=1, height=2, depth=3)["body_id"]
        part = self.cmd("create_assembly_part", body_id=body)["part_id"]
        with self.assertRaises(ValidationError):
            self.cmd("add_assembly_instance", target_id=part, rotation=[0, 0, 0, 0])
        self.document.assembly["instances"].pop()
        with self.assertRaises(ValidationError):
            self.cmd("add_assembly_instance", target_id=part, translation=[float("nan"), 0, 0])
        self.document.assembly["instances"].pop()

    def test_referenced_part_cannot_be_removed(self):
        body = self.cmd("create_box", width=1, height=1, depth=1)["body_id"]
        part = self.cmd("create_assembly_part", body_id=body)["part_id"]
        instance = self.cmd("add_assembly_instance", target_id=part)["instance_id"]
        with self.assertRaises(ValidationError):
            self.cmd("remove_assembly_component", component_id=part)
        self.cmd("remove_assembly_instance", instance_id=instance)
        self.cmd("remove_assembly_component", component_id=part)

    def test_grounded_instance_is_fixed_and_reported(self):
        body = self.cmd("create_box", width=1, height=1, depth=1)["body_id"]
        part = self.cmd("create_assembly_part", body_id=body)["part_id"]
        instance = self.cmd("add_assembly_instance", target_id=part)["instance_id"]
        result = self.cmd("set_assembly_instance_properties", instance_id=instance,
                          grounded=True)
        self.assertEqual(result["kinematics"]["grounded_instance_ids"], [instance])
        self.assertEqual(result["kinematics"]["global_dof"], 0)
        with self.assertRaisesRegex(ValidationError, "grounded assembly instance"):
            self.cmd("transform_assembly_instance", instance_id=instance,
                     translation=[3, 0, 0])
        self.cmd("set_assembly_instance_properties", instance_id=instance, grounded=False)
        moved = self.cmd("transform_assembly_instance", instance_id=instance,
                         translation=[3, 0, 0])
        self.assertEqual(moved["translation"], [3, 0, 0])

    def test_grounded_member_rejects_rigid_group_transform(self):
        body = self.cmd("create_box", width=1, height=1, depth=1)["body_id"]
        part = self.cmd("create_assembly_part", body_id=body)["part_id"]
        a = self.cmd("add_assembly_instance", target_id=part)["instance_id"]
        b = self.cmd("add_assembly_instance", target_id=part, translation=[4, 0, 0])["instance_id"]
        self.commands.execute(self.document, {"type": "add_assembly_mate", "kind": "fixed",
                                             "a_instance_id": a, "b_instance_id": b})
        self.cmd("set_assembly_instance_properties", instance_id=a, grounded=True)
        with self.assertRaisesRegex(ValidationError, "group contains a grounded instance"):
            self.cmd("transform_assembly_instance", instance_id=b, translation=[2, 0, 0])

    def test_hidden_subassembly_occurrence_is_omitted_from_scene(self):
        body = self.cmd("create_box", width=1, height=1, depth=1)["body_id"]
        part = self.cmd("create_assembly_part", body_id=body)["part_id"]
        sub = self.cmd("create_subassembly", name="Hidden group")["subassembly_id"]
        self.cmd("add_assembly_instance", target_id=part, parent_id=sub)
        root = self.cmd("add_assembly_instance", target_id=sub)["instance_id"]
        self.assertEqual(len(flatten_assembly(self.document.assembly)), 1)
        self.cmd("set_assembly_instance_properties", instance_id=root, visible=False)
        self.assertEqual(flatten_assembly(self.document.assembly), [])
        scene = render_scene(self.document, self.kernel)
        self.assertFalse(any("instance_path" in item for item in scene["objects"]))

    def test_quaternion_transform(self):
        q = [math.sqrt(0.5), 0, 0, math.sqrt(0.5)]
        point = transform_point([1, 0, 0], [1, 2, 3], q)
        for actual, expected in zip(point, [1, 3, 3]):
            self.assertAlmostEqual(actual, expected)
