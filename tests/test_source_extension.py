from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cadyx.errors import ValidationError
from cadyx.source_extension import SourceExtensionEngine


class SourceExtensionTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows command output code page required")
    def test_sandbox_command_output_decodes_windows_ansi_bytes(self) -> None:
        from cadyx.source_sandbox import _decode_windows_command_output

        self.assertEqual(len(_decode_windows_command_output(b"\x93")), 1)

    def test_source_path_policy_rejects_escape_and_non_code(self) -> None:
        with self.assertRaises(ValidationError):
            SourceExtensionEngine._source_path("../secret.txt")
        with self.assertRaises(ValidationError):
            SourceExtensionEngine._source_path("pyproject.toml")

    @unittest.skipUnless(os.name == "nt" and Path(".runtime/manifest.json").is_file(), "Windows AppContainer runtime required")
    def test_candidate_runs_in_appcontainer_and_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cadyx-source-extension-test-") as directory:
            root = Path(directory)
            (root / "cadyx").mkdir()
            (root / "tests").mkdir()
            module = root / "cadyx" / "probe.py"
            module.write_text("def answer():\n    return 41\n", encoding="utf-8")
            test_module = root / "tests" / "test_probe.py"
            sentinel = Path(os.environ["USERPROFILE"]) / f"cadyx-source-test-{os.getpid()}.txt"
            sentinel.write_text("host-sentinel", encoding="utf-8")
            self.addCleanup(sentinel.unlink, missing_ok=True)
            test_module.write_text(
                "import ctypes, socket, tempfile, unittest\nfrom pathlib import Path\nfrom cadyx.probe import answer\n"
                "class ProbeTests(unittest.TestCase):\n"
                "    def test_answer(self): self.assertEqual(answer(), 42)\n",
                encoding="utf-8",
            )
            test_module.write_text(
                test_module.read_text(encoding="utf-8")
                + f"    def test_isolation(self):\n"
                + f"        try: Path({str(sentinel)!r}).read_text(encoding='utf-8')\n"
                + "        except OSError: pass\n        else: self.fail('AppContainer read a host file')\n"
                + f"        try: Path({str(sentinel)!r}).write_text('changed', encoding='utf-8')\n"
                + "        except OSError: pass\n        else: self.fail('AppContainer wrote a host file')\n"
                + "        try: socket.create_connection(('1.1.1.1', 443), timeout=1)\n"
                + "        except OSError: pass\n        else: self.fail('AppContainer used the network')\n"
                + "        Path(tempfile.gettempdir(), 'scratch-write.txt').write_text('allowed', encoding='utf-8')\n"
                + "        self.assertIsNotNone(ctypes.WinDLL('kernel32'))\n"
                + "        with tempfile.TemporaryDirectory() as directory:\n"
                + "            path = Path(directory) / 'nested.txt'\n"
                + "            path.write_text('allowed', encoding='utf-8')\n"
                + "            self.assertEqual(path.read_text(encoding='utf-8'), 'allowed')\n"
                + "        protected = Path(__file__).parents[1] / 'cadyx' / 'probe.py'\n"
                + "        try: protected.write_text('changed', encoding='utf-8')\n"
                + "        except OSError: pass\n        else: self.fail('AppContainer wrote its read-only source')\n",
                encoding="utf-8",
            )
            with patch("cadyx.source_extension._ROOT", root):
                engine = SourceExtensionEngine(root / "extensions.sqlite3", runtime_root=Path(".runtime"))
                existing = engine.read_file("cadyx/probe.py")
                proposal = {
                    "id": "test_probe_extension", "title": "Probe", "description": "Test source changes.",
                    "version": "1.0.0", "tests": ["tests.test_probe"],
                    "files": [{"path": "cadyx/probe.py", "base_sha256": existing["sha256"],
                               "content": "def answer():\n    return 42\n"}],
                }
                failed_proposal = {**proposal, "files": [{**proposal["files"][0], "content": "def answer():\n    return 41\n"}]}
                failed = engine.develop(failed_proposal)
                self.assertEqual(failed["status"], "tests_failed")
                self.assertFalse(failed["files_applied"])
                self.assertEqual(module.read_text(encoding="utf-8"), "def answer():\n    return 41\n")
                result = engine.develop(proposal)
                self.assertEqual(result["status"], "ready", result)
                self.assertTrue(result["tests"]["passed"])
                self.assertEqual(module.read_text(encoding="utf-8"), "def answer():\n    return 42\n")
                rollback = engine.rollback("test_probe_extension")
                self.assertEqual(rollback["status"], "rolled_back")
                self.assertEqual(module.read_text(encoding="utf-8"), "def answer():\n    return 41\n")
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "host-sentinel")
            sentinel.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
