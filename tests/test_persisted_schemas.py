"""Persisted-schema compatibility tests, without loading native geometry libraries."""
from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
import unittest
from uuid import uuid4

from cadyx.errors import ValidationError
from cadyx.models import Document
from cadyx.persisted_schemas import FEATURE_SCHEMAS, OBJECT_SCHEMAS


def identifier(kind):
    return f"{kind}:{uuid4()}"


def feature_document(operation):
    body, source, tool = [identifier("body") for _ in range(3)]
    feature = {"id": identifier("feature"), "type": "feature", "body_id": body,
               "operation": operation}
    sketches = [{"id": identifier("sketch"), "type": "sketch", "name": "Section", "plane": "XZ",
                 "section_normal_axis": "Y", "section_offset": offset} for offset in (0, 1)]
    feature.update(source_sketch_id=sketches[0]["id"], source_body_id=source,
                   base_body_id=source, tool_body_id=tool, edge_indices=[0])
    if operation == "pad_sketch_to_body":
        feature["parameters"] = {"sketch_id": sketches[0]["id"], "depth": 2, "centered": False}
    elif operation == "loft_sketches":
        ids = [sketch["id"] for sketch in sketches]
        feature.update(source_sketch_ids=ids,
                       parameters={"section_sketch_ids": ids, "section_offsets": [0, 1]})
    elif operation == "create_ruled_shell":
        feature["parameters"] = {"patches": [], "sewing_tolerance": 0.01}
    else:
        feature["parameters"] = {}
    feature.update(source={"file": "part.step", "sha256": "a" * 64, "size": 12},
                   source_metrics={"volume": 1, "surface_area": 6,
                                   "bounds": {"min": [0, 0, 0], "max": [1, 1, 1]},
                                   "topology": {"solid_count": 1, "face_count": 6,
                                                "edge_count": 12, "vertex_count": 8},
                                   "brep_valid": True, "repaired": False})
    document = Document.create()
    document.objects = [{"id": body, "type": "body", "feature_ids": [feature["id"]]},
                        {"id": source, "type": "body"}, {"id": tool, "type": "body"},
                        *sketches, feature]
    return document, feature


class PersistedSchemaTests(unittest.TestCase):
    def test_every_registered_operation_roundtrips_and_requires_reciprocal_ownership(self):
        for operation in FEATURE_SCHEMAS:
            with self.subTest(operation=operation):
                document, feature = feature_document(operation)
                document.validate()
                saved = document.to_dict()
                self.assertEqual(Document.from_dict(saved).to_dict(), saved)
                feature["body_id"] = identifier("body")
                with self.assertRaisesRegex(ValidationError, "Body feature references"):
                    document.validate()

    def test_operation_specific_invalid_fields_preserve_errors(self):
        cases = {
            "extrude_sketch": ("source_sketch_id", identifier("sketch"), "Profile feature"),
            "revolve_sketch": ("source_sketch_id", identifier("sketch"), "Profile feature"),
            "pad_sketch_to_body": ("parameters", {}, "Pad feature"),
            "sweep_sketch": ("source_sketch_id", identifier("sketch"), "Sweep feature"),
            "loft_sketches": ("source_sketch_ids", [], "Loft feature section references"),
            "create_ruled_shell": ("parameters", {}, "Ruled shell feature"),
            "fillet_edges": ("edge_indices", [0, 0], "Fillet feature edge indices"),
            "resize_imported_thread": ("parameters", None, "Imported B-rep feature parameters"),
            "transform_imported_brep": ("parameters", None, "Imported B-rep feature parameters"),
            "fuse_bodies": ("tool_body_id", identifier("body"), "Boolean feature must reference existing"),
            "cut_bodies": ("tool_body_id", identifier("body"), "Boolean feature must reference existing"),
            "import_step": ("source", {}, "STEP source metadata"),
        }
        for operation, (field, value, error) in cases.items():
            with self.subTest(operation=operation):
                document, feature = feature_document(operation)
                feature[field] = value
                with self.assertRaisesRegex(ValidationError, error):
                    document.validate()

    def test_missing_source_bodies_and_changed_loft_sections_are_rejected(self):
        for operation in ("fillet_edges", "resize_imported_thread", "transform_imported_brep"):
            with self.subTest(operation=operation):
                document, feature = feature_document(operation)
                feature["source_body_id"] = feature["body_id"]
                with self.assertRaisesRegex(ValidationError, "different existing source body"):
                    document.validate()
        document, feature = feature_document("loft_sketches")
        document.objects[3]["section_offset"] = 99
        with self.assertRaisesRegex(ValidationError, "changed section metadata"):
            document.validate()
        document, feature = feature_document("loft_sketches")
        feature["parameters"]["section_offsets"] = [1, 0]
        with self.assertRaisesRegex(ValidationError, "section offsets"):
            document.validate()

    def test_legacy_extensions_and_versions_remain_copy_only(self):
        for version in range(1, 9):
            document, _ = feature_document("legacy_extension")
            saved = document.to_dict()
            saved["version"] = version
            if version == 1:
                saved["objects"] = [item for item in saved["objects"] if item["type"] != "sketch"]
            original = deepcopy(saved)
            migrated = Document.from_dict(saved)
            self.assertEqual(saved, original)
            self.assertEqual(migrated.version, 8)

    def test_object_registry_is_closed_and_body_visibility_is_checked(self):
        self.assertEqual(set(OBJECT_SCHEMAS), {"body", "feature", "sketch", "point", "line",
                                              "circle", "ellipse", "spline", "constraint"})
        document, _ = feature_document("create_box")
        document.objects[0]["visible"] = 1
        with self.assertRaisesRegex(ValidationError, "Body visible"):
            document.validate()
        document.objects[0]["type"] = "unknown"
        with self.assertRaisesRegex(ValidationError, "Unsupported document object type"):
            document.validate()

    def test_literal_command_feature_records_have_registered_schemas(self):
        # Guard actual emitter records rather than a second hand-maintained operation list.
        root = Path(__file__).resolve().parents[1] / "cadyx"
        operations = set()
        for path in root.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Dict):
                    continue
                fields = {key.value: value for key, value in zip(node.keys, node.values)
                          if isinstance(key, ast.Constant) and isinstance(key.value, str)}
                operation = fields.get("operation")
                if (isinstance(fields.get("type"), ast.Constant) and fields["type"].value == "feature"
                        and isinstance(operation, ast.Constant) and isinstance(operation.value, str)):
                    operations.add(operation.value)
        self.assertTrue(operations)
        self.assertLessEqual(operations, FEATURE_SCHEMAS.keys())

    def test_extension_emitters_have_registered_operation_names(self):
        from cadyx.advanced_features import ADVANCED_CAPABILITIES
        from cadyx.agent_authoring import CAPABILITIES
        from cadyx.manufacturing_features import CAPABILITIES as MANUFACTURING_CAPABILITIES

        # These emit their command type dynamically as the persisted operation.
        operations = {entry["type"] for entry in (
            *ADVANCED_CAPABILITIES, *CAPABILITIES, *MANUFACTURING_CAPABILITIES)
            if entry["type"] not in {"create_section_sketch", "create_sketch_from_body_section"}}
        self.assertLessEqual(operations, FEATURE_SCHEMAS.keys())

    def test_identity_and_reciprocal_feature_references_remain_graph_invariants(self):
        for mutation, error in (
            (lambda doc: doc.objects.append(deepcopy(doc.objects[0])), "Duplicate document object id"),
            (lambda doc: doc.objects[0].update(feature_ids=[]), "Feature must reference a body"),
            (lambda doc: doc.objects[0].update(feature_ids=[identifier("feature")]), "Body feature references"),
            (lambda doc: doc.objects[-1].update(source_sketch_id="sketch:invalid"), "Invalid object UUID"),
        ):
            with self.subTest(error=error):
                document, _ = feature_document("extrude_sketch")
                mutation(document)
                with self.assertRaisesRegex(ValidationError, error):
                    document.validate()
