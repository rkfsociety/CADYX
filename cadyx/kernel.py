"""Geometry boundary. The initial adapter stores validated analytic descriptors."""

from __future__ import annotations

import math
from typing import Any, Protocol

from .errors import ValidationError


class GeometryKernel(Protocol):
    def create_box(self, *, width: float, height: float, depth: float) -> dict[str, Any]: ...

    def validate(self, geometry: dict[str, Any]) -> None: ...


class PrimitiveKernelAdapter:
    """Demo adapter; it describes primitives but does not create B-Rep geometry."""

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
