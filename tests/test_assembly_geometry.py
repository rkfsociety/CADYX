from __future__ import annotations

from copy import deepcopy
import math
import tempfile
import unittest

from cadyx.assembly_geometry import (
    face_frame, make_face_reference, make_vertex_reference,
    refresh_assembly_geometry_references,
)
from cadyx.commands import CommandSystem
from cadyx.core import CadyxCore
from cadyx.errors import ValidationError
from cadyx.kernel import PrimitiveKernelAdapter
from cadyx.models import Document


class TopologyKernel(PrimitiveKernelAdapter):
    def __init__(self, faces, vertices=None):
        self.faces = faces
        self.vertices = vertices or []

    def topology(self, geometry):
        del geometry
        return {"vertices": deepcopy(self.vertices),
                "faces": deepcopy(self.faces), "edges": []}


def plane_face(index, semantic_key, center):
    return {"index": index, "semantic_key": semantic_key, "surface": "plane",
            "center": center, "normal": [0.0, 0.0, 1.0], "area": 12.0}


def sphere_face(index, semantic_key, center):
    return {"index": index, "semantic_key": semantic_key, "surface": "sphere",
            "center": center, "axis_direction": [0.0, 0.0, 1.0], "area": 12.0}


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
        self.assertEqual(mate["reference_a"]["entity_key"], "face:top-v1")
        self.assertEqual(mate["reference_b"]["body_id"], self.body_id)
        self.assertEqual(mate["frame_a"], expected)
        self.assertEqual(mate["frame_b"], expected)

    def test_vertex_references_use_point_frames_for_coincidence_and_distance(self):
        self.commands.execute(self.document, {
            "type": "remove_assembly_mate", "mate_id": self.mate()["id"],
        })
        changed_geometry = self.kernel.create_box(width=2, height=1, depth=1)
        for item in self.document.objects:
            if item.get("id") in {self.body_id, self.feature_id}:
                item["geometry"] = changed_geometry
        vertices = [
            {"index": 0, "semantic_key": "vertex:first", "position": [0.0, 0.0, 0.0]},
            {"index": 1, "semantic_key": "vertex:second", "position": [1.0, 0.0, 0.0]},
        ]
        self.kernel.vertices = vertices
        reference_a = make_vertex_reference(self.body_id, vertices[0])
        reference_b = make_vertex_reference(self.body_id, vertices[1])
        self.assertEqual(face_frame({"surface": "point", "position": vertices[0]["position"]}),
                         {"translation": [0.0, 0.0, 0.0], "rotation": [1.0, 0.0, 0.0, 0.0]})
        coincident = self.commands.execute(self.document, {
            "type": "add_assembly_mate", "kind": "coincident",
            "a_instance_id": self.a, "b_instance_id": self.b,
            "reference_a": reference_a, "reference_b": reference_b,
        })
        self.assertEqual(coincident["kinematics"]["internal_dof"], 3)
        poses = {item["id"]: item for item in self.document.assembly["instances"]}
        point_a = poses[self.a]["translation"]
        point_b = [poses[self.b]["translation"][i] + vertices[1]["position"][i]
                   for i in range(3)]
        self.assertAlmostEqual(math.dist(point_a, point_b), 0.0)
        self.commands.execute(self.document, {
            "type": "remove_assembly_mate", "mate_id": coincident["mate_id"],
        })
        separated = self.commands.execute(self.document, {
            "type": "add_assembly_mate", "kind": "distance",
            "a_instance_id": self.a, "b_instance_id": self.b,
            "offset_mm": 5.0, "reference_a": reference_a, "reference_b": reference_b,
        })
        poses = {item["id"]: item for item in self.document.assembly["instances"]}
        point_a = poses[self.a]["translation"]
        point_b = [poses[self.b]["translation"][i] + vertices[1]["position"][i]
                   for i in range(3)]
        self.assertAlmostEqual(math.dist(point_a, point_b), 5.0)
        self.assertEqual(separated["kinematics"]["internal_dof"], 5)

    def test_face_referenced_axis_relation_reaches_solved_saved_pose(self):
        self.commands.execute(self.document, {
            "type": "remove_assembly_mate", "mate_id": self.mate()["id"],
        })
        reference = make_face_reference(self.body_id, self.face)
        saved_a_pose = next(item for item in self.document.assembly["instances"]
                            if item["id"] == self.a)["translation"].copy()
        result = self.commands.execute(self.document, {
            "type": "add_assembly_mate", "kind": "perpendicular",
            "a_instance_id": self.a, "b_instance_id": self.b,
            "reference_a": reference, "reference_b": reference,
        })
        self.assertEqual(result["kinematics"]["internal_dof"], 5)
        poses = {item["id"]: item for item in self.document.assembly["instances"]}
        self.assertEqual(poses[self.a]["translation"], saved_a_pose)
        qa, qb = poses[self.a]["rotation"], poses[self.b]["rotation"]
        za = [2 * (qa[1] * qa[3] + qa[0] * qa[2]),
              2 * (qa[2] * qa[3] - qa[0] * qa[1]),
              1 - 2 * (qa[1] ** 2 + qa[2] ** 2)]
        zb = [2 * (qb[1] * qb[3] + qb[0] * qb[2]),
              2 * (qb[2] * qb[3] - qb[0] * qb[1]),
              1 - 2 * (qb[1] ** 2 + qb[2] ** 2)]
        self.assertAlmostEqual(sum(a * b for a, b in zip(za, zb, strict=True)), 0.0)
        self.assertEqual(self.document.assembly["mates"][0]["reference_a"]["entity_key"],
                         "face:top-v1")

    def test_axis_relations_reject_faces_without_an_analytic_axis(self):
        graph = deepcopy(self.document.assembly)
        mate = graph["mates"][0]
        mate["kind"] = "angle"
        sphere = sphere_face(1, "face:sphere-v1", [1.0, 2.0, 3.0])
        reference = make_face_reference(self.body_id, sphere)
        mate["reference_a"] = reference
        mate["reference_b"] = reference
        with self.assertRaisesRegex(ValidationError, "require a planar or axial face"):
            refresh_assembly_geometry_references(graph, self.document.objects, self.kernel)

    def test_vertex_rebind_rejects_a_vertex_that_moved_without_mutating_the_graph(self):
        vertices = [{"index": 0, "semantic_key": "vertex:old", "position": [0.0, 0.0, 0.0]},
                    {"index": 1, "semantic_key": "vertex:second", "position": [1.0, 0.0, 0.0]}]
        self.kernel.vertices = vertices
        graph = deepcopy(self.document.assembly)
        graph["mates"][0]["reference_a"] = make_vertex_reference(self.body_id, vertices[0])
        graph["mates"][0]["reference_b"] = make_vertex_reference(self.body_id, vertices[1])
        self.kernel.vertices = [{"index": 0, "semantic_key": "vertex:moved", "position": [0.01, 0.0, 0.0]},
                                vertices[1]]
        changed = self.kernel.create_box(width=2, height=1, depth=1)
        for item in self.document.objects:
            if item.get("id") in {self.body_id, self.feature_id}:
                item["geometry"] = changed
        before = deepcopy(graph)
        with self.assertRaisesRegex(ValidationError, "moved beyond the topology rebind tolerance"):
            refresh_assembly_geometry_references(graph, self.document.objects, self.kernel)
        self.assertEqual(graph, before)

    def test_unique_semantic_rebind_refreshes_frames_after_geometry_change(self):
        self.kernel.faces = [plane_face(1, "face:top-v2", [1.0, 2.0, 4.0])]
        new_geometry = self.kernel.create_box(width=2, height=1, depth=1)
        for item in self.document.objects:
            if item.get("type") in {"body", "feature"} and item.get("id") in {self.body_id, self.feature_id}:
                item["geometry"] = new_geometry
        result = refresh_assembly_geometry_references(
            self.document.assembly, self.document.objects, self.kernel,
        )
        self.assertEqual(self.mate()["reference_a"]["entity_key"], "face:top-v2")
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

    def test_reused_face_index_does_not_break_an_ambiguous_rebind_tie(self):
        self.kernel.faces = [plane_face(0, "face:new-a", [1.0, 2.0, 4.0]),
                             plane_face(1, "face:new-b", [1.0, 2.0, 4.0])]
        changed_geometry = self.kernel.create_box(width=2, height=1, depth=1)
        for item in self.document.objects:
            if item.get("id") in {self.body_id, self.feature_id}:
                item["geometry"] = changed_geometry
        with self.assertRaisesRegex(ValidationError, "became ambiguous"):
            refresh_assembly_geometry_references(
                self.document.assembly, self.document.objects, self.kernel,
            )

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
            mate_result = execute({"type": "add_assembly_mate", "kind": "perpendicular",
                     "a_instance_id": a, "b_instance_id": b,
                     "reference_a": reference, "reference_b": reference})
            self.assertEqual(mate_result["kinematics"]["internal_dof"], 5)

            self.kernel.faces = [plane_face(3, "face:top-v2", [1.0, 2.0, 5.0])]
            execute({"type": "set_parameter", "feature_id": feature, "name": "width", "value": 2})
            saved = core.inspect_project(project_id)
            self.assertEqual(saved["document"]["assembly"]["mates"][0]["reference_a"]["entity_key"],
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

    def test_core_roundtrips_vertex_mates_and_preserves_them_when_vertex_moves(self):
        with tempfile.TemporaryDirectory() as directory:
            core = CadyxCore(directory, kernel=self.kernel)
            project_id = core.create_project("Assembly vertex references")["project"]["id"]
            lease_id = core.acquire_lease(project_id, "human", "assembly-vertex-test")["lease_id"]

            def execute(command):
                return core.execute_command(project_id, lease_id, command)["history_entry"]["result"]

            body = execute({"type": "create_box", "width": 3, "height": 1, "depth": 1})["body_id"]
            feature = next(item["id"] for item in core.inspect_project(project_id)["document"]["objects"]
                           if item.get("type") == "feature" and item.get("operation") == "create_box")
            part = execute({"type": "create_assembly_part", "body_id": body})["part_id"]
            a = execute({"type": "add_assembly_instance", "target_id": part})["instance_id"]
            b = execute({"type": "add_assembly_instance", "target_id": part})["instance_id"]
            vertices = [
                {"index": 0, "semantic_key": "vertex:stable-a", "position": [0.0, 0.0, 0.0]},
                {"index": 1, "semantic_key": "vertex:stable-b", "position": [1.0, 0.0, 0.0]},
            ]
            self.kernel.vertices = vertices
            reference_a, reference_b = (make_vertex_reference(body, vertex) for vertex in vertices)
            execute({"type": "add_assembly_mate", "kind": "coincident",
                     "a_instance_id": a, "b_instance_id": b,
                     "reference_a": reference_a, "reference_b": reference_b})
            saved = core.inspect_project(project_id)
            mate = saved["document"]["assembly"]["mates"][0]
            self.assertEqual(mate["reference_a"]["entity_kind"], "vertex")
            self.assertEqual(mate["reference_b"]["entity_key"], "vertex:stable-b")
            revision = saved["document"]["revision"]

            self.kernel.vertices = [
                {"index": 0, "semantic_key": "vertex:moved-a", "position": [0.01, 0.0, 0.0]},
                vertices[1],
            ]
            with self.assertRaisesRegex(ValidationError, "moved beyond the topology rebind tolerance"):
                execute({"type": "set_parameter", "feature_id": feature,
                         "name": "width", "value": 4})
            self.kernel.vertices = vertices
            after = core.inspect_project(project_id)
            self.assertEqual(after["document"]["revision"], revision)
            stored = next(item for item in after["document"]["assembly"]["mates"]
                          if item["id"] == mate["id"])
            self.assertEqual(stored["reference_a"]["entity_key"], "vertex:stable-a")
            core.release_lease(project_id, lease_id)


if __name__ == "__main__":
    unittest.main()
