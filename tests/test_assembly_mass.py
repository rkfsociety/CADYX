"""Source-only mass-property regression fixtures; OCCT assertions run in Windows CI."""
from __future__ import annotations

import copy
import math
import tempfile
import unittest

from cadyx.assembly_mass import evaluate_assembly_mass_properties
from cadyx.assemblies import validate_assembly
from cadyx.commands import CommandSystem
from cadyx.core import CadyxCore
from cadyx.errors import ConflictError, NotFoundError, ValidationError
from cadyx.kernel_primitive import PrimitiveKernelAdapter
from cadyx.migrations import DOCUMENT_VERSION
from cadyx.migrations import migrate_document
from cadyx.models import Document


class MassPropertiesTests(unittest.TestCase):
    def setUp(self):
        self.document = Document.create()
        self.kernel = PrimitiveKernelAdapter()
        self.commands = CommandSystem(self.kernel)
        self.body = self.cmd("create_box", width=2, height=4, depth=6)["body_id"]
        self.part = self.cmd("create_assembly_part", body_id=self.body)["part_id"]
        self.material = {
            "name": "Specified alloy", "density_kg_m3": 1000.0,
            "source": "User-supplied nominal density",
        }

    def cmd(self, kind, **arguments):
        return self.commands.execute(self.document, {"type": kind, **arguments})

    def test_exact_box_and_repeated_rotated_instances(self):
        self.cmd("set_assembly_part_material", part_id=self.part, material=self.material)
        self.cmd("add_assembly_instance", target_id=self.part)
        q = math.sqrt(0.5)
        self.cmd("add_assembly_instance", target_id=self.part, name="Turned",
                 translation=[10, 0, 0], rotation=[q, 0, 0, q])
        self.document.validate()
        before = copy.deepcopy(self.document.to_dict())
        measured = evaluate_assembly_mass_properties(self.document, self.kernel)
        self.assertEqual(self.document.to_dict(), before)
        m = 48e-6
        self.assertAlmostEqual(measured["parts"][0]["mass_kg"], m)
        self.assertEqual(measured["parts"][0]["center_of_mass_mm"], [1, 2, 3])
        self.assertAlmostEqual(measured["parts"][0]["inertia_kg_mm2"][0][0],
                               m * (4**2 + 6**2) / 12)
        self.assertEqual(len(measured["instances"]), 2)
        centers = [entry["center_of_mass_mm"] for entry in measured["instances"]]
        for actual, expected in zip(centers[1], [8, 1, 3]):
            self.assertAlmostEqual(actual, expected)
        aggregate = measured["assembly"]
        self.assertAlmostEqual(aggregate["mass_kg"], 2 * m)
        for actual, expected in zip(aggregate["center_of_mass_mm"], [4.5, 1.5, 3]):
            self.assertAlmostEqual(actual, expected)
        # Izz includes each rotated local inertia and the COM parallel-axis shifts.
        local_zz = m * (2**2 + 4**2) / 12
        expected_zz = 2 * local_zz + 2 * m * (3.5**2 + 0.5**2)
        self.assertAlmostEqual(aggregate["inertia_kg_mm2"][2][2], expected_zz)
        self.assertEqual(measured["units"]["inertia"], "kg*mm^2")

    def test_missing_density_and_mesh_or_bad_values_fail_closed(self):
        self.cmd("add_assembly_instance", target_id=self.part)
        with self.assertRaisesRegex(ValidationError, "no density"):
            evaluate_assembly_mass_properties(self.document, self.kernel)
        for material in (
            {"name": "X", "density_kg_m3": 0, "source": "S"},
            {"name": "X", "density_kg_m3": float("nan"), "source": "S"},
            {"name": "X", "density_kg_m3": 10.0, "source": ""},
            {"name": "X", "density_kg_m3": 10.0},
        ):
            with self.assertRaises(ValidationError):
                self.cmd("set_assembly_part_material", part_id=self.part, material=material)
        self.cmd("set_assembly_part_material", part_id=self.part, material=self.material)
        self.document.validate()
        self.document.assembly["parts"][0]["material"]["source"] = ""
        with self.assertRaises(ValidationError):
            self.document.validate()

    def test_material_clear_and_legacy_migration(self):
        self.cmd("set_assembly_part_material", part_id=self.part, material=self.material)
        saved = self.document.to_dict()
        old = copy.deepcopy(saved)
        old["version"] = 10
        old["assembly"]["version"] = 2
        for part in old["assembly"]["parts"]:
            part.pop("material")
        migrated = migrate_document(old)
        self.assertEqual(migrated["version"], DOCUMENT_VERSION)
        self.assertEqual(migrated["assembly"]["version"], 4)
        self.assertIsNone(migrated["assembly"]["parts"][0]["material"])
        self.assertEqual(old["assembly"]["version"], 2)
        self.cmd("set_assembly_part_material", part_id=self.part, material=None)
        self.document.validate()
        self.assertIsNone(self.document.assembly["parts"][0]["material"])
        with self.assertRaises(NotFoundError):
            self.cmd("set_assembly_part_material", part_id="part:missing", material=self.material)

    def test_revision_bound_core_measurement(self):
        with tempfile.TemporaryDirectory() as folder:
            core = CadyxCore(folder, kernel=PrimitiveKernelAdapter())
            project = core.create_project("Mass fixture")["project"]["id"]
            lease = core.acquire_lease(project, "human", "measure")["lease_id"]
            body = core.execute_command(
                project, lease, {"type": "create_box", "width": 2,
                                 "height": 4, "depth": 6}
            )["history_entry"]["result"]["body_id"]
            part = core.execute_command(
                project, lease, {"type": "create_assembly_part", "body_id": body}
            )["history_entry"]["result"]["part_id"]
            core.execute_command(project, lease, {
                "type": "set_assembly_part_material", "part_id": part,
                "material": self.material,
            })
            core.execute_command(project, lease, {
                "type": "add_assembly_instance", "target_id": part,
            })
            revision = core.inspect_project(project)["document"]["revision"]
            result = core.validate_assembly_mass_properties(project, revision)
            self.assertEqual(result["revision"], revision)
            self.assertEqual(result["assembly"]["instance_count"], 1)
            self.assertEqual(len(result["geometry_sha256"]), 64)
            with self.assertRaises(ConflictError):
                core.validate_assembly_mass_properties(project, revision - 1)
