"""Behavioral tests for saved mesh-processing tools; no native runtime required."""
from copy import deepcopy
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest

from cadyx.command_scan import ScanCommands
from cadyx.errors import ValidationError
from cadyx.kernel_primitive import PrimitiveKernelAdapter
from cadyx.mesh_geometry import geometry_from_native_buffer, tessellate_mesh_geometry
from cadyx.mesh_processing import process_mesh_geometry
from cadyx.models import Document


def mesh(vertices, faces):
    header = struct.pack("<4s6If", b"CYSM", 1, len(vertices), len(faces), 1, len(vertices), len(faces), 1.)
    return geometry_from_native_buffer(header + struct.pack("<" + "f"*len(vertices)*3, *(x for v in vertices for x in v))
                                      + struct.pack("<" + "I"*len(faces)*3, *(i for f in faces for i in f)))[0]


def fan():
    return mesh([(0,0,0), (1,0,0), (1,1,0), (0,1,0), (.5,.5,.2)],
                [(0,1,4), (1,2,4), (2,3,4), (3,0,4)])


class MeshProcessingTests(unittest.TestCase):
    def test_clean_welds_exact_duplicates_and_removes_duplicate_and_zero_area_faces(self):
        source = mesh([(0,0,0), (1,0,0), (0,1,0), (0,0,0), (2,0,0)],
                      [(0,1,2), (3,1,2), (0,1,4)])
        original = deepcopy(source)
        result, stats = process_mesh_geometry(source, "clean", {})
        self.assertEqual(result["triangle_count"], 1)
        self.assertEqual(result["vertex_count"], 3)
        self.assertEqual(stats["after"]["degenerate_triangles"], 0)
        self.assertEqual(source, original)

    def test_components_are_edge_connected_and_threshold_is_explicit(self):
        source = mesh([(0,0,0), (1,0,0), (1,1,0), (0,1,0), (2,0,0), (2,1,0)],
                      [(0,1,2), (0,2,3), (1,4,5)])
        result, stats = process_mesh_geometry(source, "remove_small_components", {"min_triangles": 2})
        self.assertEqual(stats["before"]["components"], 2)
        self.assertEqual(stats["after"]["components"], 1)
        self.assertEqual(result["triangle_count"], 2)
        with self.assertRaises(ValidationError):
            process_mesh_geometry(source, "remove_small_components", {"min_triangles": 4})

    def test_smoothing_pins_every_boundary_vertex_and_keeps_connectivity(self):
        source = fan()
        result, stats = process_mesh_geometry(source, "smooth", {"iterations": 2, "strength": .1})
        before, after = tessellate_mesh_geometry(source), tessellate_mesh_geometry(result)
        self.assertEqual(before["positions"][:12], after["positions"][:12])
        self.assertEqual(before["triangles"], after["triangles"])
        self.assertLess(after["positions"][-1], before["positions"][-1])
        self.assertGreater(stats["max_vertex_displacement"], 0)
        self.assertEqual(stats["boundary_vertices_pinned"], 4)
        self.assertEqual(stats["after"]["nonmanifold_edges"], 0)
        with self.assertRaises(ValidationError):
            process_mesh_geometry(source, "smooth", {"iterations": True, "strength": .1})

    def test_command_preserves_source_and_saves_hash_bound_history_and_artifact(self):
        with tempfile.TemporaryDirectory() as folder:
            commands = ScanCommands()
            commands.kernel = PrimitiveKernelAdapter()
            def publish(project_id, filename, content):
                path = Path(folder) / "result.ply"
                path.write_bytes(content)
                return {"file": path.name, "sha256": hashlib.sha256(content).hexdigest()}
            commands.publish_project_scan_output = publish
            document = Document.create()
            source = {"id": "body:11111111-1111-4111-8111-111111111111", "type": "body", "name": "Test mesh",
                      "geometry": fan(), "feature_ids": ["feature:22222222-2222-4222-8222-222222222222"]}
            original = deepcopy(source["geometry"])
            document.objects = [source, {"id": "feature:22222222-2222-4222-8222-222222222222", "type": "feature",
                                        "operation": "reconstruct_scan", "body_id": source["id"]}]
            result = commands._process_mesh(document, {"body_id": source["id"], "method": "smooth",
                                                       "parameters": {"iterations": 2, "strength": .1}}, project_id="test")
            self.assertEqual(source["geometry"], original)
            self.assertFalse(source["visible"])
            self.assertEqual(document.objects[-1]["source_geometry_sha256"], original["sha256"])
            self.assertEqual(result["artifact"]["sha256"], hashlib.sha256((Path(folder)/"result.ply").read_bytes()).hexdigest())
            document.validate()
            self.assertEqual(Document.from_dict(document.to_dict()).to_dict(), document.to_dict())
            source["geometry"]["sha256"] = "b"*64
            with self.assertRaises(ValidationError):
                document.validate()

    def test_orient_repairs_winding_without_moving_vertices(self):
        source = mesh([(0,0,0), (1,0,0), (1,1,0), (0,1,0)], [(0,1,2), (0,3,2)])
        result, stats = process_mesh_geometry(source, "orient", {})
        self.assertEqual(stats["before"]["inconsistent_edges"], 1)
        self.assertEqual(stats["after"]["inconsistent_edges"], 0)
        self.assertEqual(tessellate_mesh_geometry(source)["positions"], tessellate_mesh_geometry(result)["positions"])
        self.assertEqual(stats["before"]["boundary_edges"], stats["after"]["boundary_edges"])

    def test_nonorientable_surface_requires_explicit_repair_and_counts_deleted_faces(self):
        import math
        vertices = []
        count = 7
        for i in range(count):
            angle = 2*math.pi*i/count
            for width in (-.2, .2):
                vertices.append(((2+width*math.cos(angle/2))*math.cos(angle),
                                 (2+width*math.cos(angle/2))*math.sin(angle), width*math.sin(angle/2)))
        faces = []
        for i in range(count):
            a, b = 2*i, 2*i+1
            c, d = (2*i+2, 2*i+3) if i+1<count else (1, 0)
            faces.extend([(a,c,d), (a,d,b)])
        source = mesh(vertices, faces)
        with self.assertRaisesRegex(ValidationError, "non-orientable"):
            process_mesh_geometry(source, "orient", {})
        result, stats = process_mesh_geometry(source, "repair_orientation", {})
        self.assertGreater(stats["removed_triangles"], 0)
        self.assertEqual(stats["after"]["inconsistent_edges"], 0)
        self.assertEqual(stats["after"]["nonmanifold_edges"], 0)
        self.assertLess(result["triangle_count"], source["triangle_count"])

    def test_registry_and_schema_fixture(self):
        from cadyx.capabilities import COMMAND_REGISTRY
        from tests.test_persisted_schemas import feature_document
        self.assertIn("process_mesh", COMMAND_REGISTRY.command_types)
        document, feature = feature_document("process_mesh")
        document.validate()
        feature["source_body_id"] = feature["body_id"]
        with self.assertRaises(ValidationError):
            document.validate()


if __name__ == "__main__":
    unittest.main()
