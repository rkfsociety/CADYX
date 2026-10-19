from __future__ import annotations

import unittest

from cadyx.commands import CommandSystem
from cadyx.errors import ValidationError
from cadyx.kernel import OCCTKernelAdapter
from cadyx.migrations import DOCUMENT_VERSION
from cadyx.models import Document
from cadyx.sketch_workplanes import world_position


class SketchWorkplaneTests(unittest.TestCase):
    def setUp(self):
        self.commands = CommandSystem(OCCTKernelAdapter())
        self.document = Document.create()

    def rectangle(self, sketch_id):
        self.commands.execute(self.document, {
            "type": "add_sketch_rectangle", "sketch_id": sketch_id,
            "x": 0, "y": 0, "width": 8, "height": 5,
        })

    def test_old_xy_xz_round_trip(self):
        old = self.document.to_dict()
        old["version"] = 11
        old["objects"] = [{
            "id": "sketch:legacy", "type": "sketch",
            "name": "Legacy section", "plane": "XZ",
            "section_normal_axis": "Y", "section_offset": 4.0,
        }]
        loaded = Document.from_dict(old)
        self.assertEqual(loaded.version, DOCUMENT_VERSION)
        self.assertEqual(world_position(loaded.objects[0], 2, 3), [2, 4, 3])
        self.assertNotIn("frame", loaded.objects[0])
        loaded.validate()

    def test_yz_extrude_and_custom_frame_rebuild(self):
        sketch_id = self.commands.execute(self.document, {
            "type": "create_sketch", "plane": "YZ", "name": "Side face",
        })["sketch_id"]
        self.rectangle(sketch_id)
        created = self.commands.execute(self.document, {
            "type": "extrude_sketch", "sketch_id": sketch_id, "depth": 3,
        })
        bounds = self.commands.kernel.metrics(created["geometry"])["bounds"]
        self.assertEqual([round(v, 5) for v in bounds["min"]], [0, 0, 0])
        self.assertEqual([round(v, 5) for v in bounds["max"]], [3, 8, 5])
        self.commands.execute(self.document, {
            "type": "set_sketch_workplane", "sketch_id": sketch_id,
            "plane": "CUSTOM",
            "frame": {"origin": [10, 20, 30], "normal": [0, 0, 1],
                      "x_axis": [1, 0, 0]},
        })
        body = self.document.find_object(created["body_id"], "body")
        after = self.commands.kernel.metrics(body["geometry"])["bounds"]
        self.assertEqual([round(v, 5) for v in after["min"]], [10, 20, 30])
        self.document.validate()

    def test_parallel_axes_fail_closed(self):
        sketch_id = self.commands.execute(self.document, {"type": "create_sketch"})["sketch_id"]
        with self.assertRaises(ValidationError):
            self.commands.execute(self.document, {
                "type": "set_sketch_workplane", "sketch_id": sketch_id,
                "plane": "CUSTOM", "frame": {
                    "origin": [0, 0, 0], "normal": [0, 0, 1],
                    "x_axis": [0, 0, 2],
                },
            })
        self.assertEqual(self.document.find_object(sketch_id, "sketch")["plane"], "XY")

    def test_face_attachment_extrudes_planar_face(self):
        body = self.commands.execute(self.document, {
            "type": "create_box", "name": "Datum",
            "width": 12, "height": 12, "depth": 12,
        })
        faces = self.commands.kernel.topology(body["geometry"])["faces"]
        face = next(
            face for face in faces
            if str(face.get("surface", "")).lower() in {"plane", "planar"}
            and abs(face["normal"][2]) > 0.9
        )
        sketch_id = self.commands.execute(self.document, {
            "type": "create_sketch", "plane": "FACE",
            "attachment": {
                "body_id": body["body_id"],
                "face_key": face["semantic_key"],
                "x_axis": [1, 0, 0],
            },
        })["sketch_id"]
        self.rectangle(sketch_id)
        result = self.commands.execute(self.document, {
            "type": "extrude_sketch", "sketch_id": sketch_id, "depth": 2,
        })
        self.assertGreater(self.commands.kernel.metrics(result["geometry"])["volume"], 0)
        self.document.validate()


if __name__ == "__main__":
    unittest.main()
