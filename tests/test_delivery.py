from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from cadyx.core import CadyxCore
from cadyx.errors import ConflictError, ValidationError
from cadyx.delivery import compare_step_metrics
from cadyx.models import Document, OwnerType


class CadDeliveryTests(unittest.TestCase):
    def test_failed_step_round_trip_reports_measured_deviation(self) -> None:
        source = {"brep_valid": True, "topology": {"solid_count": 1}, "volume": 100.0,
                  "bounds": {"min": [0.0, 0.0, 0.0], "max": [10.0, 10.0, 10.0]}}
        imported = {"brep_valid": True, "topology": {"solid_count": 1}, "volume": 99.0,
                    "bounds": {"min": [0.0, 0.0, 0.0], "max": [10.0, 10.0, 10.1]}}
        with self.assertRaises(ValidationError) as raised:
            compare_step_metrics(source, imported)
        error = raised.exception.as_dict()
        self.assertEqual(error["error"], "EXPORT_VERIFICATION_FAILED")
        self.assertEqual(error["diagnostics"]["category"], "export")
        measurements = error["diagnostics"]["measurements"]
        self.assertEqual(measurements["source_volume"], 100.0)
        self.assertEqual(measurements["reimported_volume"], 99.0)
        self.assertAlmostEqual(measurements["relative_volume_error"], 0.01)
        self.assertAlmostEqual(measurements["bounds_max_deviation"], 0.1)

    def test_export_command_publishes_revision_bound_step_stl_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            core = CadyxCore(temporary)
            project_id = core.create_project("Verified delivery")["project"]["id"]
            lease = core.acquire_lease(project_id, "agent", "delivery-test")
            created = core.execute_command(
                project_id, lease["lease_id"],
                {"type": "create_box", "name": "Sample body", "width": 12, "height": 8, "depth": 5},
                expected_owner_type=OwnerType.AGENT,
                expected_revision=0,
            )
            body_id = created["history_entry"]["result"]["body_id"]
            exported = core.execute_command(
                project_id, lease["lease_id"],
                {"type": "export_brep", "body_id": body_id, "include_stl": True},
                expected_owner_type=OwnerType.AGENT,
                expected_revision=created["document"]["revision"],
            )

            result = exported["history_entry"]["result"]
            directory = core.workspace._project_dir(project_id)
            manifest_path = directory / result["manifest"]["file"]
            manifest_data = manifest_path.read_bytes()
            manifest = json.loads(manifest_data)
            self.assertEqual(manifest["format"], "cadyx-cad-delivery")
            self.assertEqual(manifest["source"]["document_revision"], 1)
            self.assertEqual(manifest["source"]["body_id"], body_id)
            self.assertEqual(manifest["verification"]["step_round_trip"]["status"], "passed")
            self.assertEqual(manifest["verification"]["stl"]["status"], "passed")
            self.assertTrue(manifest["verification"]["stl"]["watertight"])
            self.assertTrue(manifest["verification"]["stl"]["orientation_consistent"])
            for key in ("step", "stl"):
                record = manifest["outputs"][key]
                artifact = (directory / record["file"]).read_bytes()
                self.assertEqual(len(artifact), record["size"])
                self.assertEqual(hashlib.sha256(artifact).hexdigest(), record["sha256"])
            self.assertEqual(hashlib.sha256(manifest_data).hexdigest(), result["manifest"]["sha256"])
            self.assertEqual(exported["document"]["revision"], 2)
            self.assertEqual(result["source_revision"], 1)
            self.assertEqual(core.inspect_project(project_id)["document"]["revision"], 2)
            self.assertEqual(core.render_project(project_id)["objects"][0]["object_id"], body_id)

    def test_export_round_trip_preserves_a_fillet_brep(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            core = CadyxCore(temporary)
            project_id = core.create_project("Rounded delivery")['project']['id']
            lease_id = core.acquire_lease(project_id, "agent", "rounded-delivery-test")["lease_id"]
            created = core.execute_command(
                project_id,
                lease_id,
                {"type": "create_box", "name": "Rounded body", "width": 24, "height": 18, "depth": 12},
                expected_owner_type=OwnerType.AGENT,
                expected_revision=0,
            )
            filleted = core.execute_command(
                project_id,
                lease_id,
                {"type": "fillet_edges", "body_id": created["history_entry"]["result"]["body_id"],
                 "edge_indices": [0], "radius": 1.25},
                expected_owner_type=OwnerType.AGENT,
                expected_revision=created["document"]["revision"],
            )
            body_id = filleted["history_entry"]["result"]["body_id"]
            exported = core.execute_command(
                project_id,
                lease_id,
                {"type": "export_brep", "body_id": body_id},
                expected_owner_type=OwnerType.AGENT,
                expected_revision=filleted["document"]["revision"],
            )
            result = exported["history_entry"]["result"]
            manifest = json.loads((core.workspace._project_dir(project_id) / result["manifest"]["file"]).read_bytes())
            step_check = manifest["verification"]["step_round_trip"]
            self.assertEqual(step_check["status"], "passed")
            self.assertEqual(step_check["source_solid_count"], 1)
            self.assertEqual(step_check["reimported_solid_count"], 1)
            self.assertLessEqual(step_check["bounds_max_deviation"], 1e-6)
            self.assertLessEqual(step_check["relative_volume_error"], 1e-8)

    def test_export_requires_an_exact_revision_and_rejects_mesh_bodies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            core = CadyxCore(temporary)
            project_id = core.create_project("Export checks")["project"]["id"]
            lease = core.acquire_lease(project_id, "human", "delivery-test")
            created = core.execute_command(
                project_id, lease["lease_id"],
                {"type": "create_box", "name": "Box", "width": 2, "height": 3, "depth": 4},
            )
            body_id = created["history_entry"]["result"]["body_id"]
            with self.assertRaises(ValidationError):
                core.execute_command(project_id, lease["lease_id"], {"type": "export_brep", "body_id": body_id})
            with self.assertRaises(ConflictError):
                core.execute_command(
                    project_id, lease["lease_id"], {"type": "export_brep", "body_id": body_id},
                    expected_revision=0,
                )
            self.assertEqual(core.inspect_project(project_id)["document"]["revision"], 1)

            mesh_document = Document.create()
            mesh_document.objects.append({
                "id": body_id, "type": "body", "name": "Scanned mesh", "feature_ids": [],
                "geometry": {"kernel": "cadyx-mesh-v1", "kind": "triangle_mesh"},
            })
            with self.assertRaises(ValidationError):
                core.commands.execute(
                    mesh_document, {"type": "export_brep", "body_id": body_id}, project_id=project_id,
                )

    def test_publisher_never_overwrites_existing_files_and_rolls_back_partial_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            core = CadyxCore(temporary)
            project_id = core.create_project("Artifact collisions")["project"]["id"]
            directory = core.workspace._project_dir(project_id)
            existing = directory / "occupied.stl"
            existing.write_bytes(b"user data")
            with self.assertRaises(ConflictError):
                core.workspace.publish_cad_delivery(project_id, {
                    "new.step": b"new step",
                    "occupied.stl": b"replacement",
                    "export.cad-delivery.json": b"manifest",
                })
            self.assertEqual(existing.read_bytes(), b"user data")
            self.assertFalse((directory / "new.step").exists())
            self.assertFalse((directory / "export.cad-delivery.json").exists())


if __name__ == "__main__":
    unittest.main()
