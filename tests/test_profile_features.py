from __future__ import annotations

import math
import tempfile
import unittest

from cadyx.core import CadyxCore
from cadyx.errors import ValidationError
from cadyx.models import Document


class ProfileFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.core = CadyxCore(self.temporary.name)
        self.project_id = self.core.create_project("Profile features")["project"]["id"]
        self.lease_id = self.core.acquire_lease(self.project_id, "agent", "profile-test")["lease_id"]

    def command(self, value: dict) -> dict:
        return self.core.execute_command(self.project_id, self.lease_id, value)["history_entry"]["result"]

    def closed_sketch(self, name: str, points: list[tuple[float, float]], plane: str = "XZ") -> tuple[str, list[str]]:
        sketch_id = self.command({"type": "create_sketch", "name": name, "plane": plane})["sketch_id"]
        point_ids = [self.command({"type": "add_sketch_element", "sketch_id": sketch_id,
                                   "element_type": "point", "x": x, "y": y})["object_id"]
                     for x, y in points]
        for index, point_id in enumerate(point_ids):
            self.command({"type": "add_sketch_element", "sketch_id": sketch_id,
                          "element_type": "line", "start_id": point_id,
                          "end_id": point_ids[(index + 1) % len(point_ids)]})
        return sketch_id, point_ids

    def test_revolution_and_extrusion_make_editable_valid_breps(self) -> None:
        bowl_sketch, bowl_points = self.closed_sketch(
            "Сечение чаши", [(0, 0), (32, 0), (32, 80), (28, 80), (28, 5), (0, 5)],
        )
        bowl = self.command({"type": "revolve_sketch", "sketch_id": bowl_sketch,
                             "name": "Чаша", "angle_degrees": 360})
        geometry = bowl["geometry"]
        expected_volume = math.pi * (32**2 * 5 + (32**2 - 28**2) * 75)
        self.assertEqual(geometry["kernel"], "occt-v1")
        self.assertEqual(geometry["kind"], "revolved_profile")
        self.assertAlmostEqual(geometry["volume"], expected_volume, delta=0.1)
        self.assertEqual(len(self.core.kernel.tessellate(geometry)["triangles"]) % 3, 0)

        handle_sketch, _ = self.closed_sketch(
            "Контур ручки",
            [(31, 60), (42, 60), (52, 55), (60, 47), (65, 40), (60, 33),
             (52, 25), (42, 20), (31, 20), (31, 26), (42, 26), (50, 29),
             (57, 35), (59, 40), (57, 45), (50, 51), (42, 54), (31, 54)],
        )
        handle = self.command({"type": "extrude_sketch", "sketch_id": handle_sketch,
                               "name": "Ручка", "depth": 12})
        self.assertEqual(handle["geometry"]["kind"], "extruded_profile")
        self.assertGreater(handle["geometry"]["volume"], 0)

        document = self.core.inspect_project(self.project_id)["document"]
        self.assertEqual(document["version"], 3)
        self.assertEqual(Document.from_dict(document).to_dict(), document)
        features = [item for item in document["objects"] if item["type"] == "feature"]
        self.assertEqual([item["operation"] for item in features], ["revolve_sketch", "extrude_sketch"])
        self.assertEqual(features[0]["source_sketch_id"], bowl_sketch)
        self.assertEqual(self.core.render_project(self.project_id)["bounds"]["max"][2], 80)

        before_volume = geometry["volume"]
        self.command({"type": "update_sketch_elements", "updates": [
            {"object_id": bowl_points[1], "changes": {"x": 34}},
        ]})
        rebuilt = self.core.inspect_project(self.project_id)["document"]
        rebuilt_body = next(item for item in rebuilt["objects"] if item.get("id") == bowl["body_id"])
        self.assertGreater(rebuilt_body["geometry"]["volume"], before_volume)
        rebuilt_feature = next(item for item in rebuilt["objects"] if item.get("id") == bowl["feature_id"])
        self.assertEqual(rebuilt_feature["geometry"], rebuilt_body["geometry"])

        with self.assertRaises(ValidationError):
            self.command({"type": "remove_sketch_object", "object_id": bowl_sketch})

    def test_revolution_rejects_open_axis_or_xy_profiles_atomically(self) -> None:
        sketch, point_ids = self.closed_sketch("Профиль", [(2, 0), (4, 0), (4, 8), (2, 8)])
        before = self.core.inspect_project(self.project_id)["document"]
        with self.assertRaises(ValidationError):
            self.command({"type": "revolve_sketch", "sketch_id": sketch})
        self.assertEqual(self.core.inspect_project(self.project_id)["document"], before)

        xz_sketch, _ = self.closed_sketch("Ось вращения", [(0, 0), (4, 0), (4, 8), (0, 8)])
        result = self.command({"type": "revolve_sketch", "sketch_id": xz_sketch,
                               "angle_degrees": 360})
        self.assertGreater(result["geometry"]["volume"], 0)
        self.assertEqual(len(point_ids), 4)

    def test_fused_mug_rebuilds_from_sources_and_renders_one_solid(self) -> None:
        cup_sketch, _ = self.closed_sketch(
            "Cup section", [(0, 0), (32, 0), (32, 80), (28, 80), (28, 5), (0, 5)],
        )
        cup = self.command({"type": "revolve_sketch", "sketch_id": cup_sketch,
                            "name": "Cup", "angle_degrees": 360})
        handle_points = [
            (31, 60), (42, 60), (52, 55), (60, 47), (65, 40), (60, 33),
            (52, 25), (42, 20), (31, 20), (31, 26), (42, 26), (50, 29),
            (57, 35), (59, 40), (57, 45), (50, 51), (42, 54), (31, 54),
        ]
        handle_sketch, handle_point_ids = self.closed_sketch("Handle", handle_points)
        handle = self.command({"type": "extrude_sketch", "sketch_id": handle_sketch,
                               "name": "Handle", "depth": 12, "centered": True})
        fused = self.command({"type": "fuse_bodies", "base_body_id": cup["body_id"],
                              "tool_body_id": handle["body_id"], "name": "Mug"})

        self.assertEqual(fused["geometry"]["kind"], "fused_profiles")
        self.assertGreater(fused["geometry"]["volume"], cup["geometry"]["volume"])
        self.assertLess(fused["geometry"]["volume"],
                        cup["geometry"]["volume"] + handle["geometry"]["volume"])
        handle_mesh = self.core.kernel.tessellate(handle["geometry"])
        self.assertAlmostEqual(min(handle_mesh["positions"][1::3]), -6)
        self.assertAlmostEqual(max(handle_mesh["positions"][1::3]), 6)

        document = self.core.inspect_project(self.project_id)["document"]
        rendered_bodies = [item["object_id"] for item in self.core.render_project(self.project_id)["objects"]
                           if item["type"] == "body"]
        self.assertEqual(rendered_bodies, [fused["body_id"]])
        self.assertEqual(Document.from_dict(document).to_dict(), document)

        before = fused["geometry"]["volume"]
        self.command({"type": "update_sketch_elements", "updates": [
            {"object_id": handle_point_ids[4], "changes": {"x": 66}},
        ]})
        rebuilt = self.core.inspect_project(self.project_id)["document"]
        rebuilt_body = next(item for item in rebuilt["objects"] if item.get("id") == fused["body_id"])
        rebuilt_feature = next(item for item in rebuilt["objects"] if item.get("id") == fused["feature_id"])
        rebuilt_handle = next(item for item in rebuilt["objects"] if item.get("id") == handle["body_id"])
        self.assertGreater(rebuilt_body["geometry"]["volume"], before)
        self.assertEqual(rebuilt_body["geometry"], rebuilt_feature["geometry"])
        self.assertEqual(rebuilt_body["geometry"]["tool"], rebuilt_handle["geometry"])

    def test_replacing_profile_rebuilds_feature_with_smooth_sampled_loop(self) -> None:
        original = [(31, 60), (42, 60), (52, 55), (60, 47), (65, 40), (60, 33),
                    (52, 25), (42, 20), (31, 20), (31, 26), (42, 26), (50, 29),
                    (57, 35), (59, 40), (57, 45), (50, 51), (42, 54), (31, 54)]
        sketch, _ = self.closed_sketch("Handle", original)
        handle = self.command({"type": "extrude_sketch", "sketch_id": sketch,
                               "name": "Handle", "depth": 12})
        points = []
        for index in range(33):
            angle = math.pi / 2 - math.pi * index / 32
            nx, nz = math.cos(angle) / 34, math.sin(angle) / 20
            length = math.hypot(nx, nz)
            points.append({"x": 31 + 34 * math.cos(angle) + 3 * nx / length,
                           "y": 40 + 20 * math.sin(angle) + 3 * nz / length})
        for index in range(32, -1, -1):
            angle = math.pi / 2 - math.pi * index / 32
            nx, nz = math.cos(angle) / 34, math.sin(angle) / 20
            length = math.hypot(nx, nz)
            points.append({"x": 31 + 34 * math.cos(angle) - 3 * nx / length,
                           "y": 40 + 20 * math.sin(angle) - 3 * nz / length})

        result = self.command({"type": "replace_sketch_profile", "sketch_id": sketch, "points": points})
        self.assertEqual(len(result["point_ids"]), 66)
        self.assertEqual(len(result["line_ids"]), 66)
        rebuilt = self.core.inspect_project(self.project_id)["document"]
        body = next(item for item in rebuilt["objects"] if item.get("id") == handle["body_id"])
        self.assertEqual(len(body["geometry"]["profile"]), 66)
        self.assertEqual(body["geometry"]["profile"], points)


if __name__ == "__main__":
    unittest.main()
