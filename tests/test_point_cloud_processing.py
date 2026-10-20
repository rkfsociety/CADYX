"""Behavioral checks for full-resolution point processing and persisted stage lineage."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from cadyx.command_scan import ScanCommands
from cadyx.errors import ValidationError
from cadyx.mesh_geometry import point_cloud_geometry
from cadyx.models import Document
from cadyx.point_cloud import process_points
from cadyx.scan import read_point_cloud_ply


class PointCloudProcessingTests(unittest.TestCase):
    def test_registered_command_and_persisted_schema(self):
        from cadyx.capabilities import COMMAND_REGISTRY
        from tests.test_persisted_schemas import feature_document
        self.assertIn("process_point_cloud", COMMAND_REGISTRY.command_types)
        document, feature = feature_document("process_point_cloud")
        document.validate()
        self.assertEqual(Document.from_dict(document.to_dict()).to_dict(), document.to_dict())
        feature["previous_feature_id"] = feature["id"]
        with self.assertRaises(ValidationError):
            document.validate()

    def test_filters_and_counts(self):
        points = [(0., 0., 0.), (.1, 0., 0.), (.2, 0., 0.), (9., 9., 9.)]
        self.assertEqual(process_points(points, "radius_filter", {"radius": 1e308, "min_neighbors": 3}), points)
        self.assertEqual(process_points(points + points[:1], "deduplicate", {}), points)
        self.assertEqual(process_points(points, "radius_filter", {"radius": .21, "min_neighbors": 2}), points[:3])
        self.assertEqual(process_points(points, "crop", {"min": [-1, -1, -1], "max": [1, 1, 1]}), points[:3])
        with self.assertRaises(ValidationError):
            process_points(points, "radius_filter", {"radius": .01, "min_neighbors": 1})
        with self.assertRaises(ValidationError):
            process_points(points, "radius_filter", {"radius": True, "min_neighbors": 1})

    def test_voxels_use_centroids_including_negative_coordinates(self):
        points = [(-.2, 0., 0.), (-.4, 0., 0.), (1., 0., 0.), (2., 0., 0.)]
        result = process_points(points, "voxel_downsample", {"voxel_size": 1.})
        self.assertEqual(len(result), 3)
        self.assertAlmostEqual(result[0][0], -.3)

    def test_full_reader_keeps_every_point_and_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cloud.ply"
            from cadyx.mesh_geometry import mesh_ply_bytes
            points = [(float(i), 0., 0.) for i in range(125010)]
            path.write_bytes(mesh_ply_bytes(point_cloud_geometry(points)))
            full, digest, stats = read_point_cloud_ply(path, full_resolution=True)
            sampled, _, _ = read_point_cloud_ply(path)
            self.assertEqual(full, points)
            self.assertEqual(stats["point_count"], len(points))
            self.assertLess(len(sampled), len(full))

    def test_stages_read_previous_full_artifact_and_preserve_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "cloud.ply"
            path.write_text("ply\nformat ascii 1.0\nelement vertex 5\nproperty float x\nproperty float y\nproperty float z\nend_header\n0 0 0\n0 0 0\n1 0 0\n2 0 0\n99 0 0\n")
            original = path.read_bytes()
            commands = ScanCommands()
            commands.kernel = Mock()
            commands.kernel.create_point_cloud.side_effect = point_cloud_geometry
            commands.resolve_project_file = lambda project_id, filename: root / filename
            def publish(project_id, filename, content):
                dest = root / ("stage-" + str(len(list(root.iterdir()))) + ".ply")
                dest.write_bytes(content)
                return {"file": dest.name}
            commands.publish_project_scan_output = publish
            document = Document.create()
            imported = commands._import_project_point_cloud(document, {"file": path.name}, project_id="project")
            first = commands._process_point_cloud(document, {"body_id": imported["body_id"], "method": "deduplicate"}, project_id="project")
            second = commands._process_point_cloud(document, {"body_id": imported["body_id"], "method": "crop", "parameters": {"min": [-1, -1, -1], "max": [3, 1, 1]}}, project_id="project")
            self.assertEqual(first["stats"]["output_points"], 4)
            self.assertEqual(second["stats"]["input_points"], 4)
            self.assertEqual(second["stats"]["output_points"], 3)
            self.assertEqual(document.objects[-1]["previous_feature_id"], first["feature_id"])
            self.assertEqual(path.read_bytes(), original)
            document.validate()
            (root / second["artifact"]["file"]).write_bytes(b"changed")
            with self.assertRaises(ValidationError):
                commands._process_point_cloud(document, {"body_id": imported["body_id"], "method": "deduplicate"}, project_id="project")
            self.assertEqual(len(document.objects), 4)


if __name__ == "__main__":
    unittest.main()
