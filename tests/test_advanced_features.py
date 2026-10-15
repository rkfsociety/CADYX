import unittest
from unittest.mock import patch

import cadyx  # installs the advanced command layer
from cadyx.commands import CommandSystem
from cadyx.models import Document
from cadyx import capabilities


class LoftKernel:
    def create_lofted_profiles(self, sections, *, section_offsets):
        self.sections = sections
        self.offsets = section_offsets
        return {"kernel": "occt-v1", "kind": "lofted_profiles", "sections": sections,
                "section_offsets": section_offsets, "volume": 1.0}


class AdvancedFeaturesTests(unittest.TestCase):
    def test_capabilities_publish_advanced_commands(self):
        names = {item["type"] for item in capabilities.COMMAND_CAPABILITIES}
        self.assertTrue({"loft_profiles", "shell_body", "draft_faces", "chamfer_edges",
                         "variable_fillet_edges", "hole", "boss"} <= names)

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
