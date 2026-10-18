from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from cadyx.errors import ValidationError
from cadyx.source_extension import SourceExtensionEngine
from cadyx.source_native import (NATIVE_SOURCE_NAMES, source_fingerprint, verified_bridge,
                                 publish_verified, build_candidate)

class NativeSourceTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.runtime = self.root / ".runtime"
        self.runtime.mkdir()
        (self.runtime / "manifest.json").write_text(json.dumps({
            "occt_version": "8.0.1", "dll_directories": ["open3d/bin"]}), encoding="utf-8")
        (self.root / "native").mkdir()
        for name in NATIVE_SOURCE_NAMES | {"CMakeLists.txt"}:
            (self.root / "native" / name).write_text("// source", encoding="utf-8")
        self.bridge = self.root / "candidate.dll"
        self.bridge.write_bytes(b"tested-binary")

    def test_native_policy_is_bounded(self):
        self.assertEqual(SourceExtensionEngine._source_path("native/kernel_primitives.cpp").name,
                         "kernel_primitives.cpp")
        for name in ("native/CMakeLists.txt", "native/unknown.cpp", "native/../secret.cpp"):
            with self.assertRaises(ValidationError):
                SourceExtensionEngine._source_path(name)

    def test_only_verified_matching_sources_select_artifact(self):
        self.assertIsNone(verified_bridge(self.root, self.runtime))
        publish_verified(self.root, self.runtime, self.bridge)
        bridge = verified_bridge(self.root, self.runtime)
        self.assertEqual(bridge.read_bytes(), b"tested-binary")
        self.assertEqual(build_candidate(self.root, self.root, self.runtime), bridge)
        (self.root / "native" / "kernel.cpp").write_text("// changed", encoding="utf-8")
        self.assertIsNone(verified_bridge(self.root, self.runtime))

    def test_changed_artifact_fails_closed(self):
        publish_verified(self.root, self.runtime, self.bridge)
        verified_bridge(self.root, self.runtime).write_bytes(b"tampered")
        with self.assertRaises(RuntimeError):
            verified_bridge(self.root, self.runtime)

    def test_fingerprint_covers_sdk_version_and_all_sources(self):
        before = source_fingerprint(self.root, self.runtime)
        (self.runtime / "manifest.json").write_text('{"occt_version":"8.0.2"}', encoding="utf-8")
        self.assertNotEqual(source_fingerprint(self.root, self.runtime), before)

    def test_compiler_failure_stops_before_publication(self):
        with patch("cadyx.source_native.shutil.which", return_value="cmake"), patch(
            "cadyx.source_native.subprocess.run"
        ) as run:
            run.return_value.returncode = 1
            run.return_value.stdout, run.return_value.stderr = "", "compile error"
            with self.assertRaisesRegex(ValidationError, "compile error"):
                build_candidate(self.root, self.root, self.runtime)
        self.assertFalse((self.runtime / "source-kernels").exists())
