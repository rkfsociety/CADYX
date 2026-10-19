"""Geometry-kernel boundary and CADYX kernel adapters."""

from __future__ import annotations

import base64
import math
import zlib
from typing import Any, Callable, Protocol

from .errors import ValidationError
from .mesh_geometry import tessellate_mesh_geometry
from .native_kernel import NativeKernel
from .kernel_descriptors import (
    GeometryDescriptor, GeometryDescriptorValidation,
    BRepDescriptor, BooleanDescriptor, BoxDescriptor, CircleDescriptor, FilletDescriptor, HoledProfileDescriptor, LoftDescriptor, MulticontourLoftDescriptor, PipeDescriptor, ProfileDescriptor, RuledShellDescriptor, ThreadDescriptor,
)
from .kernel_primitive import PrimitiveKernelAdapter as PrimitiveKernelAdapter
from .kernel_profiles import (
    _box_dimensions as _box_dimensions,
    _profile_values as _profile_values,
    _spline_control_values as _spline_control_values,
    _positive_amount as _positive_amount,
    _positive_or_signed_coordinate as _positive_or_signed_coordinate,
    _finite_coordinate as _finite_coordinate,
    _ruled_patch_values as _ruled_patch_values,
    _loft_contour_signature as _loft_contour_signature,
    _match_loft_contours as _match_loft_contours,
    _resample_loft_profile as _resample_loft_profile,
    _canonical_loft_profile as _canonical_loft_profile,
    _align_loft_profile as _align_loft_profile,
)


_MAX_OCCT_THREAD_SECTIONS = (1 << 31) - 2


class GeometryKernel(Protocol):
    def create_box(self, *, width: float, height: float, depth: float) -> BoxDescriptor: ...

    def create_extruded_profile(
        self, profile: list[dict[str, float]], *, plane: str, depth: float, centered: bool = False,
        curve_points: list[dict[str, float]] | None = None,
        segments: list[list[float]] | None = None,
    ) -> ProfileDescriptor: ...

    def create_extruded_profile_with_holes(
        self, profiles: list[list[dict[str, float]]], *, plane: str, depth: float,
        centered: bool = False,
        curve_profiles: list[list[dict[str, float]] | None] | None = None,
        segment_profiles: list[list[list[float]] | None] | None = None,
    ) -> HoledProfileDescriptor: ...

    def create_extruded_circle(
        self, center: dict[str, float], radius: float, *, plane: str, depth: float,
        centered: bool = False,
    ) -> CircleDescriptor: ...

    def translate_solid(self, geometry: GeometryDescriptor, offset: list[float]) -> BRepDescriptor: ...

    def create_lofted_profiles(
        self, sections: list[list[dict[str, float]]], *, section_offsets: list[float],
    ) -> LoftDescriptor: ...
    def create_multicontour_loft(
        self, section_groups: list[list[list[dict[str, float]]]], *, section_offsets: list[float],
    ) -> MulticontourLoftDescriptor: ...
    def create_ruled_shell(
        self, patches: list[dict[str, list[list[float]]]], *,
        sewing_tolerance: float = 1e-3, compute_volume: bool = True,
    ) -> RuledShellDescriptor: ...

    def create_revolved_profile(
        self, profile: list[dict[str, float]], *, angle_degrees: float,
        curve_points: list[dict[str, float]] | None = None,
        segments: list[list[float]] | None = None,
    ) -> ProfileDescriptor: ...

    def fuse_profiles(self, base: GeometryDescriptor, tool: GeometryDescriptor) -> BooleanDescriptor: ...

    def cut_solids(self, base: GeometryDescriptor, tool: GeometryDescriptor) -> BooleanDescriptor: ...

    def create_swept_pipe(
        self, path: list[dict[str, float]], *, plane: str, radius: float,
    ) -> PipeDescriptor: ...

    def create_metric_thread(
        self, *, major_diameter: float, pitch: float, length: float, handedness: str = "right",
    ) -> ThreadDescriptor: ...

    def create_imported_step(self, source: bytes) -> tuple[BRepDescriptor, dict[str, Any]]: ...

    def extract_section_profile(
        self, geometry: GeometryDescriptor, *, normal_axis: str, offset: float, max_points: int,
    ) -> list[list[dict[str, float]]]: ...

    def export_delivery(
        self, geometry: GeometryDescriptor, *, include_stl: bool,
        linear_deflection: float, angular_deflection: float,
    ) -> dict[str, Any]: ...

    def modify_imported_brep(
        self, base: GeometryDescriptor, *, operation: str, parameters: list[float],
    ) -> BRepDescriptor: ...

    def create_fillet(
        self, base: GeometryDescriptor, *, edge_indices: list[int], radius: float,
    ) -> FilletDescriptor: ...

    def topology(self, geometry: GeometryDescriptor) -> dict[str, Any]: ...

    def metrics(self, geometry: GeometryDescriptor) -> dict[str, Any]: ...

    def minimum_distance(
        self, first: GeometryDescriptor, second: GeometryDescriptor, *,
        progress: Callable[[str, float], None] | None = None,
    ) -> dict[str, Any]: ...

    def minimum_distance_placed(
        self, first: GeometryDescriptor, second: GeometryDescriptor, *,
        first_translation: list[float], first_rotation: list[float],
        second_translation: list[float], second_rotation: list[float],
        progress: Callable[[str, float], None] | None = None,
    ) -> dict[str, Any]: ...

    def compare_preserved_volume(
        self, reference: GeometryDescriptor, candidate: GeometryDescriptor,
    ) -> dict[str, Any]: ...

    def sample_minimum_thickness(
        self, geometry: GeometryDescriptor, *,
        minimum_thickness: float, tolerance: float,
        progress: Callable[[str, float], None] | None = None,
    ) -> dict[str, Any]: ...

    def certify_global_minimum_thickness(
        self, geometry: GeometryDescriptor, *,
        progress: Callable[[str, float], None] | None = None,
    ) -> dict[str, Any]: ...

    def validate(self, geometry: GeometryDescriptor) -> None: ...

    def tessellate(self, geometry: GeometryDescriptor) -> dict[str, Any]: ...


class OCCTKernelAdapter(GeometryDescriptorValidation):
    """Build and validate box B-reps with Open CASCADE Technology."""

    kernel_id = "occt-v1"
    _accepted_kernel_ids = {kernel_id, "primitive-v1"}

    def tessellate(self, geometry: GeometryDescriptor) -> dict[str, Any]:
        """Return kernel-derived triangles in model coordinates, without native objects."""
        # Mesh tessellation already verifies the compressed payload while decoding it.
        # Avoid serializing and validating a large scan mesh a second time here.
        if isinstance(geometry, dict) and geometry.get("kernel") == "cadyx-mesh-v1":
            return tessellate_mesh_geometry(geometry)
        checked = self.normalize_geometry(geometry)
        result = NativeKernel().geometry_graph(checked, mesh=True)
        mesh = {key: result[key] for key in (
            "positions", "triangles", "edge_positions", "edge_lines", "face_triangle_ranges",
        )}
        selected_faces = geometry.get("selected_face_indices", result.get("generated_face_indices", []))
        mesh["selected_face_indices"] = selected_faces if isinstance(selected_faces, list) else []
        return mesh

    def create_box(self, *, width: float, height: float, depth: float) -> BoxDescriptor:
        dimensions = _box_dimensions(width, height, depth)
        volume = self._box_volume(dimensions)
        return {
            "kernel": self.kernel_id,
            "kind": "box",
            "dimensions": dimensions,
            "volume": volume,
        }

    def create_extruded_profile(
        self, profile: list[dict[str, float]], *, plane: str, depth: float, centered: bool = False,
        curve_points: list[dict[str, float]] | None = None,
        segments: list[list[float]] | None = None,
    ) -> ProfileDescriptor:
        if type(centered) is not bool:
            raise ValidationError("centered must be a boolean.")
        return self._create_profile_geometry(profile, plane=plane, operation=0,
                                             amount=_positive_amount(depth, "depth"), centered=centered,
                                             curve_points=curve_points, segments=segments)

    def create_extruded_profile_with_holes(
        self, profiles: list[list[dict[str, float]]], *, plane: str, depth: float,
        centered: bool = False,
        curve_profiles: list[list[dict[str, float]] | None] | None = None,
        segment_profiles: list[list[list[float]] | None] | None = None,
    ) -> HoledProfileDescriptor:
        if type(centered) is not bool or not isinstance(profiles, list) or len(profiles) < 2:
            raise ValidationError("A multi-contour extrusion needs an outer contour and at least one hole.")
        normalized = [_profile_values(profile, plane)[0] for profile in profiles]
        if curve_profiles is not None:
            if not isinstance(curve_profiles, list) or len(curve_profiles) != len(normalized):
                raise ValidationError("Curved profile controls must match the contour list.")
            normalized_curves = [None if points is None else _spline_control_values(points, plane)
                                 for points in curve_profiles]
        else:
            normalized_curves = [None] * len(normalized)
        if segment_profiles is not None:
            if not isinstance(segment_profiles, list) or len(segment_profiles) != len(normalized):
                raise ValidationError("Analytic segment profiles must match the contour list.")
            from .profile_segments import segment_values
            normalized_segments = [
                None if segments is None else segment_values(segments)
                for segments in segment_profiles
            ]
        else:
            normalized_segments = [None] * len(normalized)
        if any(curves is not None and segments is not None
               for curves, segments in zip(normalized_curves, normalized_segments, strict=True)):
            raise ValidationError("A contour cannot combine spline controls with analytic segments.")

        def area(profile):
            return sum(point["x"] * profile[(i + 1) % len(profile)]["y"]
                       - profile[(i + 1) % len(profile)]["x"] * point["y"]
                       for i, point in enumerate(profile))

        outer_index = max(range(len(normalized)), key=lambda index: abs(area(normalized[index])))
        holes = [profile for index, profile in enumerate(normalized) if index != outer_index]
        hole_indices = [index for index in range(len(normalized)) if index != outer_index]

        def inside(point, contour):
            x, y, contained = point["x"], point["y"], False
            for index, first in enumerate(contour):
                second = contour[(index + 1) % len(contour)]
                if (first["y"] > y) != (second["y"] > y):
                    crossing = ((second["x"] - first["x"]) * (y - first["y"])
                                / (second["y"] - first["y"]) + first["x"])
                    if x < crossing:
                        contained = not contained
            return contained

        if any(not all(inside(point, normalized[outer_index]) for point in hole) for hole in holes):
            raise ValidationError("Every inner profile contour must lie completely inside the outer contour.")

        def circle(profile):
            if len(profile) < 16:
                return None
            x = sum(point["x"] for point in profile) / len(profile)
            y = sum(point["y"] for point in profile) / len(profile)
            radii = [math.hypot(point["x"] - x, point["y"] - y) for point in profile]
            radius = sum(radii) / len(radii)
            if radius <= 1e-12 or max(radii) - min(radii) > max(1e-7, radius * 1e-6):
                return None
            return {"x": x, "y": y}, radius

        amount = _positive_amount(depth, "depth")

        def extrude(index):
            profile = normalized[index]
            segments = normalized_segments[index]
            controls = normalized_curves[index]
            if segments is not None:
                return self.create_extruded_profile(profile, plane=plane, depth=amount,
                                                    centered=centered, segments=segments)
            if controls is not None:
                return self.create_extruded_profile(profile, plane=plane, depth=amount,
                                                    centered=centered, curve_points=controls)
            circular = circle(profile)
            if circular is not None:
                return self.create_extruded_circle(circular[0], circular[1], plane=plane,
                                                   depth=amount, centered=centered)
            return self.create_extruded_profile(profile, plane=plane, depth=amount, centered=centered)

        solid = extrude(outer_index)
        for hole_index in hole_indices:
            solid = self.cut_solids(solid, extrude(hole_index))
        ordered_curves = [normalized_curves[outer_index],
                          *(normalized_curves[index] for index in hole_indices)]
        ordered_segments = [normalized_segments[outer_index],
                            *(normalized_segments[index] for index in hole_indices)]
        geometry = {"kernel": self.kernel_id, "kind": "profile_with_holes", "plane": plane,
                    "profile": normalized[outer_index],
                    "holes": holes, "depth": amount, "solid": solid,
                    "volume": solid["volume"]}
        if any(points is not None for points in ordered_curves):
            geometry["curve_profiles"] = ordered_curves
        if any(segments is not None for segments in ordered_segments):
            geometry["segment_profiles"] = ordered_segments
        if centered:
            geometry["centered"] = True
        return geometry

    def create_extruded_circle(
        self, center: dict[str, float], radius: float, *, plane: str, depth: float,
        centered: bool = False,
    ) -> CircleDescriptor:
        if plane not in {"XY", "XZ"} or type(centered) is not bool:
            raise ValidationError("An analytic circular extrusion needs an XY or XZ plane and boolean centered state.")
        if (not isinstance(center, dict) or set(center) != {"x", "y"}
                or any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or not math.isfinite(value) for value in center.values())):
            raise ValidationError("Circle center must have finite x and y coordinates.")
        radius = _positive_amount(radius, "radius")
        depth = _positive_amount(depth, "depth")
        geometry = {"kernel": self.kernel_id, "kind": "extruded_circle", "plane": plane,
                    "center": {"x": float(center["x"]), "y": float(center["y"])},
                    "radius": radius, "depth": depth,
                    "volume": math.pi * radius * radius * depth}
        if centered:
            geometry["centered"] = True
        return geometry

    def translate_solid(self, geometry: GeometryDescriptor, offset: list[float]) -> BRepDescriptor:
        geometry = self.normalize_geometry(geometry)
        if (not isinstance(offset, list) or len(offset) != 3
                or any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or not math.isfinite(value) for value in offset)):
            raise ValidationError("A solid translation needs three finite coordinates.")
        native = NativeKernel()
        brep, metrics = native.export_geometry(geometry)
        source = {"kernel": self.kernel_id, "kind": "imported_brep",
                  "data": base64.b64encode(zlib.compress(brep, level=9)).decode("ascii"),
                  "volume": metrics["volume"], "brep_valid": True}
        return self.modify_imported_brep(source, operation="rigid_transform",
                                         parameters=[0.0, 0.0, 1.0, 0.0, *offset])

    def create_revolved_profile(
        self, profile: list[dict[str, float]], *, angle_degrees: float,
        curve_points: list[dict[str, float]] | None = None,
        segments: list[list[float]] | None = None,
    ) -> ProfileDescriptor:
        angle = _positive_amount(angle_degrees, "angle_degrees", maximum=360.0)
        return self._create_profile_geometry(profile, plane="XZ", operation=1,
                                             amount=math.radians(angle), angle_degrees=angle,
                                             curve_points=curve_points, segments=segments)

    def create_lofted_profiles(
        self, sections: list[list[dict[str, float]]], *, section_offsets: list[float],
        alignment: str = "minimum-displacement-v2",
        compute_volume: bool = True,
        section_axis: str = "Y",
    ) -> LoftDescriptor:
        if (not isinstance(sections, list) or len(sections) < 2
                or not isinstance(section_offsets, list) or len(section_offsets) != len(sections)):
            raise ValidationError("A loft needs at least two paired sketch sections and offsets.")
        if alignment not in {
                "independent-start-v1", "minimum-displacement-v1", "minimum-displacement-v2"}:
            raise ValidationError("Loft profile alignment mode is unsupported.")
        if type(compute_volume) is not bool:
            raise ValidationError("compute_volume must be a boolean.")
        if section_axis not in {"Y", "Z"}:
            raise ValidationError("Loft section_axis must be Y or Z.")
        plane = "XZ" if section_axis == "Y" else "XY"
        raw_sections = []
        offsets = []
        for section, offset in zip(sections, section_offsets, strict=True):
            points, _ = _profile_values(section, plane)
            raw_sections.append(points)
            offsets.append(_finite_coordinate(offset, "Loft section offset"))
        point_count = max(len(section) for section in raw_sections)
        if alignment == "independent-start-v1":
            if all(len(section) == point_count for section in raw_sections):
                normalized = [_canonical_loft_profile(section) for section in raw_sections]
            else:
                normalized = [_resample_loft_profile(section, point_count) for section in raw_sections]
        else:
            normalized = []
            for section in raw_sections:
                profile = (_resample_loft_profile(section, point_count)
                           if len(section) != point_count else list(section))
                area = sum(profile[i]["x"] * profile[(i + 1) % len(profile)]["y"]
                           - profile[(i + 1) % len(profile)]["x"] * profile[i]["y"]
                           for i in range(len(profile)))
                if area < 0:
                    profile.reverse()
                if normalized:
                    reference = normalized[0] if alignment == "minimum-displacement-v2" else normalized[-1]
                    profile = _align_loft_profile(profile, reference)
                else:
                    profile = _canonical_loft_profile(profile)
                normalized.append(profile)
        if any(right <= left for left, right in zip(offsets, offsets[1:])):
            raise ValidationError("Loft section offsets must be strictly increasing.")
        payload = {"kernel": self.kernel_id, "kind": "lofted_profiles", "sections": normalized,
                   "section_offsets": offsets, "profile_alignment": alignment}
        if section_axis != "Y":
            payload["section_axis"] = section_axis
        payload["volume"] = NativeKernel().geometry_graph(payload)["volume"] if compute_volume else 0.0
        return payload

    def create_multicontour_loft(
        self, section_groups: list[list[list[dict[str, float]]]], *, section_offsets: list[float],
        compute_volume: bool = True,
    ) -> MulticontourLoftDescriptor:
        """Build and fuse adjacent-station loft branches when section contour counts change."""
        if (not isinstance(section_groups, list) or len(section_groups) < 2
                or not isinstance(section_offsets, list) or len(section_offsets) != len(section_groups)):
            raise ValidationError("A grouped loft needs at least two paired stations and offsets.")
        if type(compute_volume) is not bool:
            raise ValidationError("compute_volume must be a boolean.")
        offsets = [_finite_coordinate(value, "Loft section offset") for value in section_offsets]
        if any(right <= left for left, right in zip(offsets, offsets[1:])):
            raise ValidationError("Loft section offsets must be strictly increasing.")

        normalized_groups: list[list[tuple[list[dict[str, float]], float, float, float]]] = []
        for group in section_groups:
            if not isinstance(group, list) or not group:
                raise ValidationError("Each loft station needs one or more closed contour profiles.")
            normalized_group = []
            for section in group:
                points, _ = _profile_values(section, "XZ")
                area, center_x, center_y, _ = _loft_contour_signature(points)
                normalized_group.append((points, area, center_x, center_y))
            normalized_groups.append(normalized_group)

        all_branches = []
        for index, (left, right) in enumerate(zip(normalized_groups, normalized_groups[1:], strict=False)):
            section_branches = _match_loft_contours(left, right)
            for left_index, right_index in section_branches:
                left_profile, right_profile = left[left_index], right[right_index]
                loft = self.create_lofted_profiles(
                    [left_profile[0], right_profile[0]],
                    section_offsets=[offsets[index], offsets[index + 1]],
                    compute_volume=False,
                )
                all_branches.append(loft)
        if not all_branches:
            raise ValidationError("Grouped loft produced no branch geometry.")
        result = {"kernel": self.kernel_id, "kind": "multicontour_loft",
                  "branches": all_branches, "volume": 0.0}
        result["volume"] = (NativeKernel().geometry_graph(result)["volume"]
                             if compute_volume else 0.0)
        return result

    def create_ruled_shell(
        self, patches: list[dict[str, list[list[float]]]], *,
        sewing_tolerance: float = 1e-3, compute_volume: bool = True,
    ) -> RuledShellDescriptor:
        normalized = _ruled_patch_values(patches)
        tolerance = _finite_coordinate(sewing_tolerance, "Ruled shell sewing tolerance")
        if not 1e-7 <= tolerance <= 1.0:
            raise ValidationError("Ruled shell sewing tolerance must be between 1e-7 and 1 mm.")
        if type(compute_volume) is not bool:
            raise ValidationError("compute_volume must be a boolean.")
        result = {"kernel": self.kernel_id, "kind": "ruled_shell", "patches": normalized,
                  "sewing_tolerance": tolerance, "volume": 0.0}
        if compute_volume:
            result["volume"] = NativeKernel().geometry_graph(result)["volume"]
        return result

    @staticmethod
    def _create_profile_geometry(
        profile: list[dict[str, float]], *, plane: str, operation: int,
        amount: float, angle_degrees: float | None = None, centered: bool = False,
        curve_points: list[dict[str, float]] | None = None,
        segments: list[list[float]] | None = None,
    ) -> ProfileDescriptor:
        normalized, _ = _profile_values(profile, plane)
        amount = _positive_amount(amount, "operation amount")
        if segments is not None:
            if curve_points is not None:
                raise ValidationError("A profile cannot combine spline and analytic segment descriptors.")
            from .profile_segments import segment_values
            segments = segment_values(segments)
            descriptor = {"kind": "extruded_profile" if operation == 0 else "revolved_profile",
                          "profile": normalized, "profile_segments": segments, "plane": plane, "centered": centered}
            descriptor["depth" if operation == 0 else "angle_degrees"] = amount if operation == 0 else angle_degrees
            result = NativeKernel().geometry_graph(descriptor)
        elif curve_points is None:
            result = NativeKernel().profile(normalized, operation=operation,
                                            plane=plane, amount=amount, centered=centered)
        else:
            curve_points = _spline_control_values(curve_points, plane)
            descriptor = {"kind": "extruded_profile" if operation == 0 else "revolved_profile",
                          "profile": normalized, "profile_curve": "closed_periodic_bspline",
                          "profile_curve_points": curve_points, "plane": plane, "centered": centered}
            descriptor["depth" if operation == 0 else "angle_degrees"] = (
                amount if operation == 0 else angle_degrees)
            result = NativeKernel().geometry_graph(descriptor)
        geometry = {
            "kernel": OCCTKernelAdapter.kernel_id,
            "kind": "extruded_profile" if operation == 0 else "revolved_profile",
            "plane": plane,
            "profile": normalized,
            "volume": result["volume"],
        }
        if segments is not None:
            geometry["profile_segments"] = segments
        if curve_points is not None:
            geometry["profile_curve"] = "closed_periodic_bspline"
            geometry["profile_curve_points"] = curve_points
        if operation == 0:
            geometry["depth"] = amount
            if centered:
                geometry["centered"] = True
        else:
            geometry["angle_degrees"] = angle_degrees
        return geometry

    def fuse_profiles(self, base: GeometryDescriptor, tool: GeometryDescriptor) -> BooleanDescriptor:
        base = self.normalize_geometry(base)
        tool = self.normalize_geometry(tool)
        kind = "fused_profiles" if base.get("kind") in {"extruded_profile", "revolved_profile"} \
            and tool.get("kind") in {"extruded_profile", "revolved_profile"} else "fused_solids"
        result = NativeKernel().geometry_graph({"kind": kind, "base": base, "tool": tool})
        return {
            "kernel": self.kernel_id,
            "kind": kind,
            "base": base,
            "tool": tool,
            "volume": result["volume"],
        }

    def cut_solids(self, base: GeometryDescriptor, tool: GeometryDescriptor) -> BooleanDescriptor:
        base = self.normalize_geometry(base)
        tool = self.normalize_geometry(tool)
        result = NativeKernel().geometry_graph({"kind": "cut_solids", "base": base, "tool": tool})
        if result["volume"] >= base["volume"] - max(1e-9, base["volume"] * 1e-12):
            raise ValidationError("Boolean cut must remove material from the base solid.")
        return {"kernel": self.kernel_id, "kind": "cut_solids", "base": base,
                "tool": tool, "volume": result["volume"]}

    def create_swept_pipe(
        self, path: list[dict[str, float]], *, plane: str, radius: float,
    ) -> PipeDescriptor:
        if plane not in {"XY", "XZ"} or not isinstance(path, list) or len(path) < 3:
            raise ValidationError("A pipe path needs at least 3 points on an XY or XZ sketch.")
        normalized: list[dict[str, float]] = []
        for point in path:
            if not isinstance(point, dict) or set(point) != {"x", "y"}:
                raise ValidationError("Every pipe path point must contain x and y coordinates.")
            values = {axis: _positive_or_signed_coordinate(point[axis]) for axis in ("x", "y")}
            if normalized and math.hypot(values["x"] - normalized[-1]["x"],
                                         values["y"] - normalized[-1]["y"]) <= 1e-7:
                raise ValidationError("Pipe path points must have distinct consecutive endpoints.")
            normalized.append(values)
        radius = _positive_amount(radius, "radius")
        geometry = {"kernel": self.kernel_id, "kind": "swept_pipe", "plane": plane,
                    "path": normalized, "radius": radius}
        geometry["volume"] = NativeKernel().geometry_graph(geometry)["volume"]
        return geometry

    def create_metric_thread(
        self, *, major_diameter: float, pitch: float, length: float, handedness: str = "right",
    ) -> ThreadDescriptor:
        major_diameter = _positive_amount(major_diameter, "major_diameter")
        pitch = _positive_amount(pitch, "pitch")
        length = _positive_amount(length, "length")
        if length < pitch:
            raise ValidationError("Thread length must be at least one pitch.")
        center_span = length - 2.0 * (5.0 * pitch / 12.0)
        section_count_value = (center_span / pitch) * 24.0
        if not math.isfinite(section_count_value):
            raise ValidationError("Thread length exceeds the section count representable by Open CASCADE.")
        section_count = math.ceil(section_count_value)
        if section_count > _MAX_OCCT_THREAD_SECTIONS:
            raise ValidationError("Thread length exceeds the section count representable by Open CASCADE.")
        if major_diameter <= 1.226869 * pitch:
            raise ValidationError("major_diameter must leave a positive ISO metric thread minor diameter.")
        if not isinstance(handedness, str) or handedness not in {"right", "left"}:
            raise ValidationError("handedness must be 'right' or 'left'.")
        geometry = {
            "kernel": self.kernel_id,
            "kind": "metric_thread",
            "major_diameter": major_diameter,
            "pitch": pitch,
            "length": length,
            "handedness": handedness,
        }
        geometry["volume"] = NativeKernel().geometry_graph(geometry)["volume"]
        return geometry

    def create_imported_step(self, source: bytes) -> tuple[BRepDescriptor, dict[str, Any]]:
        imported = NativeKernel().import_step(source)
        try:
            brep = base64.b64decode(imported["brep_data"], validate=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("OCCT returned an invalid imported B-rep.") from exc
        geometry = {
            "kernel": self.kernel_id,
            "kind": "imported_brep",
            "data": base64.b64encode(zlib.compress(brep, level=9)).decode("ascii"),
            "volume": imported["volume"],
            "brep_valid": imported["brep_valid"],
        }
        metrics = {key: imported[key] for key in (
            "volume", "surface_area", "bounds", "topology", "brep_valid", "repaired",
        )}
        roundtrip = NativeKernel().geometry_graph(geometry)
        bounds_match = all(
            math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)
            for side in ("min", "max")
            for a, b in zip(roundtrip["bounds"][side], metrics["bounds"][side], strict=True)
        )
        if (roundtrip["topology"] != metrics["topology"]
                or not bounds_match
                or roundtrip["brep_valid"] != metrics["brep_valid"]
                or not math.isclose(roundtrip["volume"], metrics["volume"], rel_tol=1e-12, abs_tol=1e-12)
                or not math.isclose(roundtrip["surface_area"], metrics["surface_area"], rel_tol=1e-12, abs_tol=1e-12)):
            raise ValidationError("Imported B-rep did not preserve the STEP model geometry.")
        return geometry, metrics

    def extract_section_profile(
        self, geometry: GeometryDescriptor, *, normal_axis: str, offset: float, max_points: int,
    ) -> list[list[dict[str, float]]]:
        if not isinstance(geometry, dict) or geometry.get("kind") != "imported_brep":
            raise ValidationError("Section extraction requires an imported OCCT B-rep.")
        try:
            compressed = base64.b64decode(geometry["data"], validate=True)
            decoder = zlib.decompressobj()
            brep = decoder.decompress(compressed, 64 * 1024 * 1024 + 1)
            if (len(brep) > 64 * 1024 * 1024 or not decoder.eof
                    or decoder.unused_data or decoder.unconsumed_tail):
                raise ValueError("invalid compressed B-rep payload")
        except (KeyError, TypeError, ValueError, zlib.error) as exc:
            raise ValidationError("Imported B-rep data is invalid or exceeds the 64 MB limit.") from exc
        result = NativeKernel().section_profile(
            brep, normal_axis=normal_axis, offset=offset, max_points=max_points,
        )
        profiles = result.get("profiles") if isinstance(result, dict) else None
        if (not isinstance(result, dict) or result.get("closed") is not True
                or not isinstance(profiles, list) or not profiles
                or sum(len(profile) for profile in profiles if isinstance(profile, list)) > max_points
                or any(not isinstance(profile, list) or len(profile) < 3
                       or any(not isinstance(point, dict) or set(point) != {"x", "y"}
                              or any(isinstance(point[key], bool) or not isinstance(point[key], (int, float))
                                     or not math.isfinite(point[key]) for key in ("x", "y"))
                              for point in profile)
                       for profile in profiles)):
            raise ValidationError("OCCT returned invalid closed section profiles.")
        return profiles

    def export_delivery(
        self,
        geometry: GeometryDescriptor,
        *,
        include_stl: bool,
        linear_deflection: float = 0.1,
        angular_deflection: float = 0.5,
    ) -> dict[str, Any]:
        if type(include_stl) is not bool:
            raise ValidationError("stl must be a boolean.")
        linear_deflection = _positive_amount(linear_deflection, "linear_deflection")
        angular_deflection = _positive_amount(angular_deflection, "angular_deflection", maximum=1.0)
        if linear_deflection < 0.001 or angular_deflection < 0.01:
            raise ValidationError("STL deflections must be at least 0.001 linear and 0.01 radians.")
        geometry = self.normalize_geometry(geometry)
        native = NativeKernel()
        brep, source_metrics = native.export_geometry(geometry)
        result: dict[str, Any] = {
            "step": native.export_step_brep(brep),
            "stl": None,
            "metrics": source_metrics,
            "stl_metrics": None,
            "kernel_version": native.version,
        }
        if include_stl:
            result["stl"], result["stl_metrics"] = native.export_stl_brep(
                brep, linear_deflection=linear_deflection, angular_deflection=angular_deflection,
            )
        return result

    def modify_imported_brep(
        self, base: GeometryDescriptor, *, operation: str, parameters: list[float],
    ) -> BRepDescriptor:
        if not isinstance(base, dict) or base.get("kind") != "imported_brep":
            raise ValidationError("Only imported OCCT B-reps can be modified.")
        modified = NativeKernel().modify_imported_brep(
            base, operation=operation, parameters=parameters,
        )
        try:
            brep = base64.b64decode(modified["brep_data"], validate=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("OCCT returned an invalid modified B-rep.") from exc
        if (not brep or len(brep) > 64 * 1024 * 1024
                or modified.get("brep_valid") is not True):
            raise ValidationError("OCCT returned an invalid or oversized modified B-rep.")
        return {
            "kernel": self.kernel_id,
            "kind": "imported_brep",
            "data": base64.b64encode(zlib.compress(brep, level=9)).decode("ascii"),
            "volume": modified["volume"],
            "brep_valid": True,
        }

    def create_fillet(
        self, base: GeometryDescriptor, *, edge_indices: list[int], radius: float,
    ) -> FilletDescriptor:
        base = self.normalize_geometry(base)
        if (not isinstance(edge_indices, list) or not edge_indices
                or any(type(index) is not int or index < 0 for index in edge_indices)
                or len(set(edge_indices)) != len(edge_indices)):
            raise ValidationError("Fillet needs distinct non-negative edge indices.")
        radius = _positive_amount(radius, "radius")
        geometry = {"kernel": self.kernel_id, "kind": "filleted_solid", "base": base,
                    "edge_indices": list(edge_indices), "radius": radius}
        geometry["volume"] = NativeKernel().geometry_graph(geometry)["volume"]
        return geometry

    def topology(self, geometry: GeometryDescriptor) -> dict[str, Any]:
        from .advanced_topology import enrich_topology

        result = NativeKernel().geometry_graph(geometry, topology=True)
        self._same_saved_volume(geometry, result["volume"])
        return enrich_topology(geometry, result)

    def mass_properties(self, geometry: GeometryDescriptor) -> dict[str, Any]:
        """Exact OCCT unit-density volume properties of one valid saved solid."""
        if isinstance(geometry, dict) and geometry.get("kernel") == "cadyx-mesh-v1":
            raise ValidationError("Mass properties require an exact B-rep, not triangle mesh geometry.")
        checked = self.normalize_geometry(geometry)
        native = NativeKernel()
        brep, summary = native.export_geometry(checked)
        self._same_saved_volume(checked, summary["volume"])
        properties = native.brep_metrics(brep)
        if properties is None or not {"volume", "center_of_mass", "inertia_unit_density"} <= properties.keys():
            raise ValidationError("Native OCCT runtime lacks mass properties; rebuild the native bridge.")
        if not math.isclose(properties["volume"], summary["volume"], rel_tol=1e-9, abs_tol=1e-8):
            raise ValidationError("B-rep mass properties do not match the source volume.")
        return properties

    def metrics(self, geometry: GeometryDescriptor) -> dict[str, Any]:
        result = NativeKernel().geometry_graph(geometry)
        self._same_saved_volume(geometry, result["volume"])
        return {key: result[key] for key in (
            "volume", "surface_area", "bounds", "topology", "brep_valid",
        )}

    def minimum_distance(
        self, first: GeometryDescriptor, second: GeometryDescriptor, *,
        progress: Callable[[str, float], None] | None = None,
    ) -> dict[str, Any]:
        if any(isinstance(geometry, dict) and geometry.get("kernel") == "cadyx-mesh-v1"
               for geometry in (first, second)):
            raise ValidationError("Minimum solid clearance requires editable OCCT B-rep bodies, not triangle meshes.")
        first = self.normalize_geometry(first)
        second = self.normalize_geometry(second)
        native = NativeKernel()
        first_brep, _ = native.export_geometry(first)
        second_brep, _ = native.export_geometry(second)
        return native.minimum_brep_distance(first_brep, second_brep, progress=progress)

    def minimum_distance_placed(
        self, first: GeometryDescriptor, second: GeometryDescriptor, *,
        first_translation: list[float], first_rotation: list[float],
        second_translation: list[float], second_rotation: list[float],
        progress: Callable[[str, float], None] | None = None,
    ) -> dict[str, Any]:
        """Measure transient rigid placements using existing native B-rep transformations."""
        from .assemblies import validate_transform

        native = NativeKernel()

        def placed(geometry: GeometryDescriptor, translation: list[float],
                   quaternion: list[float]) -> GeometryDescriptor:
            validate_transform(translation, quaternion)
            normalized = self.normalize_geometry(geometry)
            if translation == [0, 0, 0] and quaternion in (
                    [1, 0, 0, 0], [-1, 0, 0, 0]):
                return normalized
            brep, metrics = native.export_geometry(normalized)
            descriptor = {
                "kernel": self.kernel_id, "kind": "imported_brep",
                "data": base64.b64encode(zlib.compress(brep)).decode("ascii"),
                "volume": metrics["volume"], "brep_valid": True,
            }
            w, x, y, z = (float(value) for value in quaternion)
            if w < 0:
                w, x, y, z = -w, -x, -y, -z
            vector_length = math.sqrt(x * x + y * y + z * z)
            axis = ([x / vector_length, y / vector_length, z / vector_length]
                    if vector_length > 1e-12 else [0.0, 0.0, 1.0])
            angle = 2.0 * math.atan2(vector_length, w)
            return self.modify_imported_brep(
                descriptor, operation="rigid_transform",
                parameters=[*axis, angle, *translation],
            )

        return self.minimum_distance(
            placed(first, first_translation, first_rotation),
            placed(second, second_translation, second_rotation), progress=progress,
        )

    def compare_preserved_volume(
        self, reference: GeometryDescriptor, candidate: GeometryDescriptor,
    ) -> dict[str, Any]:
        if any(isinstance(geometry, dict) and geometry.get("kernel") == "cadyx-mesh-v1"
               for geometry in (reference, candidate)):
            raise ValidationError("Preserved-volume validation requires editable OCCT B-rep bodies, not triangle meshes.")
        reference = self.normalize_geometry(reference)
        candidate = self.normalize_geometry(candidate)
        native = NativeKernel()
        reference_brep, _ = native.export_geometry(reference)
        candidate_brep, _ = native.export_geometry(candidate)
        return native.brep_preservation(reference_brep, candidate_brep)

    def sample_minimum_thickness(
        self, geometry: GeometryDescriptor, *,
        minimum_thickness: float, tolerance: float,
        progress: Callable[[str, float], None] | None = None,
    ) -> dict[str, Any]:
        if isinstance(geometry, dict) and geometry.get("kernel") == "cadyx-mesh-v1":
            raise ValidationError("Wall-thickness sampling requires an editable OCCT B-rep, not a triangle mesh.")
        geometry = self.normalize_geometry(geometry)
        native = NativeKernel()
        brep, _ = native.export_geometry(geometry)
        return native.brep_thickness(brep, minimum_thickness, tolerance, progress=progress)

    def certify_global_minimum_thickness(
        self, geometry: GeometryDescriptor, *,
        progress: Callable[[str, float], None] | None = None,
    ) -> dict[str, Any]:
        if isinstance(geometry, dict) and geometry.get("kernel") == "cadyx-mesh-v1":
            raise ValidationError("Global wall-thickness certification requires an editable OCCT B-rep, not a triangle mesh.")
        geometry = self.normalize_geometry(geometry)
        native = NativeKernel()
        brep, _ = native.export_geometry(geometry)
        return native.brep_global_thickness(brep, progress=progress)



# Compatibility names for existing clients.
OCPKernelAdapter = OCCTKernelAdapter
