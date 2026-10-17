"""Direct tests for CadyxCore collaborators."""
from __future__ import annotations
import tempfile
import unittest
from cadyx.core import CadyxCore
from cadyx.core_services import CommandActivityService, EngineeringValidationService, ProjectLifecycleService, RenderingCacheService
from cadyx.runtime_cache import RuntimeCachePolicy

class CoreServiceCompositionTests(unittest.TestCase):
    def test_core_composes_services(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            core = CadyxCore(root, kernel=object())
            self.assertIsInstance(core._lifecycle, ProjectLifecycleService)
            self.assertIsInstance(core._command_activity, CommandActivityService)
            self.assertIsInstance(core._rendering, RenderingCacheService)
            self.assertIsInstance(core._engineering_validation, EngineeringValidationService)
    def test_lifecycle_state_is_owned_by_service(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            core = CadyxCore(root, kernel=object())
            created = core.create_project("service-owned-lifecycle")
            pid = created["project"]["id"]
            self.assertEqual(core.workspace_selection_snapshot(), {"type":"project","project_id":pid})
            self.assertNotIn("_workspace_selection", core.__dict__)
            self.assertIn("_workspace_selection", core._lifecycle.__dict__)
    def test_render_cache_state_is_owned_by_service(self) -> None:
        policy = RuntimeCachePolicy(validated_document_entries=2, scene_entries=3, png_entries=4, png_bytes=1024, viewport_snapshot_entries=5, viewport_snapshot_bytes=2048)
        with tempfile.TemporaryDirectory() as root:
            core = CadyxCore(root, kernel=object(), cache_policy=policy)
            self.assertIs(core._rendering._cache_policy, policy)
            self.assertNotIn("_scene_cache", core.__dict__)
            core._invalidate_project_runtime_caches("missing")

if __name__ == "__main__": unittest.main()
