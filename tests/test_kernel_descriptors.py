"""Compatibility and trust-boundary checks for saved geometry descriptors."""
from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from cadyx.errors import ValidationError
from cadyx.kernel import OCCTKernelAdapter, OCPKernelAdapter, PrimitiveKernelAdapter
from cadyx.kernel_descriptors import serialize_geometry
from cadyx.native_kernel import NativeKernel


class GeometryDescriptorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.kernel = OCCTKernelAdapter()
        # These tests exercise descriptor validation without requiring a native DLL.
        self.volume = patch.object(self.kernel, "_box_volume", return_value=30.0)
        self.volume.start()
        self.addCleanup(self.volume.stop)

    def box(self, kernel: str = "occt-v1") -> dict:
        return {"kernel": kernel, "kind": "box", "volume": 30.0,
                "dimensions": {"width": 2.0, "height": 3.0, "depth": 5.0}}

    def test_legacy_and_current_json_round_trip_without_rewriting(self) -> None:
        self.assertIs(OCPKernelAdapter, OCCTKernelAdapter)
        for kernel in ("occt-v1", "primitive-v1"):
            with self.subTest(kernel=kernel):
                source = self.box(kernel)
                before = copy.deepcopy(source)
                saved = json.loads(json.dumps(source))
                checked = self.kernel.normalize_geometry(saved)
                self.assertEqual(checked, before)
                self.assertEqual(source, before)
                if kernel == "primitive-v1":
                    PrimitiveKernelAdapter().validate(checked)

    def test_checked_snapshot_has_no_mutable_aliases_or_cached_objects(self) -> None:
        source = self.box()
        first = self.kernel.normalize_geometry(source)
        first["dimensions"]["width"] = 99.0
        self.assertEqual(source["dimensions"]["width"], 2.0)
        self.assertEqual(self.kernel.normalize_geometry(source), self.box())
        source["dimensions"]["width"] = 100.0
        self.assertEqual(first["dimensions"]["width"], 99.0)

    def test_normalization_never_bypasses_validation_on_changed_input(self) -> None:
        source = self.box()
        self.kernel.normalize_geometry(source)
        for field, value in (("volume", 31.0), ("kernel", "unknown"), ("kind", "thread")):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                self.kernel.normalize_geometry(dict(source, **{field: value}))
        source["dimensions"]["width"] = True
        with self.assertRaises(ValidationError):
            self.kernel.normalize_geometry(source)

    def test_serialization_is_order_independent_and_rejects_non_json(self) -> None:
        source = self.box()
        self.assertEqual(serialize_geometry(source), serialize_geometry(dict(reversed(list(source.items())))))
        source["dimensions"] = object()
        with self.assertRaisesRegex(ValidationError, "supported box format"):
            self.kernel.normalize_geometry(source)

    def test_unknown_mesh_encoding_is_not_accepted_as_checked_geometry(self) -> None:
        with self.assertRaises(ValidationError):
            self.kernel.normalize_geometry({"kernel": "cadyx-mesh-v1", "kind": "triangle_mesh"})


class SplineDescriptorCompatibilityTests(unittest.TestCase):
    def test_legacy_and_current_closed_splines_keep_their_saved_representation(self) -> None:
        kernel = OCCTKernelAdapter()
        points = [{"x": 0.0, "y": 0.0}, {"x": 4.0, "y": 0.0},
                  {"x": 4.0, "y": 4.0}, {"x": 0.0, "y": 4.0}]
        for curve in ("closed_catmull_rom", "closed_periodic_bspline"):
            with self.subTest(curve=curve):
                saved = {"kernel": "occt-v1", "kind": "extruded_profile", "plane": "XY",
                         "profile": points, "profile_curve_points": points,
                         "profile_curve": curve, "depth": 2.0}
                saved["volume"] = NativeKernel().geometry_graph(saved)["volume"]
                before = serialize_geometry(saved)
                checked = kernel.normalize_geometry(json.loads(before))
                self.assertEqual(serialize_geometry(checked), before)
                self.assertGreater(len(kernel.tessellate(checked)["triangles"]), 0)


if __name__ == "__main__":
    unittest.main()
