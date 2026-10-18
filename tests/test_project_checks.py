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

    def test_cli_and_shared_contract(self) -> None:
        output = StringIO()
        with patch("cadyx.check_project.CadyxCore", return_value=self.core):
            with contextlib.redirect_stdout(output):
                exit_code = check_main(["--projects-dir", self.root,
                                        "--project-id", self.project_id,
                                        "--expected-revision", "0", "--strict"])
        self.assertEqual(exit_code, 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "untested")
        contract = VALIDATION_OPERATIONS["project_check"]
        self.assertIn("project-check", contract["path"])
        self.assertIn("cadyx_check_project", {tool["name"] for tool in TOOLS})


if __name__ == "__main__":
    unittest.main()
