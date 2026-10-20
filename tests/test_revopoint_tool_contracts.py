from __future__ import annotations

import unittest
from pathlib import Path


class RevopointToolContractTests(unittest.TestCase):
    def test_each_scan_frame_waits_three_seconds_before_the_next_motion(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "tools" / "revopoint" / "turntable.cs").read_text(
            encoding="utf-8",
        )
        helper_start = source.index("private static async Task PauseAfterCaptureAsync(")
        helper_end = source.index("private static void CaptureY16Pose(", helper_start)
        helper = source[helper_start:helper_end]

        self.assertIn("await Task.Delay(TimeSpan.FromSeconds(3));", helper)
        self.assertEqual(source.count("await PauseAfterCaptureAsync();"), 2)
        self.assertEqual(source.count('"capture_completed"'), 2)
        for capture, completion, pause in zip(
            [i for i in range(len(source)) if source.startswith("CaptureY16Pose(scanCapture, prefix);", i)],
            [i for i in range(len(source)) if source.startswith('"capture_completed"', i)],
            [i for i in range(len(source)) if source.startswith("await PauseAfterCaptureAsync();", i)],
        ):
            self.assertLess(capture, completion)
            self.assertLess(completion, pause)


if __name__ == "__main__":
    unittest.main()
