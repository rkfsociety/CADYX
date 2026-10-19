"""Persisted geometry validation and canonical serialization.

Descriptor values remain ordinary JSON objects. Runtime validation is authoritative;
annotations do not replace native shape checks or rewrite legacy saved descriptors.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from typing import Literal, NotRequired, TypedDict, cast
from .errors import ValidationError
from .mesh_geometry import validate_mesh_geometry
from .native_kernel import NativeKernel
from .kernel_profiles import (
    _box_dimensions, _profile_values, _positive_amount,
    _spline_control_values, _finite_coordinate, _ruled_patch_values,
)


type GeometryValue = None | bool | int | float | str | list[GeometryValue] | dict[str, GeometryValue]
type GeometryDescriptor = dict[str, GeometryValue]


class BoxDescriptor(TypedDict):
    kernel: Literal["occt-v1", "primitive-v1"]
    kind: Literal["box"]
    dimensions: dict[str, float]
    volume: float


class OCCTDescriptor(TypedDict):
    kernel: Literal["occt-v1"]
    volume: float


class ProfileDescriptor(OCCTDescriptor):
    kind: Literal["extruded_profile", "revolved_profile"]
    plane: Literal["XY", "XZ"]
    profile: list[dict[str, float]]
    depth: NotRequired[float]
    angle_degrees: NotRequired[float]
    centered: NotRequired[bool]
    profile_curve: NotRequired[Literal["closed_catmull_rom", "closed_periodic_bspline"]]
    profile_curve_points: NotRequired[list[dict[str, float]]]
    profile_segments: NotRequired[list[list[float]]]


class CircleDescriptor(OCCTDescriptor):
    kind: Literal["extruded_circle"]
    plane: Literal["XY", "XZ"]
    center: dict[str, float]
    radius: float
    depth: float
    centered: NotRequired[bool]


class HoledProfileDescriptor(OCCTDescriptor):
    kind: Literal["profile_with_holes"]
    plane: Literal["XY", "XZ"]
    profile: list[dict[str, float]]
    holes: list[list[dict[str, float]]]
    depth: float
    solid: SolidDescriptor
    curve_profiles: NotRequired[list[list[dict[str, float]] | None]]
    segment_profiles: NotRequired[list[list[list[float]] | None]]
    centered: NotRequired[bool]


class LoftDescriptor(OCCTDescriptor):
    kind: Literal["lofted_profiles"]
    sections: list[list[dict[str, float]]]
    section_offsets: list[float]
    profile_alignment: NotRequired[str]
    section_axis: NotRequired[Literal["Y", "Z"]]


class MulticontourLoftDescriptor(OCCTDescriptor):
    kind: Literal["multicontour_loft"]
    branches: list[LoftDescriptor]


class RuledShellDescriptor(OCCTDescriptor):
    kind: Literal["ruled_shell"]
    patches: list[dict[str, list[list[float]]]]
    sewing_tolerance: float


class BooleanDescriptor(OCCTDescriptor):
    kind: Literal["fused_profiles", "fused_solids", "cut_solids"]
    base: SolidDescriptor
    tool: SolidDescriptor


class PipeDescriptor(OCCTDescriptor):
    kind: Literal["swept_pipe"]
    plane: Literal["XY", "XZ"]
    path: list[dict[str, float]]
    radius: float


class ThreadDescriptor(OCCTDescriptor):
    kind: Literal["metric_thread"]
    major_diameter: float
    pitch: float
    length: float
    handedness: Literal["left", "right"]


class BRepDescriptor(OCCTDescriptor):
    kind: Literal["imported_brep"]
    data: str
    brep_valid: bool
    selected_face_indices: NotRequired[list[int]]


class FilletDescriptor(OCCTDescriptor):
    kind: Literal["filleted_solid"]
    base: SolidDescriptor
    edge_indices: list[int]
    radius: float


class MeshDescriptor(TypedDict):
    kernel: Literal["cadyx-mesh-v1"]
    kind: Literal["triangle_mesh"]
    encoding: Literal["zlib-f32-u32-le-v1"]
    vertex_count: int
    triangle_count: int
    sha256: str
    data: str


type SolidDescriptor = (BoxDescriptor | ProfileDescriptor | CircleDescriptor | HoledProfileDescriptor
                        | LoftDescriptor | MulticontourLoftDescriptor | RuledShellDescriptor
                        | BooleanDescriptor | PipeDescriptor | ThreadDescriptor | BRepDescriptor
                        | FilletDescriptor)
type CheckedGeometry = SolidDescriptor | MeshDescriptor


def serialize_geometry(geometry: GeometryDescriptor) -> str:
    """Use the same canonical JSON for cache identity and checked normalization."""
    try:
        return json.dumps(geometry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValidationError("Geometry does not match a supported box format.") from exc


class GeometryDescriptorValidation:
    """Adapter-owned validators; construction/reconstruction stays on the adapter."""

    def validate(self, geometry: GeometryDescriptor) -> None:
        descriptor = serialize_geometry(geometry)
        self._validate_cached(descriptor)

    def normalize_geometry(self, geometry: GeometryDescriptor) -> CheckedGeometry:
        """Return an independent, validated JSON snapshot with a discriminated shape type.

        Cast only after the complete existing kind-specific/native validation. This
        keeps untrusted saved dictionaries outside the checked internal contract.
        """
        descriptor = serialize_geometry(geometry)
        self._validate_cached(descriptor)
        return cast(CheckedGeometry, json.loads(descriptor))

    @lru_cache(maxsize=128)
    def _validate_cached(self, descriptor: str) -> None:
        self._validate_uncached(json.loads(descriptor))

    def _validate_uncached(self, geometry: GeometryDescriptor) -> None:
        if isinstance(geometry, dict) and geometry.get("kernel") == "cadyx-mesh-v1":
            validate_mesh_geometry(geometry)
            return
        if not isinstance(geometry, dict):
            raise ValidationError("Geometry does not match a supported box format.")
        kernel_id = geometry.get("kernel")
        if not isinstance(kernel_id, str) or kernel_id not in self._accepted_kernel_ids:
            raise ValidationError("Geometry does not match a supported box format.")
        if kernel_id == self.kernel_id and geometry.get("kind") in {"extruded_profile", "revolved_profile"}:
            self._validate_profile_geometry(geometry)
            return
        if kernel_id == self.kernel_id and geometry.get("kind") == "profile_with_holes":
            self._validate_profile_with_holes_geometry(geometry)
            return
        if kernel_id == self.kernel_id and geometry.get("kind") == "extruded_circle":
            self._validate_extruded_circle_geometry(geometry)
            return
        if kernel_id == self.kernel_id and geometry.get("kind") == "lofted_profiles":
            self._validate_loft_geometry(geometry)
            return
        if kernel_id == self.kernel_id and geometry.get("kind") == "multicontour_loft":
            self._validate_multicontour_loft_geometry(geometry)
            return
        if kernel_id == self.kernel_id and geometry.get("kind") == "ruled_shell":
            self._validate_ruled_shell_geometry(geometry)
            return
        if kernel_id == self.kernel_id and geometry.get("kind") == "fused_profiles":
            self._validate_fused_geometry(geometry)
            return
        if kernel_id == self.kernel_id and geometry.get("kind") == "fused_solids":
            self._validate_fused_geometry(geometry)
            return
        if kernel_id == self.kernel_id and geometry.get("kind") == "cut_solids":
            self._validate_fused_geometry(geometry)
            return
        if kernel_id == self.kernel_id and geometry.get("kind") == "swept_pipe":
            self._validate_swept_pipe_geometry(geometry)
            return
        if kernel_id == self.kernel_id and geometry.get("kind") == "metric_thread":
            self._validate_metric_thread_geometry(geometry)
            return
        if kernel_id == self.kernel_id and geometry.get("kind") == "imported_brep":
            self._validate_imported_brep_geometry(geometry)
            return
        if kernel_id == self.kernel_id and geometry.get("kind") == "filleted_solid":
            self._validate_filleted_geometry(geometry)
            return
        if set(geometry) != {"kernel", "kind", "dimensions", "volume"} or geometry.get("kind") != "box":
            raise ValidationError("Geometry does not match a supported box format.")

        dimensions = geometry.get("dimensions")
        if not isinstance(dimensions, dict) or set(dimensions) != {"width", "height", "depth"}:
            raise ValidationError("Geometry dimensions are missing or invalid.")
        normalized = _box_dimensions(
            width=dimensions["width"],
            height=dimensions["height"],
            depth=dimensions["depth"],
        )
        saved_volume = geometry.get("volume")
        if isinstance(saved_volume, bool) or not isinstance(saved_volume, (int, float)):
            raise ValidationError("Geometry volume is invalid.")
        try:
            saved_volume = float(saved_volume)
        except OverflowError as exc:
            raise ValidationError("Geometry volume is invalid.") from exc
        if not math.isfinite(saved_volume) or saved_volume <= 0:
            raise ValidationError("Geometry volume is invalid.")

        actual_volume = self._box_volume(normalized)
        if not math.isclose(saved_volume, actual_volume, rel_tol=1e-12, abs_tol=1e-12):
            raise ValidationError("Saved geometry volume does not match the Open CASCADE solid.")

    @staticmethod
    def _validate_profile_geometry(geometry: GeometryDescriptor) -> None:
        if not isinstance(geometry, dict):
            raise ValidationError("Profile geometry descriptor is invalid.")
        kind = geometry.get("kind")
        if kind not in {"extruded_profile", "revolved_profile"}:
            raise ValidationError("Profile geometry descriptor is invalid.")
        amount_field = "depth" if kind == "extruded_profile" else "angle_degrees"
        expected_fields = {"kernel", "kind", "plane", "profile", "volume", amount_field}
        if kind == "extruded_profile" and "centered" in geometry:
            expected_fields.add("centered")
        curve_type = geometry.get("profile_curve")
        curved = curve_type in {"closed_catmull_rom", "closed_periodic_bspline"}
        if curved:
            expected_fields.update({"profile_curve", "profile_curve_points"})
        if "profile_segments" in geometry:
            if curved:
                raise ValidationError("Analytic and spline profile descriptors cannot be combined.")
            from .profile_segments import segment_values
            segment_values(geometry["profile_segments"])
            expected_fields.add("profile_segments")
        if set(geometry) != expected_fields or geometry.get("kernel") != "occt-v1":
            raise ValidationError("Profile geometry descriptor is invalid.")
        profile, _ = _profile_values(geometry["profile"], geometry["plane"])
        if kind == "extruded_profile":
            if geometry["plane"] not in {"XY", "XZ"}:
                raise ValidationError("Profile plane must be XY or XZ.")
            centered = geometry.get("centered", False)
            if type(centered) is not bool:
                raise ValidationError("centered must be a boolean.")
            operation, amount = 0, _positive_amount(geometry["depth"], "depth")
        else:
            if geometry["plane"] != "XZ":
                raise ValidationError("Revolution requires an XZ profile.")
            if "centered" in geometry:
                raise ValidationError("Revolution profiles cannot be centered.")
            angle = _positive_amount(geometry["angle_degrees"], "angle_degrees", maximum=360.0)
            operation, amount = 1, math.radians(angle)
            centered = False
        saved_volume = geometry.get("volume")
        if isinstance(saved_volume, bool) or not isinstance(saved_volume, (int, float)):
            raise ValidationError("Saved geometry volume is invalid.")
        try:
            saved_volume = float(saved_volume)
        except OverflowError as exc:
            raise ValidationError("Saved geometry volume is invalid.") from exc
        if not math.isfinite(saved_volume) or saved_volume <= 0:
            raise ValidationError("Saved geometry volume is invalid.")
        if "profile_segments" in geometry:
            actual = NativeKernel().geometry_graph(geometry)["volume"]
        elif curved:
            _spline_control_values(geometry["profile_curve_points"], geometry["plane"])
            graph = dict(geometry)
            graph.pop("kernel", None)
            graph.pop("volume", None)
            actual = NativeKernel().geometry_graph(graph)["volume"]
        else:
            actual = NativeKernel().profile(
                profile, operation=operation, plane=geometry["plane"], amount=amount, centered=centered,
            )["volume"]
        if not math.isclose(saved_volume, actual, rel_tol=1e-12, abs_tol=1e-12):
            raise ValidationError("Saved geometry volume does not match the Open CASCADE solid.")

    def _validate_profile_with_holes_geometry(self, geometry: GeometryDescriptor) -> None:
        required_fields = {"kernel", "kind", "plane", "profile", "holes", "depth", "solid", "volume"}
        optional_fields = {"centered", "curve_profiles", "segment_profiles"}
        if (not required_fields.issubset(geometry)
                or set(geometry) - required_fields - optional_fields
                or geometry.get("kernel") != self.kernel_id or geometry.get("kind") != "profile_with_holes"
                or not isinstance(geometry.get("holes"), list) or not geometry["holes"]
                or not isinstance(geometry.get("solid"), dict)):
            raise ValidationError("Multi-contour extrusion geometry is invalid.")
        centered = geometry.get("centered", False)
        if type(centered) is not bool:
            raise ValidationError("centered must be a boolean.")
        expected = self.create_extruded_profile_with_holes(
            [geometry["profile"], *geometry["holes"]], plane=geometry["plane"],
            depth=geometry["depth"], centered=centered,
            curve_profiles=geometry.get("curve_profiles"),
            segment_profiles=geometry.get("segment_profiles"),
        )
        comparable = dict(geometry)
        expected.pop("volume", None)
        comparable.pop("volume", None)
        if expected != comparable:
            raise ValidationError("Saved multi-contour extrusion does not match its source profiles.")

    def _validate_extruded_circle_geometry(self, geometry: GeometryDescriptor) -> None:
        if (set(geometry) not in ({"kernel", "kind", "plane", "center", "radius", "depth", "volume"},
                                  {"kernel", "kind", "plane", "center", "radius", "depth", "volume", "centered"})
                or geometry.get("kernel") != self.kernel_id or geometry.get("kind") != "extruded_circle"
                or geometry.get("plane") not in {"XY", "XZ"}):
            raise ValidationError("Analytic circular extrusion geometry is invalid.")
        center = geometry.get("center")
        if (not isinstance(center, dict) or set(center) != {"x", "y"}
                or any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or not math.isfinite(value) for value in center.values())):
            raise ValidationError("Analytic circular extrusion center is invalid.")
        radius = _positive_amount(geometry["radius"], "radius")
        depth = _positive_amount(geometry["depth"], "depth")
        centered = geometry.get("centered", False)
        if type(centered) is not bool:
            raise ValidationError("centered must be a boolean.")
        expected = self.create_extruded_circle(center, radius, plane=geometry["plane"], depth=depth,
                                               centered=centered)
        expected.pop("volume", None)
        comparable = dict(geometry)
        comparable.pop("volume", None)
        if expected != comparable:
            raise ValidationError("Analytic circular extrusion parameters do not match its saved geometry.")
        actual = NativeKernel().geometry_graph(geometry)["volume"]
        if not math.isclose(geometry["volume"], actual, rel_tol=1e-12, abs_tol=1e-12):
            raise ValidationError("Saved analytic circular extrusion volume is invalid.")

    def _validate_fused_geometry(self, geometry: GeometryDescriptor) -> None:
        if (set(geometry) != {"kernel", "kind", "base", "tool", "volume"}
                or geometry.get("kernel") != self.kernel_id
                or geometry.get("kind") not in {"fused_profiles", "fused_solids", "cut_solids"}):
            raise ValidationError("Boolean solid geometry descriptor is invalid.")
        base, tool = geometry.get("base"), geometry.get("tool")
        if not isinstance(base, dict) or not isinstance(tool, dict):
            raise ValidationError("Fused solid source geometries are invalid.")
        self.validate(base)
        self.validate(tool)
        saved_volume = geometry.get("volume")
        if isinstance(saved_volume, bool) or not isinstance(saved_volume, (int, float)):
            raise ValidationError("Saved geometry volume is invalid.")
        try:
            saved_volume = float(saved_volume)
        except OverflowError as exc:
            raise ValidationError("Saved geometry volume is invalid.") from exc
        if not math.isfinite(saved_volume) or saved_volume <= 0:
            raise ValidationError("Saved geometry volume is invalid.")
        actual = NativeKernel().geometry_graph(geometry)["volume"]
        if not math.isclose(saved_volume, actual, rel_tol=1e-12, abs_tol=1e-12):
            raise ValidationError("Saved geometry volume does not match the Open CASCADE fused solid.")

    def _validate_loft_geometry(self, geometry: GeometryDescriptor) -> None:
        if (set(geometry) not in (
                    {"kernel", "kind", "sections", "section_offsets", "volume"},
                    {"kernel", "kind", "sections", "section_offsets", "volume", "profile_alignment"},
                    {"kernel", "kind", "sections", "section_offsets", "volume", "profile_alignment", "section_axis"},
                    {"kernel", "kind", "sections", "section_offsets", "volume", "profile_alignment", "section_axis"},
                    {"kernel", "kind", "sections", "section_offsets", "volume", "profile_alignment", "section_axis"},
                )
                or geometry.get("kernel") != self.kernel_id or geometry.get("kind") != "lofted_profiles"):
            raise ValidationError("Loft geometry descriptor is invalid.")
        axis = geometry.get("section_axis", "Y")
        if axis not in {"Y", "Z"}:
            raise ValidationError("Loft section axis must be Y or Z.")
        rebuilt = self.create_lofted_profiles(
            geometry.get("sections"), section_offsets=geometry.get("section_offsets"),
            alignment=geometry.get("profile_alignment", "independent-start-v1"), section_axis=axis,
        )
        self._same_saved_volume(geometry, rebuilt["volume"])

    def _validate_multicontour_loft_geometry(self, geometry: GeometryDescriptor) -> None:
        if (set(geometry) != {"kernel", "kind", "branches", "volume"}
                or geometry.get("kind") != "multicontour_loft"
                or not isinstance(geometry.get("branches"), list) or len(geometry["branches"]) < 2
                or any(not isinstance(branch, dict) or branch.get("kind") != "lofted_profiles"
                       for branch in geometry["branches"])):
            raise ValidationError("Multi-contour loft geometry descriptor is invalid.")
        for branch in geometry["branches"]:
            if (set(branch) not in (
                        {"kernel", "kind", "sections", "section_offsets", "profile_alignment", "volume"},
                        {"kernel", "kind", "sections", "section_offsets", "profile_alignment", "section_axis", "volume"})
                    or branch.get("kernel") != self.kernel_id
                    or not isinstance(branch.get("sections"), list) or len(branch["sections"]) != 2
                    or not isinstance(branch.get("section_offsets"), list)
                    or len(branch["section_offsets"]) != 2
                    or branch.get("profile_alignment") not in {
                        "independent-start-v1", "minimum-displacement-v1", "minimum-displacement-v2"}):
                raise ValidationError("Multi-contour loft branch descriptor is invalid.")
            axis = branch.get("section_axis", "Y")
            if axis not in {"Y", "Z"}:
                raise ValidationError("Loft section axis must be Y or Z.")
            for profile in branch["sections"]:
                _profile_values(profile, "XZ" if axis == "Y" else "XY")
            left, right = (_finite_coordinate(value, "Loft section offset")
                           for value in branch["section_offsets"])
            if right <= left:
                raise ValidationError("Loft section offsets must be strictly increasing.")
        actual = NativeKernel().geometry_graph(geometry)["volume"]
        self._same_saved_volume(geometry, actual)

    def _validate_ruled_shell_geometry(self, geometry: GeometryDescriptor) -> None:
        if (set(geometry) != {"kernel", "kind", "patches", "sewing_tolerance", "volume"}
                or geometry.get("kernel") != self.kernel_id or geometry.get("kind") != "ruled_shell"):
            raise ValidationError("Ruled shell geometry descriptor is invalid.")
        patches = _ruled_patch_values(geometry.get("patches"))
        tolerance = _finite_coordinate(geometry.get("sewing_tolerance"), "Ruled shell sewing tolerance")
        if not 1e-7 <= tolerance <= 1.0:
            raise ValidationError("Ruled shell sewing tolerance must be between 1e-7 and 1 mm.")
        rebuilt = {"kernel": self.kernel_id, "kind": "ruled_shell", "patches": patches,
                   "sewing_tolerance": tolerance, "volume": 0.0}
        actual = NativeKernel().geometry_graph(rebuilt)["volume"]
        self._same_saved_volume(geometry, actual)

    def _validate_swept_pipe_geometry(self, geometry: GeometryDescriptor) -> None:
        if (set(geometry) != {"kernel", "kind", "plane", "path", "radius", "volume"}
                or geometry.get("kernel") != self.kernel_id or geometry.get("kind") != "swept_pipe"):
            raise ValidationError("Swept pipe geometry descriptor is invalid.")
        rebuilt = self.create_swept_pipe(geometry.get("path"), plane=geometry.get("plane"),
                                         radius=geometry.get("radius"))
        self._same_saved_volume(geometry, rebuilt["volume"])

    def _validate_metric_thread_geometry(self, geometry: GeometryDescriptor) -> None:
        if (set(geometry) != {"kernel", "kind", "major_diameter", "pitch", "length", "handedness", "volume"}
                or geometry.get("kernel") != self.kernel_id or geometry.get("kind") != "metric_thread"):
            raise ValidationError("Metric thread geometry descriptor is invalid.")
        rebuilt = self.create_metric_thread(
            major_diameter=geometry.get("major_diameter"),
            pitch=geometry.get("pitch"),
            length=geometry.get("length"),
            handedness=geometry.get("handedness"),
        )
        self._same_saved_volume(geometry, rebuilt["volume"])

    def _validate_imported_brep_geometry(self, geometry: GeometryDescriptor) -> None:
        allowed = {"kernel", "kind", "data", "volume", "brep_valid", "selected_face_indices"}
        required = {"kernel", "kind", "data", "volume", "brep_valid"}
        if (not set(geometry) <= allowed or not required <= set(geometry)
                or geometry.get("kernel") != self.kernel_id
                or geometry.get("kind") != "imported_brep"
                or not isinstance(geometry.get("data"), str)
                or type(geometry.get("brep_valid")) is not bool):
            raise ValidationError("Imported B-rep geometry descriptor is invalid.")
        selected_faces = geometry.get("selected_face_indices", [])
        if (not isinstance(selected_faces, list)
                or any(type(index) is not int or index < 0 for index in selected_faces)
                or len(set(selected_faces)) != len(selected_faces)):
            raise ValidationError("Imported B-rep selected-face metadata is invalid.")
        actual = NativeKernel().geometry_graph(geometry)
        self._same_saved_volume(geometry, actual["volume"])
        if actual["brep_valid"] != geometry["brep_valid"]:
            raise ValidationError("Imported B-rep validity flag does not match the Open CASCADE shape.")

    def _validate_filleted_geometry(self, geometry: GeometryDescriptor) -> None:
        if (set(geometry) != {"kernel", "kind", "base", "edge_indices", "radius", "volume"}
                or geometry.get("kernel") != self.kernel_id or geometry.get("kind") != "filleted_solid"):
            raise ValidationError("Filleted solid geometry descriptor is invalid.")
        self.validate(geometry.get("base"))
        if (not isinstance(geometry.get("edge_indices"), list) or not geometry["edge_indices"]
                or any(type(index) is not int or index < 0 for index in geometry["edge_indices"])
                or len(set(geometry["edge_indices"])) != len(geometry["edge_indices"])):
            raise ValidationError("Fillet edge indices are invalid.")
        radius = _positive_amount(geometry.get("radius"), "radius")
        actual = NativeKernel().geometry_graph(geometry)["volume"]
        self._same_saved_volume(geometry, actual)

    @staticmethod
    def _same_saved_volume(geometry: GeometryDescriptor, actual: float) -> None:
        saved = geometry.get("volume")
        if isinstance(saved, bool) or not isinstance(saved, (int, float)):
            raise ValidationError("Saved geometry volume is invalid.")
        try:
            saved = float(saved)
        except OverflowError as exc:
            raise ValidationError("Saved geometry volume is invalid.") from exc
        if not math.isfinite(saved) or saved <= 0 or not math.isclose(saved, actual, rel_tol=1e-12, abs_tol=1e-12):
            raise ValidationError("Saved geometry volume does not match the Open CASCADE solid.")

    @staticmethod
    def _box_volume(dimensions: dict[str, float]) -> float:
        return NativeKernel().box(dimensions)["volume"]
