"""Behavioral checks for full-resolution point processing and persisted stage lineage."""
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from cadyx.command_scan import ScanCommands
from cadyx.errors import ValidationError
from cadyx.mesh_geometry import point_cloud_geometry
from cadyx.models import Document
from cadyx.point_cloud import normalize_cloud_parameters, process_points
from cadyx.scan import read_point_cloud_ply


class PointCloudProcessingTests(unittest.TestCase):
    def test_agent_analysis_route_and_separate_apply(self):
        import threading
        from cadyx.core import CadyxCore
        from cadyx.mcp_server import CadyxAPI, MCPApplication
        from cadyx.web import CadyxHTTPServer, make_request_handler
        from cadyx.mesh_geometry import mesh_ply_bytes
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            core = CadyxCore(directory)
            server = CadyxHTTPServer(("127.0.0.1", 0), make_request_handler(core))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.dict("os.environ", {"CADYX_API_URL": f"http://127.0.0.1:{server.server_port}"}):
                    api = CadyxAPI()
                    self.addCleanup(api.close)
                    app = MCPApplication(api)
                    project = core.create_project("Cloud agent test")
                    project_id = project["project"]["id"]
                    points = [(float(x), float(y), .08 * (-1)**(x+y)) for x in range(7) for y in range(7)]
                    core.upload_project_scan(project_id, "cloud.ply", mesh_ply_bytes(point_cloud_geometry(points)))
                    def call(tool, args):
                        response = app.call_tool(tool, {"project_id": project_id, **args})
                        self.assertNotIn("isError", response, response)
                        return response["structuredContent"]
                    imported = call("cadyx_execute_command", {"expected_revision": 0, "command": {"type": "import_project_point_cloud", "file": "cloud.ply"}})
                    body_id = imported["command_result"]["history_entry"]["result"]["body_id"]
                    before = core.workspace.load(project_id)
                    files_before = core.workspace.list_project_files(project_id)
                    detected = call("cadyx_analyze_point_cloud", {"expected_revision": 1, "body_id": body_id, "method": "smooth", "parameters": {"radius": 2.1, "strength": 50, "iterations": 2}})
                    self.assertTrue(detected["read_only"])
                    self.assertGreater(detected["stats"]["moved_points"], 0)
                    self.assertEqual(core.workspace.load(project_id), before)
                    self.assertEqual(core.workspace.list_project_files(project_id), files_before)
                    applied = call("cadyx_execute_command", {"expected_revision": 1, "command": {"type": "process_point_cloud", "body_id": body_id, "method": "smooth", "parameters": detected["parameters"]}})
                    self.assertEqual(applied["revision"], 2)
                    saved = core.workspace.load(project_id)
                    self.assertEqual(saved["document"]["objects"][-1]["method"], "smooth")
                    stale = app.call_tool("cadyx_analyze_point_cloud", {"project_id": project_id, "expected_revision": 1, "body_id": body_id, "method": "smooth"})
                    self.assertTrue(stale["isError"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_overlap_removes_near_layers_not_distant_layers_or_base_surface(self):
        base = [(float(x), float(y), 0.) for x in range(7) for y in range(7)]
        near = [(x, y, .1) for x, y, _ in base]
        far = [(x, y, .8) for x, y, _ in base]
        parameters = {"distance": .2, "radius": 2., "tangential_distance": .05}
        self.assertEqual(process_points(base + near, "remove_overlaps", parameters), base)
        self.assertEqual(process_points(base + far, "remove_overlaps", parameters), base + far)
        # A rotated patch uses its local normal, rather than global vertical Z.
        rotated = [(z, x, y) for x, y, z in base + near]
        self.assertEqual(process_points(rotated, "remove_overlaps", parameters), rotated[:len(base)])

    def test_overlap_neighborhood_search_handles_dense_single_view_surface(self):
        points = [(x * .1, y * .1, 0.) for x in range(80) for y in range(80)]

        result = process_points(points, "remove_overlaps", {"distance": .56, "radius": 2.,
                                                              "tangential_distance": .1})

        self.assertGreaterEqual(len(result), 3)
        self.assertLess(len(result), len(points))
        self.assertEqual(len(points), 80 * 80)

    def test_smoothing_strength_and_passes_reduce_normal_noise(self):
        points = [(float(x), float(y), .08 * (-1)**(x+y)) for x in range(9) for y in range(9)]
        def roughness(p):
            return sum(v[2]**2 for v in p)
        weak = process_points(points, "smooth", {"strength": 10, "iterations": 1, "radius": 2.1})
        strong = process_points(points, "smooth", {"strength": 50, "iterations": 1, "radius": 2.1})
        repeated = process_points(points, "smooth", {"strength": 50, "iterations": 3, "radius": 2.1})
        self.assertEqual(len(repeated), len(points))
        self.assertLess(roughness(weak), roughness(points))
        self.assertLess(roughness(strong), roughness(weak))
        self.assertLess(roughness(repeated), roughness(strong))
        self.assertEqual(points[0], (0., 0., .08))

    def test_surface_tools_preserve_sparse_and_collinear_regions(self):
        points = [(float(i), 0., 0.) for i in range(12)]
        for method in ("smooth", "remove_overlaps"):
            self.assertEqual(process_points(points, method, {"radius": 3.}), points)
            self.assertEqual(process_points(points, method, {"radius": 1.}), points)

    def test_surface_parameter_validation(self):
        for method, parameters in [("smooth", {"strength": True}), ("smooth", {"iterations": 1.5}),
                                   ("smooth", {"strength": 101}), ("smooth", {"radius": float("nan")}),
                                   ("remove_overlaps", {"distance": 2.}), ("remove_overlaps", {"unknown": 1})]:
            with self.subTest(method=method, parameters=parameters):
                with self.assertRaises(ValidationError):
                    normalize_cloud_parameters(method, parameters)
        for rate in (True, 0, -1, 51, float("nan")):
            with self.subTest(rate=rate), self.assertRaises(ValidationError):
                normalize_cloud_parameters("isolation_filter", {"rate": rate})

    def test_isolation_rate_caps_removals_and_preserves_uniform_surface(self):
        surface = [(float(x), float(y), 0.) for x in range(10) for y in range(10)]
        isolated = [(30. + i * 3, 30., 30.) for i in range(10)]
        points = surface + isolated
        low = process_points(points, "isolation_filter", {"rate": 5})
        high = process_points(points, "isolation_filter", {"rate": 15})
        self.assertEqual(len(points) - len(low), 5)
        self.assertEqual(len(points) - len(high), 10)
        self.assertEqual(low[:len(surface)], surface)
        self.assertEqual(process_points(surface, "isolation_filter", {"rate": 15}), surface)

    def test_uniform_and_geometric_simplification_respect_ratio_and_geometry(self):
        points = [(float(x), float(y), .1 * ((x * y) % 3)) for x in range(20) for y in range(15)]
        uniform = process_points(points, "simplify", {"strategy": "uniform", "ratio": 40})
        geometric = process_points(points, "simplify", {"strategy": "geometric", "ratio": 40})
        self.assertEqual(len(uniform), 180)
        self.assertEqual(len(set(uniform)), len(uniform))
        self.assertGreaterEqual(len(geometric), 3)
        self.assertLess(len(geometric), len(points))
        self.assertTrue(all(all(min(point[axis] for point in points) <= value <= max(point[axis] for point in points)
                                for axis, value in enumerate(candidate)) for candidate in geometric))
        self.assertEqual(points[0], (0., 0., 0.))

    def test_kd_tree_bounds_and_queries_match_brute_force(self):
        from cadyx.point_cloud_surface import _build_kd_tree, _nearest_points, _points_in_box

        points = [(float((i * 37) % 17), float((i * 19) % 23), float((i * 11) % 13))
                  for i in range(128)] + [(0., 0., 0.)] * 4
        tree = _build_kd_tree(points, list(range(len(points))))

        def check_bounds(node):
            indices = (node[3] if node[3] is not None
                       else check_bounds(node[4]) + check_bounds(node[5]))
            for axis in range(3):
                self.assertEqual(node[0][axis], min(points[i][axis] for i in indices))
                self.assertEqual(node[1][axis], max(points[i][axis] for i in indices))
            return indices

        self.assertEqual(sorted(check_bounds(tree)), list(range(len(points))))
        for query, radius in [((0., 0., 0.), math.inf), ((5., 10., 6.), 8.),
                              ((100., 100., 100.), 4.)]:
            with self.subTest(query=query, radius=radius):
                expected = sorted((math.dist(query, point), index)
                                  for index, point in enumerate(points)
                                  if math.dist(query, point) <= radius)[:7]
                actual = _nearest_points(points, tree, query, radius, 7, [0],
                                         budget_message="budget exceeded")
                self.assertEqual(actual, expected)
        minimum, maximum = (3., 4., 2.), (8., 12., 9.)
        expected = {index for index, point in enumerate(points)
                    if all(minimum[axis] <= point[axis] <= maximum[axis] for axis in range(3))}
        self.assertEqual(set(_points_in_box(points, tree, minimum, maximum)), expected)

    def test_nearest_neighbors_handle_large_and_subnormal_distances(self):
        from cadyx.point_cloud_surface import _build_kd_tree, _nearest_points

        for scale in (1e-200, 1e200):
            with self.subTest(scale=scale):
                points = [(0., 0., 0.), (scale, 0., 0.),
                          (0., scale, 0.), (scale, scale, 0.)]
                tree = _build_kd_tree(points, list(range(len(points))))
                neighbors = _nearest_points(points, tree, points[0], scale * 1.1, 4, [0],
                                            budget_message="budget exceeded")
                self.assertEqual([index for _, index in neighbors], [0, 1, 2])
                self.assertEqual([distance for distance, _ in neighbors], [0., scale, scale])

    def test_isolation_filter_does_not_join_diagonal_outlier_at_large_scale(self):
        scale = 1e200
        points = [(0., 0., 0.), (scale, 0., 0.), (0., scale, 0.),
                  (scale, scale, 0.), (3.5 * scale, 3.5 * scale, 0.)]
        self.assertEqual(process_points(points, "isolation_filter", {"rate": 20}), points[:4])

    def test_geometric_simplification_handles_large_finite_coordinates(self):
        # Squaring these finite bounds overflows, although the cloud is valid.
        points = [(float(x) * 1e154, float(y) * 1e154, 0.)
                  for x in range(20) for y in range(15)]
        result = process_points(points, "simplify", {"strategy": "geometric", "ratio": 40})
        self.assertGreaterEqual(len(result), 3)
        self.assertLess(len(result), len(points))
        self.assertTrue(all(math.isfinite(value) for point in result for value in point))

    def test_voxel_centroids_do_not_overflow_finite_coordinates(self):
        # Summing a voxel before dividing would overflow for either outer cluster.
        points = [(x, 0., 0.) for x in (-1e308, 0., 1e308) for _ in range(3)]
        result = process_points(points, "voxel_downsample", {"voxel_size": 1e308})
        self.assertEqual(result, [(-1e308, 0., 0.), (0., 0., 0.), (1e308, 0., 0.)])

    def test_surface_budget_fails_without_mutating_input(self):
        from unittest.mock import patch
        points = [(float(x), float(y), 0.) for x in range(4) for y in range(4)]
        before = list(points)
        with patch("cadyx.point_cloud_surface.MAX_SURFACE_COMPARISONS", 1):
            with self.assertRaises(ValidationError) as caught:
                process_points(points, "smooth", {"radius": 2.})
        self.assertEqual(caught.exception.code, "RESOURCE_LIMIT")
        self.assertEqual(points, before)

    def test_analysis_does_not_publish_or_change_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "cloud.ply"
            from cadyx.mesh_geometry import mesh_ply_bytes
            base = [(float(x), float(y), 0.) for x in range(7) for y in range(7)]
            path.write_bytes(mesh_ply_bytes(point_cloud_geometry(base + [(x,y,.1) for x,y,_ in base])))
            commands = ScanCommands()
            commands.kernel = Mock()
            commands.kernel.create_point_cloud.side_effect = point_cloud_geometry
            commands.resolve_project_file = lambda project_id, filename: root / filename
            def publish(project_id, filename, content):
                dest = root / "filtered.ply"
                dest.write_bytes(content)
                return {"file": dest.name}
            commands.publish_project_scan_output = Mock(side_effect=publish)
            document = Document.create()
            imported = commands._import_project_point_cloud(document, {"file": path.name}, project_id="project")
            before = document.to_dict()
            command = {"body_id": imported["body_id"], "method": "remove_overlaps",
                       "parameters": {"distance": .2, "radius": 2., "tangential_distance": .05}}
            detected = commands._process_point_cloud(document, command, project_id="project", analysis_only=True)
            self.assertEqual(document.to_dict(), before)
            commands.publish_project_scan_output.assert_not_called()
            self.assertEqual(list(root.iterdir()), [path])
            applied = commands._process_point_cloud(document, command, project_id="project")
            self.assertEqual(detected["stats"]["removed_points"], len(base))
            self.assertEqual(applied["stats"]["output_points"], detected["stats"]["output_points"])
            document.validate()
            self.assertEqual(Document.from_dict(document.to_dict()).to_dict(), document.to_dict())

    def test_registered_command_and_persisted_schema(self):
        from cadyx.capabilities import COMMAND_REGISTRY
        from tests.test_persisted_schemas import feature_document
        self.assertIn("process_point_cloud", COMMAND_REGISTRY.command_types)
        from cadyx.capabilities import COMMAND_CAPABILITIES
        process_schema = next(item for item in COMMAND_CAPABILITIES if item["type"] == "process_point_cloud")
        self.assertIn("simplify", process_schema["parameter_schemas"]["method"]["enum"])
        document, feature = feature_document("process_point_cloud")
        document.validate()
        self.assertEqual(Document.from_dict(document.to_dict()).to_dict(), document.to_dict())
        feature["previous_feature_id"] = feature["id"]
        with self.assertRaises(ValidationError):
            document.validate()

    def test_filters_and_counts(self):
        points = [(0., 0., 0.), (.1, 0., 0.), (.2, 0., 0.), (9., 9., 9.)]
        self.assertEqual(process_points(points, "radius_filter", {"radius": 1e308, "min_neighbors": 3}), points)
        self.assertEqual(process_points(points + points[:1], "deduplicate", {}), points)
        self.assertEqual(process_points(points, "radius_filter", {"radius": .21, "min_neighbors": 2}), points[:3])
        self.assertEqual(process_points(points, "crop", {"min": [-1, -1, -1], "max": [1, 1, 1]}), points[:3])
        with self.assertRaises(ValidationError):
            process_points(points, "radius_filter", {"radius": .01, "min_neighbors": 1})
        with self.assertRaises(ValidationError):
            process_points(points, "radius_filter", {"radius": True, "min_neighbors": 1})

    def test_voxels_use_centroids_including_negative_coordinates(self):
        points = [(-.2, 0., 0.), (-.4, 0., 0.), (1., 0., 0.), (2., 0., 0.)]
        result = process_points(points, "voxel_downsample", {"voxel_size": 1.})
        self.assertEqual(len(result), 3)
        self.assertAlmostEqual(result[0][0], -.3)

    def test_full_reader_keeps_every_point_and_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cloud.ply"
            from cadyx.mesh_geometry import mesh_ply_bytes
            points = [(float(i), 0., 0.) for i in range(125010)]
            path.write_bytes(mesh_ply_bytes(point_cloud_geometry(points)))
            full, digest, stats = read_point_cloud_ply(path, full_resolution=True)
            sampled, _, _ = read_point_cloud_ply(path)
            self.assertEqual(full, points)
            self.assertEqual(stats["point_count"], len(points))
            self.assertLess(len(sampled), len(full))

    def test_stages_read_previous_full_artifact_and_preserve_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "cloud.ply"
            path.write_text("ply\nformat ascii 1.0\nelement vertex 5\nproperty float x\nproperty float y\nproperty float z\nend_header\n0 0 0\n0 0 0\n1 0 0\n2 0 0\n99 0 0\n")
            original = path.read_bytes()
            commands = ScanCommands()
            commands.kernel = Mock()
            commands.kernel.create_point_cloud.side_effect = point_cloud_geometry
            commands.resolve_project_file = lambda project_id, filename: root / filename
            def publish(project_id, filename, content):
                dest = root / ("stage-" + str(len(list(root.iterdir()))) + ".ply")
                dest.write_bytes(content)
                return {"file": dest.name}
            commands.publish_project_scan_output = publish
            document = Document.create()
            imported = commands._import_project_point_cloud(document, {"file": path.name}, project_id="project")
            first = commands._process_point_cloud(document, {"body_id": imported["body_id"], "method": "deduplicate"}, project_id="project")
            second = commands._process_point_cloud(document, {"body_id": imported["body_id"], "method": "crop", "parameters": {"min": [-1, -1, -1], "max": [3, 1, 1]}}, project_id="project")
            self.assertEqual(first["stats"]["output_points"], 4)
            self.assertEqual(second["stats"]["input_points"], 4)
            self.assertEqual(second["stats"]["output_points"], 3)
            self.assertEqual(document.objects[-1]["previous_feature_id"], first["feature_id"])
            self.assertEqual(path.read_bytes(), original)
            document.validate()
            (root / second["artifact"]["file"]).write_bytes(b"changed")
            with self.assertRaises(ValidationError):
                commands._process_point_cloud(document, {"body_id": imported["body_id"], "method": "deduplicate"}, project_id="project")
            self.assertEqual(len(document.objects), 4)


if __name__ == "__main__":
    unittest.main()
