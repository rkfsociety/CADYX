"""Geometry-kernel boundary and CADYX kernel adapters."""

from __future__ import annotations

import math
from typing import Any, Protocol

from .errors import ValidationError
from .native_kernel import NativeKernel


class GeometryKernel(Protocol):
    def create_box(self, *, width: float, height: float, depth: float) -> dict[str, Any]: ...

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


class OCCTKernelAdapter:
    """Build and validate box B-reps with Open CASCADE Technology."""

    kernel_id = "occt-v1"
    _accepted_kernel_ids = {kernel_id, "primitive-v1"}

    def tessellate(self, geometry: dict[str, Any]) -> dict[str, Any]:
        """Return kernel-derived triangles in model coordinates, without native objects."""
        self.validate(geometry)
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

    def validate(self, geometry: dict[str, Any]) -> None:
        if not isinstance(geometry, dict):
            raise ValidationError("Geometry does not match a supported box format.")
        kernel_id = geometry.get("kernel")
        if not isinstance(kernel_id, str) or kernel_id not in self._accepted_kernel_ids:
            raise ValidationError("Geometry does not match a supported box format.")
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
