import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools" / "revopoint"
sys.path.insert(0, str(TOOLS))

from fuse_known_pose_pair import (  # noqa: E402
    align_second_frame,
    fuse_pair,
    overlap_metrics,
    rotate_about_axis,
    voxel_average,
    write_ascii_ply,
)


class KnownPosePairFusionTests(unittest.TestCase):
    def test_arbitrary_axis_rotation_and_inverse_alignment(self):
        point = (2.0, 1.0, 0.0)
        pivot = (1.0, 1.0, 0.0)
        axis = (0.0, 0.0, 1.0)
        turned = rotate_about_axis(point, pivot, axis, 90.0)
        self.assertAlmostEqual(turned[0], 1.0)
        self.assertAlmostEqual(turned[1], 2.0)
        restored = align_second_frame([turned], 90.0, pivot, axis)[0]
        for actual, expected in zip(restored, point):
            self.assertAlmostEqual(actual, expected)

    def test_overlap_and_voxel_average_report_real_counts(self):
        cloud = [(0.1, 0.1, 0.1), (0.2, 0.2, 0.2), (2.1, 0.1, 0.1)]
        overlap = overlap_metrics(cloud, cloud, sample_stride=1)
        self.assertEqual(overlap["reference_to_aligned_second"]["within_1"], 1.0)
        self.assertEqual(overlap["aligned_second_to_reference"]["within_5"], 1.0)
        merged = voxel_average(cloud, 1.0)
        self.assertEqual(len(merged), 2)
        self.assertAlmostEqual(merged[0][0], 0.15)

    def test_pair_fusion_preserves_sources_and_writes_pose_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_path = root / "first.ply"
            second_path = root / "second.ply"
            output_path = root / "fused.ply"
            original = [(1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (2.0, 0.0, 1.0)]
            turned = [rotate_about_axis(point, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 15.0)
                      for point in original]
            write_ascii_ply(first_path, original, "source first")
            write_ascii_ply(second_path, turned, "source second")
            first_bytes, second_bytes = first_path.read_bytes(), second_path.read_bytes()

            metadata = fuse_pair(first_path, second_path, output_path, 15.0,
                                 (0.0, 0.0, 1.0), (0.0, 0.0, 0.0),
                                 voxel_size=0.5, sample_stride=1)

            self.assertEqual(first_path.read_bytes(), first_bytes)
            self.assertEqual(second_path.read_bytes(), second_bytes)
            self.assertTrue(output_path.is_file())
            saved = json.loads(output_path.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "provisional_fused_candidate")
            self.assertEqual(saved["registration"]["overlap"]["reference_to_aligned_second"]["within_1"], 1.0)
            self.assertEqual(metadata["output_points"], 3)
            with self.assertRaises(FileExistsError):
                fuse_pair(first_path, second_path, output_path, 15.0,
                          (0.0, 0.0, 1.0), (0.0, 0.0, 0.0),
                          voxel_size=0.5, sample_stride=1)


if __name__ == "__main__":
    unittest.main()
