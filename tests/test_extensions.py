"""Native-free tests for explicit extension composition and bootstrap invariants."""
from __future__ import annotations

from copy import deepcopy
import importlib
import inspect
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cadyx.base_commands import base_command_handlers
from cadyx.capabilities import BASE_COMMAND_CAPABILITIES, COMMAND_CAPABILITIES, COMMAND_REGISTRY
from cadyx.commands import CommandSystem
from cadyx.errors import ValidationError
from cadyx.extensions import CommandExtension, ExtensionRegistry, builtin_extensions
from cadyx.models import Document


def contract(name):
    return {"type": name, "required": [], "optional": [], "constraints": []}


class ExtensionRegistryTests(unittest.TestCase):
    def test_builtin_order_and_discovery_are_repeatable_and_isolated(self):
        baseline = deepcopy(BASE_COMMAND_CAPABILITIES)
        public = deepcopy(COMMAND_CAPABILITIES)
        first = ExtensionRegistry(BASE_COMMAND_CAPABILITIES, builtin_extensions(), base_handlers=base_command_handlers())
        second = ExtensionRegistry(BASE_COMMAND_CAPABILITIES, builtin_extensions(), base_handlers=base_command_handlers())
        self.assertEqual(first.extension_names, (
            "advanced_features", "agent_authoring", "curve_editing", "manufacturing_features",
            "semantic_selection", "selector_contracts", "scan_reverse"))
        self.assertEqual(first.capabilities, public)
        self.assertEqual(second.capabilities, public)
        self.assertEqual(first.command_types, COMMAND_REGISTRY.command_types)
        self.assertEqual(first.handlers, COMMAND_REGISTRY.handlers)
        snapshot = first.capabilities
        snapshot[0]["required"].append("injected")
        self.assertEqual(first.capabilities, second.capabilities)
        self.assertEqual(BASE_COMMAND_CAPABILITIES, baseline)
        self.assertEqual(COMMAND_CAPABILITIES, public)

    def test_duplicate_commands_and_extension_names_fail_before_dispatch(self):
        handler = Mock()
        extension = CommandExtension("one", (contract("extension"),), handler)
        cases = (
            ((contract("base"), contract("base")), (), "Duplicate command"),
            ((contract("extension"),), (extension,), "Duplicate command"),
            ((), (extension, CommandExtension("two", extension.capabilities, handler)), "Duplicate command"),
            ((), (extension, CommandExtension("one")), "Duplicate extension"),
            ((), (CommandExtension("unbound", (contract("extension"),)),), "without a handler"),
        )
        for base, extensions, error in cases:
            with self.subTest(error=error):
                with self.assertRaisesRegex(ValueError, error):
                    ExtensionRegistry(base, extensions)
        handler.assert_not_called()

    def test_refinements_cannot_add_remove_or_duplicate_command_names(self):
        for rewrite in (lambda entries: (), lambda entries: entries * 2,
                        lambda entries: (contract("replacement"),)):
            with self.subTest(rewrite=rewrite):
                with self.assertRaisesRegex(ValueError, "changed registered commands"):
                    ExtensionRegistry((contract("base"),), (CommandExtension("bad", refine=rewrite),))

    def test_dispatch_and_middleware_have_explicit_order_and_forward_context(self):
        calls = []
        system, document, progress = object(), Document.create(), Mock()

        def handler(owner, doc, command, **context):
            self.assertIs(owner, system)
            self.assertIs(doc, document)
            self.assertEqual(context, {"progress": progress, "project_id": "project-id"})
            calls.append("handler")
            return {"ok": True}

        def middleware(name):
            def execute(next_execute, owner, doc, command, **context):
                self.assertIs(owner, system)
                calls.append(name + ":before")
                result = next_execute(doc, command, **context)
                calls.append(name + ":after")
                return result
            return execute

        registry = ExtensionRegistry((contract("base"),), (
            CommandExtension("extension", (contract("extension"),), handler),
            CommandExtension("inner", middleware=middleware("inner")),
            CommandExtension("outer", middleware=middleware("outer")),
        ))
        base = Mock(return_value={"base": True})
        execute = registry.bind(system, base)
        self.assertEqual(execute(document, {"type": "extension"}, progress=progress,
                                 project_id="project-id"), {"ok": True})
        self.assertEqual(calls, ["outer:before", "inner:before", "handler", "inner:after", "outer:after"])
        base.assert_not_called()
        self.assertEqual(execute(document, {"type": "base"}), {"base": True})
        base.assert_called_once_with(document, {"type": "base"}, progress=None, project_id=None)

    def test_unknown_and_malformed_commands_keep_typed_errors(self):
        system = CommandSystem(Mock())
        for command, message in ((None, "command must be a JSON object."),
                                 ({"type": "unknown"}, "Unsupported command type: 'unknown'."),
                                 ({"type": []}, "Unsupported command type: [].")):
            with self.subTest(command=command):
                with self.assertRaises(ValidationError) as error:
                    system.execute(Document.create(), command)
                self.assertEqual(str(error.exception), message)
                expected = "UNSUPPORTED_OPERATION" if isinstance(command, dict) and command.get("type") != "base" else "VALIDATION_ERROR"
                self.assertEqual(error.exception.code, expected)

    def test_handlers_are_usable_without_installation(self):
        from cadyx.advanced_features import _execute
        document = Document.create()
        result = _execute(SimpleNamespace(), document,
                          {"type": "create_section_sketch", "offset": 5})
        self.assertEqual(document.objects[0]["id"], result["sketch_id"])
        self.assertEqual(document.objects[0]["section_offset"], 5)
        document.validate()

    def test_imports_do_not_replace_class_methods_or_render_functions(self):
        from cadyx import kernel, rendering
        methods = (CommandSystem.execute, kernel.OCCTKernelAdapter.topology, rendering.render_scene)
        contracts = deepcopy(COMMAND_CAPABILITIES)
        for name in (*COMMAND_REGISTRY.extension_names, "advanced_topology"):
            module_name = "capability_refinements" if name == "selector_contracts" else name
            module = importlib.import_module("cadyx." + module_name)
            self.assertFalse(hasattr(module, "install"))
        importlib.reload(importlib.import_module("cadyx"))
        self.assertEqual(methods, (CommandSystem.execute, kernel.OCCTKernelAdapter.topology, rendering.render_scene))
        self.assertTrue(inspect.getsourcefile(CommandSystem.execute).endswith("commands.py"))
        self.assertTrue(inspect.getsourcefile(kernel.OCCTKernelAdapter.topology).endswith("kernel.py"))
        self.assertEqual(COMMAND_CAPABILITIES, contracts)

    def test_fresh_process_import_orders_have_identical_contracts(self):
        root = Path(__file__).resolve().parents[1]
        expected = json.loads(json.dumps(COMMAND_CAPABILITIES))
        for names in (("scan_reverse", "advanced_features", "commands", "capabilities"),
                      ("capabilities", "advanced_topology", "agent_authoring", "commands")):
            with self.subTest(names=names):
                code = ("import importlib,json; "
                        + "; ".join(f"importlib.import_module('cadyx.{name}')" for name in names)
                        + "; from cadyx.capabilities import COMMAND_CAPABILITIES; print(json.dumps(COMMAND_CAPABILITIES))")
                result = subprocess.run([sys.executable, "-c", code], cwd=root,
                                        check=True, capture_output=True, text=True, timeout=15)
                self.assertEqual(json.loads(result.stdout), expected)

    def test_face_selection_never_captures_edge_indices(self):
        from cadyx.semantic_selection import execute_with_selection
        for kind in ("shell_body", "draft_faces"):
            for selector in ({"face_indices": [7]}, {"face_keys": ["face:test"]}):
                with self.subTest(kind=kind, selector=selector):
                    feature = {"id": "feature:result", "type": "feature"}
                    document = SimpleNamespace(objects=[
                        {"id": "body:source", "type": "body", "geometry": {"volume": 1}}, feature])
                    kernel = Mock()
                    kernel.topology.return_value = {"faces": [{"index": 7, "semantic_key": "face:test"}]}
                    execute = Mock(return_value={"feature_id": feature["id"]})
                    with patch("cadyx.semantic_selection.capture_edge_references") as capture:
                        execute_with_selection(execute, SimpleNamespace(kernel=kernel), document,
                                               {"type": kind, "body_id": "body:source", **selector})
                    capture.assert_not_called()
                    self.assertNotIn("edge_references", feature)
                    self.assertNotIn("edge_keys", feature)
                    self.assertEqual(execute.call_args.args[1]["face_indices"], [7])

    def test_scene_filter_preserves_document_metadata_and_original_objects(self):
        from cadyx.agent_authoring import filter_consumed_sources
        document = Document.create()
        document.model_parameters = {"size": {"value": 2, "unit": "mm"}}
        document.parameter_bindings = [{"object_id": "feature:result", "path": "depth", "expression": "size"}]
        source = {"id": "body:source", "type": "body"}
        document.objects = [source, {"id": "body:result", "type": "body"},
                            {"id": "feature:result", "type": "feature", "operation": "shell_body",
                             "source_body_id": source["id"]}]
        original = document.to_dict()
        filtered = filter_consumed_sources(document)
        self.assertEqual(document.to_dict(), original)
        self.assertNotIn(source, filtered.objects)
        self.assertEqual(filtered.model_parameters, document.model_parameters)
        self.assertEqual(filtered.parameter_bindings, document.parameter_bindings)
        self.assertEqual(filtered.id, document.id)
        self.assertEqual(filtered.revision, document.revision)
