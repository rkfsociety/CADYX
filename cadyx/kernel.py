"""Geometry-kernel boundary and CADYX kernel adapters."""

from __future__ import annotations

import json
import base64
import math
import zlib
from functools import lru_cache
from typing import Any, Protocol

from .errors import ValidationError
from .mesh_geometry import tessellate_mesh_geometry, validate_mesh_geometry
from .native_kernel import NativeKernel


class GeometryKernel(Protocol):
    def create_box(self, *, width: float, height: float, depth: float) -> dict[str, Any]: ...

    def create_extruded_profile(
        self, profile: list[dict[str, float]], *, plane: str, depth: float, centered: bool = False,
    ) -> dict[str, Any]: ...

    def create_lofted_profiles(
        self, sections: list[list[dict[str, float]]], *, section_offsets: list[float],
    ) -> dict[str, Any]: ...

    def create_revolved_profile(
        self, profile: list[dict[str, float]], *, angle_degrees: float,
    ) -> dict[str, Any]: ...

    def fuse_profiles(self, base: dict[str, Any], tool: dict[str, Any]) -> dict[str, Any]: ...

    def cut_solids(self, base: dict[str, Any], tool: dict[str, Any]) -> dict[str, Any]: ...

    def create_swept_pipe(
        self, path: list[dict[str, float]], *, plane: str, radius: float,
    ) -> dict[str, Any]: ...

    def create_metric_thread(
        self, *, major_diameter: float, pitch: float, length: float, handedness: str = "right",
    ) -> dict[str, Any]: ...

    def create_imported_step(self, source: bytes) -> tuple[dict[str, Any], dict[str, Any]]: ...

    def extract_section_profile(
        self, geometry: dict[str, Any], *, normal_axis: str, offset: float, max_points: int,
    ) -> list[list[dict[str, float]]]: ...

    def export_delivery(
        self, geometry: dict[str, Any], *, include_stl: bool,
        linear_deflection: float, angular_deflection: float,
    ) -> dict[str, Any]: ...

    def modify_imported_brep(
        self, base: dict[str, Any], *, operation: str, parameters: list[float],
    ) -> dict[str, Any]: ...

    def create_fillet(
        self, base: dict[str, Any], *, edge_indices: list[int], radius: float,
    ) -> dict[str, Any]: ...

    def topology(self, geometry: dict[str, Any]) -> dict[str, Any]: ...

    def metrics(self, geometry: dict[str, Any]) -> dict[str, Any]: ...

    def validate(self, geometry: dict[str, Any]) -> None: ...

    def tessellate(self, geometry: dict[str, Any]) -> dict[str, Any]: ...


def _box_dimensions(width: Any, height: Any, depth: Any) -> dict[str, float]:
    dimensions = {"width": width, "height": height, "depth": depth}
    normalized: dict[str, float] = {}
    for name, value in dimensions.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationError(f"{name} must be a number.")
        try:
            number = float(value)
        except OverflowError as exc:
            raise ValidationError(f"{name} must be a finite positive number.") from exc
        if not math.isfinite(number) or number <= 0:
            raise ValidationError(f"{name} must be a finite positive number.")
        normalized[name] = number
    return normalized


def _profile_values(profile: Any, plane: Any) -> tuple[list[dict[str, float]], int]:
    if plane not in {"XY", "XZ"}:
        raise ValidationError("Profile plane must be XY or XZ.")
    if not isinstance(profile, list) or not 3 <= len(profile) <= 4096:
        raise ValidationError("A profile needs between 3 and 4096 points.")
    normalized = []
    for point in profile:
        if not isinstance(point, dict) or set(point) != {"x", "y"}:
            raise ValidationError("Every profile point must contain x and y coordinates.")
        values = {}
        for name in ("x", "y"):
            value = point[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationError("Profile coordinates must be finite numbers.")
            try:
                value = float(value)
            except OverflowError as exc:
                raise ValidationError("Profile coordinates must be finite numbers.") from exc
            if not math.isfinite(value):
                raise ValidationError("Profile coordinates must be finite numbers.")
            values[name] = value
        normalized.append(values)
    area = sum(
        point["x"] * normalized[(index + 1) % len(normalized)]["y"]
        - normalized[(index + 1) % len(normalized)]["x"] * point["y"]
        for index, point in enumerate(normalized)
    )
    if not math.isfinite(area) or abs(area) <= 1e-12:
        raise ValidationError("Profile area must be non-zero.")
    plane_code = 0 if plane == "XY" else 1
    return normalized, plane_code


def _positive_amount(value: Any, name: str, *, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a number.")
    try:
        amount = float(value)
    except OverflowError as exc:
        raise ValidationError(f"{name} must be finite and positive.") from exc
    if not math.isfinite(amount) or amount <= 0 or (maximum is not None and amount > maximum):
        suffix = f" and no greater than {maximum:g}" if maximum is not None else ""
        raise ValidationError(f"{name} must be finite, positive{suffix}.")
    return amount


def _positive_or_signed_coordinate(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("Pipe path coordinates must be finite numbers.")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValidationError("Pipe path coordinates must be finite numbers.") from exc
    if not math.isfinite(number):
        raise ValidationError("Pipe path coordinates must be finite numbers.")
    return number


def _finite_coordinate(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be finite.")
    number = float(value)
    if not math.isfinite(number):
        raise ValidationError(f"{name} must be finite.")
    return number


def _resample_loft_profile(profile: list[dict[str, float]], count: int) -> list[dict[str, float]]:
    points = [(point["x"], point["y"]) for point in profile]
    area = sum(points[i][0] * points[(i + 1) % len(points)][1]
               - points[(i + 1) % len(points)][0] * points[i][1]
               for i in range(len(points)))
    if area < 0:
        points.reverse()
    lengths = [math.hypot(points[(i + 1) % len(points)][0] - point[0],
                          points[(i + 1) % len(points)][1] - point[1])
               for i, point in enumerate(points)]
    perimeter = sum(lengths)
    if not math.isfinite(perimeter) or perimeter <= 1e-7:
        raise ValidationError("Loft section perimeter must be finite and positive.")
    samples: list[dict[str, float]] = []
    edge = 0
    start = 0.0
    for index in range(count):
        distance = perimeter * index / count
        while edge < len(lengths) - 1 and start + lengths[edge] < distance:
            start += lengths[edge]
            edge += 1
        ratio = (distance - start) / lengths[edge]
        first, second = points[edge], points[(edge + 1) % len(points)]
        samples.append({"x": first[0] + ratio * (second[0] - first[0]),
                        "y": first[1] + ratio * (second[1] - first[1])})
    anchor = min(range(count), key=lambda i: (samples[i]["x"], samples[i]["y"]))
    return samples[anchor:] + samples[:anchor]


def _canonical_loft_profile(profile: list[dict[str, float]]) -> list[dict[str, float]]:
    points = list(profile)
    area = sum(points[i]["x"] * points[(i + 1) % len(points)]["y"]
               - points[(i + 1) % len(points)]["x"] * points[i]["y"]
               for i in range(len(points)))
    if area < 0:
        points.reverse()
    anchor = min(range(len(points)), key=lambda i: (points[i]["x"], points[i]["y"]))
    return points[anchor:] + points[:anchor]


class OCCTKernelAdapter:
    """Build and validate box B-reps with Open CASCADE Technology."""

    kernel_id = "occt-v1"
    _accepted_kernel_ids = {kernel_id, "primitive-v1"}

    def tessellate(self, geometry: dict[str, Any]) -> dict[str, Any]:
        """Return kernel-derived triangles in model coordinates, without native objects."""
        # Mesh tessellation already verifies the compressed payload while decoding it.
        # Avoid serializing and validating a large scan mesh a second time here.
        if isinstance(geometry, dict) and geometry.get("kernel") == "cadyx-mesh-v1":
            return tessellate_mesh_geometry(geometry)
        self.validate(geometry)
        result = NativeKernel().geometry_graph(geometry, mesh=True)
        return {key: result[key] for key in ("positions", "triangles")}

    def create_box(self, *, width: float, height: float, depth: float) -> dict[str, Any]:
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
    ) -> dict[str, Any]:
        if type(centered) is not bool:
            raise ValidationError("centered must be a boolean.")
        return self._create_profile_geometry(profile, plane=plane, operation=0,
                                             amount=_positive_amount(depth, "depth"), centered=centered)

    def create_revolved_profile(
        self, profile: list[dict[str, float]], *, angle_degrees: float,
    ) -> dict[str, Any]:
        angle = _positive_amount(angle_degrees, "angle_degrees", maximum=360.0)
        return self._create_profile_geometry(profile, plane="XZ", operation=1,
                                             amount=math.radians(angle), angle_degrees=angle)

    def create_lofted_profiles(
        self, sections: list[list[dict[str, float]]], *, section_offsets: list[float],
    ) -> dict[str, Any]:
        if (not isinstance(sections, list) or not 2 <= len(sections) <= 64
                or not isinstance(section_offsets, list) or len(section_offsets) != len(sections)):
            raise ValidationError("A loft needs 2 to 64 paired sketch sections and offsets.")
        raw_sections = []
        offsets = []
        for section, offset in zip(sections, section_offsets, strict=True):
            points, _ = _profile_values(section, "XZ")
            raw_sections.append(points)
            offsets.append(_finite_coordinate(offset, "Loft section offset"))
        point_count = max(len(section) for section in raw_sections)
        if all(len(section) == point_count for section in raw_sections):
            normalized = [_canonical_loft_profile(section) for section in raw_sections]
        else:
            normalized = [_resample_loft_profile(section, point_count) for section in raw_sections]
        if any(right <= left for left, right in zip(offsets, offsets[1:])):
            raise ValidationError("Loft section offsets must be strictly increasing.")
        payload = {"kernel": self.kernel_id, "kind": "lofted_profiles", "sections": normalized,
                   "section_offsets": offsets}
        payload["volume"] = NativeKernel().geometry_graph(payload)["volume"]
        return payload

    @staticmethod
    def _create_profile_geometry(
        profile: list[dict[str, float]], *, plane: str, operation: int,
        amount: float, angle_degrees: float | None = None, centered: bool = False,
    ) -> dict[str, Any]:
        normalized, _ = _profile_values(profile, plane)
        amount = _positive_amount(amount, "operation amount")
        result = NativeKernel().profile(normalized, operation=operation,
                                        plane=plane, amount=amount, centered=centered)
        geometry = {
            "kernel": OCCTKernelAdapter.kernel_id,
            "kind": "extruded_profile" if operation == 0 else "revolved_profile",
            "plane": plane,
            "profile": normalized,
            "volume": result["volume"],
        }
        if operation == 0:
            geometry["depth"] = amount
            if centered:
                geometry["centered"] = True
        else:
            geometry["angle_degrees"] = angle_degrees
        return geometry

    def fuse_profiles(self, base: dict[str, Any], tool: dict[str, Any]) -> dict[str, Any]:
        self.validate(base)
        self.validate(tool)
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

    def cut_solids(self, base: dict[str, Any], tool: dict[str, Any]) -> dict[str, Any]:
        self.validate(base)
        self.validate(tool)
        result = NativeKernel().geometry_graph({"kind": "cut_solids", "base": base, "tool": tool})
        if result["volume"] >= base["volume"] - max(1e-9, base["volume"] * 1e-12):
            raise ValidationError("Boolean cut must remove material from the base solid.")
        return {"kernel": self.kernel_id, "kind": "cut_solids", "base": base,
                "tool": tool, "volume": result["volume"]}

    def create_swept_pipe(
        self, path: list[dict[str, float]], *, plane: str, radius: float,
    ) -> dict[str, Any]:
        if plane not in {"XY", "XZ"} or not isinstance(path, list) or not 3 <= len(path) <= 512:
            raise ValidationError("A pipe path needs 3 to 512 points on an XY or XZ sketch.")
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
    ) -> dict[str, Any]:
        major_diameter = _positive_amount(major_diameter, "major_diameter")
        pitch = _positive_amount(pitch, "pitch")
        length = _positive_amount(length, "length")
        if length < pitch or length / pitch > 64:
            raise ValidationError("Thread length must be at least one pitch and no more than 64 pitches.")
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

    def create_imported_step(self, source: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
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
        self, geometry: dict[str, Any], *, normal_axis: str, offset: float, max_points: int,
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
        geometry: dict[str, Any],
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
        self.validate(geometry)
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
        self, base: dict[str, Any], *, operation: str, parameters: list[float],
    ) -> dict[str, Any]:
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
        self, base: dict[str, Any], *, edge_indices: list[int], radius: float,
    ) -> dict[str, Any]:
        self.validate(base)
        if (not isinstance(edge_indices, list) or not 1 <= len(edge_indices) <= 128
                or any(type(index) is not int or index < 0 for index in edge_indices)
                or len(set(edge_indices)) != len(edge_indices)):
            raise ValidationError("Fillet needs 1 to 128 distinct non-negative edge indices.")
        radius = _positive_amount(radius, "radius")
        geometry = {"kernel": self.kernel_id, "kind": "filleted_solid", "base": base,
                    "edge_indices": list(edge_indices), "radius": radius}
        geometry["volume"] = NativeKernel().geometry_graph(geometry)["volume"]
        return geometry

    def topology(self, geometry: dict[str, Any]) -> dict[str, Any]:
        result = NativeKernel().geometry_graph(geometry, topology=True)
        self._same_saved_volume(geometry, result["volume"])
        return result

    def metrics(self, geometry: dict[str, Any]) -> dict[str, Any]:
        result = NativeKernel().geometry_graph(geometry)
        self._same_saved_volume(geometry, result["volume"])
        return {key: result[key] for key in (
            "volume", "surface_area", "bounds", "topology", "brep_valid",
        )}

    def validate(self, geometry: dict[str, Any]) -> None:
        try:
            descriptor = json.dumps(geometry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValidationError("Geometry does not match a supported box format.") from exc
        self._validate_cached(descriptor)

    @lru_cache(maxsize=128)
    def _validate_cached(self, descriptor: str) -> None:
        self._validate_uncached(json.loads(descriptor))

    def _validate_uncached(self, geometry: dict[str, Any]) -> None:
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
        if kernel_id == self.kernel_id and geometry.get("kind") == "lofted_profiles":
            self._validate_loft_geometry(geometry)
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
    def _validate_profile_geometry(geometry: dict[str, Any]) -> None:
        if not isinstance(geometry, dict):
            raise ValidationError("Profile geometry descriptor is invalid.")
        kind = geometry.get("kind")
        if kind not in {"extruded_profile", "revolved_profile"}:
            raise ValidationError("Profile geometry descriptor is invalid.")
        amount_field = "depth" if kind == "extruded_profile" else "angle_degrees"
        expected_fields = {"kernel", "kind", "plane", "profile", "volume", amount_field}
        if kind == "extruded_profile" and "centered" in geometry:
            expected_fields.add("centered")
        if set(geometry) != expected_fields or geometry.get("kernel") != OCCTKernelAdapter.kernel_id:
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
        actual = NativeKernel().profile(
            profile, operation=operation, plane=geometry["plane"], amount=amount, centered=centered,
        )["volume"]
        if not math.isclose(saved_volume, actual, rel_tol=1e-12, abs_tol=1e-12):
            raise ValidationError("Saved geometry volume does not match the Open CASCADE solid.")

    def _validate_fused_geometry(self, geometry: dict[str, Any]) -> None:
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

    def _validate_loft_geometry(self, geometry: dict[str, Any]) -> None:
        if (set(geometry) != {"kernel", "kind", "sections", "section_offsets", "volume"}
                or geometry.get("kernel") != self.kernel_id or geometry.get("kind") != "lofted_profiles"):
            raise ValidationError("Loft geometry descriptor is invalid.")
        rebuilt = self.create_lofted_profiles(geometry.get("sections"),
                                              section_offsets=geometry.get("section_offsets"))
        self._same_saved_volume(geometry, rebuilt["volume"])

    def _validate_swept_pipe_geometry(self, geometry: dict[str, Any]) -> None:
        if (set(geometry) != {"kernel", "kind", "plane", "path", "radius", "volume"}
                or geometry.get("kernel") != self.kernel_id or geometry.get("kind") != "swept_pipe"):
            raise ValidationError("Swept pipe geometry descriptor is invalid.")
        rebuilt = self.create_swept_pipe(geometry.get("path"), plane=geometry.get("plane"),
                                         radius=geometry.get("radius"))
        self._same_saved_volume(geometry, rebuilt["volume"])

    def _validate_metric_thread_geometry(self, geometry: dict[str, Any]) -> None:
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

    def _validate_imported_brep_geometry(self, geometry: dict[str, Any]) -> None:
        if (set(geometry) != {"kernel", "kind", "data", "volume", "brep_valid"}
                or geometry.get("kernel") != self.kernel_id
                or geometry.get("kind") != "imported_brep"
                or not isinstance(geometry.get("data"), str)
                or type(geometry.get("brep_valid")) is not bool):
            raise ValidationError("Imported B-rep geometry descriptor is invalid.")
        actual = NativeKernel().geometry_graph(geometry)
        self._same_saved_volume(geometry, actual["volume"])
        if actual["brep_valid"] != geometry["brep_valid"]:
            raise ValidationError("Imported B-rep validity flag does not match the Open CASCADE shape.")

    def _validate_filleted_geometry(self, geometry: dict[str, Any]) -> None:
        if (set(geometry) != {"kernel", "kind", "base", "edge_indices", "radius", "volume"}
                or geometry.get("kernel") != self.kernel_id or geometry.get("kind") != "filleted_solid"):
            raise ValidationError("Filleted solid geometry descriptor is invalid.")
        self.validate(geometry.get("base"))
        if (not isinstance(geometry.get("edge_indices"), list)
                or not 1 <= len(geometry["edge_indices"]) <= 128
                or any(type(index) is not int or index < 0 for index in geometry["edge_indices"])
                or len(set(geometry["edge_indices"])) != len(geometry["edge_indices"])):
            raise ValidationError("Fillet edge indices are invalid.")
        radius = _positive_amount(geometry.get("radius"), "radius")
        actual = NativeKernel().geometry_graph(geometry)["volume"]
        self._same_saved_volume(geometry, actual)

    @staticmethod
    def _same_saved_volume(geometry: dict[str, Any], actual: float) -> None:
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


# Compatibility name for clients of the former Python-binding adapter.
OCPKernelAdapter = OCCTKernelAdapter


class PrimitiveKernelAdapter:
    """Demo adapter; it describes primitives but does not create B-Rep geometry."""

    def tessellate(self, geometry: dict[str, Any]) -> dict[str, Any]:
        self.validate(geometry)
        return OCCTKernelAdapter().tessellate(geometry)

    def create_box(self, *, width: float, height: float, depth: float) -> dict[str, Any]:
        dimensions = {"width": width, "height": height, "depth": depth}
        for name, value in dimensions.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationError(f"{name} must be a number.")
            if not math.isfinite(value) or value <= 0:
                raise ValidationError(f"{name} must be a finite positive number.")
        return {
            "kernel": "primitive-v1",
            "kind": "box",
            "dimensions": dimensions,
            "volume": width * height * depth,
        }

    def validate(self, geometry: dict[str, Any]) -> None:
        if not isinstance(geometry, dict) or geometry.get("kernel") != "primitive-v1":
            raise ValidationError("Geometry does not match the primitive kernel format.")
        dimensions = geometry.get("dimensions")
        if not isinstance(dimensions, dict):
            raise ValidationError("Geometry dimensions are missing.")
        expected = self.create_box(
            width=dimensions.get("width"),
            height=dimensions.get("height"),
            depth=dimensions.get("depth"),
        )
        if geometry != expected:
            raise ValidationError("Saved geometry descriptor is inconsistent.")

    def create_extruded_profile(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValidationError("The primitive demo kernel does not build extruded B-reps.")

    def create_lofted_profiles(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValidationError("The primitive demo kernel does not build lofted B-reps.")

    def fuse_profiles(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValidationError("The primitive demo kernel does not fuse B-rep solids.")

    def cut_solids(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValidationError("The primitive demo kernel does not cut B-rep solids.")

    def create_revolved_profile(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValidationError("The primitive demo kernel does not build revolved B-reps.")
