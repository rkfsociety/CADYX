from __future__ import annotations

import unittest
from uuid import uuid4

from cadyx.command_sketches import _handle_remove_sketch_object
from cadyx.errors import ValidationError
from cadyx.models import Document


class SketchRemovalTests(unittest.TestCase):
    def _document(self):
        document = Document.create()
        document.model_parameters = {"SketchCoordinate": {"value": 0.0, "unit": "mm"}}
        sketch_id = f"sketch:{uuid4()}"
        point_ids = [f"point:{uuid4()}" for _ in range(3)]
        line_id = f"line:{uuid4()}"
        circle_id = f"circle:{uuid4()}"
        document.objects = [
            {"id": sketch_id, "type": "sketch", "name": "Section", "plane": "XY"},
            {"id": point_ids[0], "type": "point", "sketch_id": sketch_id, "x": 0.0, "y": 0.0},
            {"id": point_ids[1], "type": "point", "sketch_id": sketch_id, "x": 2.0, "y": 0.0},
            {"id": point_ids[2], "type": "point", "sketch_id": sketch_id, "x": 1.0, "y": 1.0},
            {"id": line_id, "type": "line", "sketch_id": sketch_id,
             "start_id": point_ids[0], "end_id": point_ids[1]},
            {"id": circle_id, "type": "circle", "sketch_id": sketch_id,
             "center_id": point_ids[2], "radius": 0.5},
        ]
        document.parameter_bindings = [
            {"object_id": point_ids[0], "path": "x", "expression": "SketchCoordinate"},
        ]
        document.validate()
        return document, sketch_id

    def test_cascade_removes_sketch_elements_and_parameter_bindings_atomically(self):
        document, sketch_id = self._document()

        result = _handle_remove_sketch_object(None, document, {
            "type": "remove_sketch_object", "object_id": sketch_id, "cascade": True,
        })

        self.assertEqual(result, {"object_id": sketch_id, "removed_count": 6})
        self.assertEqual(document.objects, [])
        self.assertEqual(document.parameter_bindings, [])
        document.validate()

    def test_sketch_with_elements_requires_explicit_cascade(self):
        document, sketch_id = self._document()
        before = list(document.objects)

        with self.assertRaises(ValidationError):
            _handle_remove_sketch_object(None, document, {
                "type": "remove_sketch_object", "object_id": sketch_id,
            })

        self.assertEqual(document.objects, before)

    def test_cascade_does_not_remove_a_sketch_used_by_a_feature(self):
        document, sketch_id = self._document()
        feature_id = f"feature:{uuid4()}"
        document.objects.append({
            "id": feature_id, "type": "feature", "operation": "extrude_sketch",
            "body_id": f"body:{uuid4()}",
            "source_sketch_id": sketch_id,
        })
        before = list(document.objects)

        with self.assertRaises(ValidationError):
            _handle_remove_sketch_object(None, document, {
                "type": "remove_sketch_object", "object_id": sketch_id, "cascade": True,
            })

        self.assertEqual(document.objects, before)
