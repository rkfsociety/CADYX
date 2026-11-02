"""Portable regression tests for the Windows source sandbox I/O boundary."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cadyx.errors import ValidationError
from cadyx.source_sandbox import (
    SourceSandboxUnavailable,
    _appcontainer_environment_block,
    _read_appcontainer_result,
)


class AppContainerBoundaryTests(unittest.TestCase):
    def test_environment_is_allowlisted_sorted_and_double_terminated(self) -> None:
        self.assertEqual(
            _appcontainer_environment_block({"TEMP": "C:\\Tmp", "Path": "C:\\Bin"}),
            "Path=C:\\Bin\0TEMP=C:\\Tmp\0\0",
        )
        self.assertEqual(_appcontainer_environment_block({}), "\0\0")

    def test_environment_rejects_embedded_nul_and_duplicate_names(self) -> None:
        invalid = (
            {"PATH": "safe\0UNSAFE=1"},
            {"PATH": "a", "path": "b"},
            {"PATH": 123},
            {"NOT_ALLOWED": "x"},
            {"PATH=OTHER": "x"},
            {"PATH\0OTHER": "x"},
        )
        for environment in invalid:
            with self.subTest(environment=environment):
                with self.assertRaises(ValidationError):
                    _appcontainer_environment_block(environment)

    def test_result_reader_accepts_small_utf8_results(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text('{"message":"готово"}', encoding="utf-8")
            self.assertEqual(_read_appcontainer_result(path), '{"message":"готово"}')

    def test_result_reader_rejects_oversized_untrusted_files(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_bytes(b"x" * (1024 * 1024 + 1))
            with self.assertRaisesRegex(SourceSandboxUnavailable, "1 MiB"):
                _read_appcontainer_result(path)

    def test_diagnostic_reader_only_reads_bounded_tail(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostics.txt"
            path.write_bytes(b"x" * (2 * 1024 * 1024) + b"finished")
            self.assertEqual(_read_appcontainer_result(path, tail_bytes=12), "xxxxfinished")


if __name__ == "__main__":
    unittest.main()
