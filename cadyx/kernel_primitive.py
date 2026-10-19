"""Legacy demo primitive descriptors; no production B-rep construction."""
from __future__ import annotations

import math
from typing import Any, Callable
from .errors import ValidationError
from .kernel_descriptors import GeometryDescriptor


class PrimitiveKernelAdapter:
    """Demo adapter; it describes primitives but does not create B-Rep geometry."""

    def tessellate(self, geometry: GeometryDescriptor) -> dict[str, Any]:
        self.validate(geometry)
        from .kernel import OCCTKernelAdapter

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

    def mass_properties(self, geometry: GeometryDescriptor) -> dict[str, Any]:
        """Analytic cuboid fixture only; not a replacement for production OCCT."""
        self.validate(geometry)
        dims = geometry["dimensions"]
        width, height, depth = (float(dims[k]) for k in ("width", "height", "depth"))
        volume = width * height * depth
        tensor = [
            [volume * (height**2 + depth**2) / 12, 0.0, 0.0],
            [0.0, volume * (width**2 + depth**2) / 12, 0.0],
            [0.0, 0.0, volume * (width**2 + height**2) / 12],
        ]
        return {
            "volume": volume,
            "center_of_mass": [width / 2, height / 2, depth / 2],
            "inertia_unit_density": tensor,
        }

    def validate(self, geometry: GeometryDescriptor) -> None:
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

    def minimum_distance(
        self, first: GeometryDescriptor, second: GeometryDescriptor, *,
        progress: Callable[[str, float], None] | None = None,
    ) -> dict[str, Any]:
        raise ValidationError("The primitive demo kernel does not measure solid clearance.")

    def compare_preserved_volume(
        self, reference: GeometryDescriptor, candidate: GeometryDescriptor,
    ) -> dict[str, Any]:
        raise ValidationError("The primitive demo kernel does not measure preserved solid volume.")

    def sample_minimum_thickness(
        self, geometry: GeometryDescriptor, *,
        minimum_thickness: float, tolerance: float,
        progress: Callable[[str, float], None] | None = None,
    ) -> dict[str, Any]:
        raise ValidationError("The primitive demo kernel does not measure solid wall thickness.")

    def certify_global_minimum_thickness(
        self, geometry: GeometryDescriptor, *,
        progress: Callable[[str, float], None] | None = None,
    ) -> dict[str, Any]:
        raise ValidationError("The primitive demo kernel does not certify solid wall thickness.")

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
