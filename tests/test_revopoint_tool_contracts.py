from __future__ import annotations

import unittest
from pathlib import Path


class RevopointToolContractTests(unittest.TestCase):
    def test_360_scan_homes_both_table_axes_before_setting_speed_or_capturing(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "tools" / "revopoint" / "scan_360.ps1").read_text(
            encoding="utf-8",
        )
        scan_start = source.index("$complete = $false")
        scan_work = source[scan_start:]

        zero = scan_work.index("'zero'")
        speed = scan_work.index("'speed'")
        capture = scan_work.index("'capture-sweep'")
        self.assertLess(zero, speed)
        self.assertLess(speed, capture)

    def test_capture_sweep_finishes_before_frame_cleanup_starts(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "tools" / "revopoint" / "scan_360.ps1").read_text(encoding="utf-8")
        work = source[source.index("$complete = $false"):]
        self.assertLess(work.index("'capture-sweep'"), work.index("$preprocessingWorker = Start-Process"))
        self.assertIn("'--timing', 'after_capture_before_registration'", work)
        self.assertLess(work.index("[IO.File]::WriteAllText($workerStopPath"),
                        work.index("$preprocessingWorker = Start-Process"))

    def test_rotation_step_tolerance_handles_observed_overshoot_without_relaxing_closure(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "tools" / "revopoint" / "turntable.cs").read_text(
            encoding="utf-8",
        )

        self.assertIn("private const double RotationSetpointToleranceDegrees = 1.5;", source)
        self.assertIn("Math.Abs(sweepMeasured - degrees) <= RotationSetpointToleranceDegrees", source)
        self.assertIn("Math.Abs(SignedAngleDelta(baselineAngle, finalAngle)) > 1.0", source)

    def test_zero_refuses_to_move_either_axis_when_tilt_exceeds_ten_degrees(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "tools" / "revopoint" / "turntable.cs").read_text(
            encoding="utf-8",
        )
        zero_start = source.index('if (operation == "zero")')
        zero_end = source.index("string beforeText =", zero_start)
        zero = source[zero_start:zero_end]

        self.assertIn("Math.Abs(tiltBaseline) > MaxSafeTiltDegrees", zero)
        self.assertLess(zero.index("Math.Abs(tiltBaseline) > MaxSafeTiltDegrees"), zero.index('"+CT,TOZERO;"'))
        self.assertLess(zero.index("Math.Abs(tiltBaseline) > MaxSafeTiltDegrees"), zero.index('"+CR,TOZERO;"'))

    def test_cleanup_exit_code_discrepancy_requires_hash_verified_outputs_before_alignment(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "tools" / "revopoint" / "scan_360.ps1").read_text(
            encoding="utf-8",
        )

        self.assertIn("$preprocessingWorker.Refresh()", source)
        self.assertIn("if ($workerExitCode -ne 0 -and $verifiedCleanupOutputs)", source)
        self.assertIn("Get-FileHash -LiteralPath $cleanedPath -Algorithm SHA256", source)
        worker_section = source[source.index("$preprocessingWorker.Refresh()") :]
        self.assertLess(worker_section.index("if (-not $verifiedCleanupOutputs)"),
                        worker_section.index("if (-not $SkipTableMarkerAlignment)"))

    def test_each_scan_frame_waits_one_second_before_the_next_motion(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "tools" / "revopoint" / "turntable.cs").read_text(
            encoding="utf-8",
        )
        helper_start = source.index("private static async Task PauseAfterCaptureAsync(")
        helper_end = source.index("private static void CaptureY16Pose(", helper_start)
        helper = source[helper_start:helper_end]

        self.assertIn("await Task.Delay(TimeSpan.FromSeconds(1));", helper)
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
