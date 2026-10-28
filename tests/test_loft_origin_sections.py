from __future__ import annotations

import unittest
from types import SimpleNamespace

from cadyx.core import CadyxCore

from cadyx.commands import CommandSystem
from cadyx.kernel import OCCTKernelAdapter
from cadyx.models import Document


class LoftOriginSectionsTests(unittest.TestCase):
    def test_named_origin_create_update_and_rebuild(self) -> None:
        for plane, axis, dim in (("XY", "Z", 2), ("XZ", "Y", 1)):
            with self.subTest(plane=plane):
                kernel = OCCTKernelAdapter()
                commands = CommandSystem(kernel)
                document = Document.create()
                sketches = []
                for offset in (None, 10.0):
                    command = {"type": "create_sketch", "plane": plane}
                    if offset is not None:
                        command.update(section_normal_axis=axis, section_offset=offset)
                    sketch_id = commands.execute(document, command)["sketch_id"]
                    commands.execute(document, {
                        "type": "add_sketch_rectangle", "sketch_id": sketch_id,
                        "x": -2, "y": -2, "width": 4, "height": 4,
                    })
                    sketches.append(sketch_id)
                loft = commands.execute(document, {
                    "type": "loft_sketches", "sketch_ids": sketches,
                })
                commands.execute(document, {
                    "type": "update_loft_sections", "feature_id": loft["feature_id"],
                    "section_groups": [[sketch] for sketch in sketches],
                })
                commands._rebuild_changed_features(document, {sketches[0]})
                body = document.find_object(loft["body_id"], "body")
                metrics = kernel.metrics(body["geometry"])
                self.assertTrue(metrics["brep_valid"])
                self.assertEqual(metrics["topology"]["solid_count"], 1)
                self.assertAlmostEqual(metrics["bounds"]["min"][dim], 0.0, places=5)
                self.assertAlmostEqual(metrics["bounds"]["max"][dim], 10.0, places=5)
                self.assertAlmostEqual(metrics["volume"], 160.0, places=5)
                document.validate()
                kernel.validate(body["geometry"])
                CadyxCore._validate_geometry(SimpleNamespace(kernel=kernel), document)
