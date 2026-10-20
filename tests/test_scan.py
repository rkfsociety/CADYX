import hashlib
import os
import struct
import tempfile
import unittest
from pathlib import Path

from cadyx.core import CadyxCore
from cadyx.errors import ConflictError, NotFoundError, ValidationError
from cadyx.kernel import OCCTKernelAdapter
from cadyx.mesh_geometry import (
    geometry_from_native_buffer,
    mesh_ply_bytes,
    tessellate_mesh_geometry,
    validate_mesh_geometry,
)
from cadyx.scan import DEFAULT_SCAN_PARAMETERS, ScanProcessor, normalize_scan_parameters


def _native_mesh_buffer(*, source_kind=1, input_vertices=4, input_triangles=4):
    vertices = (
        (0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0), (0.0, 0.0, 1.0),
    )
    triangles = ((0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3))
    header = struct.pack("<4s6If", b"CYSM", 1, len(vertices), len(triangles),
                         source_kind, input_vertices, input_triangles, 0.75)
    positions = struct.pack("<12f", *(coordinate for vertex in vertices for coordinate in vertex))
    indices = struct.pack("<12I", *(index for triangle in triangles for index in triangle))
    return header + positions + indices


class _FakeNative:
    version = "0.20.0"

    def __init__(self):
        self.source_has_faces = None
        self.source_path = None
        self.source_bytes = None
        self.parameters = None

    def process(self, path, parameters, *, source_has_faces, progress=None):
        self.source_has_faces = source_has_faces
        self.source_path = Path(path)
        self.source_bytes = self.source_path.read_bytes()
        self.parameters = dict(parameters)
        if progress is not None:
            progress({"phase": "read_source"})
            progress({"phase": "serialize_mesh"})
        return _native_mesh_buffer(source_kind=1 if source_has_faces else 2,
                                   input_vertices=4, input_triangles=4 if source_has_faces else 0)


class _FakeScanProcessor:
    def __init__(self):
        self.calls = []

    def reconstruct(self, path, parameters, *, expected_sha256=None, progress=None):
        source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected_sha256 is not None and source_hash != expected_sha256:
            raise ValidationError("Source PLY changed since import; import the updated scan as a new feature.")
        geometry, stats = geometry_from_native_buffer(_native_mesh_buffer(source_kind=2))
        stats["processor_version"] = "0.20.0"
        if progress is not None:
            progress({"phase": "validate_source"})
            progress({"phase": "hash_source", "source_kind": "point_cloud"})
            progress({"phase": "prepare_parameters", "parameters": dict(parameters)})
            progress({"phase": "read_source"})
            progress({"phase": "serialize_mesh"})
            progress({"phase": "verify_source"})
            progress({"phase": "validate_result"})
        self.calls.append((path, dict(parameters), expected_sha256))
        return geometry, stats, source_hash


class ScanGeometryTests(unittest.TestCase):
    def test_native_mesh_descriptor_round_trips_through_geometry_kernel(self):
        geometry, metadata = geometry_from_native_buffer(_native_mesh_buffer())
        kernel = OCCTKernelAdapter()
        kernel.validate(geometry)
        mesh = kernel.tessellate(geometry)

        self.assertEqual(metadata["output_vertices"], 4)
        self.assertEqual(metadata["output_triangles"], 4)
        self.assertEqual(len(mesh["positions"]), 12)
        self.assertEqual(len(mesh["triangles"]), 12)
        self.assertEqual(mesh, tessellate_mesh_geometry(geometry))

    def test_mesh_descriptor_rejects_corruption_and_out_of_range_indices(self):
        geometry, _ = geometry_from_native_buffer(_native_mesh_buffer())
        geometry["data"] = geometry["data"][:-4] + "AAAA"
        with self.assertRaises(ValidationError):
            validate_mesh_geometry(geometry)

        malformed = bytearray(_native_mesh_buffer())
        struct.pack_into("<I", malformed, 32 + 4 * 12, 9)
        with self.assertRaises(ValidationError):
            geometry_from_native_buffer(bytes(malformed))

    def test_scan_parameters_are_bounded_and_integer_parameters_are_integral(self):
        self.assertEqual(normalize_scan_parameters({}), DEFAULT_SCAN_PARAMETERS)
        self.assertEqual(normalize_scan_parameters({"reconstruction_method": "ball_pivoting"})[
            "reconstruction_method"], "ball_pivoting")
        for method in (True, None, {}, "unknown"):
            with self.subTest(reconstruction_method=method), self.assertRaises(ValidationError):
                normalize_scan_parameters({"reconstruction_method": method})
        for value in (True, 1.5, 99, 100001):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                normalize_scan_parameters({"target_triangles": value})
        for value in (True, -0.01, 0.51, float("inf"), "0.1"):
            with self.subTest(density_quantile=value), self.assertRaises(ValidationError):
                normalize_scan_parameters({"poisson_density_quantile": value})
        self.assertEqual(normalize_scan_parameters({"poisson_density_quantile": 0.05})[
            "poisson_density_quantile"], 0.05)

    def test_editable_mesh_exports_as_binary_little_endian_ply(self):
        geometry, metadata = geometry_from_native_buffer(_native_mesh_buffer())
        encoded = mesh_ply_bytes(geometry)
        header, body = encoded.split(b"end_header\n", 1)

        self.assertIn(b"format binary_little_endian 1.0", header)
        self.assertIn(b"element vertex 4", header)
        self.assertIn(b"element face 4", header)
        self.assertEqual(len(body), metadata["output_vertices"] * 12 + metadata["output_triangles"] * 13)

    def test_processor_preflights_ply_and_selects_cloud_reader(self):
        header = (
            b"ply\nformat ascii 1.0\nelement vertex 4\n"
            b"property float x\nproperty float y\nproperty float z\nend_header\n"
        )
        native = _FakeNative()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cloud.ply"
            path.write_bytes(header + b"0 0 0\n1 0 0\n0 1 0\n0 0 1\n")
            original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            geometry, stats, source_hash = ScanProcessor(native).reconstruct(path, {})

        self.assertFalse(native.source_has_faces)
        self.assertEqual(native.parameters["reconstruction_method"], "poisson")
        self.assertEqual(source_hash, original_hash)
        self.assertEqual(stats["source_kind"], "point_cloud")
        self.assertEqual(stats["reconstruction_method"], "poisson")
        validate_mesh_geometry(geometry)

    def test_ball_pivoting_method_passes_through_native_scan_boundary(self):
        header = (
            b"ply\nformat ascii 1.0\nelement vertex 4\n"
            b"property float x\nproperty float y\nproperty float z\nend_header\n"
        )
        native = _FakeNative()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cloud.ply"
            path.write_bytes(header + b"0 0 0\n1 0 0\n0 1 0\n0 0 1\n")
            _, stats, _ = ScanProcessor(native).reconstruct(
                path, {"reconstruction_method": "ball_pivoting"},
            )
        self.assertEqual(native.parameters["reconstruction_method"], "ball_pivoting")
        self.assertEqual(stats["reconstruction_method"], "ball_pivoting")

    @unittest.skipUnless(os.name == "nt", "Windows native scan paths use the ASCII staging copy")
    def test_unicode_workspace_path_uses_verified_ascii_native_copy(self):
        header = (
            b"ply\nformat ascii 1.0\nelement vertex 4\n"
            b"property float x\nproperty float y\nproperty float z\nend_header\n"
        )
        source_bytes = header + b"0 0 0\n1 0 0\n0 1 0\n0 0 1\n"
        native = _FakeNative()
        with tempfile.TemporaryDirectory() as temporary:
            source_folder = Path(temporary) / "Исходные кадры"
            source_folder.mkdir()
            source_path = source_folder / "облако.ply"
            source_path.write_bytes(source_bytes)

            geometry, stats, source_hash = ScanProcessor(native).reconstruct(source_path, {})

            self.assertNotEqual(native.source_path, source_path)
            self.assertTrue(native.source_path.as_posix().isascii())
            self.assertFalse(native.source_path.exists())
            self.assertEqual(native.source_bytes, source_bytes)
            self.assertEqual(source_path.read_bytes(), source_bytes)
            self.assertEqual(source_hash, hashlib.sha256(source_bytes).hexdigest())
            self.assertEqual(stats["source_kind"], "point_cloud")
            validate_mesh_geometry(geometry)


class ScanCommandTests(unittest.TestCase):
    def test_failed_scan_is_visible_and_leaves_document_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scans").mkdir()
            core = CadyxCore(str(root), kernel=OCCTKernelAdapter(), scan_processor=_FakeScanProcessor())
            project_id = core.create_project("Failed scan visualization")["project"]["id"]
            lease = core.acquire_lease(project_id, "agent", "scan-failure-test")

            with self.assertRaises(NotFoundError):
                core.execute_command(project_id, lease["lease_id"], {
                    "type": "import_scan", "folder": "scans", "file": "missing.ply",
                })

            activity = core.events.activity_snapshot(project_id)[-1]
            self.assertEqual(activity["state"], "failed")
            self.assertEqual(activity["phase"], "starting")
            self.assertIn("not found", activity["error"].lower())
            self.assertEqual(core.inspect_project(project_id)["document"]["revision"], 0)

    def test_scan_import_and_parameter_edit_require_a_lease_and_persist_mesh(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            processor = _FakeScanProcessor()
            core = CadyxCore(str(root), kernel=OCCTKernelAdapter(), scan_processor=processor)
            source_folder = root / "scan-samples"
            source_folder.mkdir()
            source_path = source_folder / "cover.ply"
            source_path.write_bytes(
                b"ply\nformat ascii 1.0\nelement vertex 4\nelement face 4\nend_header\n"
                b"original source bytes\n"
            )
            original = source_path.read_bytes()
            project = core.create_project("Scan command test")["project"]
            project_id = project["id"]
            command = {
                "type": "import_scan", "folder": "scan-samples", "file": "cover.ply",
                "name": "Cover", "parameters": {"target_triangles": 5000},
            }

            with self.assertRaises(ConflictError):
                core.execute_command(project_id, None, command)
            lease = core.acquire_lease(project_id, "human", "scan-test")
            subscription_id, subscription = core.events.subscribe(project_id)
            imported = core.execute_command(project_id, lease["lease_id"], command)
            seen_events = []
            while not subscription.empty():
                seen_events.append(subscription.get_nowait())
            core.events.unsubscribe(subscription_id)
            feature_id = imported["history_entry"]["result"]["feature_id"]
            changed = core.execute_command(project_id, lease["lease_id"], {
                "type": "set_scan_parameter", "feature_id": feature_id,
                "name": "voxel_size", "value": 1.25,
            })

            snapshot = core.inspect_project(project_id)
            scene = core.render_project(project_id)
            feature = next(item for item in snapshot["document"]["objects"] if item["id"] == feature_id)
            body = next(item for item in snapshot["document"]["objects"] if item["id"] == feature["body_id"])

            self.assertEqual(imported["document"]["revision"], 1)
            self.assertEqual(changed["document"]["revision"], 2)
            self.assertEqual(feature["parameters"]["voxel_size"], 1.25)
            self.assertEqual(body["geometry"]["kernel"], "cadyx-mesh-v1")
            self.assertEqual(len(scene["objects"][0]["triangles"]), 12)
            self.assertEqual(source_path.read_bytes(), original)
            self.assertEqual(len(processor.calls), 2)
            artifact = imported["history_entry"]["result"]["artifact"]
            output_path = source_folder / artifact["file"]
            self.assertTrue(output_path.is_file())
            self.assertIn(b"format binary_little_endian 1.0", output_path.read_bytes())
            self.assertEqual(hashlib.sha256(output_path.read_bytes()).hexdigest(), artifact["sha256"])
            self.assertFalse(artifact["file"].startswith(".cadyx-tmp-"))
            activities = core.events.activity_snapshot(project_id)
            activity = activities[-1]
            self.assertEqual(len(activities), 2)
            self.assertEqual(activity["state"], "completed")
            self.assertEqual(activity["result"]["artifact"]["file"], artifact["file"])
            self.assertEqual(activity["parameters"]["target_triangles"], 5000)
            self.assertEqual(activity["source"], {"folder": "scan-samples", "file": "cover.ply"})
            phases = [item.data["phase"] for item in seen_events if item.event == "agent.activity"]
            self.assertIn("read_source", phases)
            self.assertIn("write_output", phases)
            self.assertTrue(any(item["name"] == artifact["file"]
                                for item in core.workspace.list_folder_files("scan-samples")))
            hidden_temporary = source_folder / ".cadyx-tmp-incomplete.part"
            hidden_temporary.write_bytes(b"partial")
            self.assertFalse(any(item["name"] == hidden_temporary.name
                                 for item in core.workspace.list_folder_files("scan-samples")))
            with self.assertRaises(NotFoundError):
                core.workspace.resolve_folder_file("scan-samples", hidden_temporary.name)
            repeated = core.workspace.publish_scan_output("scan-samples", "cover.ply", output_path.read_bytes())
            self.assertFalse(repeated["created"])
            output_path.write_bytes(b"user file occupying the generated name")
            with self.assertRaises(ConflictError):
                core.workspace.publish_scan_output("scan-samples", "cover.ply", mesh_ply_bytes(body["geometry"]))

            source_path.write_bytes(original + b"updated")
            before_revision = snapshot["document"]["revision"]
            with self.assertRaises(ValidationError):
                core.execute_command(project_id, lease["lease_id"], {
                    "type": "set_scan_parameter", "feature_id": feature_id,
                    "name": "poisson_depth", "value": 7,
                })
            self.assertEqual(core.inspect_project(project_id)["document"]["revision"], before_revision)

    def test_project_ply_import_preserves_source_and_reprocesses_from_staged_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            processor = _FakeScanProcessor()
            core = CadyxCore(str(root), kernel=OCCTKernelAdapter(), scan_processor=processor)
            project_id = core.create_project("Project scan import")["project"]["id"]
            source_path = root / "Back cover.ply"
            original = (
                b"ply\nformat binary_little_endian 1.0\nelement vertex 4\n"
                b"property float x\nproperty float y\nproperty float z\nend_header\nscan bytes"
            )
            source_path.write_bytes(original)
            uploaded = core.upload_project_scan(project_id, source_path.name, original)
            staged_path = core.workspace.resolve_project_file(project_id, source_path.name)
            self.assertEqual(uploaded["size"], len(original))
            self.assertEqual(staged_path.read_bytes(), original)

            lease = core.acquire_lease(project_id, "agent", "project-scan-test")
            imported = core.execute_command(project_id, lease["lease_id"], {
                "type": "import_project_scan", "file": source_path.name,
                "name": "Kobra S1 Back Cover", "parameters": {"target_triangles": 5000},
            })
            feature_id = imported["history_entry"]["result"]["feature_id"]
            aligned = core.execute_command(project_id, lease["lease_id"], {
                "type": "align_scan", "body_id": imported["history_entry"]["result"]["body_id"],
                "mode": "explicit", "basis": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                "origin": [0, 0, 0], "units": "unitless", "scale": 1.0, "ground_z": True,
            })
            changed = core.execute_command(project_id, lease["lease_id"], {
                "type": "set_scan_parameter", "feature_id": feature_id,
                "name": "voxel_size", "value": 1.25,
            })

            snapshot = core.inspect_project(project_id)
            feature = next(item for item in snapshot["document"]["objects"] if item["id"] == feature_id)
            body = next(item for item in snapshot["document"]["objects"] if item["id"] == feature["body_id"])
            artifact = changed["history_entry"]["result"]["artifact"]
            output_path = core.workspace.resolve_project_file(project_id, artifact["file"])
            self.assertEqual(snapshot["document"]["revision"], 3)
            self.assertEqual(feature["source"]["project_file"], source_path.name)
            self.assertEqual(feature["parameters"]["voxel_size"], 1.25)
            self.assertEqual(feature["alignment"]["units"], "unitless")
            self.assertTrue(changed["history_entry"]["result"]["alignment_reapplied"])
            self.assertEqual(aligned["history_entry"]["result"]["artifact"]["project"],
                             core.workspace._project_dir(project_id).name)
            self.assertEqual(body["geometry"]["kernel"], "cadyx-mesh-v1")
            self.assertEqual(source_path.read_bytes(), original)
            self.assertEqual(staged_path.read_bytes(), original)
            self.assertTrue(output_path.is_file())
            self.assertEqual(hashlib.sha256(output_path.read_bytes()).hexdigest(), artifact["sha256"])
            self.assertEqual(len(processor.calls), 2)



if __name__ == "__main__":
    unittest.main()
