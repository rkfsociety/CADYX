import unittest

from cadyx.model_parameters import evaluate_expression
from cadyx.errors import NotFoundError, ValidationError
from cadyx.commands import CommandSystem
from cadyx.kernel import OCCTKernelAdapter
from cadyx.models import Document
from cadyx.semantic_selection import capture_edge_references, resolve_edge_references


class ModelParameterExpressionTests(unittest.TestCase):
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
