from __future__ import annotations

import unittest

from cadyx.curve_editing import _execute
from cadyx.errors import ConflictError, ValidationError
from cadyx.models import Document


class CurveEditingTests(unittest.TestCase):
    def _command(self) -> dict[str, object]:
        return {
            "type": "save_traced_curve_version",
            "name": "Reference contour",
            "source_image_sha256": "ab" * 32,
            "coordinate_system": {
                "origin": {"x": 0.0, "y": 0.0},
                "units_per_pixel": 0.5,
                "unit": "mm",
                "y_axis": "down",
            },
            "segments": [
                {
                    "kind": "line",
                    "start": {"x": 0.0, "y": 0.0},
                    "end": {"x": 20.0, "y": 0.0},
                },
                {
                    "kind": "arc",
                    "center": {"x": 20.0, "y": 10.0},
                    "radius_pixels": 10.0,
                    "start_angle_degrees": -90.0,
                    "sweep_degrees": 90.0,
                },
            ],
            "closed": False,
        }

    def test_save_recomputes_exact_line_and_arc_and_records_provenance(self) -> None:
        document = Document.create()
        result = _execute(None, document, self._command())
        document.validate()

        self.assertEqual(result["trace_version"], 1)
        sketch = next(item for item in document.objects if item["id"] == result["sketch_id"])
        self.assertEqual(sketch["trace_source_image_sha256"], "ab" * 32)
        self.assertEqual(sketch["trace_coordinate_system"]["units_per_pixel"], 0.5)
        self.assertEqual(sketch["trace_version"], 1)
        circles = [item for item in document.objects if item.get("sketch_id") == sketch["id"] and item["type"] == "circle"]
        self.assertEqual(len(circles), 1)
        self.assertAlmostEqual(circles[0]["radius"], 5.0)

    def test_each_save_creates_new_immutable_version(self) -> None:
        document = Document.create()
        first = _execute(None, document, self._command())
        second_command = self._command()
        second_command["trace_id"] = first["trace_id"]
        second_command["base_version"] = 1
        second = _execute(None, document, second_command)
        document.validate()

        self.assertEqual(second["trace_version"], 2)
        self.assertNotEqual(first["sketch_id"], second["sketch_id"])
        versions = sorted(
            item["trace_version"]
            for item in document.objects
            if item.get("type") == "sketch" and item.get("trace_id") == first["trace_id"]
        )
        self.assertEqual(versions, [1, 2])

    def test_stale_base_version_is_rejected(self) -> None:
        document = Document.create()
        first = _execute(None, document, self._command())
        stale = self._command()
        stale["trace_id"] = first["trace_id"]
        stale["base_version"] = 0
        with self.assertRaises(ConflictError):
            _execute(None, document, stale)

    def test_disconnected_segments_are_rejected(self) -> None:
        document = Document.create()
        command = self._command()
        command["segments"][1]["center"] = {"x": 100.0, "y": 100.0}  # type: ignore[index]
        with self.assertRaises(ValidationError):
            _execute(None, document, command)


if __name__ == "__main__":
    unittest.main()
