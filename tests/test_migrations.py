from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cadyx.core import CadyxCore
from cadyx.errors import LeaseRequiredError, ValidationError
from cadyx.kernel import PrimitiveKernelAdapter
from cadyx.migrations import _migrate, migrate_document, migrate_project
from cadyx.models import Document


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.core = CadyxCore(self.temp.name, kernel=PrimitiveKernelAdapter())
        self.id = self.core.create_project("Legacy")["project"]["id"]
        self.lease = self.core.acquire_lease(self.id, "human", "test")["lease_id"]
        self.core.execute_command(self.id, self.lease,
                                  {"type": "create_box", "width": 2, "height": 3, "depth": 4})
        self.path = self.core.workspace._project_dir(self.id) / "project.json"
        self.legacy = json.loads(self.path.read_text())
        self.legacy["document"]["version"] = 1
        self.legacy["document"].pop("assembly", None)  # Actual v1 had no assembly graph.
        self.legacy["extension"] = {"source": "preserve"}
        self.write(self.legacy)

    def write(self, value):
        self.path.write_text(json.dumps(value), encoding="utf-8")

    def test_migration_preserves_snapshot_and_is_idempotent(self):
        original = copy.deepcopy(self.legacy)
        expected = copy.deepcopy(original)
        expected["document"]["version"] = 11
        expected["document"]["assembly"] = {"version": 3, "units": "mm", "parts": [], "subassemblies": [], "instances": [], "mates": []}
        expected["document"]["model_parameters"] = {}
        expected["document"]["parameter_bindings"] = []
        migrated = migrate_project(self.legacy)
        self.assertEqual(migrated, expected)
        self.assertEqual(self.legacy, original)
        self.assertEqual(migrate_project(migrated), expected)
        migrated["history"][0]["command"]["width"] = 99
        self.assertEqual(self.legacy, original)
        self.assertEqual(Document.from_dict(original["document"]).to_dict(), expected["document"])

    def test_v7_document_migration_adds_named_parameters_and_bindings(self):
        document = {"id": "document:legacy", "version": 7, "revision": 3, "objects": []}
        migrated = migrate_document(document)

        self.assertEqual(migrated["version"], 11)
        self.assertEqual(migrated["assembly"]["version"], 3)
        self.assertEqual(migrated["model_parameters"], {})
        self.assertEqual(migrated["parameter_bindings"], [])
        self.assertEqual(document["version"], 7)

    def test_project_migration_deep_copies_nested_document_once(self):
        original = copy.deepcopy(self.legacy)
        with patch("cadyx.migrations.deepcopy", wraps=copy.deepcopy) as clone:
            migrated = migrate_project(self.legacy)

        self.assertEqual(clone.call_count, 1)
        migrated["document"]["objects"][0]["geometry"]["width"] = 99
        self.assertEqual(self.legacy, original)

    def test_reads_failed_commands_and_failed_save_preserve_bytes(self):
        before = self.path.read_bytes()
        self.assertEqual(self.core.workspace.load(self.id)["document"]["version"], 11)
        self.core.list_projects()
        self.core.inspect_project(self.id)
        self.core.heartbeat(self.id, self.lease)
        with self.assertRaises(LeaseRequiredError):
            self.core.execute_command(self.id, None, {"type": "create_sketch"})
        with self.assertRaises(ValidationError):
            self.core.execute_command(self.id, self.lease, {"type": "create_sketch", "plane": "bad"})
        with patch("cadyx.workspace.os.replace", side_effect=OSError("save failed")):
            with self.assertRaises(OSError):
                self.core.execute_command(self.id, self.lease, {"type": "create_sketch"})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])
        self.core.execute_command(self.id, self.lease, {"type": "create_sketch"})
        saved = json.loads(self.path.read_text())
        for key in ("format", "version", "id", "name", "created_at", "extension"):
            self.assertEqual(saved[key], self.legacy[key])
        self.assertEqual(saved["history"][:-1], self.legacy["history"])
        self.assertEqual(saved["document"]["objects"][:-1], self.legacy["document"]["objects"])
        self.assertEqual(saved["document"]["id"], self.legacy["document"]["id"])
        self.assertEqual(saved["document"]["revision"], 2)
        self.assertEqual(saved["document"]["version"], 11)
        reloaded = CadyxCore(self.temp.name, kernel=PrimitiveKernelAdapter()).inspect_project(self.id)
        self.assertEqual(reloaded["document"], saved["document"])
        self.assertEqual(reloaded["history"], saved["history"])
        before = self.path.read_bytes()
        self.core.workspace.load(self.id)
        self.assertEqual(self.path.read_bytes(), before)

    def test_sequential_steps_require_a_complete_chain(self):
        calls = []

        def step(value):
            calls.append(value["version"])
            value["version"] += 1

        source = {"version": 1, "data": {"id": "unchanged"}}
        result = _migrate(source, kind="test", current=3, steps={1: step, 2: step})
        self.assertEqual(calls, [1, 2])
        self.assertEqual(result, {"version": 3, "data": {"id": "unchanged"}})
        self.assertEqual(source["version"], 1)
        for steps in ({1: step}, {1: lambda value: None}, {1: lambda value: value.update(version=3)}):
            with self.assertRaises(ValidationError):
                _migrate(source, kind="test", current=3, steps=steps)
        for key in ("id", "revision", "objects"):
            for version in (1, 2):
                document = copy.deepcopy(self.legacy["document"])
                document["version"] = version
                document.pop(key)
                with self.assertRaises(ValidationError):
                    Document.from_dict(document)

    def test_independent_versions_and_corrupt_snapshots_are_rejected_without_write(self):
        changes = []
        for level in ("project", "document"):
            for version in (None, True, "1", 0, -1, 99):
                changes.append((level, "version", version))
        changes += [("project", "format", "other"), ("project", "history", []),
                    ("document", "objects", None), ("document", "objects", [{}]),
                    ("document", "objects", [{"type": []}]),
                    ("document", "objects", [{"type": "sketch"}])]
        for level, key, value in changes:
            with self.subTest(level=level, key=key, value=value):
                corrupt = copy.deepcopy(self.legacy)
                target = corrupt if level == "project" else corrupt["document"]
                if value is None and key == "version":
                    target.pop(key)
                else:
                    target[key] = value
                self.write(corrupt)
                before = self.path.read_bytes()
                with self.assertRaises(ValidationError):
                    self.core.inspect_project(self.id)
                with self.assertRaises(ValidationError):
                    self.core.execute_command(self.id, self.lease, {"type": "create_sketch"})
                self.assertEqual(self.path.read_bytes(), before)
        for value in (None, [], 1):
            with self.assertRaises(ValidationError):
                migrate_project(value)
            with self.assertRaises(ValidationError):
                migrate_document(value)
