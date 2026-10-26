from __future__ import annotations

import unittest
from uuid import uuid4

from cadyx.commands import CommandSystem
from cadyx.models import Document


class RemoveObjectsBindingTests(unittest.TestCase):
    def test_removing_a_bound_sketch_point_removes_its_parameter_binding(self) -> None:
        document = Document.create()
        sketch_id = f"sketch:{uuid4()}"
        point_id = f"point:{uuid4()}"
        document.objects.extend([
            {"id": sketch_id, "type": "sketch", "name": "Support", "plane": "XY"},
            {"id": point_id, "type": "point", "sketch_id": sketch_id, "x": 2.0, "y": 0.0},
        ])
        document.parameter_bindings.append({
            "object_id": point_id, "path": "x", "expression": "2",
        })
        document.validate()

        result = CommandSystem._remove_objects(document, {
            "type": "remove_objects", "object_ids": [point_id],
        })

        self.assertEqual(result["removed_count"], 1)
        self.assertEqual(document.parameter_bindings, [])
        self.assertIsNone(document.find_object(point_id))
        document.validate()
