import base64
import unittest
import zlib
from unittest.mock import patch

from cadyx import capabilities
from cadyx.advanced_features import _raw_brep
from cadyx.commands import CommandSystem
from cadyx.models import Document
from cadyx.rebuild_regression import run_rebuild_regression


class LoftKernel:
    def create_lofted_profiles(self, sections, *, section_offsets):
        self.sections = sections
        self.offsets = section_offsets
        points = [point for section in sections for point in section]
        return {"kernel": "occt-v1", "kind": "lofted_profiles", "sections": sections,
                "section_offsets": section_offsets, "volume": 1.0, "brep_valid": True,
                "bounds": {"min": [min(p["x"] for p in points), min(section_offsets),
                                   min(p["y"] for p in points)],
                           "max": [max(p["x"] for p in points), max(section_offsets),
                                   max(p["y"] for p in points)]},
                "topology": {"solid_count": 1}}

    def validate(self, geometry):
        return None

    def metrics(self, geometry):
        return {"volume": geometry["volume"], "bounds": geometry["bounds"],
                "topology": geometry["topology"], "brep_valid": geometry["brep_valid"]}


class AdvancedFeaturesTests(unittest.TestCase):
    def test_capabilities_publish_advanced_commands(self):
        names = {item["type"] for item in capabilities.COMMAND_CAPABILITIES}
        self.assertTrue({"loft_profiles", "shell_body", "draft_faces", "chamfer_edges",
                         "variable_fillet_edges", "hole", "boss"} <= names)
        sweep = next(item for item in capabilities.COMMAND_CAPABILITIES
                     if item["type"] == "sweep_sketch")
        self.assertIn("at least 3 points", " ".join(sweep["constraints"]))

    def test_section_sketches_can_loft_without_step_provenance(self):
        kernel = LoftKernel()
        system = CommandSystem(kernel)
        document = Document.create()
        sketches = []
        for offset, scale in ((0.0, 10.0), (20.0, 8.0)):
            sketch_id = system.execute(document, {"type": "create_section_sketch", "offset": offset})["sketch_id"]
            system.execute(document, {"type": "add_sketch_polyline", "sketch_id": sketch_id, "closed": True,
                                      "points": [{"x": 0, "y": 0}, {"x": scale, "y": 0},
                                                 {"x": scale, "y": scale}, {"x": 0, "y": scale}]})
            sketches.append(sketch_id)
        result = system.execute(document, {"type": "loft_profiles", "sketch_ids": sketches,
                                           "section_offsets": [0.0, 20.0]})
        self.assertTrue(result["body_id"].startswith("body:"))
        self.assertEqual(kernel.offsets, [0.0, 20.0])
        document.validate()

    def test_loft_profiles_survive_cold_and_warm_rebuild(self):
        kernel = LoftKernel()
        document = Document.create()
        system = CommandSystem(kernel)
        sketches = []
        for offset, size in ((10.0, 8.0), (30.0, 6.0)):
            sketch_id = system.execute(
                document, {"type": "create_section_sketch", "offset": offset},
            )["sketch_id"]
            system.execute(document, {
                "type": "add_sketch_rectangle", "sketch_id": sketch_id,
                "x": -size / 2, "y": -size / 2, "width": size, "height": size,
            })
            sketches.append(sketch_id)
        result = system.execute(document, {
            "type": "loft_profiles", "sketch_ids": sketches,
            "section_offsets": [10.0, 30.0],
        })
        document.validate()
        before = document.to_dict()

        class Core:
            def __init__(self):
                self.kernel = kernel
                self.commands = CommandSystem(kernel)

        status, evidence = run_rebuild_regression(
            Core(), document, max_bodies=128, checkpoint=lambda: None,
        )

        self.assertEqual(status, "pass", evidence)
        self.assertEqual(evidence["compared_bodies"], 1)
        self.assertEqual(evidence["metric_difference_count"], 0)
        self.assertEqual(document.to_dict(), before)

    def test_imported_brep_decodes_without_native_export_round_trip(self):
        raw = b"DBRep_DrawableShape\noptimized-path\n"
        geometry = {
            "kernel": "occt-v1",
            "kind": "imported_brep",
            "data": base64.b64encode(zlib.compress(raw)).decode("ascii"),
            "volume": 1.0,
            "brep_valid": True,
        }
        with patch("cadyx.advanced_features.NativeKernel") as native:
            self.assertEqual(_raw_brep(geometry), raw)
        native.assert_not_called()

    def test_full_circle_pattern_count_parameter_rebuilds_the_array(self):
        system = CommandSystem(LoftKernel())
        document = Document.create()
        source_id = "body:00000000-0000-0000-0000-000000000001"
        result_id = "body:00000000-0000-0000-0000-000000000002"
        feature_id = "feature:00000000-0000-0000-0000-000000000001"
        source_geometry = {"kernel": "occt-v1", "kind": "imported_brep", "data": "eJwDAAAAAAE=",
                           "volume": 10.0, "brep_valid": True}
        result_geometry = {"kernel": "occt-v1", "kind": "imported_brep", "data": "eJwDAAAAAAE=",
                           "volume": 100.0, "brep_valid": True}
        document.objects.extend([
            {"id": source_id, "type": "body", "name": "Seed", "feature_ids": [],
             "geometry": source_geometry},
            {"id": result_id, "type": "body", "name": "Pattern", "feature_ids": [feature_id],
             "geometry": source_geometry},
            {"id": feature_id, "type": "feature", "body_id": result_id,
             "source_body_id": source_id, "operation": "circular_pattern_body",
             "parameters": {"count": 10, "axis_origin": [0, 0, 0], "axis_direction": [0, 0, 1],
                            "angle_step_degrees": 36.0, "full_circle": True},
             "geometry": source_geometry},
        ])
        with patch("cadyx.advanced_features._advanced_native", return_value=result_geometry) as native:
            result = system.execute(document, {"type": "set_parameter", "feature_id": feature_id,
                                               "name": "count", "value": 12})

        native.assert_called_once_with(source_geometry, 7, [12.0, 0, 0, 0, 0, 0, 1, 30.0])
        self.assertEqual(result["value"], 12)
        self.assertEqual(document.objects[2]["parameters"]["count"], 12)
        self.assertEqual(document.objects[2]["parameters"]["angle_step_degrees"], 30.0)
        self.assertEqual(document.objects[1]["geometry"], result_geometry)

    def test_pattern_can_be_switched_to_full_circle_editing(self):
        system = CommandSystem(LoftKernel())
        document = Document.create()
        source_id = "body:00000000-0000-0000-0000-000000000001"
        result_id = "body:00000000-0000-0000-0000-000000000002"
        feature_id = "feature:00000000-0000-0000-0000-000000000001"
        geometry = {"kernel": "occt-v1", "kind": "imported_brep", "data": "eJwDAAAAAAE=",
                    "volume": 10.0, "brep_valid": True}
        document.objects.extend([
            {"id": source_id, "type": "body", "name": "Seed", "feature_ids": [],
             "geometry": geometry},
            {"id": result_id, "type": "body", "name": "Pattern", "feature_ids": [feature_id],
             "geometry": geometry},
            {"id": feature_id, "type": "feature", "body_id": result_id,
             "source_body_id": source_id, "operation": "circular_pattern_body",
             "parameters": {"count": 10, "axis_origin": [0, 0, 0], "axis_direction": [0, 0, 1],
                            "angle_step_degrees": 35.0, "full_circle": False},
             "geometry": geometry},
        ])
        with patch("cadyx.advanced_features._advanced_native", return_value=geometry) as native:
            system.execute(document, {"type": "set_parameter", "feature_id": feature_id,
                                      "name": "full_circle", "value": True})

        native.assert_called_once_with(geometry, 7, [10.0, 0, 0, 0, 0, 0, 1, 36.0])
        self.assertTrue(document.objects[2]["parameters"]["full_circle"])
        self.assertEqual(document.objects[2]["parameters"]["angle_step_degrees"], 36.0)

    def test_derived_pattern_rebuilds_after_source_body_change(self):
        system = CommandSystem(LoftKernel())
        document = Document.create()
        source_id = "body:00000000-0000-0000-0000-000000000001"
        pattern_id = "body:00000000-0000-0000-0000-000000000002"
        feature_id = "feature:00000000-0000-0000-0000-000000000001"
        source_geometry = {"kernel": "occt-v1", "kind": "imported_brep", "data": "eJwDAAAAAAE=",
                           "volume": 10.0, "brep_valid": True}
        pattern_geometry = {"kernel": "occt-v1", "kind": "imported_brep", "data": "eJwDAAAAAAE=",
                            "volume": 100.0, "brep_valid": True}
        document.objects.extend([
            {"id": source_id, "type": "body", "name": "Seed", "feature_ids": [],
             "geometry": source_geometry},
            {"id": pattern_id, "type": "body", "name": "Pattern", "feature_ids": [feature_id],
             "geometry": pattern_geometry},
            {"id": feature_id, "type": "feature", "body_id": pattern_id,
             "source_body_id": source_id, "operation": "circular_pattern_body",
             "parameters": {"count": 10, "axis_origin": [0, 0, 0], "axis_direction": [0, 0, 1],
                            "angle_step_degrees": 36.0, "full_circle": True},
             "geometry": pattern_geometry},
        ])
        changed_geometry = {**source_geometry, "data": "eJwBAAAAAAE=", "volume": 11.0}
        rebuilt_geometry = {**pattern_geometry, "data": "eJwBAAAAAAE=", "volume": 110.0}
        document.objects[0]["geometry"] = changed_geometry
        with patch("cadyx.advanced_features._advanced_native", return_value=rebuilt_geometry) as native:
            system._rebuild_derived_features(document, {source_id})

        native.assert_called_once_with(changed_geometry, 7, [10.0, 0, 0, 0, 0, 0, 1, 36.0])
        self.assertEqual(document.objects[1]["geometry"], rebuilt_geometry)
        self.assertEqual(document.objects[2]["geometry"], rebuilt_geometry)

    def test_shell_routes_to_native_and_persists_feature(self):
        system = CommandSystem(LoftKernel())
        document = Document.create()
        source_id = "body:00000000-0000-0000-0000-000000000001"
        feature_id = "feature:00000000-0000-0000-0000-000000000001"
        source_geometry = {"kernel": "occt-v1", "kind": "imported_brep", "data": "eJwDAAAAAAE=",
                           "volume": 10.0, "brep_valid": True}
        document.objects.extend([
            {"id": source_id, "type": "body", "name": "Source", "feature_ids": [feature_id],
             "geometry": source_geometry},
            {"id": feature_id, "type": "feature", "body_id": source_id, "operation": "seed",
             "geometry": source_geometry},
        ])
        result_geometry = {"kernel": "occt-v1", "kind": "imported_brep", "data": "eJwDAAAAAAE=",
                           "volume": 8.0, "brep_valid": True}
        with patch("cadyx.advanced_features._advanced_native", return_value=result_geometry) as native:
            result = system.execute(document, {"type": "shell_body", "body_id": source_id,
                                               "face_indices": [0], "thickness": -2.0})
        native.assert_called_once()
        created = next(item for item in document.objects if item.get("id") == result["feature_id"])
        self.assertEqual(created["operation"], "shell_body")
        self.assertEqual(created["parameters"]["thickness"], -2.0)
        document.validate()


if __name__ == "__main__":
    unittest.main()
