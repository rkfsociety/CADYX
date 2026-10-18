"""Native-free regression cases for revision-bound semantic topology selection."""
import unittest
from unittest.mock import Mock

from cadyx.errors import ConflictError, ValidationError
from cadyx.models import Document
from cadyx.semantic_selection import _resolve, execute_with_selection
from cadyx.topology_queries import query_topology


def topology():
    return {"faces": [
        {"index": 1, "semantic_key": "face:a", "surface": "cylinder", "radius": 2.5,
         "area": 30, "center": [0, 0, 5], "axis_origin": [0, 0, 0], "axis_direction": [0, 0, 1]},
        {"index": 3, "semantic_key": "face:b", "surface": "cylinder", "radius": 2.5,
         "area": 30, "center": [10, 0, 5], "axis_origin": [10, 0, 0], "axis_direction": [0, 0, 1]},
        {"index": 4, "semantic_key": "face:c", "surface": "plane", "area": 50,
         "center": [0, 0, 10], "normal": [0, 0, 1]}],
        "edges": [{"index": 2, "semantic_key": "edge:a", "curve": "circle",
                   "radius": 2.5, "length": 15.708, "center": [0, 0, 0], "direction": [0, 0, 1]}]}


class TopologyQueryTests(unittest.TestCase):
    def test_cylinder_query_and_ambiguity(self):
        q = {"entity": "face", "surface": "cylinder",
             "radius": {"value": 2.5, "tolerance": 0.01},
             "axis": {"origin": [0, 0, 0], "direction": [0, 0, 1], "tolerance": 0.01}}
        self.assertEqual(query_topology(topology(), q)["candidates"][0]["index"], 1)
        self.assertEqual(query_topology(topology(), {"entity": "face", "surface": "cylinder"})["status"], "ambiguous")
        self.assertEqual(query_topology(topology(), {"entity": "face", "surface": "sphere"})["status"], "no_match")

    def test_numeric_spatial_normal_filters_and_limit(self):
        self.assertEqual(query_topology(topology(), {"entity": "edge",
            "diameter": {"value": 5, "tolerance": 0}, "length": {"value": 15.708, "tolerance": 0},
            "orientation": {"vector": [0, 0, -1]}})["match_count"], 1)
        self.assertEqual(query_topology(topology(), {"entity": "face",
            "normal": {"vector": [0, 0, 1], "mode": "aligned"},
            "position": {"point": [0, 0, 10], "tolerance": 0.1},
            "center_in_box": {"min": [-1,-1,9], "max": [1,1,11]}})["match_count"], 1)
        capped = query_topology(topology(), {"entity": "face", "max_results": 1})
        self.assertEqual(capped["match_count"], 3)
        self.assertTrue(capped["truncated"])
        self.assertEqual(len(capped["candidates"]), 1)

    def test_bad_filters_rejected(self):
        for q in ({"entity":"face","radius":{"value":float("nan"),"tolerance":1}},
                  {"entity":"edge","normal":{"vector":[0,0,1]}},
                  {"entity":"face","axis":{"origin":[0,0,0],"direction":[0,0,0],"tolerance":1}},
                  {"entity":"face","area":{"value":1,"tolerance":-1}},
                  {"entity":"face","max_results":True}, {"entity":"face","bad":1}):
            with self.subTest(q=q), self.assertRaises(ValidationError):
                query_topology(topology(), q)

    def test_rebuild_requeries_descriptors_without_permanent_indices(self):
        old, new = topology(), topology()
        new["faces"][0]["index"] = 9
        q = {"entity":"face", "surface":"cylinder",
             "axis":{"origin":[0,0,0],"direction":[0,0,1],"tolerance":0}}
        self.assertEqual(query_topology(old,q)["candidates"][0]["semantic_key"],
                         query_topology(new,q)["candidates"][0]["semantic_key"])
        kernel = Mock()
        kernel.topology.return_value = new
        self.assertEqual(_resolve(kernel, {}, keys=["face:a"], collection="faces"), [9])

    def test_colliding_or_missing_keys_never_silently_select(self):
        kernel = Mock()
        kernel.topology.return_value = {"faces":[{"index":1,"semantic_key":"same"},
                                                  {"index":2,"semantic_key":"same"}]}
        with self.assertRaisesRegex(ConflictError, "ambiguous"):
            _resolve(kernel, {}, keys=["same"], collection="faces")
        with self.assertRaisesRegex(ConflictError, "Topology changed"):
            _resolve(kernel, {}, keys=["missing"], collection="faces")

    def test_stale_topology_guard_rejects_before_mutation(self):
        document = Document.create()
        document.objects.append({"id":"body:test","type":"body","name":"Box",
                                 "feature_ids":[],"geometry":{}})
        handler, system = Mock(), Mock()
        with self.assertRaises(ConflictError):
            execute_with_selection(handler, system, document,
                                   {"type":"fillet_edges","body_id":"body:test",
                                    "edge_indices":[0],"radius":1,
                                    "topology_revision":document.revision+1})
        handler.assert_not_called()


if __name__ == "__main__":
    unittest.main()
