"""Core/CLI/contract regression coverage for headless project checks."""
from __future__ import annotations

import contextlib
import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from cadyx.api_contracts import VALIDATION_OPERATIONS
from cadyx.check_project import main as check_main
from cadyx.core import CadyxCore
from cadyx.errors import ConflictError, ValidationError
from cadyx.mcp_server import TOOLS


class ProjectCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = str(Path(temp.name) / "projects")
        self.core = CadyxCore(self.root)
        self.project_id = self.core.create_project("Checker fixture")["project"]["id"]

    def test_empty_project_has_explicit_untested_and_strict_ci_failure(self) -> None:
        report = self.core.check_project(self.project_id, 0)
        self.assertEqual(report["revision"], 0)
        self.assertEqual(report["status"], "untested")
        self.assertTrue(report["passed"])
        self.assertEqual(report["counts"], {"pass": 1, "fail": 0, "untested": 2})
        strict = self.core.check_project(self.project_id, 0, strict=True)
        self.assertEqual(strict["status"], "untested")
        self.assertFalse(strict["passed"])
        self.assertEqual(self.core.workspace.load(self.project_id)["document"]["revision"], 0)

    def test_revision_and_budgets_reject_invalid_inputs(self) -> None:
        with self.assertRaises(ConflictError):
            self.core.check_project(self.project_id, 1)
        for options in ({"strict": "yes"}, {"max_bodies": 0},
                        {"time_budget_seconds": 0}, {"require_assembly": 1}):
            with self.subTest(options=options), self.assertRaises(ValidationError):
                self.core.check_project(self.project_id, 0, **options)

    def test_cancel_and_unsupported_assembly_drawings_remain_untested(self) -> None:
        report = self.core.check_project(
            self.project_id, 0, strict=True, require_assembly=True,
            require_drawings=True, cancelled=lambda: True,
        )
        stages = {stage["id"]: stage for stage in report["stages"]}
        self.assertIn("cancelled", stages["geometry"]["evidence"]["reason"])
        self.assertTrue(stages["assembly"]["required"])
        self.assertEqual(stages["drawings"]["status"], "untested")
        self.assertFalse(report["passed"])

    def test_specification_dispatch_reuses_existing_validator(self) -> None:
        spec = {"schema_version": 1, "title": "Fixture", "checks": [
            {"id": "bounds", "operation": "dimensions", "arguments": {
                "body_id": "body:test", "expected_dimensions": {"x": 1, "y": 1, "z": 1},
                "tolerance": 0,
            }},
        ]}
        evidence = {"status": "fail", "revision": 0, "method": "axis_aligned_bounding_box"}
        with patch.object(self.core, "validate_body_dimensions", return_value=evidence) as validator:
            report = self.core.check_project(self.project_id, 0, specification=spec)
        validator.assert_called_once()
        stage = next(s for s in report["stages"] if s["id"] == "specification")
        self.assertEqual(stage["status"], "fail")
        self.assertEqual(stage["evidence"]["checks"][0]["evidence"], evidence)
        self.assertFalse(report["passed"])

    def test_box_has_valid_geometry_and_eligible_step_preflight(self) -> None:
        from cadyx.models import OwnerType
        lease = self.core.acquire_lease(self.project_id, "agent", "checker")
        try:
            committed = self.core.execute_command(
                self.project_id, lease["lease_id"],
                {"type": "create_box", "width": 2, "height": 3, "depth": 5},
                expected_owner_type=OwnerType.AGENT, expected_revision=0,
            )
        finally:
            self.core.release_lease(self.project_id, lease["lease_id"])
        body_id = committed["history_entry"]["result"]["body_id"]
        report = self.core.check_project(self.project_id, 1, export_body_id=body_id, strict=True)
        stages = {stage["id"]: stage for stage in report["stages"]}
        self.assertEqual(stages["geometry"]["status"], "pass")
        self.assertEqual(stages["export_eligibility"]["status"], "pass")
        self.assertTrue(report["passed"])

    def _create_box_for_rebuild(self) -> None:
        from cadyx.models import OwnerType
        lease = self.core.acquire_lease(self.project_id, "agent", "rebuild-check")
        try:
            self.core.execute_command(
                self.project_id, lease["lease_id"],
                {"type": "create_box", "width": 2, "height": 3, "depth": 5},
                expected_owner_type=OwnerType.AGENT, expected_revision=0,
            )
        finally:
            self.core.release_lease(self.project_id, lease["lease_id"])

    def test_opt_in_rebuild_is_read_only_and_revision_bound(self) -> None:
        self._create_box_for_rebuild()
        before = self.core.workspace.load(self.project_id)
        report = self.core.check_project(
            self.project_id, 1, deterministic_rebuild=True, strict=True,
        )
        stage = next(s for s in report["stages"] if s["id"] == "deterministic_rebuild")
        self.assertEqual(stage["status"], "pass")
        self.assertEqual(stage["evidence"]["compared_bodies"], 1)
        self.assertEqual(stage["evidence"]["metric_difference_count"], 0)
        self.assertEqual(self.core.workspace.load(self.project_id), before)
        with self.assertRaises(ConflictError):
            self.core.check_project(self.project_id, 0, deterministic_rebuild=True)

    def test_rebuild_detects_divergent_geometry_and_does_not_save(self) -> None:
        self._create_box_for_rebuild()
        before = self.core.workspace.load(self.project_id)
        original = self.core.kernel.create_box
        def drifting_box(*, width: float, height: float, depth: float):
            return original(width=width + 1.0, height=height, depth=depth)
        with patch.object(self.core.kernel, "create_box", side_effect=drifting_box):
            report = self.core.check_project(self.project_id, 1, deterministic_rebuild=True)
        stage = next(s for s in report["stages"] if s["id"] == "deterministic_rebuild")
        self.assertEqual(stage["status"], "fail")
        self.assertGreater(stage["evidence"]["metric_difference_count"], 0)
        self.assertFalse(report["passed"])
        self.assertEqual(self.core.workspace.load(self.project_id), before)

    def test_unsupported_rebuild_remains_explicitly_untested(self) -> None:
        self._create_box_for_rebuild()
        from cadyx.rebuild_regression import run_rebuild_regression
        from cadyx.models import Document
        original = Document.from_dict(self.core.workspace.load(self.project_id)["document"])
        feature = next(obj for obj in original.objects if obj["type"] == "feature")
        feature["operation"] = "import_step"
        status, evidence = run_rebuild_regression(
            self.core, original, max_bodies=128, checkpoint=lambda: None,
        )
        self.assertEqual(status, "untested")
        self.assertIn("import_step", evidence["unsupported_operations"])

    def test_rebuild_input_contract_and_budget(self) -> None:
        with self.assertRaises(ValidationError):
            self.core.check_project(self.project_id, 0, deterministic_rebuild="yes")
        self._create_box_for_rebuild()
        report = self.core.check_project(
            self.project_id, 1, deterministic_rebuild=True,
            cancelled=lambda: True,
        )
        self.assertEqual(
            next(s["status"] for s in report["stages"] if s["id"] == "deterministic_rebuild"),
            "untested",
        )
        contract = VALIDATION_OPERATIONS["project_check"]
        self.assertIn("deterministic_rebuild", contract["input_schema"]["properties"])

    def test_cli_and_shared_contract(self) -> None:
        output = StringIO()
        with patch("cadyx.check_project.CadyxCore", return_value=self.core):
            with contextlib.redirect_stdout(output):
                exit_code = check_main(["--projects-dir", self.root,
                                        "--project-id", self.project_id,
                                        "--expected-revision", "0", "--strict",
                                        "--deterministic-rebuild"])
        self.assertEqual(exit_code, 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "untested")
        self.assertIn("deterministic_rebuild", {
            stage["id"] for stage in json.loads(output.getvalue())["stages"]
        })
        contract = VALIDATION_OPERATIONS["project_check"]
        self.assertIn("project-check", contract["path"])
        self.assertIn("cadyx_check_project", {tool["name"] for tool in TOOLS})


if __name__ == "__main__":
    unittest.main()
