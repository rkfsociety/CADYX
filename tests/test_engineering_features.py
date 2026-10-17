from __future__ import annotations

import math
import tempfile
import unittest

from cadyx.core import CadyxCore
from cadyx.errors import ValidationError


class EngineeringFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.core = CadyxCore(self.temporary.name)
        self.project_id = self.core.create_project("Engineering features")["project"]["id"]
        self.lease_id = self.core.acquire_lease(self.project_id, "agent", "engineering-test")["lease_id"]

    def command(self, value: dict) -> dict:
        return self.core.execute_command(self.project_id, self.lease_id, value)["history_entry"]["result"]

    def test_global_minimum_thickness_certifies_convex_polyhedron(self) -> None:
        body = self.command({"type": "create_box", "width": 2.0, "height": 3.0, "depth": 5.0})
        revision = self.core.inspect_project(self.project_id)["document"]["revision"]

        passed = self.core.validate_body_thickness(
            self.project_id, body["body_id"], revision, 1.9, 0.0, method="global_minimum",
        )
        self.assertEqual(passed["status"], "pass")
        self.assertTrue(passed["certified"])
        self.assertEqual(passed["method"], "OCCT_convex_polyhedron_minimum_width")
        self.assertEqual(passed["metric_definition"], "minimum_caliper_width")
        self.assertAlmostEqual(passed["minimum_global_thickness"], 2.0, places=7)
        self.assertLessEqual(passed["minimum_bound"], 2.0)
        self.assertGreaterEqual(passed["maximum_bound"], 2.0)
        self.assertGreater(passed["direction_count"], 0)

        failed = self.core.validate_body_thickness(
            self.project_id, body["body_id"], revision, 2.1, 0.0, method="global_minimum",
        )
        self.assertEqual(failed["status"], "fail")
        self.assertTrue(failed["certified"])

        boundary = self.core.validate_body_thickness(
            self.project_id, body["body_id"], revision, 2.0, 0.0, method="global_minimum",
        )
        self.assertEqual(boundary["status"], "untested")
        self.assertTrue(boundary["certified"])

    def test_global_minimum_thickness_reports_unsupported_curved_shape(self) -> None:
        cylinder = self.core.kernel.create_extruded_circle(
            {"x": 0.0, "y": 0.0}, 2.0, plane="XY", depth=5.0,
        )
        measured = self.core.kernel.certify_global_minimum_thickness(cylinder)
        self.assertFalse(measured["supported"])
        self.assertTrue(measured["reason"])

    @staticmethod
    def _proper_crossings(left: list[tuple[float, float]], right: list[tuple[float, float]]) -> int:
        def orientation(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
            return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

        crossings = 0
        for a, b in zip(left, left[1:] + left[:1]):
            for c, d in zip(right, right[1:] + right[:1]):
                if (max(a[0], b[0]) < min(c[0], d[0]) - 1e-9
                        or max(c[0], d[0]) < min(a[0], b[0]) - 1e-9
                        or max(a[1], b[1]) < min(c[1], d[1]) - 1e-9
                        or max(c[1], d[1]) < min(a[1], b[1]) - 1e-9):
                    continue
                first, second = orientation(a, b, c), orientation(a, b, d)
                third, fourth = orientation(c, d, a), orientation(c, d, b)
                if first * second < -1e-16 and third * fourth < -1e-16:
                    crossings += 1
        return crossings

    def test_involute_spur_gear_pair_uses_reference_geometry(self) -> None:
        module, pinion_teeth, gear_teeth, face_width = 2.0, 20, 40, 8.0
        pitch_radius = module * pinion_teeth / 2
        center_distance = module * (pinion_teeth + gear_teeth) / 2
        profiles = []
        bodies = []
        for name, teeth, center_x, rotation in (
            ("Pinion", pinion_teeth, 0.0, 0.0),
            ("Gear", gear_teeth, center_distance, 180.0 - 180.0 / gear_teeth),
        ):
            sketch_id = self.command({"type": "create_sketch", "name": f"{name} profile", "plane": "XY"})["sketch_id"]
            profile = self.command({
                "type": "add_sketch_involute_gear",
                "sketch_id": sketch_id,
                "module": module,
                "tooth_count": teeth,
                "pressure_angle_degrees": 20.0,
                "center_x": center_x,
                "rotation_degrees": rotation,
            })
            self.assertTrue(profile["closed"])
            self.assertEqual(len(profile["point_ids"]), len(profile["line_ids"]))
            parameters = profile["gear_parameters"]
            self.assertAlmostEqual(parameters["pitch_radius"], module * teeth / 2)
            self.assertAlmostEqual(parameters["base_radius"], module * teeth / 2 * math.cos(math.radians(20)))
            self.assertAlmostEqual(parameters["tip_radius"], module * (teeth / 2 + 1))
            self.assertAlmostEqual(parameters["root_radius"], module * (teeth / 2 - 1.25))
            profiles.append(profile)
            bodies.append(self.command({
                "type": "extrude_sketch", "sketch_id": sketch_id,
                "name": name, "depth": face_width,
            }))

        self.assertAlmostEqual(center_distance, pitch_radius + module * gear_teeth / 2)
        self.assertEqual([body["geometry"]["kind"] for body in bodies], ["extruded_profile"] * 2)
        scene = self.core.render_project(self.project_id)
        visible_bodies = [item for item in scene["objects"] if item["type"] == "body"]
        self.assertEqual([item["object_id"] for item in visible_bodies],
                         [body["body_id"] for body in bodies])
        self.assertAlmostEqual(scene["bounds"]["min"][2], 0.0)
        self.assertAlmostEqual(scene["bounds"]["max"][2], face_width)
        self.assertTrue(all(body["triangles"] for body in visible_bodies))
        self.assertEqual(len(profiles), 2)
        document_objects = self.core.inspect_project(self.project_id)["document"]["objects"]
        points_by_id = {item["id"]: (item["x"], item["y"])
                        for item in document_objects if item["type"] == "point"}
        outlines = [[points_by_id[point_id] for point_id in profile["point_ids"]]
                    for profile in profiles]
        self.assertEqual(self._proper_crossings(*outlines), 0)

    def test_metric_thread_is_editable_single_solid_brep(self) -> None:
        result = self.command({
            "type": "create_metric_thread",
            "name": "M12x1.5 threaded rod",
            "major_diameter": 12.0,
            "pitch": 1.5,
            "length": 9.0,
        })
        geometry = result["geometry"]
        self.assertEqual(geometry["kind"], "metric_thread")
        self.assertEqual(geometry["handedness"], "right")
        self.assertAlmostEqual(geometry["major_diameter"] - 1.226869 * geometry["pitch"], 10.1596965)
        mesh = self.core.kernel.tessellate(geometry)
        self.assertGreater(len(mesh["triangles"]), 0)
        z_coordinates = mesh["positions"][2::3]
        self.assertGreaterEqual(min(z_coordinates), -1e-5)
        self.assertLessEqual(max(z_coordinates), geometry["length"] + 1e-5)
        self.assertGreater(geometry["volume"], 0)
        self.assertLess(geometry["volume"], math.pi * (geometry["major_diameter"] / 2) ** 2 * geometry["length"])
        minor_radius = geometry["major_diameter"] / 2 - (17 / 24) * (math.sqrt(3) / 2) * geometry["pitch"]
        self.assertGreater(geometry["volume"], math.pi * minor_radius ** 2 * geometry["length"])

        edited = self.command({"type": "set_parameter", "feature_id": result["feature_id"],
                               "name": "length", "value": 12.0})
        self.assertEqual(edited["geometry"]["length"], 12.0)
        self.assertGreater(edited["geometry"]["volume"], geometry["volume"])
        left_hand = self.command({"type": "set_parameter", "feature_id": result["feature_id"],
                                  "name": "handedness", "value": "left"})
        self.assertEqual(left_hand["geometry"]["handedness"], "left")
        project = self.core.inspect_project(self.project_id)
        feature = next(item for item in project["document"]["objects"]
                       if item.get("id") == result["feature_id"])
        self.assertEqual(feature["parameters"]["length"], 12.0)
        self.assertEqual(feature["parameters"]["handedness"], "left")
        self.assertEqual(feature["geometry"], left_hand["geometry"])
        self.assertEqual(project["lock"]["state"], "AI_LOCKED")

    def test_metric_thread_rejects_impossible_minor_diameter_atomically(self) -> None:
        before = self.core.inspect_project(self.project_id)["document"]
        with self.assertRaises(ValidationError):
            self.command({"type": "create_metric_thread", "major_diameter": 1.0,
                          "pitch": 1.0, "length": 2.0})
        self.assertEqual(self.core.inspect_project(self.project_id)["document"], before)

    def test_involute_gear_rejects_rack_undercut_atomically(self) -> None:
        sketch_id = self.command({"type": "create_sketch", "plane": "XY"})["sketch_id"]
        before = self.core.inspect_project(self.project_id)["document"]
        with self.assertRaises(ValidationError):
            self.command({"type": "add_sketch_involute_gear", "sketch_id": sketch_id,
                          "module": 2.0, "tooth_count": 17, "pressure_angle_degrees": 20.0})
        self.assertEqual(self.core.inspect_project(self.project_id)["document"], before)


if __name__ == "__main__":
    unittest.main()
