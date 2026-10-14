"""Geometry-kernel boundary and CADYX kernel adapters."""

from __future__ import annotations

import math
from typing import Any, Protocol

from .errors import ValidationError
from .mesh_geometry import tessellate_mesh_geometry, validate_mesh_geometry
from .native_kernel import NativeKernel


class GeometryKernel(Protocol):
    def create_box(self, *, width: float, height: float, depth: float) -> dict[str, Any]: ...

    def create_extruded_profile(
        self, profile: list[dict[str, float]], *, plane: str, depth: float, centered: bool = False,
    ) -> dict[str, Any]: ...

    def create_revolved_profile(
        self, profile: list[dict[str, float]], *, angle_degrees: float,
    ) -> dict[str, Any]: ...

    def fuse_profiles(self, base: dict[str, Any], tool: dict[str, Any]) -> dict[str, Any]: ...

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


class OCCTKernelAdapter:
    """Build and validate box B-reps with Open CASCADE Technology."""

    kernel_id = "occt-v1"
    _accepted_kernel_ids = {kernel_id, "primitive-v1"}

    def tessellate(self, geometry: dict[str, Any]) -> dict[str, Any]:
        """Return kernel-derived triangles in model coordinates, without native objects."""
        self.validate(geometry)
        if geometry.get("kernel") == "cadyx-mesh-v1":
            return tessellate_mesh_geometry(geometry)
        if geometry.get("kind") in {"extruded_profile", "revolved_profile"}:
            operation = 0 if geometry["kind"] == "extruded_profile" else 1
            amount = geometry["depth"] if operation == 0 else math.radians(geometry["angle_degrees"])
            result = NativeKernel().profile(
                geometry["profile"], operation=operation, plane=geometry["plane"],
                amount=amount, centered=geometry.get("centered", False), mesh=True,
            )
            return {key: result[key] for key in ("positions", "triangles")}
        if geometry.get("kind") == "fused_profiles":
            result = NativeKernel().fuse_profiles(geometry["base"], geometry["tool"], mesh=True)
            return {key: result[key] for key in ("positions", "triangles")}
        result = NativeKernel().box(geometry["dimensions"], mesh=True)
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
        self._validate_profile_geometry(base)
        self._validate_profile_geometry(tool)
        result = NativeKernel().fuse_profiles(base, tool)
        return {
            "kernel": self.kernel_id,
            "kind": "fused_profiles",
            "base": base,
            "tool": tool,
            "volume": result["volume"],
        }

    def validate(self, geometry: dict[str, Any]) -> None:
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
        if kernel_id == self.kernel_id and geometry.get("kind") == "fused_profiles":
            self._validate_fused_geometry(geometry)
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

    @staticmethod
    def _validate_fused_geometry(geometry: dict[str, Any]) -> None:
        if (set(geometry) != {"kernel", "kind", "base", "tool", "volume"}
                or geometry.get("kernel") != OCCTKernelAdapter.kernel_id):
            raise ValidationError("Fused profile geometry descriptor is invalid.")
        base, tool = geometry.get("base"), geometry.get("tool")
        if not isinstance(base, dict) or not isinstance(tool, dict):
            raise ValidationError("Fused profile source geometries are invalid.")
        OCCTKernelAdapter._validate_profile_geometry(base)
        OCCTKernelAdapter._validate_profile_geometry(tool)
        saved_volume = geometry.get("volume")
        if isinstance(saved_volume, bool) or not isinstance(saved_volume, (int, float)):
            raise ValidationError("Saved geometry volume is invalid.")
        try:
            saved_volume = float(saved_volume)
        except OverflowError as exc:
            raise ValidationError("Saved geometry volume is invalid.") from exc
        if not math.isfinite(saved_volume) or saved_volume <= 0:
            raise ValidationError("Saved geometry volume is invalid.")
        actual = NativeKernel().fuse_profiles(base, tool)["volume"]
        if not math.isclose(saved_volume, actual, rel_tol=1e-12, abs_tol=1e-12):
            raise ValidationError("Saved geometry volume does not match the Open CASCADE fused solid.")

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

    def fuse_profiles(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValidationError("The primitive demo kernel does not fuse B-rep solids.")

    def create_revolved_profile(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValidationError("The primitive demo kernel does not build revolved B-reps.")
