from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from tools.revopoint.capture_continuous_turn import interpolate_angle, select_uniform_frames


class ContinuousTurnCaptureTests(unittest.TestCase):
    def test_interpolates_unwrapped_encoder_angle_across_modulo_wrap(self) -> None:
        start = datetime(2026, 10, 21, tzinfo=timezone.utc)
        samples = [(start, 0.0), (start + timedelta(seconds=1), 180.0),
                   (start + timedelta(seconds=2), 360.0)]
        self.assertEqual(interpolate_angle(start + timedelta(seconds=0.5), samples), 90.0)

    def test_selects_unique_frames_at_each_thirty_degree_target(self) -> None:
        start = datetime(2026, 10, 21, tzinfo=timezone.utc)
        rows = [{"file": f"frame_{index:05d}.raw", "frame_mtime_utc":
                 (start + timedelta(seconds=index)).isoformat()} for index in range(12)]
        encoder = [(start + timedelta(seconds=index), float(index * 30)) for index in range(13)]
        selected = select_uniform_frames(rows, encoder)
        self.assertEqual(len(selected), 12)
        self.assertEqual([round(angle) for _, angle in selected], list(range(0, 360, 30)))
        self.assertEqual(len({row["file"] for row, _ in selected}), 12)

    def test_refuses_a_run_without_enough_unique_views(self) -> None:
        start = datetime(2026, 10, 21, tzinfo=timezone.utc)
        rows = [{"file": f"frame_{index:05d}.raw", "frame_mtime_utc": start.isoformat()} for index in range(12)]
        encoder = [(start, float(index * 30)) for index in range(12)]
        with self.assertRaisesRegex(ValueError, "No distinct Y16 frame"):
            select_uniform_frames(rows, encoder)


if __name__ == "__main__":
    unittest.main()
