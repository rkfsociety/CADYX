import hashlib
import struct
import unittest

from cadyx.commands import CommandSystem
from cadyx.mesh_geometry import geometry_from_native_buffer, tessellate_mesh_geometry
from cadyx.models import Document


def _box_mesh(*, x=1.0, y=1.0, z=1.0):
    vertices = (
        (0.0, 0.0, 0.0), (x, 0.0, 0.0), (x, y, 0.0), (0.0, y, 0.0),
        (0.0, 0.0, z), (x, 0.0, z), (x, y, z), (0.0, y, z),
    )
    triangles = (
        (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
        (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
        (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
    )
    header = struct.pack(
        "<4s6If", b"CYSM", 1, len(vertices), len(triangles), 1,
        len(vertices), len(triangles), 0.1,
    )
    positions = struct.pack(
        f"<{len(vertices) * 3}f", *(coordinate for vertex in vertices for coordinate in vertex),
    )
    indices = struct.pack(
        f"<{len(triangles) * 3}I", *(index for triangle in triangles for index in triangle),
    )
    return geometry_from_native_buffer(header + positions + indices)[0]


class _Kernel:
    def __init__(self, target_mesh=None):
        self.target_mesh = target_mesh

    def tessellate(self, geometry):
        if geometry.get("kernel") == "fake-brep":
            return self.target_mesh
        return tessellate_mesh_geometry(geometry)

    def create_lofted_profiles(self, sections, *, section_offsets):
        return {
            "kernel": "fake-brep", "kind": "loft", "sections": sections,
            "section_offsets": section_offsets,
        }


def _document_with_scan(geometry):
    document = Document.create()
    body_id = "body:11111111-1111-4111-8111-111111111111"
    feature_id = "feature:22222222-2222-4222-8222-222222222222"
    document.objects.extend([
        {"id": body_id, "type": "body", "name": "Scan", "feature_ids": [feature_id], "geometry": geometry},
        {
            "id": feature_id, "type": "feature", "operation": "reconstruct_scan", "body_id": body_id,
            "source": {"folder": "scan", "file": "part.ply", "sha256": "0" * 64},
            "artifact": {"file": "part.cadyx-raw.ply", "sha256": "1" * 64, "size": 1},
            "parameters": {}, "stats": {}, "processor": {"name": "test", "version": "1"},
        },
    ])
    return document, body_id, feature_id


class ScanReverseTests(unittest.TestCase):
    def test_align_scan_maps_dominant_axis_to_y_and_records_units(self):
        geometry = _box_mesh(x=2.0, y=10.0, z=1.0)
        document, body_id, feature_id = _document_with_scan(geometry)
        published = []

        def publish(folder, filename, data):
            record = {"file": f"{filename}.aligned", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
            published.append(record)
            return record

        system = CommandSystem(_Kernel(), publish_scan_output=publish)
        result = system.execute(document, {
            "type": "align_scan", "body_id": body_id, "long_axis": "Y", "units": "mm", "scale": 2.0,
        })

        feature = next(item for item in document.objects if item["id"] == feature_id)
        self.assertEqual(result["alignment"]["units"], "mm")
        self.assertGreater(result["alignment"]["aligned_span"][1], result["alignment"]["aligned_span"][0])
        self.assertGreater(result["alignment"]["aligned_span"][1], result["alignment"]["aligned_span"][2])
        self.assertAlmostEqual(result["alignment"]["aligned_span"][1], 20.0, places=4)
        self.assertEqual(feature["raw_artifact"]["file"], "part.cadyx-raw.ply")
        self.assertEqual(feature["artifact"], published[-1])
        document.validate()

    def test_parameter_edit_reapplies_alignment_through_registered_middleware(self):
        raw = _box_mesh(x=2.0, y=10.0, z=1.0)
        document, body_id, feature_id = _document_with_scan(raw)
        body, feature = document.objects
        raw_artifact = dict(feature["artifact"])

        class ReprocessSystem(CommandSystem):
            def _set_scan_parameter(self, document, command, *, progress=None):
                # Isolate reconstruction from the middleware under test. A real
                # reprocess replaces the aligned mesh with source-space geometry.
                body["geometry"] = raw
                feature["artifact"] = raw_artifact
                return {"artifact": raw_artifact}

        def publish(folder, filename, data):
            return {"file": filename + ".aligned", "sha256": hashlib.sha256(data).hexdigest(),
                    "size": len(data)}

        system = ReprocessSystem(_Kernel(), publish_scan_output=publish)
        system.execute(document, {"type": "align_scan", "body_id": body_id,
                                  "long_axis": "Y", "units": "mm", "scale": 2.0})
        expected_geometry = body["geometry"]
        phases = []
        result = system.execute(document, {"type": "set_scan_parameter", "feature_id": feature_id,
                                           "name": "voxel_size", "value": 1}, progress=phases.append)
        self.assertEqual(body["geometry"], expected_geometry)
        self.assertTrue(result["alignment_reapplied"])
        self.assertEqual(feature["raw_artifact"], raw_artifact)
        self.assertEqual(result["artifact"], feature["artifact"])
        self.assertIn({"phase": "reapply_scan_alignment"}, phases)
        document.validate()

    def test_scan_section_creates_closed_xz_profile_and_lofts_directly(self):
        geometry = _box_mesh()
        document, body_id, _ = _document_with_scan(geometry)
        kernel = _Kernel()
        system = CommandSystem(kernel)
        section = system.execute(document, {
            "type": "create_sketch_from_scan_section", "body_id": body_id, "normal_axis": "Y",
            "offset": 0.5, "join_tolerance": 1e-5, "simplify_tolerance": 1e-6,
        })
        first_sketch = next(item for item in document.objects if item.get("id") == section["sketch_ids"][0])
        self.assertEqual(first_sketch["plane"], "XZ")
        self.assertEqual(first_sketch["section_normal_axis"], "Y")
        self.assertEqual(first_sketch["section_offset"], 0.5)
        self.assertEqual(section["contours"][0]["point_count"], 4)
        self.assertAlmostEqual(section["contours"][0]["area"], 1.0, places=6)

        second = system.execute(document, {
            "type": "create_sketch_from_scan_section", "body_id": body_id, "normal_axis": "Y",
            "offset": 0.75, "join_tolerance": 1e-5, "simplify_tolerance": 1e-6,
        })
        loft = system.execute(document, {
            "type": "loft_sketches", "sketch_ids": [section["sketch_ids"][0], second["sketch_ids"][0]],
            "name": "Recovered",
        })
        self.assertEqual(loft["geometry"]["kernel"], "fake-brep")
        self.assertEqual(loft["geometry"]["section_offsets"], [0.5, 0.75])
        document.validate()

    def test_scan_section_series_returns_groups_for_multistation_loft(self):
        geometry = _box_mesh()
        document, body_id, _ = _document_with_scan(geometry)
        system = CommandSystem(_Kernel())
        result = system.execute(document, {
            "type": "create_scan_sections", "body_id": body_id, "normal_axis": "Y",
            "offsets": [0.25, 0.5, 0.75], "join_tolerance": 1e-5, "simplify_tolerance": 1e-6,
        })
        self.assertEqual(result["offsets"], [0.25, 0.5, 0.75])
        self.assertEqual(len(result["section_groups"]), 3)
        self.assertTrue(all(len(group) == 1 for group in result["section_groups"]))
        document.validate()

    def test_compare_identical_brep_tessellation_to_scan_is_zero(self):
        geometry = _box_mesh()
        scan_mesh = tessellate_mesh_geometry(geometry)
        document, scan_body_id, _ = _document_with_scan(geometry)
        body_id = "body:33333333-3333-4333-8333-333333333333"
        feature_id = "feature:44444444-4444-4444-8444-444444444444"
        document.objects.extend([
            {"id": body_id, "type": "body", "name": "Recovered", "feature_ids": [feature_id],
             "geometry": {"kernel": "fake-brep", "kind": "test"}},
            {"id": feature_id, "type": "feature", "operation": "test_brep", "body_id": body_id,
             "parameters": {}},
        ])
        system = CommandSystem(_Kernel(scan_mesh))
        result = system.execute(document, {
            "type": "compare_body_to_scan", "body_id": body_id, "scan_body_id": scan_body_id,
            "max_samples": 64, "tolerance": 1e-6, "bidirectional": True,
        })
        self.assertAlmostEqual(result["scan_to_body"]["rms"], 0.0, places=12)
        self.assertAlmostEqual(result["body_to_scan"]["max"], 0.0, places=12)
        self.assertEqual(result["scan_to_body"]["within_tolerance_percent"], 100.0)


if __name__ == "__main__":
    unittest.main()
