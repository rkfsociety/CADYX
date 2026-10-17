import math
import unittest

from cadyx.commands import CommandSystem
from cadyx.kernel import OCCTKernelAdapter
from cadyx.models import Document


class AgentAuthoringNativeTests(unittest.TestCase):
    def setUp(self):
        self.kernel = OCCTKernelAdapter()
        self.system = CommandSystem(self.kernel)

    def seed_box(self, document, *, width=40.0, height=30.0, depth=10.0):
        geometry = self.kernel.create_box(width=width, height=height, depth=depth)
        body_id = "body:00000000-0000-0000-0000-000000000030"
        feature_id = "feature:00000000-0000-0000-0000-000000000030"
        document.objects.extend(
            [
                {
                    "id": body_id,
                    "type": "body",
                    "name": "Native seed",
                    "feature_ids": [feature_id],
                    "geometry": geometry,
                },
                {
                    "id": feature_id,
                    "type": "feature",
                    "body_id": body_id,
                    "operation": "seed",
                    "geometry": geometry,
                },
            ]
        )
        return body_id

    @staticmethod
    def body(document, body_id):
        return next(item for item in document.objects if item.get("id") == body_id)

    def test_countersink_produces_analytic_cylinder_and_cone_measurements(self):
        document = Document.create()
        body_id = self.seed_box(document)
        result = self.system.execute(
            document,
            {
                "type": "countersink_hole",
                "body_id": body_id,
                "origin": [20.0, 15.0, 0.0],
                "axis": [0.0, 0.0, 1.0],
                "hole_diameter": 6.0,
                "countersink_diameter": 12.0,
                "angle_degrees": 90.0,
            },
        )
        body = self.body(document, result["body_id"])
        topology = self.kernel.topology(body["geometry"])
        cylinders = [
            face
            for face in topology["faces"]
            if face.get("surface") == "cylinder" and "radius" in face
        ]
        cones = [
            face
            for face in topology["faces"]
            if face.get("surface") == "cone" and "semi_angle" in face
        ]
        self.assertTrue(any(math.isclose(face["radius"], 3.0, rel_tol=1e-7) for face in cylinders))
        self.assertTrue(any(math.isclose(abs(face["semi_angle"]), math.pi / 4, rel_tol=1e-7) for face in cones))
        self.assertTrue(all("semantic_key" in face for face in topology["faces"]))
        self.assertTrue(all(face.get("area", 0.0) > 0.0 for face in topology["faces"]))
        self.assertTrue(all(len(face.get("center", [])) == 3 for face in topology["faces"]))
        self.assertTrue(all(len(face.get("uv_bounds", [])) == 4 for face in topology["faces"]))

    def test_arbitrary_workplane_extrusion_has_expected_world_bounds(self):
        document = Document.create()
        result = self.system.execute(
            document,
            {
                "type": "extrude_profile_on_plane",
                "profile": [
                    {"x": 0.0, "y": 0.0},
                    {"x": 10.0, "y": 0.0},
                    {"x": 10.0, "y": 5.0},
                    {"x": 0.0, "y": 5.0},
                ],
                "origin": [2.0, 3.0, 4.0],
                "normal": [0.0, 1.0, 0.0],
                "x_axis": [1.0, 0.0, 0.0],
                "depth": 4.0,
            },
        )
        body = self.body(document, result["body_id"])
        metrics = self.kernel.metrics(body["geometry"])
        for actual, expected in zip(metrics["bounds"]["min"], [2.0, 3.0, -1.0], strict=True):
            self.assertAlmostEqual(actual, expected, places=6)
        for actual, expected in zip(metrics["bounds"]["max"], [12.0, 7.0, 4.0], strict=True):
            self.assertAlmostEqual(actual, expected, places=6)


if __name__ == "__main__":
    unittest.main()
