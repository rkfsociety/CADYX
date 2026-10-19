"""Rebuild command handlers and implementation.

Core owns lease/revision checks and atomic persistence; these methods operate on
the staged Document supplied by the shared CommandSystem.
"""
from __future__ import annotations

import math
from typing import Any, Callable
from .errors import ConflictError, ValidationError
from .models import Document
from .profile_segments import sketch_segment_profiles, sketch_segments
from .sketches import (closed_profile, closed_profiles, closed_spline_profile,
                       generated_gear_curve_profiles, open_path)


class RebuildCommands:
    def _profile_geometry(
        self, document: Document, feature: dict[str, Any], parameters: dict[str, Any], *,
        profile: list[dict[str, float]] | None = None, plane: str | None = None,
        curve_points: list[dict[str, float]] | None = None,
        profiles: list[list[dict[str, float]]] | None = None,
        curve_profiles: list[list[dict[str, float]] | None] | None = None,
        segment_profiles: list[list[list[float]] | None] | None = None,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        source_sketch_id = (feature.get("source_sketch_id") or parameters.get("source_sketch_id")
                            or parameters.get("sketch_id"))
        operation = feature.get("operation")
        if operation in {"pad_sketch_to_body", "rib_from_sketch", "pocket_sketch"}:
            sketch = document.find_object(source_sketch_id, "sketch")
            if sketch is None:
                raise ValidationError("Manufacturing feature source sketch is missing.")
            plane, profiles = closed_profiles(document.objects, source_sketch_id)
            depth = parameters["depth"]
            centered = parameters.get("centered", False)
            segment_profiles = sketch_segment_profiles(document.objects, source_sketch_id) if len(profiles) > 1 else None
            tool = (self.kernel.create_extruded_profile(profiles[0], plane=plane, depth=depth, centered=centered,
                                                       segments=sketch_segments(document.objects, source_sketch_id))
                    if len(profiles) == 1 else
                    self.kernel.create_extruded_profile_with_holes(
                        profiles, plane=plane, depth=depth, centered=centered,
                        segment_profiles=segment_profiles,
                    ))
            axis = sketch.get("section_normal_axis")
            offset = sketch.get("section_offset", 0.0)
            if offset:
                shift = [0.0, 0.0, 0.0]
                shift[2 if plane == "XY" and axis == "Z" else 1] = offset
                tool = self.kernel.translate_solid(tool, shift)
            source_body = document.find_object(feature.get("source_body_id"), "body")
            if source_body is None:
                raise ValidationError("Manufacturing feature source body is missing.")
            if operation == "pocket_sketch":
                return self.kernel.cut_solids(source_body["geometry"], tool)
            return self.kernel.fuse_profiles(source_body["geometry"], tool)
        if operation != "loft_sketches" and (profile is None or plane is None):
            spline_profile = closed_spline_profile(document.objects, source_sketch_id)
            if spline_profile is None:
                if operation == "extrude_sketch":
                    plane, profiles = closed_profiles(document.objects, source_sketch_id)
                    profile = profiles[0]
                    curve_profiles = generated_gear_curve_profiles(document.objects, source_sketch_id, profiles)
                    segment_profiles = sketch_segment_profiles(document.objects, source_sketch_id) if len(profiles) > 1 else None
                else:
                    plane, profile = closed_profile(document.objects, source_sketch_id)
                curve_points = None
            else:
                plane, profile, curve_points = spline_profile
        if operation == "extrude_sketch":
            if progress is not None:
                progress({"phase": "kernel_extrusion"})
            options = {"plane": plane, "depth": parameters["depth"],
                       "centered": parameters.get("centered", False)}
            if curve_points is not None:
                options["curve_points"] = curve_points
            elif profiles is not None and len(profiles) > 1:
                return self.kernel.create_extruded_profile_with_holes(
                    profiles, plane=plane, depth=parameters["depth"],
                    centered=parameters.get("centered", False), curve_profiles=curve_profiles,
                    segment_profiles=segment_profiles,
                )
            if curve_points is None:
                options["segments"] = sketch_segments(document.objects, source_sketch_id)
            return self.kernel.create_extruded_profile(profile, **options)
        if operation == "revolve_sketch":
            if progress is not None:
                progress({"phase": "kernel_revolution"})
            if plane != "XZ":
                raise ValidationError("Revolution requires a sketch on the XZ plane.")
            options = {"angle_degrees": parameters["angle_degrees"]}
            if curve_points is not None:
                options["curve_points"] = curve_points
            if curve_points is None:
                options["segments"] = sketch_segments(document.objects, source_sketch_id)
            return self.kernel.create_revolved_profile(profile, **options)
        if operation == "loft_sketches":
            sketch_ids = parameters.get("section_sketch_ids")
            if (not isinstance(sketch_ids, list) or len(sketch_ids) < 2
                    or not isinstance(parameters.get("section_offsets"), list)):
                raise ValidationError("Loft feature section references are invalid.")
            groups = parameters.get("section_sketch_groups", [[value] for value in sketch_ids])
            sections, offsets, section_axes = [], [], []
            for group in groups:
                group_profiles, group_offsets = [], []
                for sketch_id in group:
                    sketch = document.find_object(sketch_id)
                    if sketch is None:
                        raise ValidationError("Loft section sketch is missing.")
                    axis = sketch.get("section_normal_axis")
                    if sketch.get("plane") != ("XZ" if axis == "Y" else "XY" if axis == "Z" else None):
                        raise ValidationError("Loft section sketch plane does not match its normal axis.")
                    section_axes.append(axis)
                    _, section = closed_profile(document.objects, sketch_id)
                    group_profiles.append(section)
                    group_offsets.append(sketch.get("section_offset"))
                if not group_offsets or any(value != group_offsets[0] for value in group_offsets):
                    raise ValidationError("Loft section group has inconsistent section offsets.")
                sections.append(group_profiles)
                offsets.append(group_offsets[0])
            if len(set(section_axes)) != 1:
                raise ValidationError("Loft sections must use one normal axis.")
            section_axis = section_axes[0]
            if any(len(group) != 1 for group in sections):
                return self.kernel.create_multicontour_loft(sections, section_offsets=offsets, section_axis=section_axis)
            return self.kernel.create_lofted_profiles(
                [group[0] for group in sections], section_offsets=offsets, section_axis=section_axis,
            )
        raise ValidationError("Unsupported sketch feature operation.")

    def _rebuild_changed_features(
        self, document: Document, changed_sketches: set[str], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        by_id = {item["id"]: item for item in document.objects}
        changed_bodies: set[str] = set()
        features = [item for item in document.objects if item.get("type") == "feature"]
        for feature in features:
            source_sketch_ids = feature.get("source_sketch_ids", [])
            if feature.get("source_sketch_id"):
                source_sketch_ids = [feature["source_sketch_id"]]
            elif feature.get("operation") in {"rib_from_sketch", "pocket_sketch"}:
                sketch_id = (feature.get("parameters") or {}).get("sketch_id")
                source_sketch_ids = [sketch_id] if isinstance(sketch_id, str) else []
            if (feature.get("operation") not in {"extrude_sketch", "revolve_sketch", "sweep_sketch", "loft_sketches", "pad_sketch_to_body"}
                    and feature.get("operation") not in {"rib_from_sketch", "pocket_sketch"}
                    or not changed_sketches.intersection(source_sketch_ids)):
                continue
            if progress is not None:
                progress({"phase": "rebuild_features"})
            body = by_id.get(feature.get("body_id"))
            if body is None or body.get("type") != "body":
                raise ValidationError("Profile feature refers to a missing body.")
            if feature.get("operation") == "sweep_sketch":
                plane, path = open_path(document.objects, feature.get("source_sketch_id"))
                geometry = self.kernel.create_swept_pipe(
                    path, plane=plane, radius=feature["parameters"]["radius"],
                )
            elif feature.get("operation") == "loft_sketches":
                geometry = self._profile_geometry(document, feature, feature["parameters"], progress=progress)
            else:
                geometry = self._profile_geometry(document, feature, feature["parameters"], progress=progress)
            body["geometry"] = geometry
            feature["geometry"] = geometry
            changed_bodies.add(body["id"])
        self._rebuild_derived_features(document, changed_bodies, progress=progress)

    def _rebuild_derived_features(
        self, document: Document, changed_bodies: set[str], *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if not changed_bodies:
            return
        by_id = {item["id"]: item for item in document.objects}
        rebuilt_bodies = set(changed_bodies)
        features = [item for item in document.objects if item.get("type") == "feature"]
        for feature in features:
            operation = feature.get("operation")
            if operation in {"fillet_edges", "chamfer_edges"}:
                sources = {feature.get("source_body_id")}
            elif operation in {"linear_pattern_body", "circular_pattern_body", "hole", "boss", "pad_sketch_to_body",
                               "rib_from_sketch", "pocket_sketch",
                               "through_hole", "counterbore_hole", "countersink_hole", "threaded_hole"}:
                sources = {feature.get("source_body_id")}
            elif operation in {"fuse_bodies", "cut_bodies"}:
                sources = {feature.get("base_body_id"), feature.get("tool_body_id")}
            else:
                continue
            if not sources & rebuilt_bodies:
                continue
            body = by_id.get(feature.get("body_id"))
            if body is None or body.get("type") != "body":
                raise ValidationError("Derived feature refers to a missing result body.")
            if progress is not None:
                progress({"phase": "kernel_boolean_fuse" if operation == "fuse_bodies" else
                          "kernel_boolean_cut" if operation == "cut_bodies" else
                          "kernel_fillet" if operation == "fillet_edges" else
                          "kernel_chamfer" if operation == "chamfer_edges" else operation,
                          "feature_id": feature.get("id"), "feature_name": body.get("name")})
            if operation == "fillet_edges":
                source = by_id.get(feature.get("source_body_id"))
                if source is None or source.get("type") != "body":
                    raise ValidationError("Fillet feature refers to a missing source body.")
                edges = feature.get("edge_indices", [])
                source_topology = self.kernel.topology(source["geometry"])
                edges = self._resolve_feature_edges(feature, source["geometry"], edges, source_topology)
                feature["edge_indices"] = list(edges)
                if progress is not None:
                    topology_edges = source_topology.get("edges", [])
                    selected_edges = [edge for edge in topology_edges
                                      if isinstance(edge, dict) and edge.get("index") in set(edges)]
                    progress({"phase": "kernel_fillet", "feature_id": feature["id"],
                              "feature_name": body.get("name"), "resolved_edge_indices": list(edges),
                              "resolved_edges": [{key: edge[key] for key in
                                  ("index", "curve", "center", "length", "start", "end", "direction")
                                  if key in edge} for edge in selected_edges]})
                geometry = self.kernel.create_fillet(source["geometry"],
                    edge_indices=edges, radius=feature["parameters"]["radius"])
            elif operation == "chamfer_edges":
                source = by_id.get(feature.get("source_body_id"))
                if source is None or source.get("type") != "body":
                    raise ValidationError("Chamfer feature refers to a missing source body.")
                from .advanced_features import _advanced_native
                edges = feature["parameters"].get("edge_indices", [])
                source_topology = self.kernel.topology(source["geometry"])
                edges = self._resolve_feature_edges(feature, source["geometry"], edges, source_topology)
                feature["parameters"]["edge_indices"] = list(edges)
                geometry = _advanced_native(source["geometry"], 2,
                    [feature["parameters"]["distance"]], edges)
            elif operation in {"pad_sketch_to_body", "rib_from_sketch", "pocket_sketch"}:
                geometry = self._profile_geometry(document, feature, feature["parameters"], progress=progress)
            elif operation in {"linear_pattern_body", "circular_pattern_body", "hole", "boss"}:
                source = by_id.get(feature.get("source_body_id"))
                if source is None or source.get("type") != "body":
                    raise ValidationError("Advanced feature refers to a missing source body.")
                parameters = feature["parameters"]
                if operation == "linear_pattern_body":
                    operation_id = 6
                    values = [float(parameters["count"]), *parameters["offset"]]
                elif operation == "circular_pattern_body":
                    operation_id = 7
                    values = [float(parameters["count"]), *parameters["axis_origin"],
                              *parameters["axis_direction"], parameters["angle_step_degrees"]]
                else:
                    operation_id = 9 if operation == "hole" else 10
                    values = [*parameters["origin"], *parameters["axis"],
                              parameters["diameter"], parameters["depth"]]
                from .advanced_features import _advanced_native
                geometry = _advanced_native(source["geometry"], operation_id, values)
            elif operation in {"through_hole", "counterbore_hole", "countersink_hole", "threaded_hole"}:
                source = by_id.get(feature.get("source_body_id"))
                if source is None or source.get("type") != "body":
                    raise ValidationError("Hole feature refers to a missing source body.")
                from .agent_authoring import _agent_native, _cylinder_cut
                from .advanced_features import _advanced_native
                parameters = feature["parameters"]
                origin, axis = parameters["origin"], parameters["axis"]
                if operation == "through_hole":
                    geometry, _, _ = _cylinder_cut(self.kernel, source["geometry"], origin=origin,
                        axis=axis, diameter=parameters["diameter"], through=True, depth=None,
                        label="Through hole")
                elif operation == "counterbore_hole":
                    geometry, _, _ = _cylinder_cut(self.kernel, source["geometry"], origin=origin,
                        axis=axis, diameter=parameters["hole_diameter"], through=parameters["through"],
                        depth=parameters.get("hole_depth"), label="Counterbore main hole")
                    geometry = _advanced_native(geometry, 9, [*origin, *axis,
                        parameters["counterbore_diameter"], parameters["counterbore_depth"]])
                elif operation == "countersink_hole":
                    geometry, _, _ = _cylinder_cut(self.kernel, source["geometry"], origin=origin,
                        axis=axis, diameter=parameters["hole_diameter"], through=parameters["through"],
                        depth=parameters.get("hole_depth"), label="Countersink main hole")
                    sink_depth = (parameters["countersink_diameter"] - parameters["hole_diameter"]) / 2.0 / math.tan(math.radians(parameters["angle_degrees"]) / 2.0)
                    geometry = _agent_native(geometry, 1, [*origin, *axis,
                        parameters["countersink_diameter"], parameters["hole_diameter"], sink_depth])
                else:
                    from .agent_authoring import _rotation_from_z
                    from .thread_profiles import thread_tool
                    thread = thread_tool(self.kernel, major_diameter=parameters["major_diameter"],
                        pitch=parameters["pitch"], length=parameters["length"],
                        handedness=parameters["handedness"], thread_profile=parameters.get("thread_profile"))
                    rotation_axis, angle, _ = _rotation_from_z(axis)
                    tool = _agent_native(thread, 2, [*rotation_axis, angle, *origin])
                    geometry = self.kernel.cut_solids(source["geometry"], tool)
            elif operation == "fuse_bodies":
                base, tool = by_id.get(feature.get("base_body_id")), by_id.get(feature.get("tool_body_id"))
                if any(item is None or item.get("type") != "body" for item in (base, tool)):
                    raise ValidationError("Boolean fuse feature refers to a missing source body.")
                geometry = self.kernel.fuse_profiles(base["geometry"], tool["geometry"])
            else:
                base, tool = by_id.get(feature.get("base_body_id")), by_id.get(feature.get("tool_body_id"))
                if any(item is None or item.get("type") != "body" for item in (base, tool)):
                    raise ValidationError("Boolean cut feature refers to a missing source body.")
                geometry = self.kernel.cut_solids(base["geometry"], tool["geometry"])
            body["geometry"] = geometry
            feature["geometry"] = geometry
            rebuilt_bodies.add(body["id"])

    def _resolve_feature_edges(
        self, feature: dict[str, Any], geometry: dict[str, Any], fallback_indices: list[int],
        topology: dict[str, Any] | None = None,
    ) -> list[int]:
        if feature.get("edge_keys"):
            from .semantic_selection import _resolve
            try:
                return _resolve(self.kernel, geometry, keys=feature["edge_keys"], collection="edges",
                                topology=topology)
            except (ConflictError, ValidationError):
                # Semantic keys include geometric coordinates, so dimension changes
                # can invalidate them even when the selected design edge still exists.
                # Fall back to the stored geometric intent and require a unique match.
                pass
        references = feature.get("edge_references")
        if references:
            from .semantic_selection import resolve_edge_references
            return resolve_edge_references(self.kernel, geometry, references, topology=topology)
        return fallback_indices
