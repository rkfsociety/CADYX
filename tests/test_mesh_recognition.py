"""Measured reference recognition and editable project-tree persistence."""
from copy import deepcopy
import math
import random
import unittest

from cadyx.commands import CommandSystem
from cadyx.errors import ValidationError
from cadyx.kernel_primitive import PrimitiveKernelAdapter
from cadyx.mesh_recognition import _circles, _simple_cycles, _strict_circles, recognize_mesh_geometry
from cadyx.models import Document
from tests.test_mesh_processing import mesh


def box():
    return mesh([(0,0,0),(10,0,0),(10,8,0),(0,8,0),(0,0,6),(10,0,6),(10,8,6),(0,8,6)],
                [(0,2,1),(0,3,2),(4,5,6),(4,6,7),(0,1,5),(0,5,4),
                 (1,2,6),(1,6,5),(2,3,7),(2,7,6),(3,0,4),(3,4,7)])


class MeshRecognitionTests(unittest.TestCase):
    def test_box_planes_and_closed_sections_are_measured_without_source_edits(self):
        source = box()
        original = deepcopy(source)
        report = recognize_mesh_geometry(source, {"min_plane_area": 1., "distance_tolerance": .1,"min_contour_length":1.})
        self.assertEqual(len(report["planes"]),6)
        self.assertTrue(any(section["closed"] for section in report["sections"]))
        self.assertTrue(all(plane["max_error"] < 1e-6 for plane in report["planes"]))
        self.assertTrue(report["projections"])
        self.assertTrue(report["projections"][0]["closed"])
        self.assertEqual(source,original)
        self.assertEqual(report["source_geometry_sha256"],source["sha256"])

    def test_arc_support_finds_radius_and_rejects_straight_lines(self):
        points = [(3+5*math.cos(i*math.pi/100),4+5*math.sin(i*math.pi/100)) for i in range(101)]
        fits = _circles(points,.01,random.Random(0))
        self.assertTrue(fits)
        self.assertTrue(_strict_circles(points,.01,random.Random(0)))
        self.assertAlmostEqual(fits[0]["radius"],5,places=5)
        self.assertAlmostEqual(fits[0]["coverage_degrees"],180,places=5)
        self.assertEqual(_circles([(i,0) for i in range(100)],.01,random.Random(0)),[])

    def test_command_creates_nested_editable_references_and_persistent_report(self):
        document = Document.create()
        body = {"id":"body:11111111-1111-4111-8111-111111111111","type":"body","name":"Reference box",
                "geometry":box(),"feature_ids":["feature:22222222-2222-4222-8222-222222222222"]}
        document.objects = [body,{"id":body["feature_ids"][0],"type":"feature","operation":"reconstruct_scan","body_id":body["id"]}]
        system = CommandSystem(PrimitiveKernelAdapter())
        before = deepcopy(body["geometry"])
        result = system.execute(document,{"type":"recognize_mesh","body_id":body["id"],
                                          "parameters":{"min_plane_area":1.,"distance_tolerance":.1,"min_contour_length":1.}})
        self.assertGreater(result["reference_sketches"],6)
        feature = document.find_object(result["feature_id"],"feature")
        self.assertEqual(feature["body_id"],body["id"])
        self.assertTrue(all(document.find_object(i,"sketch")["plane"]=="CUSTOM" for i in feature["source_sketch_ids"]))
        self.assertEqual(body["geometry"],before)
        document.validate()
        self.assertEqual(Document.from_dict(document.to_dict()).to_dict(),document.to_dict())
        old_ids = set(feature["source_sketch_ids"])
        refreshed = system.execute(document,{"type":"recognize_mesh","body_id":body["id"],
                                  "parameters":{"min_plane_area":1.,"distance_tolerance":.1,"min_contour_length":1.}})
        self.assertEqual(refreshed["feature_id"],result["feature_id"])
        self.assertFalse(old_ids.intersection(o["id"] for o in document.objects))
        document.validate()
        body["geometry"]["sha256"] = "b"*64
        with self.assertRaises(ValidationError):
            document.validate()

    def test_projection_splits_touching_cycles_into_simple_profiles(self):
        chain = [(0,0),(1,0),(1,1),(0,1),(0,0),(-1,0),(-1,-1),(0,-1)]
        cycles = _simple_cycles(chain)
        self.assertEqual(len(cycles),2)
        self.assertTrue(all(len(cycle)==len(set(cycle)) for cycle in cycles))

    def test_parameter_and_persisted_schema_validation(self):
        with self.assertRaises(ValidationError):
            recognize_mesh_geometry(box(),{"distance_tolerance":-1})
        from tests.test_persisted_schemas import feature_document
        document,feature = feature_document("recognize_mesh")
        document.validate()
        feature["source_sketch_ids"] = ["sketch:missing"]
        with self.assertRaises(ValidationError):
            document.validate()


if __name__ == "__main__":
    unittest.main()
