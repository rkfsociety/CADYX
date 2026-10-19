import unittest

from cadyx.commands import CommandSystem
from cadyx.kernel import OCCTKernelAdapter
from cadyx.models import Document


class PersistentSemanticEdgeRebuildTests(unittest.TestCase):
    def test_semantic_fillet_rebuild_resolves_stale_key_from_edge_intent(self):
        system = CommandSystem(OCCTKernelAdapter())
        document = Document.create()
        system.execute(document, {"type": "create_model_parameter", "name": "BaseWidth",
                                  "value": 80, "unit": "mm"})
        base = system.execute(document, {"type": "create_box", "width": 80, "height": 60, "depth": 20})
        edges = system.kernel.topology(base["geometry"])["edges"]
        selected = next(edge for edge in edges
                        if edge["curve"] == "line" and edge["center"][0] > 79 and edge["length"] == 60)
        fillet = system.execute(document, {"type": "fillet_edges", "body_id": base["body_id"],
            "edge_keys": [selected["semantic_key"]], "topology_revision": document.revision, "radius": 2})
        system.execute(document, {"type": "bind_model_parameter", "object_id": base["feature_id"],
            "path": "parameters.width", "expression": "BaseWidth"})

        system.execute(document, {"type": "set_model_parameter", "name": "BaseWidth", "value": 100})

        feature = next(item for item in document.objects if item["id"] == fillet["feature_id"])
        self.assertEqual(len(feature.get("edge_references", [])), 1)
        source = next(item for item in document.objects if item["id"] == base["body_id"])
        source_topology = system.kernel.topology(source["geometry"])
        resolved = next(edge for edge in source_topology["edges"]
                        if edge["index"] == feature["edge_indices"][0])
        self.assertAlmostEqual(resolved["center"][0], source_topology["bounds"]["max"][0])
        self.assertEqual(resolved["length"], 60)
        document.validate()


if __name__ == "__main__":
    unittest.main()
