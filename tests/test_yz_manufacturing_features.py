from __future__ import annotations

import tempfile
import unittest

from cadyx.core import CadyxCore
from cadyx.models import Document


class YZManufacturingFeatureTests(unittest.TestCase):
    def test_yz_pad_and_pocket_place_frames_on_creation_and_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            core = CadyxCore(directory)
            document = Document.create()
            project_id = "yz-manufacturing-placement"

            def execute(command):
                return core.commands.execute(document, command, project_id=project_id)

            pad_base = execute({
                "type": "create_box", "width": 4, "height": 4, "depth": 4,
            })["body_id"]
            pad_sketch = execute({
                "type": "create_sketch", "plane": "YZ",
                "section_normal_axis": "X", "section_offset": 3,
            })["sketch_id"]
            execute({
                "type": "add_sketch_rectangle", "sketch_id": pad_sketch,
                "x": 3, "y": 1, "width": 2, "height": 2,
            })
            pad = execute({
                "type": "pad_sketch_to_body", "body_id": pad_base,
                "sketch_id": pad_sketch, "depth": 2,
            })
            pad_body = document.find_object(pad["body_id"], "body")
            metrics = core.kernel.metrics(pad_body["geometry"])
            self.assertAlmostEqual(metrics["volume"], 70, places=7)
            self.assertAlmostEqual(metrics["bounds"]["max"][0], 5, places=7)

            execute({
                "type": "set_parameter", "feature_id": pad["feature_id"],
                "name": "depth", "value": 3,
            })
            document.validate()
            core._validate_geometry(document)
            metrics = core.kernel.metrics(pad_body["geometry"])
            self.assertAlmostEqual(metrics["volume"], 74, places=7)
            self.assertAlmostEqual(metrics["bounds"]["max"][0], 6, places=7)

            pocket_base = execute({
                "type": "create_box", "width": 4, "height": 4, "depth": 4,
            })["body_id"]
            pocket_sketch = execute({
                "type": "create_sketch", "plane": "YZ",
                "section_normal_axis": "X", "section_offset": 2,
            })["sketch_id"]
            execute({
                "type": "add_sketch_rectangle", "sketch_id": pocket_sketch,
                "x": 1, "y": 1, "width": 2, "height": 2,
            })
            pocket = execute({
                "type": "pocket_sketch", "body_id": pocket_base,
                "sketch_id": pocket_sketch, "depth": 1,
            })
            pocket_body = document.find_object(pocket["body_id"], "body")
            self.assertAlmostEqual(core.kernel.metrics(pocket_body["geometry"])["volume"], 60, places=7)

            execute({
                "type": "set_parameter", "feature_id": pocket["feature_id"],
                "name": "depth", "value": 2,
            })
            document.validate()
            core._validate_geometry(document)
            self.assertAlmostEqual(core.kernel.metrics(pocket_body["geometry"])["volume"], 56, places=7)

            execute({
                "type": "create_model_parameter", "name": "PocketDepth",
                "value": 2, "unit": "mm",
            })
            execute({
                "type": "bind_model_parameter", "object_id": pocket["feature_id"],
                "path": "parameters.depth", "expression": "PocketDepth",
            })
            execute({"type": "set_model_parameter", "name": "PocketDepth", "value": 1})
            document.validate()
            core._validate_geometry(document)
            pocket_body = document.find_object(pocket["body_id"], "body")
            self.assertAlmostEqual(core.kernel.metrics(pocket_body["geometry"])["volume"], 60, places=7)


if __name__ == "__main__":
    unittest.main()
