import unittest
from types import SimpleNamespace
from urllib.parse import urlsplit
from cadyx.web_routes import _dispatch_files, match_route
class AgentFolderInventoryTests(unittest.TestCase):
    def test_agent_inventory_includes_folders(self):
        class Workspace:
            def list_project_files(self, project): return [{"name": "x.ply"}]
            def list_project_folders(self, project): return [{"name": "series"}]
            def list_project_subfolder_files(self, project, folder): return []
            def list_project_subfolders(self, project, folder): return [{"name": "nested"}]
        class Handler:
            def _json(self, code, data): self.data = data
        h = Handler()
        root = "/api/v1/agent/projects/p/files"
        _dispatch_files(h, SimpleNamespace(workspace=Workspace()), match_route("GET", root), urlsplit(root))
        self.assertEqual(h.data["folders"], [{"name": "series"}])
        child = "/api/v1/agent/projects/p/folders/series/files"
        _dispatch_files(h, SimpleNamespace(workspace=Workspace()), match_route("GET", child), urlsplit(child))
        self.assertEqual(h.data["folders"], [{"name": "nested"}])
