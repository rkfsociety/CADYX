from __future__ import annotations

import unittest
import json
import os
import struct
import subprocess
import tempfile
from pathlib import Path


class RevopointToolContractTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Native Revopoint tools require Windows")
    def test_saved_y16_conversion_preserves_input_and_rejects_invalid_data(self) -> None:
        root = Path(__file__).resolve().parents[1]
        executable = root / "tools" / "revopoint" / "build" / "CaptureY16.exe"
        if not executable.exists():
            self.skipTest("Build the native Revopoint tools to run the converter integration test")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            frame = directory / "input.raw"
            frame_bytes = struct.pack("<H", 30000) * 200 + bytes(640 * 400 * 2 - 400)
            frame.write_bytes(frame_bytes)
            q_file = directory / "Q.bin"
            matrix = [0.0] * 16
            matrix[0] = matrix[5] = matrix[11] = 1.0
            q_file.write_bytes(struct.pack("<16f", *matrix))
            output = directory / "pose"
            command = [str(executable), "--input-raw", str(frame), "--out-prefix", str(output),
                       "--q-file", str(q_file), "--depth-scale", "0.005"]
            result = subprocess.run(command, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.with_suffix(".raw").read_bytes(), frame_bytes)
            self.assertEqual(json.loads(output.with_suffix(".json").read_text())["point_count"], 200)
            self.assertNotEqual(subprocess.run(command, capture_output=True, timeout=30).returncode, 0)
            frame.write_bytes(b"invalid")
            command[4] = str(directory / "invalid")
            self.assertNotEqual(subprocess.run(command, capture_output=True, timeout=30).returncode, 0)
            self.assertFalse((directory / "invalid.raw").exists())
            dead_parent = subprocess.run(
                [str(executable), "--live-only", "--live-frames-dir", str(directory),
                 "--stop-file", str(directory / "stop"), "--parent-pid", "4294967295"],
                capture_output=True, timeout=30,
            )
            self.assertNotEqual(dead_parent.returncode, 0)
            self.assertIn(b"Could not monitor the controlling process", dead_parent.stderr)

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

    def test_each_scan_frame_is_saved_before_the_next_motion(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "tools" / "revopoint" / "turntable.cs").read_text(
            encoding="utf-8",
        )
        helper = source[source.index("private static void CaptureY16Pose("):source.index("private static string QuoteWindowsArgument(")]
        self.assertIn("process.WaitForExit(30000)", helper)
        self.assertIn("process.ExitCode != 0", helper)
        self.assertIn('"--input-raw", frame', helper)
        self.assertIn("stream.NextStationaryFrame()", helper)
        self.assertIn('new[] { ".raw", ".ply", ".json" }', helper)
        self.assertIn("!file.Exists || file.Length == 0", helper)
        self.assertNotIn("PauseAfterCaptureAsync", source)
        self.assertEqual(source.count('"capture_completed"'), 2)
        for capture, completion in zip(
            [i for i in range(len(source)) if source.startswith("CaptureY16Pose(scanCapture, stream, prefix);", i)],
            [i for i in range(len(source)) if source.startswith('"capture_completed"', i)],
        ):
            self.assertLess(capture, completion)
        capture_source = (Path(__file__).resolve().parents[1] / "tools" / "revopoint" / "capture_y16.cpp").read_text(encoding="utf-8")
        self.assertIn('CheckHr(source->Shutdown(), "Stop depth camera source");', capture_source)
        self.assertIn('"--live-only", "--live-frames-dir", directory', source)
        self.assertIn('"--parent-pid", Process.GetCurrentProcess().Id', source)
        self.assertIn("WaitForSingleObject(parent.value, 0)", capture_source)
        self.assertIn("latest >= baseline + 2", source)
        self.assertIn("previousTargetAngle", source)


if __name__ == "__main__":
    unittest.main()
