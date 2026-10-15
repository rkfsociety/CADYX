from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from cadyx.core import CadyxCore
from cadyx.errors import LeaseRequiredError, ValidationError
from cadyx.kernel import PrimitiveKernelAdapter
from cadyx.models import Document


class SketchDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.core = CadyxCore(self.temp.name, kernel=PrimitiveKernelAdapter())
        self.project = self.core.create_project("Sketch test")["project"]["id"]
        self.lease = self.core.acquire_lease(self.project, "agent", "test")["lease_id"]
        self.path = self.core.workspace._project_dir(self.project) / "project.json"
        self.sketch = self.command(type="create_sketch")["sketch_id"]
        self.a = self.point(0, 0)
        self.b = self.point(3, 0)
        self.line = self.command(type="add_sketch_element", sketch_id=self.sketch,
                                 element_type="line", start_id=self.a, end_id=self.b)["object_id"]

    def command(self, **command):
        return self.core.execute_command(self.project, self.lease, command)["history_entry"]["result"]

    def point(self, x, y):
        return self.command(type="add_sketch_element", sketch_id=self.sketch,
                            element_type="point", x=x, y=y)["object_id"]

    def constraint(self, kind, refs, value=None):
        return self.command(type="add_sketch_constraint", sketch_id=self.sketch,
                            kind=kind, references=refs, value=value)["constraint_id"]

    def rejected(self, **command):
        before = self.path.read_bytes()
        with self.assertRaises(ValidationError):
            self.command(**command)
        self.assertEqual(before, self.path.read_bytes())

    def test_step_import_feature_validates_source_metadata_and_measurements(self):
        document = Document.create()
        body_id, feature_id = f"body:{uuid4()}", f"feature:{uuid4()}"
        document.objects.extend([
            {"id": body_id, "type": "body", "name": "Imported", "feature_ids": [feature_id],
             "geometry": {"kernel": "occt-v1", "kind": "imported_brep", "data": "eA==",
                          "volume": 1, "brep_valid": False}},
            {"id": feature_id, "type": "feature", "operation": "import_step", "body_id": body_id,
             "source": {"file": "part.stp", "sha256": "a" * 64, "size": 12},
             "source_metrics": {
                 "volume": 1.0, "surface_area": 6.0,
                 "bounds": {"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0]},
                 "topology": {"solid_count": 1, "face_count": 6, "edge_count": 12, "vertex_count": 8},
                 "brep_valid": False, "repaired": False,
             }},
        ])
        document.validate()

        for key, value in (("file", "../outside.stp"), ("file", "part.iges"),
                           ("size", 64 * 1024 * 1024 + 1)):
            with self.subTest(key=key, value=value):
                corrupted = copy.deepcopy(document)
                feature = next(item for item in corrupted.objects if item["id"] == feature_id)
                feature["source"][key] = value
                with self.assertRaises(ValidationError):
                    corrupted.validate()

    def test_constraints_and_atomic_edit_preserve_relationships_on_reload(self):
        horizontal = self.constraint("horizontal", [self.line])
        self.constraint("distance", [self.a, self.b], 3)
        self.rejected(type="update_sketch_elements", updates=[{"object_id": self.b, "changes": {"y": 1}}])
        self.command(type="update_sketch_elements", updates=[
            {"object_id": self.a, "changes": {"y": 1}},
            {"object_id": self.b, "changes": {"y": 1}},
        ])
        saved = self.core.inspect_project(self.project)
        reloaded = CadyxCore(self.temp.name, kernel=PrimitiveKernelAdapter()).inspect_project(self.project)
        self.assertEqual(saved["document"], reloaded["document"])
        self.assertEqual(saved["history"], reloaded["history"])
        by_id = {item["id"]: item for item in reloaded["document"]["objects"]}
        self.assertEqual(by_id[horizontal]["references"], [self.line])
        self.assertEqual(by_id[self.line]["start_id"], self.a)
        self.assertEqual(by_id[self.b]["y"], 1)

    def test_each_constraint_accepts_satisfied_and_rejects_unsatisfied_geometry(self):
        c = self.point(0, 4)
        same = self.point(0, 0)
        vertical = self.command(type="add_sketch_element", sketch_id=self.sketch,
                                element_type="line", start_id=self.a, end_id=c)["object_id"]
        circle = self.command(type="add_sketch_element", sketch_id=self.sketch,
                              element_type="circle", center_id=self.a, radius=2)["object_id"]
        for kind, good, bad, value in [
            ("horizontal", [self.line], [vertical], None),
            ("vertical", [vertical], [self.line], None),
            ("coincident", [self.a, same], [self.a, self.b], None),
            ("distance", [self.a, self.b], [self.a, c], 3),
            ("radius", [circle], [circle], 2),
        ]:
            with self.subTest(kind=kind):
                self.constraint(kind, good, value)
                self.rejected(type="add_sketch_constraint", sketch_id=self.sketch,
                              kind=kind, references=bad, value=3 if kind == "radius" else value)
        self.rejected(type="update_sketch_elements", updates=[{"object_id": circle, "changes": {"radius": 3}}])

    def test_invalid_geometry_references_and_constraint_inputs_are_atomic(self):
        other = self.command(type="create_sketch")["sketch_id"]
        foreign = self.command(type="add_sketch_element", sketch_id=other,
                               element_type="point", x=0, y=1)["object_id"]
        for command in [
            {"type": "create_sketch", "plane": "YZ"},
            {"type": "create_sketch", "name": ""},
            *[{"type": "add_sketch_element", "sketch_id": self.sketch, "element_type": "point", "x": x, "y": 0}
              for x in [True, "1", None, float("inf"), float("nan")]],
            {"type": "add_sketch_element", "sketch_id": self.sketch, "element_type": "line", "start_id": self.a, "end_id": foreign},
            {"type": "add_sketch_element", "sketch_id": self.sketch, "element_type": "line", "start_id": self.a, "end_id": self.a},
            {"type": "add_sketch_element", "sketch_id": self.sketch, "element_type": "circle", "center_id": self.a, "radius": 0},
            *[{"type": "add_sketch_constraint", "sketch_id": self.sketch, "kind": kind, "references": refs, "value": value}
              for kind, refs, value in [("unknown", [], None), ("horizontal", [], None),
                  ("distance", [self.a, self.b], -1), ("distance", [self.a, self.b], True),
                  ("coincident", [self.a, self.a], None), ("horizontal", [self.a], None),
                  ("distance", [self.a, foreign], 1), ("radius", [self.line], 1)]],
            {"type": "update_sketch_elements", "updates": [{"object_id": self.a, "changes": {"id": self.b}}]},
            {"type": "remove_sketch_object", "object_id": self.a},
            {"type": "remove_sketch_object", "object_id": self.sketch},
        ]:
            with self.subTest(command=command):
                self.rejected(**command)

    def test_remove_requires_references_to_be_removed_first(self):
        constraint = self.constraint("horizontal", [self.line])
        self.rejected(type="remove_sketch_object", object_id=self.line)
        for object_id in [constraint, self.line, self.a, self.b, self.sketch]:
            self.command(type="remove_sketch_object", object_id=object_id)
        self.assertEqual(self.core.inspect_project(self.project)["document"]["objects"], [])

    def test_tolerance_and_storage_order_are_explicit(self):
        self.constraint("horizontal", [self.line])
        self.command(type="update_sketch_elements", updates=[{"object_id": self.b, "changes": {"y": 5e-8}}])
        self.rejected(type="update_sketch_elements", updates=[{"object_id": self.b, "changes": {"y": 2e-7}}])
        document = self.core.inspect_project(self.project)["document"]
        document["objects"].reverse()
        self.assertEqual(Document.from_dict(document).to_dict(), document)

    def test_mirror_copies_selected_geometry_and_its_point_dependencies(self):
        result = self.command(
            type="mirror_sketch_elements", sketch_id=self.sketch,
            object_ids=[self.line], axis_start={"x": 0, "y": 0}, axis_end={"x": 1, "y": 0},
        )
        self.assertEqual(result["instance_count"], 2)
        self.assertEqual(len(result["point_ids"]), 2)
        self.assertEqual(len(result["line_ids"]), 1)
        document = self.core.inspect_project(self.project)["document"]
        by_id = {item["id"]: item for item in document["objects"]}
        mirrored_points = [by_id[object_id] for object_id in result["point_ids"]]
        self.assertEqual({(item["x"], item["y"]) for item in mirrored_points}, {(0, 0), (3, 0)})
        mirrored_line = by_id[result["line_ids"][0]]
        self.assertNotEqual(mirrored_line["start_id"], self.a)
        self.assertNotEqual(mirrored_line["end_id"], self.b)

        result = self.command(
            type="mirror_sketch_elements", sketch_id=self.sketch,
            object_ids=[self.line], axis_start={"x": 0, "y": 0}, axis_end={"x": 0, "y": 1},
        )
        document = self.core.inspect_project(self.project)["document"]
        by_id = {item["id"]: item for item in document["objects"]}
        self.assertEqual({(by_id[item]["x"], by_id[item]["y"]) for item in result["point_ids"]}, {(0, 0), (-3, 0)})

    def test_linear_and_circular_patterns_preserve_object_graphs(self):
        self.constraint("horizontal", [self.line])
        linear = self.command(
            type="pattern_sketch_elements", sketch_id=self.sketch, object_ids=[self.line],
            mode="linear", count=3, offset_x=10, offset_y=2,
        )
        self.assertEqual(linear["instance_count"], 3)
        self.assertEqual(len(linear["line_ids"]), 2)
        document = self.core.inspect_project(self.project)["document"]
        by_id = {item["id"]: item for item in document["objects"]}
        self.assertEqual(
            {(by_id[item]["x"], by_id[item]["y"]) for item in linear["point_ids"]},
            {(10, 2), (13, 2), (20, 4), (23, 4)},
        )
        for object_id in linear["line_ids"]:
            line = by_id[object_id]
            self.assertIn(line["start_id"], linear["point_ids"])
            self.assertIn(line["end_id"], linear["point_ids"])
        self.assertEqual(sum(item["type"] == "constraint" for item in document["objects"]), 1)

        circle = self.command(type="add_sketch_circle", sketch_id=self.sketch, x=4, y=0, radius=1)
        circular = self.command(
            type="pattern_sketch_elements", sketch_id=self.sketch, object_ids=[circle["circle_id"]],
            mode="circular", count=4, center_x=0, center_y=0, angle_step_degrees=90,
        )
        self.assertEqual(len(circular["circle_ids"]), 3)
        document = self.core.inspect_project(self.project)["document"]
        by_id = {item["id"]: item for item in document["objects"]}
        centers = [by_id[by_id[item]["center_id"]] for item in circular["circle_ids"]]
        self.assertEqual({(round(item["x"], 7), round(item["y"], 7)) for item in centers}, {(0, 4), (-4, 0), (0, -4)})

    def test_sketch_copy_commands_reject_invalid_or_repeated_inputs_atomically(self):
        other = self.command(type="create_sketch")["sketch_id"]
        constraint = self.constraint("horizontal", [self.line])
        for command in [
            {"type": "mirror_sketch_elements", "sketch_id": self.sketch, "object_ids": [self.line],
             "axis_start": {"x": 0, "y": 0}, "axis_end": {"x": 0, "y": 0}},
            {"type": "mirror_sketch_elements", "sketch_id": self.sketch, "object_ids": [constraint],
             "axis_start": {"x": 0, "y": 0}, "axis_end": {"x": 0, "y": 1}},
            {"type": "pattern_sketch_elements", "sketch_id": self.sketch, "object_ids": [self.line],
             "mode": "linear", "count": 2, "offset_x": 0, "offset_y": 0},
            {"type": "pattern_sketch_elements", "sketch_id": self.sketch, "object_ids": [self.line],
             "mode": "linear", "count": 65, "offset_x": 1, "offset_y": 0},
            {"type": "pattern_sketch_elements", "sketch_id": self.sketch, "object_ids": [self.line],
             "mode": "circular", "count": 3, "center_x": 0, "center_y": 0, "angle_step_degrees": 180},
            {"type": "pattern_sketch_elements", "sketch_id": self.sketch, "object_ids": [self.line],
             "mode": "unknown", "count": 2},
            {"type": "mirror_sketch_elements", "sketch_id": other, "object_ids": [self.line],
             "axis_start": {"x": 0, "y": 0}, "axis_end": {"x": 1, "y": 0}},
        ]:
            with self.subTest(command=command):
                self.rejected(**command)

    def test_load_rejects_unsatisfied_saved_constraint(self):
        self.constraint("horizontal", [self.line])
        project = json.loads(self.path.read_text())
        point = next(item for item in project["document"]["objects"] if item["id"] == self.b)
        point["y"] = 1
        self.path.write_text(json.dumps(project))
        with self.assertRaises(ValidationError):
            self.core.inspect_project(self.project)

    def test_model_rejects_corruption_independently_of_commands(self):
        original = self.core.inspect_project(self.project)["document"]
        for change in [
            lambda d: d["objects"].append(copy.deepcopy(d["objects"][0])),
            lambda d: d["objects"][0].update(id="sketch:invalid"),
            lambda d: d["objects"][1].update(x="wrong"),
            lambda d: d["objects"][3].update(end_id="point:" + str(uuid4())),
            lambda d: d.update(version=99),
            lambda d: d.update(version=True),
        ]:
            value = copy.deepcopy(original)
            change(value)
            with self.subTest(value=value), self.assertRaises(ValidationError):
                Document.from_dict(value)
        corrupt = json.loads(self.path.read_text())
        corrupt["document"]["objects"][1]["x"] = "wrong"
        self.path.write_text(json.dumps(corrupt))
        with self.assertRaises(ValidationError):
            self.core.inspect_project(self.project)

    def test_sketch_commands_require_lease(self):
        with self.assertRaises(LeaseRequiredError):
            self.core.execute_command(self.project, None, {"type": "create_sketch"})

    def test_v1_migration_preserves_objects_history_and_failed_command_bytes(self):
        project = self.core.create_project("Legacy")["project"]["id"]
        lease = self.core.acquire_lease(project, "human", "legacy")["lease_id"]
        box = self.core.execute_command(project, lease, {"type": "create_box", "width": 2, "height": 3, "depth": 4})
        path = self.core.workspace._project_dir(project) / "project.json"
        legacy = json.loads(path.read_text())
        legacy["document"]["version"] = 1
        path.write_text(json.dumps(legacy))
        before = path.read_bytes()
        inspected = self.core.inspect_project(project)
        self.assertEqual(inspected["document"]["version"], 3)
        self.assertEqual(inspected["document"]["objects"], legacy["document"]["objects"])
        self.assertEqual(inspected["history"], legacy["history"])
        self.assertEqual(path.read_bytes(), before)
        with self.assertRaises(ValidationError):
            self.core.execute_command(project, lease, {"type": "create_sketch", "plane": "bad"})
        self.assertEqual(path.read_bytes(), before)
        feature = box["history_entry"]["result"]["feature_id"]
        self.core.execute_command(project, lease, {"type": "set_parameter", "feature_id": feature, "name": "width", "value": 5})
        persisted = json.loads(path.read_text())
        self.assertEqual(persisted["document"]["version"], 3)
        self.assertEqual(persisted["history"][0], legacy["history"][0])
        self.assertEqual(persisted["document"]["objects"][1]["id"], feature)


if __name__ == "__main__":
    unittest.main()
