import unittest

import cadyx  # installs extension layers
from cadyx.commands import CommandSystem
from cadyx.models import Document


class ExtensionKernel:
    def topology(self, geometry):
        return {"volume": geometry.get("volume", 1.0),
                "edges": [{"index": 3, "semantic_key": "edge:test"}],
                "faces": [{"index": 5, "semantic_key": "face:test"}]}

    def create_fillet(self, base, *, edge_indices, radius):
        self.fillet_edges = edge_indices
        return {"kernel": "occt-v1", "kind": "filleted_solid", "base": base,
                "edge_indices": edge_indices, "radius": radius, "volume": 9.0}

    def create_extruded_profile(self, profile, *, plane, depth, centered=False):
        return {"kernel": "occt-v1", "kind": "extruded_profile", "plane": plane,
                "profile": profile, "depth": depth, "centered": centered, "volume": 1.0}

    def fuse_profiles(self, base, tool):
        self.fused = (base, tool)
        return {"kernel": "occt-v1", "kind": "fused_solids", "base": base, "tool": tool, "volume": 11.0}


class ModelingExtensionTests(unittest.TestCase):
    @staticmethod
    def seed_body(document):
        body_id = "body:00000000-0000-0000-0000-000000000010"
        feature_id = "feature:00000000-0000-0000-0000-000000000010"
        geometry = {"kernel": "occt-v1", "kind": "box",
                    "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0}, "volume": 1.0}
        document.objects.extend([
            {"id": body_id, "type": "body", "name": "Seed", "feature_ids": [feature_id], "geometry": geometry},
            {"id": feature_id, "type": "feature", "body_id": body_id, "operation": "seed", "geometry": geometry},
        ])
        return body_id

    def test_semantic_edge_key_resolves_at_command_time(self):
        kernel = ExtensionKernel()
        system = CommandSystem(kernel)
        document = Document.create()
        body_id = self.seed_body(document)
        result = system.execute(document, {"type": "fillet_edges", "body_id": body_id,
                                           "edge_keys": ["edge:test"], "radius": 0.2})
        self.assertEqual(kernel.fillet_edges, [3])
        self.assertTrue(result["body_id"].startswith("body:"))

    def test_rib_from_closed_sketch_fuses_tool(self):
        kernel = ExtensionKernel()
        system = CommandSystem(kernel)
        document = Document.create()
        body_id = self.seed_body(document)
        sketch_id = system.execute(document, {"type": "create_sketch", "plane": "XY", "name": "Rib"})["sketch_id"]
        system.execute(document, {"type": "add_sketch_polyline", "sketch_id": sketch_id, "closed": True,
                                  "points": [[0, 0], [1, 0], [1, 0.2], [0, 0.2]]})
        result = system.execute(document, {"type": "rib_from_sketch", "body_id": body_id,
                                           "sketch_id": sketch_id, "depth": 2.0})
        self.assertTrue(result["body_id"].startswith("body:"))
        self.assertEqual(kernel.fused[1]["depth"], 2.0)
        document.validate()


if __name__ == "__main__":
    unittest.main()
