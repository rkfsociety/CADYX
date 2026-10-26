from __future__ import annotations

import unittest
from uuid import uuid4

from cadyx.commands import CommandSystem
from cadyx.kernel import PrimitiveKernelAdapter
from cadyx.models import Document


class ClearModelTests(unittest.TestCase):
    def test_clear_model_removes_geometry_parameters_and_bindings(self) -> None:
        document = Document.create()
        sketch_id = f"sketch:{uuid4()}"
        point_id = f"point:{uuid4()}"
        document.objects.extend([
            {"id": sketch_id, "type": "sketch", "name": "Profile", "plane": "XY"},
            {"id": point_id, "type": "point", "sketch_id": sketch_id, "x": 10.0, "y": 0.0},
        ])
        document.model_parameters["Width"] = {"value": 10.0, "unit": "mm"}
        document.parameter_bindings.append({
            "object_id": point_id, "path": "x", "expression": "Width",
        })
        document.validate()

        result = CommandSystem(PrimitiveKernelAdapter()).execute(document, {"type": "clear_model"})

        self.assertEqual(result["removed_object_count"], 2)
        self.assertEqual(document.objects, [])
        self.assertEqual(document.model_parameters, {})
        self.assertEqual(document.parameter_bindings, [])
        self.assertEqual(document.assembly["parts"], [])
        document.validate()


if __name__ == "__main__":
    unittest.main()
