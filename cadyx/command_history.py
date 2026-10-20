"""History command handlers and implementation.

Core owns lease/revision checks and atomic persistence; these methods operate on
the staged Document supplied by the shared CommandSystem.
"""
from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Callable
from uuid import uuid4
from .errors import NotFoundError, ValidationError
from .models import Document
from .command_values import _number


class HistoryCommands:
    @staticmethod
    def _remove_body(document: Document, command: dict[str, Any]) -> dict[str, Any]:
        if not command.keys() <= {"type", "body_id", "remove_source_file"}:
            raise ValidationError("Body removal contains unsupported fields.")
        remove_source_file = command.get("remove_source_file", False)
        if type(remove_source_file) is not bool:
            raise ValidationError("remove_source_file must be a boolean.")
        body_id = command.get("body_id")
        if not isinstance(body_id, str):
            raise NotFoundError("Body to remove was not found.")
        body = next((item for item in document.objects
                     if item.get("id") == body_id and item.get("type") == "body"), None)
        if body is None:
            raise NotFoundError("Body to remove was not found.")

        if any(part["body_id"] == body_id for part in document.assembly["parts"]):
            raise ValidationError("Body is referenced by an assembly part; remove the component first.")

        # Do not leave dependent features or sections pointing at a missing body.
        # A feature's body_id identifies its own output and is safe to cascade.
        def contains_reference(value: Any) -> bool:
            if isinstance(value, dict):
                return any(contains_reference(child) for child in value.values())
            if isinstance(value, list):
                return any(contains_reference(child) for child in value)
            return value == body_id

        dependents = []
        for item in document.objects:
            if item is body:
                continue
            if item.get("type") == "feature" and item.get("body_id") == body_id:
                continue
            if contains_reference(item):
                dependents.append(item.get("id"))
        if dependents:
            raise ValidationError("Body is referenced by other document objects and cannot be removed.")

        feature_ids = set(body.get("feature_ids", []))
        source_file = None
        if remove_source_file:
            import_features = [
                item for item in document.objects
                if item.get("id") in feature_ids and item.get("operation") == "import_step"
            ]
            if len(import_features) != 1:
                raise ValidationError("Removing a staged source file requires one imported STEP feature owned by this body.")
            source_file = import_features[0].get("source", {}).get("file")
            if not isinstance(source_file, str):
                raise ValidationError("The imported STEP feature has no valid source filename.")
            shared_source = any(
                item.get("type") == "feature"
                and item.get("operation") == "import_step"
                and item.get("id") not in feature_ids
                and item.get("source", {}).get("file") == source_file
                for item in document.objects
            )
            if shared_source:
                raise ValidationError("The STEP source file is still used by another imported body and cannot be removed.")
        document.objects[:] = [
            item for item in document.objects
            if item is not body and item.get("id") not in feature_ids
        ]
        document.parameter_bindings[:] = [
            binding for binding in document.parameter_bindings
            if binding.get("object_id") not in feature_ids
        ]
        return {
            "body_id": body_id,
            "removed_feature_ids": sorted(feature_ids),
            **({"source_file_to_remove": source_file} if source_file else {}),
        }

    @staticmethod
    def _remove_objects(document: Document, command: dict[str, Any]) -> dict[str, Any]:
        if command.keys() != {"type", "object_ids"}:
            raise ValidationError("remove_objects requires only object_ids.")
        object_ids = command.get("object_ids")
        if (not isinstance(object_ids, list) or not object_ids
                or any(not isinstance(value, str) or not value for value in object_ids)
                or len(set(object_ids)) != len(object_ids)):
            raise ValidationError("object_ids must be a non-empty list of unique object IDs.")
        by_id = {item["id"]: item for item in document.objects}
        missing = [value for value in object_ids if value not in by_id]
        if missing:
            raise NotFoundError(f"Objects to remove were not found: {', '.join(missing)}")
        allowed_types = {"body", "feature", "sketch", "point", "line", "circle", "ellipse", "spline", "constraint"}
        unsupported = [value for value in object_ids if by_id[value].get("type") not in allowed_types]
        if unsupported:
            raise ValidationError(f"Objects cannot be removed with remove_objects: {', '.join(unsupported)}")
        removed = set(object_ids)

        def references_removed(value: Any) -> bool:
            if isinstance(value, dict):
                return any(references_removed(child) for key, child in value.items() if key != "id")
            if isinstance(value, list):
                return any(references_removed(child) for child in value)
            return isinstance(value, str) and value in removed

        external = [item["id"] for item in document.objects
                    if item["id"] not in removed and references_removed(item)]
        if external:
            raise ValidationError(f"Objects are still referenced by retained objects: {', '.join(external)}")
        if any(part["body_id"] in removed for part in document.assembly["parts"]):
            raise ValidationError("Assembly parts reference bodies in the removal group.")
        document.objects[:] = [item for item in document.objects if item["id"] not in removed]
        return {"removed_object_ids": object_ids, "removed_count": len(object_ids)}

    def _repair_profile_chain(
        self, document: Document, command: dict[str, Any], *,
        progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
    ) -> dict[str, Any]:
        required = {"type", "feature_id", "parameter_name", "temporary_value", "restore_value",
                    "finish_feature_ids", "finish_edge_indices"}
        if command.keys() != required:
            raise ValidationError("repair_profile_chain requires a source feature, refresh parameter and finish selectors.")
        feature_id = command["feature_id"]
        source_feature = document.find_object(feature_id, "feature")
        if source_feature is None or source_feature.get("operation") != "extrude_sketch":
            raise ValidationError("Recovery needs an existing sketch-driven extrusion feature.")
        parameter_name = command["parameter_name"]
        if not isinstance(parameter_name, str) or parameter_name not in document.model_parameters:
            raise ValidationError("Recovery refresh parameter was not found.")
        temporary_value = _number(command["temporary_value"], "temporary_value")
        restore_value = _number(command["restore_value"], "restore_value")
        current_value = float(document.model_parameters[parameter_name]["value"])
        if (not math.isclose(restore_value, current_value, rel_tol=0.0, abs_tol=1e-12)
                or math.isclose(temporary_value, restore_value, rel_tol=0.0, abs_tol=1e-12)):
            raise ValidationError("Recovery must refresh geometry temporarily and restore the current parameter value.")

        finish_feature_ids = command["finish_feature_ids"]
        selectors = command["finish_edge_indices"]
        if (not isinstance(finish_feature_ids, list) or not finish_feature_ids
                or any(not isinstance(value, str) or not value for value in finish_feature_ids)
                or len(set(finish_feature_ids)) != len(finish_feature_ids)
                or not isinstance(selectors, dict) or set(selectors) != set(finish_feature_ids)):
            raise ValidationError("Recovery needs matching unique finish feature IDs and fresh edge selectors.")

        by_id = {item["id"]: item for item in document.objects}
        feature_order = {item["id"]: index for index, item in enumerate(document.objects)
                         if item.get("type") == "feature"}
        if any(value not in feature_order for value in finish_feature_ids):
            raise NotFoundError("A recovery finish feature was not found.")
        ordered_ids = sorted(finish_feature_ids, key=feature_order.__getitem__)

        descendants = {source_feature.get("body_id")}
        changed = True
        while changed:
            changed = False
            for item in document.objects:
                if item.get("type") != "feature" or item.get("body_id") in descendants:
                    continue
                operation = item.get("operation")
                sources = ({item.get("base_body_id"), item.get("tool_body_id")}
                           if operation in {"fuse_bodies", "cut_bodies"}
                           else {item.get("source_body_id")})
                if sources & descendants:
                    descendants.add(item.get("body_id"))
                    changed = True
        recovered: list[dict[str, Any]] = []
        previous_source = None
        for old_id in ordered_ids:
            feature = by_id[old_id]
            operation = feature.get("operation")
            if operation not in {"fillet_edges", "chamfer_edges"}:
                raise ValidationError("Only fillet and chamfer descendants can be restored by this recovery command.")
            source_body_id = feature.get("source_body_id")
            if source_body_id not in descendants or (previous_source is not None and source_body_id != previous_source):
                raise ValidationError("Finish features must form one downstream chain from the repaired extrusion.")
            output_body = by_id.get(feature.get("body_id"))
            if output_body is None or output_body.get("type") != "body":
                raise ValidationError("A recovery finish feature has no result body.")
            edge_indices = selectors[old_id]
            if (not isinstance(edge_indices, list) or not edge_indices
                    or any(type(index) is not int or index < 0 for index in edge_indices)
                    or len(set(edge_indices)) != len(edge_indices)):
                raise ValidationError("Each recovery finish operation needs unique fresh edge indices.")
            path = "parameters.radius" if operation == "fillet_edges" else "parameters.distance"
            size_key = "radius" if operation == "fillet_edges" else "distance"
            size = _number((feature.get("parameters") or {}).get(size_key), size_key)
            if size <= 0:
                raise ValidationError(f"Recovery finish parameter {size_key} must be positive.")
            bindings = [deepcopy(binding) for binding in document.parameter_bindings
                        if binding.get("object_id") == old_id]
            if any(binding.get("path") != path for binding in bindings):
                raise ValidationError("Recovery found an unsupported finish-feature parameter binding.")
            recovered.append({"old_feature_id": old_id, "operation": operation,
                              "source_body_id": source_body_id, "body_name": output_body.get("name"),
                              "size": size, "size_key": size_key, "path": path,
                              "bindings": bindings, "edge_indices": edge_indices})
            previous_source = feature.get("body_id")

        staged = deepcopy(document)
        staged._command_object_index = None
        for item in reversed(recovered):
            old_body_id = by_id[item["old_feature_id"]]["body_id"]
            self._remove_body(staged, {"type": "remove_body", "body_id": old_body_id})
        self._set_model_parameter(staged, {"type": "set_model_parameter", "name": parameter_name,
                                           "value": temporary_value}, progress=progress)
        self._set_model_parameter(staged, {"type": "set_model_parameter", "name": parameter_name,
                                           "value": restore_value}, progress=progress)

        output_body_by_old_id: dict[str, str] = {}
        restored_features = []
        for item in recovered:
            source_body_id = output_body_by_old_id.get(item["source_body_id"], item["source_body_id"])
            operation = item["operation"]
            nested = {"type": operation, "body_id": source_body_id,
                      "edge_indices": item["edge_indices"], item["size_key"]: item["size"]}
            if item["body_name"]:
                nested["name"] = item["body_name"]
            result = self.execute(staged, nested, progress=progress, project_id=project_id)
            new_feature_id, new_body_id = result.get("feature_id"), result.get("body_id")
            if not isinstance(new_feature_id, str) or not isinstance(new_body_id, str):
                raise ValidationError("A recovery finish operation did not produce a feature and body.")
            output_body_by_old_id[by_id[item["old_feature_id"]]["body_id"]] = new_body_id
            for binding in item["bindings"]:
                self._bind_model_parameter(staged, {"object_id": new_feature_id,
                    "path": binding["path"], "expression": binding["expression"]}, progress=progress)
            restored_features.append({"operation": operation, "feature_id": new_feature_id,
                                      "body_id": new_body_id, "edge_indices": item["edge_indices"]})

        staged.validate()
        document.objects[:] = staged.objects
        document.model_parameters = staged.model_parameters
        document.parameter_bindings = staged.parameter_bindings
        document.assembly = staged.assembly
        return {"feature_id": feature_id, "parameter_name": parameter_name,
                "parameter_value": restore_value, "restored_features": restored_features}

    @staticmethod
    def _consumed_body_ids(document: Document) -> set[str]:
        consumed: set[str] = set()
        for feature in document.objects:
            if feature.get("type") != "feature":
                continue
            if feature.get("operation") in {"fuse_bodies", "cut_bodies"}:
                consumed.update((feature.get("base_body_id"), feature.get("tool_body_id")))
            elif feature.get("operation") in {
                "fillet_edges", "resize_imported_thread", "transform_imported_brep",
            }:
                consumed.add(feature.get("source_body_id"))
        return consumed - {None}

    def _prepare_edge_references(self, document: Document) -> None:
        by_id = {item["id"]: item for item in document.objects}
        from .semantic_selection import capture_edge_references
        for feature in document.objects:
            if feature.get("type") != "feature" or feature.get("operation") not in {"fillet_edges", "chamfer_edges"}:
                continue
            if feature.get("edge_references"):
                continue
            source = by_id.get(feature.get("source_body_id"))
            if source is None or source.get("type") != "body":
                raise ValidationError("Topology feature source body was not found.")
            indices = feature.get("edge_indices")
            if not isinstance(indices, list):
                indices = (feature.get("parameters") or {}).get("edge_indices")
            if not isinstance(indices, list) or not indices:
                raise ValidationError("Topology feature edge indices are unavailable for stable rebuild.")
            feature["edge_references"] = capture_edge_references(self.kernel, source["geometry"], indices)

    def _merge_consecutive_fillet_features(
        self, document: Document, command: dict[str, Any], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        feature_ids = command.get("feature_ids")
        label = command.get("name", "Объединённое скругление")
        if (not isinstance(feature_ids, list) or len(feature_ids) < 2
                or any(not isinstance(value, str) or not value for value in feature_ids)
                or len(set(feature_ids)) != len(feature_ids)):
            raise ValidationError("feature_ids must contain at least two unique fillet feature IDs.")
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > 120:
            raise ValidationError("Merged fillet name must be non-empty text up to 120 characters.")

        staged = deepcopy(document)
        self._prepare_edge_references(staged)
        by_id = {item["id"]: item for item in staged.objects}
        features = [by_id.get(feature_id) for feature_id in feature_ids]
        if any(feature is None or feature.get("type") != "feature"
               or feature.get("operation") != "fillet_edges" for feature in features):
            raise ValidationError("Every selected feature must be an editable fillet feature.")
        feature_order = [item["id"] for item in staged.objects if item.get("type") == "feature"]
        if [feature["id"] for feature in features] != sorted(feature_ids, key=feature_order.index):
            raise ValidationError("Fillet features must be supplied in document order.")

        radius = _number(features[0].get("parameters", {}).get("radius"), "radius")
        for index, feature in enumerate(features):
            current_radius = _number(feature.get("parameters", {}).get("radius"), "radius")
            if not math.isclose(current_radius, radius, rel_tol=1e-12, abs_tol=1e-12):
                raise ValidationError("Only fillet features with the same radius can be merged.")
            if index and feature.get("source_body_id") != features[index - 1].get("body_id"):
                raise ValidationError("Fillet features must form one consecutive source-body chain.")

        source_body = by_id.get(features[0].get("source_body_id"))
        old_body_ids = [feature.get("body_id") for feature in features]
        if (source_body is None or source_body.get("type") != "body"
                or any(not isinstance(body_id, str) or by_id.get(body_id, {}).get("type") != "body"
                       for body_id in old_body_ids)):
            raise ValidationError("The fillet chain references a missing source or result body.")
        removed_ids = set(feature_ids) | set(old_body_ids)

        downstream_features = [item for item in staged.objects
                               if item.get("type") == "feature" and item.get("id") not in feature_ids
                               and item.get("source_body_id") == old_body_ids[-1]]
        for intermediate_body_id, next_feature in zip(old_body_ids[:-1], features[1:], strict=True):
            consumers = [item for item in staged.objects if item.get("type") == "feature"
                         and item.get("source_body_id") == intermediate_body_id]
            if consumers != [next_feature]:
                raise ValidationError("An intermediate fillet body has a branching dependency and cannot be merged.")

        def references_removed(value: Any) -> bool:
            if isinstance(value, dict):
                return any(references_removed(child) for key, child in value.items() if key != "id")
            if isinstance(value, list):
                return any(references_removed(child) for child in value)
            return isinstance(value, str) and value in removed_ids

        for item in staged.objects:
            if item.get("id") in removed_ids or not references_removed(item):
                continue
            if (item.get("type") == "feature" and item in downstream_features
                    and item.get("source_body_id") == old_body_ids[-1]):
                continue
            raise ValidationError("A retained object references an intermediate fillet feature or body.")

        old_bindings = [binding for binding in staged.parameter_bindings
                        if binding["object_id"] in feature_ids]
        radius_bindings = [binding for binding in old_bindings
                           if binding["path"] == "parameters.radius"]
        if len(old_bindings) != len(radius_bindings):
            raise ValidationError("Only radius bindings can be transferred from merged fillets.")
        binding_expression = None
        if radius_bindings:
            if (len(radius_bindings) != len(features)
                    or len({binding["expression"] for binding in radius_bindings}) != 1):
                raise ValidationError("Merged fillets must share the same radius expression binding.")
            binding_expression = radius_bindings[0]["expression"]

        references = [reference for feature in features
                      for reference in feature.get("edge_references", [])]
        if not references or any(not isinstance(reference, dict) for reference in references):
            raise ValidationError("Every fillet needs stored geometric edge references before merging.")
        from .semantic_selection import capture_edge_references, resolve_edge_references
        edge_indices = resolve_edge_references(self.kernel, source_body["geometry"], references)
        if len(set(edge_indices)) != len(edge_indices):
            raise ValidationError("Merged fillets resolve to duplicate edges on the common source body.")
        if progress is not None:
            progress({"phase": "kernel_fillet", "feature_count": len(features),
                      "edge_count": len(edge_indices)})
        geometry = self.kernel.create_fillet(source_body["geometry"],
                                             edge_indices=edge_indices, radius=radius)
        edge_references = capture_edge_references(self.kernel, source_body["geometry"], edge_indices)
        topology_edges = self.kernel.topology(source_body["geometry"]).get("edges", [])
        edge_keys = [edge["semantic_key"] for edge in topology_edges
                     if edge.get("index") in set(edge_indices) and isinstance(edge.get("semantic_key"), str)]
        if len(edge_keys) != len(edge_indices):
            raise ValidationError("The merged fillet selection lacks complete semantic edge keys.")

        body_id, feature_id = f"body:{uuid4()}", f"feature:{uuid4()}"
        merged_body = {"id": body_id, "type": "body", "name": label.strip(),
                       "feature_ids": [feature_id], "geometry": geometry}
        merged_feature = {"id": feature_id, "type": "feature", "operation": "fillet_edges",
                          "body_id": body_id, "source_body_id": source_body["id"],
                          "edge_indices": edge_indices, "edge_references": edge_references,
                          "edge_keys": edge_keys, "parameters": {"radius": radius},
                          "geometry": geometry}
        for downstream in downstream_features:
            downstream["source_body_id"] = body_id
        staged.parameter_bindings = [binding for binding in staged.parameter_bindings
                                     if binding["object_id"] not in feature_ids]
        if binding_expression is not None:
            staged.parameter_bindings.append({"object_id": feature_id,
                "path": "parameters.radius", "expression": binding_expression})

        removed_positions = [index for index, item in enumerate(staged.objects)
                             if item.get("id") in removed_ids]
        insertion_index = min(removed_positions)
        staged.objects = [item for item in staged.objects if item.get("id") not in removed_ids]
        staged.objects[insertion_index:insertion_index] = [merged_body, merged_feature]
        self._rebuild_derived_features(staged, {source_body["id"]}, progress=progress)
        staged.validate()
        self._commit_staged_document(document, staged)
        return {"feature_id": feature_id, "body_id": body_id, "source_body_id": source_body["id"],
                "merged_feature_ids": feature_ids, "edge_indices": edge_indices,
                "radius": radius, "geometry": geometry}

    def _insert_fillet_before_feature(
        self, document: Document, command: dict[str, Any], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        upstream_id = command.get("upstream_feature_id")
        downstream_id = command.get("downstream_feature_id")
        source_body_id = command.get("source_body_id")
        dependent_ids = command.get("dependent_feature_ids")
        if (not all(isinstance(value, str) for value in (upstream_id, downstream_id, source_body_id))
                or not isinstance(dependent_ids, list) or any(not isinstance(value, str) for value in dependent_ids)
                or len(set(dependent_ids)) != len(dependent_ids)):
            raise ValidationError("Fillet insertion needs feature/body IDs and a unique dependent feature list.")
        radius = _number(command.get("radius"), "radius")
        label = command.get("name", "Вставленное скругление")
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > 120:
            raise ValidationError("Fillet name must be a non-empty string of at most 120 characters.")
        staged = deepcopy(document)
        self._prepare_edge_references(staged)
        by_id = {item["id"]: item for item in staged.objects}
        upstream, downstream = by_id.get(upstream_id), by_id.get(downstream_id)
        source = by_id.get(source_body_id)
        if (upstream is None or upstream.get("type") != "feature" or upstream.get("operation") != "boss"
                or downstream is None or downstream.get("type") != "feature"
                or downstream.get("operation") != "pocket_sketch"
                or source is None or source.get("type") != "body"
                or downstream.get("source_body_id") != source_body_id
                or upstream.get("source_body_id") != downstream.get("body_id")):
            raise ValidationError("Fillet insertion requires a boss currently after a pocket on the same body chain.")
        boss_body = by_id.get(upstream.get("body_id"))
        slot_body = by_id.get(downstream.get("body_id"))
        if any(body is None or body.get("type") != "body" for body in (boss_body, slot_body)):
            raise ValidationError("Fillet insertion source or downstream result body is missing.")
        dependents = []
        for feature_id in dependent_ids:
            feature = by_id.get(feature_id)
            if (feature is None or feature.get("type") != "feature"
                    or feature.get("source_body_id") != boss_body["id"]):
                raise ValidationError("Each dependent feature must currently consume the boss result body.")
            dependents.append(feature)
        if upstream_id in dependent_ids or downstream_id in dependent_ids:
            raise ValidationError("Upstream and downstream features cannot also be listed as dependents.")

        upstream["source_body_id"] = source_body_id
        self._rebuild_bound_feature(staged, upstream_id, progress=progress)
        parameters = upstream["parameters"]
        origin, axis = parameters.get("origin"), parameters.get("axis")
        diameter = parameters.get("diameter")
        if (not isinstance(origin, list) or len(origin) != 3 or not isinstance(axis, list) or len(axis) != 3
                or not isinstance(diameter, (int, float))):
            raise ValidationError("Boss geometry lacks an analytic axis or diameter for root-edge selection.")
        topology = self.kernel.topology(boss_body["geometry"])
        edges = topology.get("edges")
        if not isinstance(edges, list):
            raise ValidationError("Boss topology does not expose edge metadata.")
        root_edges = []
        for edge in edges:
            if (not isinstance(edge, dict) or edge.get("curve") != "circle"
                    or not math.isclose(float(edge.get("radius", -1)), float(diameter) / 2,
                                        rel_tol=0.0, abs_tol=1e-5)):
                continue
            edge_origin, edge_axis = edge.get("axis_origin"), edge.get("axis_direction")
            if (not isinstance(edge_origin, list) or len(edge_origin) != 3
                    or not isinstance(edge_axis, list) or len(edge_axis) != 3
                    or any(abs(float(edge_origin[i]) - float(origin[i])) > 1e-5 for i in range(3))):
                continue
            axis_dot = abs(sum(float(edge_axis[i]) * float(axis[i]) for i in range(3)))
            if axis_dot >= 1.0 - 1e-6:
                root_edges.append(edge)
        if len(root_edges) != 1 or type(root_edges[0].get("index")) is not int:
            raise ValidationError("Boss base circle is not uniquely selectable on the uncut boss feature.")
        edge_indices = [root_edges[0]["index"]]
        geometry = self.kernel.create_fillet(boss_body["geometry"], edge_indices=edge_indices, radius=radius)
        from .semantic_selection import capture_edge_references
        edge_references = capture_edge_references(self.kernel, boss_body["geometry"], edge_indices)
        body_id, feature_id = f"body:{uuid4()}", f"feature:{uuid4()}"
        fillet_body = {"id": body_id, "type": "body", "name": label.strip(),
                       "feature_ids": [feature_id], "geometry": geometry}
        fillet_feature = {"id": feature_id, "type": "feature", "operation": "fillet_edges",
                          "body_id": body_id, "source_body_id": boss_body["id"],
                          "edge_indices": edge_indices, "edge_references": edge_references,
                          "edge_keys": [root_edges[0]["semantic_key"]],
                          "parameters": {"radius": radius}, "geometry": geometry}
        downstream["source_body_id"] = body_id
        for dependent in dependents:
            dependent["source_body_id"] = slot_body["id"]

        ordered_feature_ids = [upstream_id, feature_id, downstream_id]
        pair_objects = []
        pair_ids = {upstream_id, feature_id, downstream_id}
        for target_id in ordered_feature_ids:
            target = fillet_feature if target_id == feature_id else by_id[target_id]
            body = fillet_body if target_id == feature_id else by_id.get(target.get("body_id"))
            if body is None or body.get("type") != "body":
                raise ValidationError("Feature insertion could not locate its paired result body.")
            pair_objects.extend([body, target])
        original_ids = {item["id"] for item in staged.objects
                        if item.get("type") == "feature" and item.get("id") in pair_ids}
        body_ids = {by_id[target_id].get("body_id") for target_id in (upstream_id, downstream_id)}
        body_ids.add(body_id)
        selected_ids = original_ids | body_ids
        insertion_index = min(index for index, item in enumerate(staged.objects) if item["id"] in selected_ids)
        staged.objects = [item for item in staged.objects if item["id"] not in selected_ids]
        staged.objects[insertion_index:insertion_index] = pair_objects
        self._rebuild_derived_features(staged, {source_body_id}, progress=progress)
        staged.validate()
        self._commit_staged_document(document, staged)
        return {"body_id": body_id, "feature_id": feature_id, "source_body_id": boss_body["id"],
                "downstream_feature_id": downstream_id, "edge_indices": edge_indices,
                "geometry": geometry}


def _handle_insert_fillet_before_feature(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._insert_fillet_before_feature(document, command, progress=progress)


def _handle_merge_consecutive_fillet_features(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._merge_consecutive_fillet_features(document, command, progress=progress)


def _handle_remove_body(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._remove_body(document, command)


def _handle_remove_objects(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._remove_objects(document, command)


def _handle_repair_profile_chain(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    return self._repair_profile_chain(document, command, progress=progress, project_id=project_id)


def _handle_clear_model(
    self: Any, document: Document, command: dict[str, Any], *,
    progress: Callable[[dict[str, Any]], None] | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    if command.keys() != {"type"}:
        raise ValidationError("clear_model does not accept additional fields.")
    removed_object_count = len(document.objects)
    removed_body_count = sum(item.get("type") == "body" for item in document.objects)
    removed_sketch_count = sum(item.get("type") == "sketch" for item in document.objects)
    document.objects.clear()
    from .assemblies import empty_assembly
    document.assembly = empty_assembly()
    return {
        "removed_object_count": removed_object_count,
        "removed_body_count": removed_body_count,
        "removed_sketch_count": removed_sketch_count,
        "project_files_preserved": True,
    }


COMMAND_HANDLERS = {
    "insert_fillet_before_feature": _handle_insert_fillet_before_feature,
    "merge_consecutive_fillet_features": _handle_merge_consecutive_fillet_features,
    "remove_body": _handle_remove_body,
    "remove_objects": _handle_remove_objects,
    "repair_profile_chain": _handle_repair_profile_chain,
    "clear_model": _handle_clear_model,
}
