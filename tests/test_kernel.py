"""Conformance checks for the Open CASCADE geometry-kernel adapter."""

from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory

from cadyx.core import CadyxCore
from cadyx.errors import ValidationError
from cadyx.kernel import OCPKernelAdapter, PrimitiveKernelAdapter


class OCPKernelAdapterConformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.kernel = OCPKernelAdapter()

    def test_create_box_returns_valid_occt_geometry_with_kernel_volume(self) -> None:
        geometry = self.kernel.create_box(width=2, height=3, depth=5)

        self.assertEqual(geometry["kernel"], "occt-v1")
        self.assertEqual(geometry["kind"], "box")
        self.assertEqual(geometry["dimensions"], {"width": 2.0, "height": 3.0, "depth": 5.0})
        self.assertAlmostEqual(geometry["volume"], 30.0)
        self.assertIsNone(self.kernel.validate(geometry))

    def test_create_box_rejects_non_positive_non_finite_and_non_numeric_dimensions(self) -> None:
        invalid_dimensions = (0, -1, float("inf"), float("nan"), True, "2")
        for width in invalid_dimensions:
            with self.subTest(width=width), self.assertRaises(ValidationError):
                self.kernel.create_box(width=width, height=2, depth=3)

    def test_validate_rejects_inconsistent_saved_volume(self) -> None:
        geometry = self.kernel.create_box(width=2, height=3, depth=5)
        geometry["volume"] = 31.0

        with self.assertRaises(ValidationError):
            self.kernel.validate(geometry)

    def test_validate_rejects_unknown_geometry_format(self) -> None:
        with self.assertRaises(ValidationError):
            self.kernel.validate({"kernel": "unknown-v1"})

    def test_native_construction_failure_returns_validation_error(self) -> None:
        # Positive dimensions below OCCT's construction tolerance reach the
        # native boundary; its exception must not escape or crash the host.
        with self.assertRaises(ValidationError):
            self.kernel.create_box(width=1e-12, height=2, depth=3)

    def test_centered_extrusion_is_symmetric_about_the_sketch_plane(self) -> None:
        geometry = self.kernel.create_extruded_profile(
            [{"x": 2, "y": 0}, {"x": 4, "y": 0}, {"x": 4, "y": 8}, {"x": 2, "y": 8}],
            plane="XZ", depth=10, centered=True,
        )

        positions = self.kernel.tessellate(geometry)["positions"]
        y_coordinates = positions[1::3]
        self.assertAlmostEqual(min(y_coordinates), -5)
        self.assertAlmostEqual(max(y_coordinates), 5)
        self.assertTrue(geometry["centered"])

    def test_profile_fuse_requires_one_connected_solid(self) -> None:
        base = self.kernel.create_extruded_profile(
            [{"x": 1, "y": 0}, {"x": 3, "y": 0}, {"x": 3, "y": 2}, {"x": 1, "y": 2}],
            plane="XZ", depth=2, centered=True,
        )
        separated = self.kernel.create_extruded_profile(
            [{"x": 10, "y": 0}, {"x": 12, "y": 0}, {"x": 12, "y": 2}, {"x": 10, "y": 2}],
            plane="XZ", depth=2, centered=True,
        )

        with self.assertRaisesRegex(ValidationError, "one connected solid"):
            self.kernel.fuse_profiles(base, separated)

    def test_tessellation_accepts_legacy_descriptor(self) -> None:
        legacy = PrimitiveKernelAdapter().create_box(width=2, height=3, depth=5)
        mesh = self.kernel.tessellate(legacy)
        self.assertEqual(len(mesh["triangles"]), 36)

    def test_validate_accepts_existing_primitive_descriptors(self) -> None:
        legacy_geometry = PrimitiveKernelAdapter().create_box(width=2, height=3, depth=5)

        self.assertIsNone(self.kernel.validate(legacy_geometry))

    def test_default_core_loads_projects_saved_with_primitive_adapter(self) -> None:
        with TemporaryDirectory() as projects_dir:
            legacy_core = CadyxCore(projects_dir, kernel=PrimitiveKernelAdapter())
            project_id = legacy_core.create_project("Legacy geometry")["project"]["id"]
            lease = legacy_core.acquire_lease(project_id, "human", "compatibility-test")
            legacy_core.execute_command(
                project_id,
                lease["lease_id"],
                {"type": "create_box", "width": 2, "height": 3, "depth": 5},
            )

            current_core = CadyxCore(projects_dir)
            inspected = current_core.inspect_project(project_id)

        self.assertIsInstance(current_core.kernel, OCPKernelAdapter)
        self.assertEqual(inspected["document"]["objects"][0]["geometry"]["kernel"], "primitive-v1")


if __name__ == "__main__":
    unittest.main()
