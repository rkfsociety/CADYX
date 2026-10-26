"""Isolated command families and their shared dispatch/atomicity contracts."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from cadyx import command_bodies, command_delivery, command_history, command_parameters
from cadyx import command_scan, command_sketch_curves, command_sketch_profiles, command_sketches
from cadyx.base_commands import COMMAND_FAMILIES, base_command_handlers
from cadyx.capabilities import BASE_COMMAND_CAPABILITIES, COMMAND_CAPABILITIES, COMMAND_REGISTRY
from cadyx.command_rebuild import RebuildCommands
from cadyx.commands import CommandSystem
from cadyx.core import CadyxCore
from cadyx.errors import NotFoundError, ValidationError
from cadyx.extensions import ExtensionRegistry
from cadyx.kernel import PrimitiveKernelAdapter
from cadyx.models import Document
from cadyx.sketches import closed_profile, closed_profiles


class ProfileContext(command_sketch_profiles.SketchProfileCommands, command_sketches.SketchCommands):
    pass


class CurveContext(command_sketch_curves.SketchCurveCommands, command_sketches.SketchCommands):
    pass


def call(module, context, document, command, **options):
    return module.COMMAND_HANDLERS[command["type"]](context, document, command, **options)


def sketch_document(context):
    if not hasattr(context, "kernel"):
        context.kernel = PrimitiveKernelAdapter()
    document = Document.create()
    result = call(command_sketches, context, document, {"type": "create_sketch"})
    return document, result["sketch_id"]


class CommandFamilyTests(unittest.TestCase):
    def test_every_discovered_command_has_one_registered_handler(self):
        base = base_command_handlers()
        self.assertEqual(set(base), {entry["type"] for entry in BASE_COMMAND_CAPABILITIES})
        self.assertEqual(set(COMMAND_REGISTRY.handlers), {entry["type"] for entry in COMMAND_CAPABILITIES})
        self.assertEqual(len(base), sum(len(entries) for _, entries in COMMAND_FAMILIES))
        for family, entries in COMMAND_FAMILIES:
            for name, handler in entries.items():
                with self.subTest(family=family, command=name):
                    self.assertEqual(handler.__module__, "cadyx.command_" + family)
                    self.assertIs(COMMAND_REGISTRY.handlers[name], handler)
        names = set()
        for family in CommandSystem.__bases__:
            helpers = {name for name, value in vars(family).items()
                       if name.startswith("_") and callable(value)}
            self.assertFalse(names & helpers, "Helper methods must not silently override another family")
            names.update(helpers)

    def test_missing_extra_noncallable_and_duplicate_base_handlers_are_rejected(self):
        base = BASE_COMMAND_CAPABILITIES[:1]
        name = base[0]["type"]
        for handlers in ({}, {name: Mock(), "extra": Mock()}, {name: None}):
            with self.subTest(handlers=handlers):
                with self.assertRaises(ValueError):
                    ExtensionRegistry(base, (), base_handlers=handlers)
        with patch("cadyx.base_commands.COMMAND_FAMILIES", (("one", {name: Mock()}), ("two", {name: Mock()}))):
            with self.assertRaisesRegex(ValueError, "Duplicate base command registration"):
                base_command_handlers()

    def test_sketch_creation_and_construction_point_without_full_command_system(self):
        context = command_sketches.SketchCommands()
        document, sketch = sketch_document(context)
        result = call(command_sketches, context, document,
                      {"type": "add_sketch_construction_point", "sketch_id": sketch, "x": 2, "y": 3})
        point = next(item for item in document.objects if item["id"] == result["point_id"])
        self.assertTrue(point["construction"])
        self.assertEqual((point["x"], point["y"]), (2, 3))
        document.validate()

    def test_profile_rectangle_retains_closed_geometry_and_progress(self):
        context = ProfileContext()
        context._rebuild_changed_features = Mock()
        document, sketch = sketch_document(context)
        progress = Mock()
        call(command_sketch_profiles, context, document,
             {"type": "add_sketch_rectangle", "sketch_id": sketch, "x": 1, "y": 2,
              "width": 4, "height": 3}, progress=progress)
        plane, profile = closed_profile(document.objects, sketch)
        self.assertEqual(plane, "XY")
        self.assertEqual({(point["x"], point["y"]) for point in profile}, {(1, 2), (5, 2), (5, 5), (1, 5)})
        context._rebuild_changed_features.assert_called_once_with(document, {sketch}, progress=progress)
        self.assertTrue(progress.called)
        self.assertEqual(progress.call_args.args[0]["phase"], "add_sketch_rectangle")
        document.validate()

    def test_sketch_geometry_batch_adds_common_profiles_to_one_sketch(self):
        context = ProfileContext()
        context._rebuild_changed_features = Mock()
        document, sketch = sketch_document(context)
        result = call(command_sketch_profiles, context, document, {
            "type": "add_sketch_geometry_batch", "sketch_id": sketch, "elements": [
                {"type": "rectangle", "x": 0, "y": 0, "width": 10, "height": 8},
                {"type": "circle", "x": 3, "y": 4, "radius": 1},
                {"type": "regular_polygon", "center_x": 7, "center_y": 4,
                 "side_count": 6, "radius": 2},
            ],
        })
        self.assertEqual(result["element_count"], 3)
        self.assertEqual(len(closed_profiles(document.objects, sketch)[1]), 3)
        self.assertEqual(document.find_object(sketch, "sketch")["id"], sketch)
        context._rebuild_changed_features.assert_called()
        document.validate()

    def test_curve_alias_keeps_bspline_mode_and_inputs(self):
        context = CurveContext()
        context._rebuild_changed_features = Mock()
        document, sketch = sketch_document(context)
        result = call(command_sketch_curves, context, document,
                      {"type": "add_sketch_bspline", "sketch_id": sketch,
                       "points": [{"x": 0, "y": 0}, {"x": 1, "y": 2}, {"x": 3, "y": 0}]})
        spline = next(item for item in document.objects if item["type"] == "spline")
        self.assertEqual(spline["mode"], "bspline")
        self.assertEqual(spline["point_ids"], result["point_ids"])
        document.validate()

    def test_body_creation_and_history_removal_are_independent_of_facade(self):
        context = command_bodies.BodyCommands()
        context.kernel = PrimitiveKernelAdapter()
        document = Document.create()
        result = call(command_bodies, context, document,
                      {"type": "create_box", "width": 2, "height": 3, "depth": 4})
        document.validate()
        self.assertEqual(result["geometry"]["dimensions"]["width"], 2)
        removed = call(command_history, command_history.HistoryCommands(), document,
                       {"type": "remove_body", "body_id": result["body_id"]})
        self.assertEqual(removed["removed_feature_ids"], [result["feature_id"]])
        self.assertEqual(document.objects, [])
        document.validate()

    def test_parameter_batch_validates_before_storing_any_values(self):
        context = command_parameters.ParameterCommands()
        document = Document.create()
        before = document.to_dict()
        with self.assertRaisesRegex(ValidationError, "valid identifiers"):
            call(command_parameters, context, document,
                 {"type": "create_model_parameters", "parameters": {
                     "length": {"value": 4}, "invalid name": {"value": 2}}})
        self.assertEqual(document.to_dict(), before)
        call(command_parameters, context, document,
             {"type": "create_model_parameters", "parameters": {"length": {"value": 4}}})
        self.assertEqual(document.model_parameters, {"length": {"value": 4, "unit": "mm"}})
        document.validate()

    def test_scan_handler_keeps_source_provenance_and_artifact_writer(self):
        context = command_scan.ScanCommands()
        source = Path("source.ply")
        geometry = {"kernel": "cadyx-mesh-v1"}
        stats = {"source_kind": "point_cloud", "processor_version": "test",
                 "input_vertices": 4, "input_triangles": 0, "output_vertices": 4, "output_triangles": 4}
        context.resolve_scan = Mock(return_value=source)
        context.scan_processor = Mock()
        context.scan_processor.reconstruct.return_value = geometry, stats, "a" * 64
        context._publish_scan_output = Mock(return_value={"file": "output.ply"})
        document = Document.create()
        progress = Mock()
        result = call(command_scan, context, document,
                      {"type": "import_scan", "folder": "scan", "file": "source.ply"}, progress=progress)
        feature = document.objects[1]
        self.assertEqual(feature["source"], {"folder": "scan", "file": "source.ply", "sha256": "a" * 64})
        self.assertEqual(result["artifact"], {"file": "output.ply"})
        context._publish_scan_output.assert_called_once_with("scan", "source.ply", geometry, progress)
        document.validate()

    def test_project_scan_handler_passes_project_context_and_keeps_provenance(self):
        context = command_scan.ScanCommands()
        source = Path("project-source.ply")
        geometry = {"kernel": "cadyx-mesh-v1"}
        stats = {"source_kind": "triangle_mesh", "processor_version": "test",
                 "input_vertices": 4, "input_triangles": 4, "output_vertices": 4, "output_triangles": 4}
        context.resolve_project_file = Mock(return_value=source)
        context.scan_processor = Mock()
        context.scan_processor.reconstruct.return_value = geometry, stats, "b" * 64
        context._publish_scan_source_output = Mock(return_value={"file": "processed.ply"})
        document = Document.create()
        result = call(command_scan, context, document, {
            "type": "import_project_scan", "file": "source.ply",
        }, project_id="project-id")

        self.assertEqual(document.objects[1]["source"], {"project_file": "source.ply", "sha256": "b" * 64})
        self.assertEqual(result["artifact"], {"file": "processed.ply"})
        context.resolve_project_file.assert_called_once_with("project-id", "source.ply")
        context._publish_scan_source_output.assert_called_once_with(
            {"project_file": "source.ply"}, "source.ply", geometry, None, "project-id",
        )
        document.validate()

    def test_step_handler_passes_project_context_and_preserves_source(self):
        context = command_delivery.DeliveryCommands()
        context.kernel = Mock()
        context.kernel.create_imported_step.return_value = ({"kernel": "mock"}, {
            "volume": 1, "surface_area": 6, "bounds": {"min": [0, 0, 0], "max": [1, 1, 1]},
            "topology": {"solid_count": 1, "face_count": 6, "edge_count": 12, "vertex_count": 8},
            "brep_valid": True, "repaired": False})
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.step"
            original = b"ISO-10303-21;"
            source.write_bytes(original)
            context.resolve_project_file = Mock(return_value=source)
            document = Document.create()
            result = call(command_delivery, context, document,
                          {"type": "import_step", "file": "source.step", "show_in_preview": False},
                          project_id="project-id")
            context.resolve_project_file.assert_called_once_with("project-id", "source.step")
            context.kernel.create_imported_step.assert_called_once_with(original)
            self.assertEqual(source.read_bytes(), original)
            self.assertFalse(result["visible"])
            document.validate()

    def test_rebuild_falls_back_from_stale_keys_to_saved_edge_intent(self):
        context = RebuildCommands()
        context.kernel = Mock()
        geometry = {"kernel": "mock"}
        feature = {"edge_keys": ["old-key"], "edge_references": [{"curve": "line", "length": 4}]}
        with patch("cadyx.semantic_selection._resolve", side_effect=ValidationError("changed")), \
             patch("cadyx.semantic_selection.resolve_edge_references", return_value=[7]) as resolve:
            self.assertEqual(context._resolve_feature_edges(feature, geometry, [1]), [7])
        resolve.assert_called_once_with(
            context.kernel, geometry, feature["edge_references"], topology=None,
        )

    def test_failed_family_batch_preserves_saved_revision_document_and_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            core = CadyxCore(temporary, kernel=PrimitiveKernelAdapter())
            project = core.create_project("Atomic family test")["project"]["id"]
            lease = core.acquire_lease(project, "agent", "test")["lease_id"]
            def execute(command):
                return core.execute_command(project, lease, command)["history_entry"]["result"]
            sketch = execute({"type": "create_sketch"})["sketch_id"]
            point = execute({"type": "add_sketch_point", "sketch_id": sketch, "x": 1, "y": 2})["point_id"]
            before = deepcopy(core.inspect_project(project))
            with self.assertRaises(NotFoundError):
                execute({"type": "update_sketch_elements", "updates": [
                    {"object_id": point, "changes": {"x": 99}},
                    {"object_id": "point:missing", "changes": {"x": 0}}]})
            after = core.inspect_project(project)
            for field in ("document", "history"):
                self.assertEqual(after[field], before[field])
