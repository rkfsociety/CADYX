from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.revopoint.register_point_clouds_first_frame import source_paths


class PointCloudRegistrationInputTests(unittest.TestCase):
    def test_resolves_all_twelve_legacy_pose_clouds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            run_id = "revopoint_20261020T103638Z"
            for index in range(12):
                (project / f"{run_id}_pose_pose_{index:03d}.ply").touch()
            paths = source_paths(project, run_id)
        self.assertEqual(len(paths), 12)
        self.assertEqual(paths[0].name, f"{run_id}_pose_pose_000.ply")
        self.assertEqual(paths[-1].name, f"{run_id}_pose_pose_011.ply")

    def test_refuses_incomplete_frame_sets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            run_id = "revopoint_20261020T103638Z"
            for index in range(11):
                (project / f"{run_id}_pose_{index:03d}.ply").touch()
            with self.assertRaises(FileNotFoundError):
                source_paths(project, run_id)


if __name__ == "__main__":
    unittest.main()
