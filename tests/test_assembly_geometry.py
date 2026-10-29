from __future__ import annotations

from copy import deepcopy
import tempfile
import unittest

from cadyx.assembly_geometry import face_frame, make_face_reference, refresh_assembly_geometry_references
from cadyx.commands import CommandSystem
from cadyx.core import CadyxCore
from cadyx.errors import ValidationError
from cadyx.kernel import PrimitiveKernelAdapter
from cadyx.models import Document


class TopologyKernel(PrimitiveKernelAdapter):
    def __init__(self, faces):
        self.faces = faces

    def topology(self, geometry):
        del geometry
        return {"faces": deepcopy(self.faces), "edges": []}


def plane_face(index, semantic_key, center):
    return {"index": index, "semantic_key": semantic_key, "surface": "plane",
            "center": center, "normal": [0.0, 0.0, 1.0], "area": 12.0}


class AssemblyGeometryReferenceTests(unittest.TestCase):
    def setUp(self):
        self.face = plane_face(0, "face:top-v1", [1.0, 2.0, 3.0])
        self.kernel = TopologyKernel([self.face])
        self.commands = CommandSystem(self.kernel)
        self.document = Document.create()
        created = self.commands.execute(self.document, {
            "type": "create_box", "width": 1, "height": 1, "depth": 1,
        })
        body = created["body_id"]
        self.body_id = body
        self.feature_id = created["feature_id"]
        part = self.commands.execute(self.document, {
            "type": "create_assembly_part", "body_id": body,
        })["part_id"]
        self.a = self.commands.execute(self.document, {
            "type": "add_assembly_instance", "target_id": part,
        })["instance_id"]
        self.b = self.commands.execute(self.document, {
            "type": "add_assembly_instance", "target_id": part,
            "translation": [8, 0, 0],
        })["instance_id"]
        reference = make_face_reference(body, self.face)
        self.commands.execute(self.document, {
            "type": "add_assembly_mate", "kind": "fixed",
            "a_instance_id": self.a, "b_instance_id": self.b,
            "reference_a": reference, "reference_b": reference,
        })

    def mate(self):
        return self.document.assembly["mates"][0]

    def test_selected_face_creates_frames_and_persists_both_references(self):
        mate = self.mate()
        expected = face_frame(self.face)
        self.assertEqual(mate["reference_a"]["face_key"], "face:top-v1")
        self.assertEqual(mate["reference_b"]["body_id"], self.body_id)
        self.assertEqual(mate["frame_a"], expected)
        self.assertEqual(mate["frame_b"], expected)

    def test_unique_semantic_rebind_refreshes_frames_after_geometry_change(self):
        self.kernel.faces = [plane_face(1, "face:top-v2", [1.0, 2.0, 4.0])]
        new_geometry = self.kernel.create_box(width=2, height=1, depth=1)
        for item in self.document.objects:
            if item.get("type") in {"body", "feature"} and item.get("id") in {self.body_id, self.feature_id}:
                item["geometry"] = new_geometry
        result = refresh_assembly_geometry_references(
            self.document.assembly, self.document.objects, self.kernel,
        )
        self.assertEqual(self.mate()["reference_a"]["face_key"], "face:top-v2")
        self.assertEqual(self.mate()["frame_a"]["translation"], [1.0, 2.0, 4.0])
        self.assertEqual(self.mate()["frame_b"], self.mate()["frame_a"])
        poses = {pose["instance_id"]: pose["translation"] for pose in result["instance_poses"]}
        for left, right in zip(poses[self.a], poses[self.b], strict=True):
            self.assertAlmostEqual(left, right)

    def test_ambiguous_face_rebind_fails_without_mutating_the_saved_graph(self):
        self.kernel.faces = [plane_face(1, "face:new-a", [1.0, 2.0, 4.0]),
                             plane_face(2, "face:new-b", [1.0, 2.0, 4.0])]
        changed_geometry = self.kernel.create_box(width=2, height=1, depth=1)
        for item in self.document.objects:
            if item.get("id") in {self.body_id, self.feature_id}:
                item["geometry"] = changed_geometry
        before = deepcopy(self.document.assembly)
        with self.assertRaisesRegex(ValidationError, "became ambiguous"):
            refresh_assembly_geometry_references(
                self.document.assembly, self.document.objects, self.kernel,
            )
        self.assertEqual(self.document.assembly, before)

    def test_read_validation_requires_the_saved_semantic_face(self):
        self.kernel.faces = [plane_face(1, "face:changed", [1.0, 2.0, 3.0])]
        changed_geometry = self.kernel.create_box(width=2, height=1, depth=1)
        for item in self.document.objects:
            if item.get("id") in {self.body_id, self.feature_id}:
                item["geometry"] = changed_geometry
        with self.assertRaisesRegex(ValidationError, "stale or missing"):
            refresh_assembly_geometry_references(
                self.document.assembly, self.document.objects, self.kernel,
                allow_rebind=False,
            )

    def test_core_rebinds_unique_faces_and_rejects_ambiguous_geometry_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            core = CadyxCore(directory, kernel=self.kernel)
            project_id = core.create_project("Assembly face references")["project"]["id"]
            lease_id = core.acquire_lease(project_id, "human", "assembly-editor-test")["lease_id"]

            def execute(command):
                return core.execute_command(project_id, lease_id, command)["history_entry"]["result"]

            body = execute({"type": "create_box", "width": 1, "height": 1, "depth": 1})["body_id"]
            feature = next(item["id"] for item in core.inspect_project(project_id)["document"]["objects"]
                           if item.get("type") == "feature" and item.get("operation") == "create_box")
            part = execute({"type": "create_assembly_part", "body_id": body})["part_id"]
            a = execute({"type": "add_assembly_instance", "target_id": part})["instance_id"]
            b = execute({"type": "add_assembly_instance", "target_id": part})["instance_id"]
            reference = make_face_reference(body, self.face)
            execute({"type": "add_assembly_mate", "kind": "fixed",
                     "a_instance_id": a, "b_instance_id": b,
                     "reference_a": reference, "reference_b": reference})

            self.kernel.faces = [plane_face(3, "face:top-v2", [1.0, 2.0, 5.0])]
            execute({"type": "set_parameter", "feature_id": feature, "name": "width", "value": 2})
            saved = core.inspect_project(project_id)
            self.assertEqual(saved["document"]["assembly"]["mates"][0]["reference_a"]["face_key"],
                             "face:top-v2")
            revision = saved["document"]["revision"]

            self.kernel.faces = [plane_face(4, "face:new-a", [1.0, 2.0, 5.0]),
                                 plane_face(5, "face:new-b", [1.0, 2.0, 5.0])]
            with self.assertRaisesRegex(ValidationError, "became ambiguous"):
                execute({"type": "set_parameter", "feature_id": feature, "name": "width", "value": 3})
            self.kernel.faces = [plane_face(3, "face:top-v2", [1.0, 2.0, 5.0])]
            after = core.inspect_project(project_id)
            self.assertEqual(after["document"]["revision"], revision)
            saved_feature = next(item for item in after["document"]["objects"]
                                 if item.get("id") == feature)
            self.assertEqual(saved_feature["parameters"]["width"], 2)
            core.release_lease(project_id, lease_id)


if __name__ == "__main__":
    unittest.main()
