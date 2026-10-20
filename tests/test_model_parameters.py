import math
import unittest

from cadyx.model_parameters import evaluate_expression
from cadyx.errors import NotFoundError, ValidationError
from cadyx.commands import CommandSystem
from cadyx.kernel import OCCTKernelAdapter
from cadyx.native_kernel import NativeKernel
from cadyx.models import Document
from cadyx.semantic_selection import capture_edge_references, resolve_edge_references
from cadyx.profile_segments import sketch_segment_profiles
from cadyx.sketches import closed_profiles


class ModelParameterExpressionTests(unittest.TestCase):
    def test_full_circle_holes_remain_analytic_across_parameter_rebuild(self):
        system = CommandSystem(OCCTKernelAdapter())
        document = Document.create()
        system.execute(document, {"type": "create_model_parameters", "parameters": {
            "HoleRadius": {"value": 2, "unit": "mm"},
            "Depth": {"value": 5, "unit": "mm"},
        }})
        sketch = system.execute(document, {"type": "create_sketch", "plane": "XY"})["sketch_id"]
        system.execute(document, {"type": "add_sketch_rectangle", "sketch_id": sketch,
                                  "x": -10, "y": -10, "width": 20, "height": 20})
        circle = system.execute(document, {"type": "add_sketch_circle", "sketch_id": sketch,
                                           "x": 0, "y": 0, "radius": 2})
        system.execute(document, {"type": "bind_model_parameter", "object_id": circle["circle_id"],
                                  "path": "radius", "expression": "HoleRadius"})
        plane, profiles = closed_profiles(document.objects, sketch)
        segments = sketch_segment_profiles(document.objects, sketch)
        self.assertEqual(plane, "XY")
        self.assertEqual(len(profiles), 2)
        self.assertIsNotNone(segments)
        self.assertEqual([segment[0] for segment in segments[0]], [1, 1, 1, 1])
        self.assertIsNone(segments[1])

        body = system.execute(document, {"type": "extrude_sketch", "sketch_id": sketch,
                                         "depth": 5})
        system.execute(document, {"type": "bind_model_parameter", "object_id": body["feature_id"],
                                  "path": "parameters.depth", "expression": "Depth"})
        geometry = body["geometry"]
        self.assertEqual(sum(segments is None for segments in geometry["segment_profiles"]), 1)
        analytic_hole = next(segments for segments in geometry["segment_profiles"] if segments is not None)
        self.assertEqual(len(analytic_hole), 4)
        self.assertAlmostEqual(geometry["volume"], (400 - 4 * math.pi) * 5, places=8)
        topology = NativeKernel().geometry_graph(geometry, topology=True)
        self.assertEqual(sum(edge["curve"] == "circle" for edge in topology["edges"]), 8)
        system.execute(document, {"type": "set_model_parameter", "name": "HoleRadius", "value": 3})
        rebuilt = next(item for item in document.objects if item["id"] == body["body_id"])
        self.assertAlmostEqual(rebuilt["geometry"]["volume"], (400 - 9 * math.pi) * 5, places=8)
        analytic_hole = next(segments for segments in rebuilt["geometry"]["segment_profiles"]
                             if segments is not None)
        self.assertEqual(len(analytic_hole), 4)

    def test_repair_profile_chain_rebuilds_source_and_finishers_atomically(self):
        from types import SimpleNamespace
        from cadyx.core import CadyxCore

        kernel = OCCTKernelAdapter()
        system = CommandSystem(kernel)
        document = Document.create()
        system.execute(document, {"type": "create_model_parameters", "parameters": {
            "Width": {"value": 20, "unit": "mm"},
            "Depth": {"value": 10, "unit": "mm"},
            "FilletRadius": {"value": 1, "unit": "mm"},
            "ChamferDistance": {"value": 0.5, "unit": "mm"},
        }})
        sketch = system.execute(document, {"type": "create_sketch", "plane": "XY"})["sketch_id"]
        rectangle = system.execute(document, {"type": "add_sketch_rectangle", "sketch_id": sketch,
                                              "x": -10, "y": -10, "width": 20, "height": 20})
        point_ids = rectangle["point_ids"]
        bindings = []
        for point_id, x_expr, y_expr in zip(
            point_ids, ["-Width/2", "Width/2", "Width/2", "-Width/2"],
            ["-Width/2", "-Width/2", "Width/2", "Width/2"], strict=True,
        ):
            bindings.extend([
                {"object_id": point_id, "path": "x", "expression": x_expr},
                {"object_id": point_id, "path": "y", "expression": y_expr},
            ])
        system.execute(document, {"type": "bind_model_parameters", "bindings": bindings})
        extrusion = system.execute(document, {"type": "extrude_sketch", "sketch_id": sketch,
                                               "depth": 10, "name": "Основание"})
        system.execute(document, {"type": "bind_model_parameter", "object_id": extrusion["feature_id"],
                                  "path": "parameters.depth", "expression": "Depth"})
        topology = kernel.topology(next(item for item in document.objects
                                        if item["id"] == extrusion["body_id"])["geometry"])
        vertical = [edge["index"] for edge in topology["edges"]
                    if edge.get("curve") == "line" and abs(abs(edge["end"][2]-edge["start"][2])-10) < 1e-7
                    and abs(edge["start"][0]-edge["end"][0]) < 1e-7
                    and abs(edge["start"][1]-edge["end"][1]) < 1e-7]
        self.assertTrue(vertical)
        fillet = system.execute(document, {"type": "fillet_edges", "body_id": extrusion["body_id"],
                                           "edge_indices": [vertical[0]], "radius": 1,
                                           "name": "Скругление 1 мм"})
        system.execute(document, {"type": "bind_model_parameter", "object_id": fillet["feature_id"],
                                  "path": "parameters.radius", "expression": "FilletRadius"})
        fillet_body = next(item for item in document.objects if item["id"] == fillet["body_id"])
        chamfer_topology = kernel.topology(fillet_body["geometry"])
        chamfer_edges = [edge["index"] for edge in chamfer_topology["edges"]
                         if edge.get("curve") == "line" and abs(edge.get("length", 0)-20) < 1e-7
                         and abs(edge.get("center", [0, 0, 1])[2]) < 1e-7]
        self.assertTrue(chamfer_edges)
        chamfer = system.execute(document, {"type": "chamfer_edges", "body_id": fillet["body_id"],
                                            "edge_indices": [chamfer_edges[0]], "distance": 0.5,
                                            "name": "Фаска 0,5 мм"})
        system.execute(document, {"type": "bind_model_parameter", "object_id": chamfer["feature_id"],
                                  "path": "parameters.distance", "expression": "ChamferDistance"})

        stale = kernel.create_extruded_profile(
            [{"x": -9, "y": -10}, {"x": 9, "y": -10}, {"x": 9, "y": 10}, {"x": -9, "y": 10}],
            plane="XY", depth=10,
        )
        for object_id in (extrusion["body_id"], extrusion["feature_id"]):
            item = next(value for value in document.objects if value["id"] == object_id)
            item["geometry"] = stale

        result = system.execute(document, {
            "type": "repair_profile_chain", "feature_id": extrusion["feature_id"],
            "parameter_name": "Width", "temporary_value": 20.1, "restore_value": 20,
            "finish_feature_ids": [fillet["feature_id"], chamfer["feature_id"]],
            "finish_edge_indices": {fillet["feature_id"]: [vertical[0]],
                                    chamfer["feature_id"]: [chamfer_edges[0]]},
        })
        document.validate()
        CadyxCore._validate_geometry(SimpleNamespace(kernel=kernel), document)
        self.assertEqual(result["parameter_value"], 20)
        self.assertEqual([item["operation"] for item in result["restored_features"]],
                         ["fillet_edges", "chamfer_edges"])
    def test_multicontour_pad_keeps_exact_slot_arcs_on_rebuild(self):
        system = CommandSystem(OCCTKernelAdapter())
        document = Document.create()
        base = system.execute(document, {"type": "create_box", "width": 50, "height": 30, "depth": 5})
        sketch = system.execute(document, {"type": "create_sketch", "plane": "XZ"})["sketch_id"]
        system.execute(document, {"type": "add_sketch_rectangle", "sketch_id": sketch,
                                  "x": -20, "y": 4.9, "width": 40, "height": 30})
        system.execute(document, {"type": "add_sketch_slot_centered", "sketch_id": sketch,
                                  "center_point": {"x": 0, "y": 20}, "total_length": 20,
                                  "slot_width": 8, "rotation_degrees": 90})
        plane, contours = closed_profiles(document.objects, sketch)
        self.assertEqual(plane, "XZ")
        self.assertEqual(len(contours), 2)
        segments = sketch_segment_profiles(document.objects, sketch)
        self.assertIsNotNone(segments)
        self.assertEqual(sum(segment[0] == 1 for contour in segments if contour
                             for segment in contour), 2)

        pad = system.execute(document, {"type": "pad_sketch_to_body", "body_id": base["body_id"],
                                        "sketch_id": sketch, "depth": 10})
        tool = pad["geometry"]["tool"]
        self.assertEqual(tool["kind"], "profile_with_holes")
        self.assertEqual(len(tool["segment_profiles"]), 2)
        self.assertAlmostEqual(tool["volume"], (40 * 30 - 8 * 12 - 16 * math.pi) * 10, places=8)

        def slot_arc_edges(geometry):
            topology = NativeKernel().geometry_graph(geometry, topology=True)
            return [edge for edge in topology["edges"]
                    if edge.get("curve") == "circle"]

        arcs = slot_arc_edges(tool)
        self.assertEqual(len(arcs), 4)
        self.assertAlmostEqual(sum(edge["length"] for edge in arcs), 4 * math.pi * 4, places=7)
        self.assertEqual(system.kernel.metrics(pad["geometry"])["topology"]["solid_count"], 1)

        system.execute(document, {"type": "set_parameter", "feature_id": pad["feature_id"],
                                   "name": "depth", "value": 11})
        rebuilt_body = next(item for item in document.objects if item["id"] == pad["body_id"])
        rebuilt_tool = rebuilt_body["geometry"]["tool"]
        self.assertEqual(rebuilt_tool["segment_profiles"], tool["segment_profiles"])
        arcs = slot_arc_edges(rebuilt_tool)
        self.assertEqual(len(arcs), 4)
        self.assertAlmostEqual(sum(edge["length"] for edge in arcs), 4 * math.pi * 4, places=7)
        self.assertEqual(system.kernel.metrics(rebuilt_body["geometry"])["topology"]["solid_count"], 1)
        document.validate()

    def test_converging_bound_roots_rebuild_shared_fuse_once_with_final_inputs(self):
        system = CommandSystem(OCCTKernelAdapter())
        document = Document.create()
        system.execute(document, {"type": "create_model_parameters", "parameters": {
            "LeftWidth": {"value": 10, "unit": "mm"},
            "RightWidth": {"value": 10, "unit": "mm"},
        }})
        left = system.execute(document, {"type": "create_box", "width": 10, "height": 10, "depth": 10})
        right = system.execute(document, {"type": "create_box", "width": 10, "height": 10, "depth": 10})
        fused = system.execute(document, {"type": "fuse_bodies", "base_body_id": left["body_id"],
                                          "tool_body_id": right["body_id"]})
        system.execute(document, {"type": "bind_model_parameters", "bindings": [
            {"object_id": left["feature_id"], "path": "parameters.width", "expression": "LeftWidth"},
            {"object_id": right["feature_id"], "path": "parameters.width", "expression": "RightWidth"},
        ]})
        fuse_calls = 0
        original_fuse = system.kernel.fuse_profiles

        def count_fuse(*args, **kwargs):
            nonlocal fuse_calls
            fuse_calls += 1
            return original_fuse(*args, **kwargs)

        system.kernel.fuse_profiles = count_fuse
        system.execute(document, {"type": "set_model_parameter", "name": "LeftWidth", "value": 12})
        self.assertEqual(fuse_calls, 1)
        feature = next(item for item in document.objects if item.get("id") == fused["feature_id"])
        metrics = system.kernel.metrics(feature["geometry"])
        self.assertEqual(metrics["bounds"]["max"][0] - metrics["bounds"]["min"][0], 12)
        self.assertEqual(metrics["topology"]["solid_count"], 1)
        document.validate()

    def test_legacy_polyline_gear_converts_to_editable_closed_spline_on_rebuild(self):
        system = CommandSystem(OCCTKernelAdapter())
        document = Document.create()
        system.execute(document, {"type": "create_model_parameter", "name": "GearTeeth",
                                  "value": 20, "unit": "unitless"})
        sketch_id = system.execute(document, {"type": "create_sketch", "name": "Legacy gear", "plane": "XY"})["sketch_id"]
        system.execute(document, {"type": "add_sketch_involute_gear", "sketch_id": sketch_id,
                                  "module": 2, "tooth_count": 20})
        feature = system.execute(document, {"type": "extrude_sketch", "sketch_id": sketch_id, "depth": 8})
        sketch = next(item for item in document.objects if item["id"] == sketch_id)
        generated = sketch["generated_gear_profile"]
        points = generated["point_ids"]
        spline_id = generated["spline_id"]
        spline_index = next(index for index, item in enumerate(document.objects) if item["id"] == spline_id)
        document.objects.pop(spline_index)
        line_ids = [f"line:legacy-{index}" for index in range(len(points))]
        sketch_index = next(index for index, item in enumerate(document.objects) if item["id"] == sketch_id)
        document.objects[sketch_index + 1:sketch_index + 1] = [
            {"id": line_id, "type": "line", "sketch_id": sketch_id,
             "start_id": point_id, "end_id": points[(index + 1) % len(points)]}
            for index, (line_id, point_id) in enumerate(zip(line_ids, points, strict=True))
        ]
        generated.pop("spline_id")
        generated["line_ids"] = line_ids
        system.execute(document, {"type": "bind_model_parameter", "object_id": sketch_id,
                                  "path": "generated_gear_profile.parameters.tooth_count",
                                  "expression": "GearTeeth"})

        system.execute(document, {"type": "set_model_parameter", "name": "GearTeeth", "value": 18})

        sketch = next(item for item in document.objects if item["id"] == sketch_id)
        generated = sketch["generated_gear_profile"]
        self.assertIn("spline_id", generated)
        self.assertNotIn("line_ids", generated)
        spline = next(item for item in document.objects if item["id"] == generated["spline_id"])
        self.assertEqual(spline["mode"], "closed")
        rebuilt_body = next(item for item in document.objects if item["id"] == feature["body_id"])
        self.assertEqual(rebuilt_body["geometry"]["profile_curve"], "closed_periodic_bspline")
        document.validate()

    def test_internal_gear_profile_rebuild_preserves_its_periodic_spline_cavity(self):
        system = CommandSystem(OCCTKernelAdapter())
        document = Document.create()
        system.execute(document, {"type": "create_model_parameter", "name": "RingModule",
                                 "value": 2, "unit": "mm"})
        sketch_id = system.execute(document, {"type": "create_sketch", "name": "Ring gear", "plane": "XY"})["sketch_id"]
        system.execute(document, {"type": "add_sketch_circle", "sketch_id": sketch_id,
                                 "x": 0, "y": 0, "radius": 80})
        system.execute(document, {"type": "add_sketch_internal_involute_gear", "sketch_id": sketch_id,
                                 "module": 2, "tooth_count": 60})
        body = system.execute(document, {"type": "extrude_sketch", "sketch_id": sketch_id, "depth": 8})
        system.execute(document, {"type": "bind_model_parameter", "object_id": sketch_id,
            "path": "generated_gear_profile.parameters.module", "expression": "RingModule"})
        original_volume = body["geometry"]["volume"]

        system.execute(document, {"type": "set_model_parameter", "name": "RingModule", "value": 2.1})

        rebuilt = next(item for item in document.objects if item["id"] == body["body_id"])
        self.assertEqual(rebuilt["geometry"]["solid"]["tool"]["profile_curve"], "closed_periodic_bspline")
        self.assertNotAlmostEqual(rebuilt["geometry"]["volume"], original_volume, places=5)
        document.validate()

    def test_gear_sketch_parameters_regenerate_profile_and_dependent_brep(self):
        system = CommandSystem(OCCTKernelAdapter())
        document = Document.create()
        system.execute(document, {"type": "create_model_parameter", "name": "GearModule",
                                 "value": 2, "unit": "mm"})
        sketch_id = system.execute(document, {"type": "create_sketch", "name": "Sun gear", "plane": "XY"})["sketch_id"]
        system.execute(document, {"type": "add_sketch_involute_gear", "sketch_id": sketch_id,
                                 "module": 2, "tooth_count": 20})
        extruded = system.execute(document, {"type": "extrude_sketch", "sketch_id": sketch_id, "depth": 8})
        system.execute(document, {"type": "bind_model_parameter", "object_id": sketch_id,
            "path": "generated_gear_profile.parameters.module", "expression": "GearModule"})
        original = next(item for item in document.objects if item["id"] == extruded["body_id"])
        original_volume = original["geometry"]["volume"]

        system.execute(document, {"type": "set_model_parameter", "name": "GearModule", "value": 2.2})

        sketch = next(item for item in document.objects if item["id"] == sketch_id)
        body = next(item for item in document.objects if item["id"] == extruded["body_id"])
        self.assertEqual(sketch["generated_gear_profile"]["parameters"]["module"], 2.2)
        self.assertNotAlmostEqual(body["geometry"]["volume"], original_volume, places=5)
        document.validate()

    def test_gear_tooth_count_binding_regenerates_loop_and_dependent_brep(self):
        system = CommandSystem(OCCTKernelAdapter())
        document = Document.create()
        system.execute(document, {"type": "create_model_parameter", "name": "GearTeeth",
                                 "value": 20, "unit": "unitless"})
        sketch_id = system.execute(document, {"type": "create_sketch", "name": "Planet gear", "plane": "XY"})["sketch_id"]
        system.execute(document, {"type": "add_sketch_involute_gear", "sketch_id": sketch_id,
                                 "module": 2, "tooth_count": 20})
        extruded = system.execute(document, {"type": "extrude_sketch", "sketch_id": sketch_id, "depth": 9})
        system.execute(document, {"type": "bind_model_parameter", "object_id": sketch_id,
            "path": "generated_gear_profile.parameters.tooth_count", "expression": "GearTeeth"})
        sketch = next(item for item in document.objects if item["id"] == sketch_id)
        old_point_count = len(sketch["generated_gear_profile"]["point_ids"])
        old_volume = next(item["geometry"]["volume"] for item in document.objects
                          if item["id"] == extruded["body_id"])

        system.execute(document, {"type": "set_model_parameter", "name": "GearTeeth", "value": 18})

        sketch = next(item for item in document.objects if item["id"] == sketch_id)
        body = next(item for item in document.objects if item["id"] == extruded["body_id"])
        self.assertEqual(sketch["generated_gear_profile"]["parameters"]["tooth_count"], 18)
        self.assertLess(len(sketch["generated_gear_profile"]["point_ids"]), old_point_count)
        self.assertEqual(sketch["generated_gear_profile"]["spline_id"], next(
            item["id"] for item in document.objects
            if item["type"] == "spline" and item["sketch_id"] == sketch["id"]
        ))
        self.assertNotAlmostEqual(body["geometry"]["volume"], old_volume, places=5)
        document.validate()

    def test_merges_consecutive_same_radius_fillets_and_rebuilds_dependents(self):
        system = CommandSystem(OCCTKernelAdapter())
        document = Document.create()
        system.execute(document, {"type": "create_model_parameter", "name": "FilletRadius",
                                 "value": 3, "unit": "mm"})
        base = system.execute(document, {"type": "create_box", "width": 80,
                                         "height": 60, "depth": 20})
        edges = system.kernel.topology(base["geometry"])["edges"]
        first_edge = next(edge["index"] for edge in edges if edge["curve"] == "line"
                          and edge["center"] == [40, 0, 0] and edge["length"] == 80)
        first = system.execute(document, {"type": "fillet_edges", "body_id": base["body_id"],
            "edge_indices": [first_edge], "radius": 3})
        next_edges = system.kernel.topology(first["geometry"])["edges"]
        second_edge = next(edge["index"] for edge in next_edges if edge["curve"] == "line"
                           and edge["center"] == [40, 60, 20] and edge["length"] == 80)
        second = system.execute(document, {"type": "fillet_edges", "body_id": first["body_id"],
            "edge_indices": [second_edge], "radius": 3})
        for result in (first, second):
            system.execute(document, {"type": "bind_model_parameter", "object_id": result["feature_id"],
                "path": "parameters.radius", "expression": "FilletRadius"})
        boss = system.execute(document, {"type": "boss", "body_id": second["body_id"],
            "origin": [40, 30, 20], "axis": [0, 0, 1], "diameter": 8, "depth": 5})
        sequential_volume = next(item["geometry"]["volume"] for item in document.objects
            if item["id"] == boss["body_id"])

        merged = system.execute(document, {"type": "merge_consecutive_fillet_features",
            "feature_ids": [first["feature_id"], second["feature_id"]], "name": "RootFilletsR3"})

        features = [item for item in document.objects if item.get("type") == "feature"]
        fillets = [item for item in features if item.get("operation") == "fillet_edges"]
        boss_feature = next(item for item in features if item["id"] == boss["feature_id"])
        merged_feature = next(item for item in fillets if item["id"] == merged["feature_id"])
        merged_body = next(item for item in document.objects if item["id"] == boss["body_id"])
        self.assertEqual(len(fillets), 1)
        self.assertEqual(len(merged_feature["edge_indices"]), 2)
        self.assertEqual(merged_feature["parameters"]["radius"], 3)
        self.assertEqual(boss_feature["source_body_id"], merged["body_id"])
        self.assertAlmostEqual(merged_body["geometry"]["volume"], sequential_volume, places=6)
        self.assertEqual(len(document.parameter_bindings), 1)
        self.assertEqual(document.parameter_bindings[0]["object_id"], merged["feature_id"])
        system.execute(document, {"type": "set_model_parameter", "name": "FilletRadius", "value": 4})
        merged_feature = next(item for item in document.objects if item["id"] == merged["feature_id"])
        self.assertEqual(merged_feature["parameters"]["radius"], 4)
        document.validate()

    def test_invalid_fillet_merge_leaves_document_unchanged(self):
        system = CommandSystem(OCCTKernelAdapter())
        document = Document.create()
        before = document.to_dict()
        with self.assertRaises(ValidationError):
            system.execute(document, {"type": "merge_consecutive_fillet_features",
                                      "feature_ids": ["feature:missing", "feature:also-missing"]})
        self.assertEqual(document.to_dict(), before)

    def test_fillet_merge_rejects_different_radius_bindings_even_at_same_value(self):
        system = CommandSystem(OCCTKernelAdapter())
        document = Document.create()
        system.execute(document, {"type": "create_model_parameters", "parameters": {
            "NominalRadius": {"value": 3, "unit": "mm"},
            "ClearanceLimit": {"value": 3, "unit": "mm"},
        }})
        base = system.execute(document, {"type": "create_box", "width": 80,
                                         "height": 60, "depth": 20})
        edges = system.kernel.topology(base["geometry"])["edges"]
        first_edge = next(edge["index"] for edge in edges if edge["curve"] == "line"
                          and edge["center"] == [40, 0, 0] and edge["length"] == 80)
        first = system.execute(document, {"type": "fillet_edges", "body_id": base["body_id"],
            "edge_indices": [first_edge], "radius": 3})
        next_edges = system.kernel.topology(first["geometry"])["edges"]
        second_edge = next(edge["index"] for edge in next_edges if edge["curve"] == "line"
                           and edge["center"] == [40, 60, 20] and edge["length"] == 80)
        second = system.execute(document, {"type": "fillet_edges", "body_id": first["body_id"],
            "edge_indices": [second_edge], "radius": 3})
        system.execute(document, {"type": "bind_model_parameter", "object_id": first["feature_id"],
            "path": "parameters.radius", "expression": "NominalRadius"})
        system.execute(document, {"type": "bind_model_parameter", "object_id": second["feature_id"],
            "path": "parameters.radius", "expression": "min(NominalRadius, ClearanceLimit)"})
        before = document.to_dict()

        with self.assertRaises(ValidationError):
            system.execute(document, {"type": "merge_consecutive_fillet_features",
                "feature_ids": [first["feature_id"], second["feature_id"]]})

        self.assertEqual(document.to_dict(), before)

    def test_evaluates_named_values_and_arithmetic(self):
        self.assertEqual(evaluate_expression("BaseLength / 2 + 5", {"BaseLength": 140}), 75)
        self.assertEqual(evaluate_expression("min(Target, (Width - Gap) / 2)",
                                             {"Target": 3, "Width": 90, "Gap": 52}), 3)
        self.assertEqual(evaluate_expression("min(Target, (Width - Gap) / 2)",
                                             {"Target": 3, "Width": 90, "Gap": 88}), 1)

    def test_rejects_unknown_names_and_non_arithmetic_syntax(self):
        for expression in ("Missing + 1", "__import__('os')", "BaseLength.real", "min(Target)", "min(Target, key=Target)"):
            with self.subTest(expression=expression), self.assertRaises(ValidationError):
                evaluate_expression(expression, {"BaseLength": 140})

    def test_rejects_division_by_zero_and_non_finite_results(self):
        for expression in ("1 / 0", "1e308 * 1e308"):
            with self.subTest(expression=expression), self.assertRaises(ValidationError):
                evaluate_expression(expression, {})

    def test_stored_edge_intent_resolves_after_a_small_dimension_change(self):
        kernel = OCCTKernelAdapter()
        original = kernel.create_box(width=80, height=60, depth=10)
        references = capture_edge_references(kernel, original, [1])
        revised = kernel.create_box(width=80, height=66, depth=10)
        resolved = resolve_edge_references(kernel, revised, references)
        self.assertEqual(len(resolved), 1)
        edge = next(item for item in kernel.topology(revised)["edges"] if item["index"] == resolved[0])
        self.assertEqual(edge["center"], [0, 33, 10])
        self.assertEqual(edge["length"], 66)

    def test_rebuilt_fillet_saves_the_resolved_edge_indices(self):
        system = CommandSystem(OCCTKernelAdapter())
        document = Document.create()
        system.execute(document, {"type": "create_model_parameter", "name": "BaseLength",
                                  "value": 80, "unit": "mm"})
        base = system.execute(document, {"type": "create_box", "width": 80, "height": 60, "depth": 20})
        edges = system.kernel.topology(base["geometry"])["edges"]
        selected = next(edge["index"] for edge in edges if edge["center"][0] > 79 and edge["curve"] == "line")
        fillet = system.execute(document, {"type": "fillet_edges", "body_id": base["body_id"],
            "edge_indices": [selected], "radius": 2})
        system.execute(document, {"type": "bind_model_parameter", "object_id": base["feature_id"],
            "path": "parameters.width", "expression": "BaseLength"})

        system.execute(document, {"type": "set_model_parameter", "name": "BaseLength", "value": 100})

        feature = next(item for item in document.objects if item["id"] == fillet["feature_id"])
        self.assertEqual(feature["geometry"]["edge_indices"], feature["edge_indices"])
        self.assertEqual(len(feature.get("edge_references", [])), 1)
        document.validate()

    def test_inserts_boss_root_fillet_before_a_slot_that_interrupts_the_circle(self):
        system = CommandSystem(OCCTKernelAdapter())
        document = Document.create()
        base = system.execute(document, {"type": "create_box", "width": 100, "height": 80, "depth": 10})
        sketch = system.execute(document, {"type": "create_sketch", "plane": "XY"})["sketch_id"]
        system.execute(document, {"type": "add_sketch_rectangle", "sketch_id": sketch,
                                  "x": 5, "y": 5, "width": 10, "height": 10})
        slot = system.execute(document, {"type": "pocket_sketch", "body_id": base["body_id"],
                                         "sketch_id": sketch, "depth": 10})
        boss = system.execute(document, {"type": "boss", "body_id": slot["body_id"],
            "origin": [50, 40, 10], "axis": [0, 0, 1], "diameter": 40, "depth": 25})
        bore = system.execute(document, {"type": "through_hole", "body_id": boss["body_id"],
            "origin": [50, 40, 35], "axis": [0, 0, -1], "diameter": 14})

        result = system.execute(document, {"type": "insert_fillet_before_feature",
            "upstream_feature_id": boss["feature_id"], "downstream_feature_id": slot["feature_id"],
            "source_body_id": base["body_id"], "dependent_feature_ids": [bore["feature_id"]],
            "radius": 3, "name": "CenterBossBaseFilletR3"})

        fillet = next(item for item in document.objects if item["id"] == result["feature_id"])
        boss_feature = next(item for item in document.objects if item["id"] == boss["feature_id"])
        slot_feature = next(item for item in document.objects if item["id"] == slot["feature_id"])
        bore_feature = next(item for item in document.objects if item["id"] == bore["feature_id"])
        self.assertEqual(boss_feature["source_body_id"], base["body_id"])
        self.assertEqual(slot_feature["source_body_id"], result["body_id"])
        self.assertEqual(bore_feature["source_body_id"], slot["body_id"])
        order = [item["id"] for item in document.objects]
        self.assertLess(order.index(boss["feature_id"]), order.index(fillet["id"]))
        self.assertLess(order.index(fillet["id"]), order.index(slot["feature_id"]))
        self.assertLess(order.index(slot["feature_id"]), order.index(bore["feature_id"]))
        document.validate()

    def test_batch_parameter_creation_and_bindings_are_atomic(self):
        system = CommandSystem(OCCTKernelAdapter())
        document = Document.create()
        system.execute(document, {"type": "create_model_parameters", "parameters": {
            "BaseLength": {"value": 100, "unit": "mm"},
            "BaseWidth": {"value": 60, "unit": "mm"},
        }})
        box = system.execute(document, {"type": "create_box", "width": 100, "height": 60, "depth": 10})
        result = system.execute(document, {"type": "bind_model_parameters", "bindings": [
            {"object_id": box["feature_id"], "path": "parameters.width", "expression": "BaseLength"},
            {"object_id": box["feature_id"], "path": "parameters.height", "expression": "BaseWidth"},
        ]})
        self.assertEqual(len(result["bindings"]), 2)
        self.assertEqual(document.model_parameters["BaseLength"]["value"], 100)
        self.assertEqual(len(document.parameter_bindings), 2)
        before = document.to_dict()
        with self.assertRaises(NotFoundError):
            system.execute(document, {"type": "bind_model_parameters", "bindings": [
                {"object_id": box["feature_id"], "path": "parameters.width", "expression": "BaseWidth"},
                {"object_id": "feature:missing", "path": "parameters.depth", "expression": "BaseLength"},
            ]})
        self.assertEqual(document.to_dict(), before)

    def test_bound_box_dimension_rebuilds_when_model_parameter_changes(self):
        system = CommandSystem(OCCTKernelAdapter())
        document = Document.create()
        system.execute(document, {"type": "create_model_parameter", "name": "BaseLength",
                                  "value": 140, "unit": "mm"})
        box = system.execute(document, {"type": "create_box", "width": 140, "height": 90, "depth": 10})
        system.execute(document, {"type": "bind_model_parameter", "object_id": box["feature_id"],
                                  "path": "parameters.width", "expression": "BaseLength"})

        result = system.execute(document, {"type": "set_model_parameter", "name": "BaseLength", "value": 160})

        self.assertEqual(result["value"], 160)
        self.assertEqual(document.model_parameters["BaseLength"]["value"], 160)
        body = next(item for item in document.objects if item["id"] == box["body_id"])
        self.assertEqual(body["geometry"]["dimensions"]["width"], 160)
        document.validate()

    def test_invalid_bound_update_is_atomic(self):
        system = CommandSystem(OCCTKernelAdapter())
        document = Document.create()
        system.execute(document, {"type": "create_model_parameter", "name": "BaseLength",
                                  "value": 140, "unit": "mm"})
        box = system.execute(document, {"type": "create_box", "width": 140, "height": 90, "depth": 10})
        system.execute(document, {"type": "bind_model_parameter", "object_id": box["feature_id"],
                                  "path": "parameters.width", "expression": "BaseLength"})
        with self.assertRaises(ValidationError):
            system.execute(document, {"type": "set_model_parameter", "name": "BaseLength", "value": 0})
        self.assertEqual(document.model_parameters["BaseLength"]["value"], 140)
        self.assertEqual(document.objects[1]["geometry"]["dimensions"]["width"], 140)

    def test_bound_sketch_coordinate_rebuilds_profile_feature(self):
        system = CommandSystem(OCCTKernelAdapter())
        document = Document.create()
        system.execute(document, {"type": "create_model_parameter", "name": "BaseLength",
                                  "value": 100, "unit": "mm"})
        sketch = system.execute(document, {"type": "create_sketch", "plane": "XY"})["sketch_id"]
        profile = system.execute(document, {"type": "add_sketch_polyline", "sketch_id": sketch,
                                            "closed": True, "points": [
                                                {"x": 0, "y": 0}, {"x": 100, "y": 0},
                                                {"x": 100, "y": 50}, {"x": 0, "y": 50},
                                            ]})
        extrusion = system.execute(document, {"type": "extrude_sketch", "sketch_id": sketch,
                                             "depth": 10})
        point_id = profile["point_ids"][1]
        system.execute(document, {"type": "bind_model_parameter", "object_id": point_id,
                                  "path": "x", "expression": "BaseLength"})

        system.execute(document, {"type": "set_model_parameter", "name": "BaseLength", "value": 160})

        feature = next(item for item in document.objects if item["id"] == extrusion["feature_id"])
        self.assertEqual(feature["geometry"]["profile"][1]["x"], 160)
        document.validate()

    def test_bound_sketch_coordinate_is_solved_before_profile_rebuild(self):
        system = CommandSystem(OCCTKernelAdapter())
        document = Document.create()
        system.execute(document, {"type": "create_model_parameter", "name": "BaseWidth",
                                  "value": 10, "unit": "mm"})
        sketch = system.execute(document, {"type": "create_sketch", "plane": "XY"})["sketch_id"]
        profile = system.execute(document, {"type": "add_sketch_polyline", "sketch_id": sketch,
            "closed": True, "points": [{"x": 0, "y": 0}, {"x": 10, "y": 0},
                                        {"x": 10, "y": 10}, {"x": 0, "y": 10}]})
        extrusion = system.execute(document, {"type": "extrude_sketch", "sketch_id": sketch, "depth": 5})
        system.execute(document, {"type": "add_sketch_constraint", "sketch_id": sketch,
                                  "kind": "horizontal", "references": [profile["line_ids"][0]]})
        system.execute(document, {"type": "bind_model_parameter", "object_id": profile["point_ids"][1],
                                  "path": "x", "expression": "BaseWidth"})

        system.execute(document, {"type": "set_model_parameter", "name": "BaseWidth", "value": 20})

        points = {item["id"]: item for item in document.objects if item.get("type") == "point"}
        self.assertAlmostEqual(points[profile["point_ids"][1]]["y"],
                               points[profile["point_ids"][0]]["y"], delta=1e-7)
        feature = next(item for item in document.objects if item.get("id") == extrusion["feature_id"])
        self.assertEqual(feature["geometry"]["profile"][1]["x"], 20)
        document.validate()

    def test_bound_counterbore_parameter_rebuilds_dependent_feature_atomically(self):
        system = CommandSystem(OCCTKernelAdapter())
        document = Document.create()
        system.execute(document, {"type": "create_model_parameter", "name": "CounterboreDiameter",
                                  "value": 14, "unit": "mm"})
        base = system.execute(document, {"type": "create_box", "width": 80, "height": 60, "depth": 10})
        hole = system.execute(document, {"type": "counterbore_hole", "body_id": base["body_id"],
            "origin": [20, 20, 10], "axis": [0, 0, -1], "hole_diameter": 8,
            "counterbore_diameter": 14, "counterbore_depth": 5, "through": True})
        system.execute(document, {"type": "bind_model_parameter", "object_id": hole["feature_id"],
            "path": "parameters.counterbore_diameter", "expression": "CounterboreDiameter"})
        before = next(item for item in document.objects if item["id"] == hole["body_id"])["geometry"]["volume"]

        system.execute(document, {"type": "set_model_parameter", "name": "CounterboreDiameter", "value": 18})

        feature = next(item for item in document.objects if item["id"] == hole["feature_id"])
        body = next(item for item in document.objects if item["id"] == hole["body_id"])
        self.assertEqual(feature["parameters"]["counterbore_diameter"], 18)
        self.assertLess(body["geometry"]["volume"], before)
        document.validate()


if __name__ == "__main__":
    unittest.main()
