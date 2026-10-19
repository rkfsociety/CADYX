"""Focused in-memory checks for project-file scan import and rebuilds."""

from __future__ import annotations

import struct
import unittest
from pathlib import Path
from unittest.mock import Mock

from cadyx.command_scan import ScanCommands
from cadyx.mesh_geometry import geometry_from_native_buffer
from cadyx.models import Document


def _mesh():
    vertices = (0., 0., 0., 1., 0., 0., 0., 1., 0., 0., 0., 1.)
    triangles = (0, 2, 1, 0, 1, 3, 0, 3, 2, 1, 2, 3)
    header = struct.pack("<4s6If", b"CYSM", 1, 4, 4, 1, 4, 4, 0.1)
    return geometry_from_native_buffer(
        header + struct.pack("<12f", *vertices) + struct.pack("<12I", *triangles)
    )


class ProjectScanImportTests(unittest.TestCase):
    def test_import_and_parameter_edit_keep_project_source_provenance(self):
        geometry, _ = _mesh()
        stats = {
            "source_kind": "triangle_mesh", "processor_version": "test",
            "input_vertices": 4, "input_triangles": 4,
            "output_vertices": 4, "output_triangles": 4,
        }
        processor = Mock()
        processor.reconstruct.return_value = geometry, stats, "a" * 64
        resolver = Mock(return_value=Path("back-cover.ply"))
        publish = Mock(return_value={"file": "processed.cadyx-a.ply"})
        commands = ScanCommands()
        commands.resolve_scan = None
        commands.resolve_project_file = resolver
        commands.scan_processor = processor
        commands.publish_project_scan_output = publish
        document = Document.create()

        imported = commands._import_project_scan(
            document, {"file": "back-cover.ply", "name": "Back cover"},
            project_id="project-1",
        )
        feature_id = imported["feature_id"]
        feature = next(item for item in document.objects if item["id"] == feature_id)
        self.assertEqual(feature["source"], {"project_file": "back-cover.ply", "sha256": "a" * 64})
        self.assertEqual(imported["artifact"]["file"], "processed.cadyx-a.ply")

        commands._set_scan_parameter(
            document,
            {"feature_id": feature_id, "name": "target_triangles", "value": 5000},
            project_id="project-1",
        )
        self.assertEqual(resolver.call_count, 2)
        self.assertEqual(processor.reconstruct.call_args.kwargs["expected_sha256"], "a" * 64)
        self.assertEqual(publish.call_count, 2)
        self.assertEqual(feature["parameters"]["target_triangles"], 5000)
        document.validate()


if __name__ == "__main__":
    unittest.main()
