from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cadyx.core import CadyxCore
from cadyx.errors import ConflictError, LeaseRequiredError, ValidationError
from cadyx.models import OwnerType


class ProjectLockingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.core = CadyxCore(str(Path(self.temporary_directory.name) / "projects"))
        self.project_id = self.core.create_project("Lock test")["project"]["id"]
        self.create_box = {
            "type": "create_box",
            "name": "Test box",
            "width": 20,
            "height": 10,
            "depth": 5,
        }

    def test_ai_cannot_edit_human_locked_project(self) -> None:
        human_lease = self.core.acquire_lease(self.project_id, "human", "person-1")
        with self.assertRaises(ConflictError):
            self.core.acquire_lease(self.project_id, "agent", "agent-1")
        with self.assertRaises(ConflictError):
            self.core.execute_command(
                self.project_id,
                human_lease["lease_id"],
                self.create_box,
                expected_owner_type=OwnerType.AGENT,
            )

    def test_human_cannot_edit_ai_locked_project(self) -> None:
        agent_lease = self.core.acquire_lease(self.project_id, "agent", "agent-1")
        with self.assertRaises(ConflictError):
            self.core.acquire_lease(self.project_id, "human", "person-1")
        with self.assertRaises(ConflictError):
            self.core.execute_command(
                self.project_id,
                agent_lease["lease_id"],
                self.create_box,
                expected_owner_type=OwnerType.HUMAN,
            )

    def test_commands_without_lease_are_rejected(self) -> None:
        with self.assertRaises(LeaseRequiredError):
            self.core.execute_command(self.project_id, None, self.create_box)
        detail = self.core.inspect_project(self.project_id)
        self.assertEqual(detail["document"]["revision"], 0)
        self.assertEqual(detail["history"], [])

    def test_release_allows_another_owner_to_acquire(self) -> None:
        agent_lease = self.core.acquire_lease(self.project_id, "agent", "agent-1")
        self.core.release_lease(self.project_id, agent_lease["lease_id"])
        human_lease = self.core.acquire_lease(self.project_id, "human", "person-1")
        self.assertEqual(human_lease["owner_type"], "human")
        self.assertEqual(self.core.inspect_project(self.project_id)["lock"]["state"], "HUMAN_LOCKED")

    def test_successful_command_saves_document_and_history_together(self) -> None:
        lease = self.core.acquire_lease(self.project_id, "agent", "agent-1")
        result = self.core.execute_command(self.project_id, lease["lease_id"], self.create_box)
        detail = self.core.inspect_project(self.project_id)
        self.assertEqual(result["document"]["revision"], 1)
        self.assertEqual(detail["document"]["revision"], 1)
        self.assertEqual(len(detail["history"]), 1)
        self.assertEqual(detail["history"][0]["actor_type"], "agent")
        self.assertEqual(len(detail["document"]["objects"]), 2)

    def test_set_parameter_updates_feature_and_body(self) -> None:
        lease = self.core.acquire_lease(self.project_id, "human", "person-1")
        created = self.core.execute_command(self.project_id, lease["lease_id"], self.create_box)
        feature_id = created["history_entry"]["result"]["feature_id"]
        self.core.execute_command(
            self.project_id,
            lease["lease_id"],
            {"type": "set_parameter", "feature_id": feature_id, "name": "width", "value": 40},
        )
        detail = self.core.inspect_project(self.project_id)
        body = next(item for item in detail["document"]["objects"] if item["type"] == "body")
        self.assertEqual(body["geometry"]["dimensions"]["width"], 40.0)
        self.assertEqual(detail["document"]["revision"], 2)

    def test_failed_command_does_not_change_saved_project(self) -> None:
        lease = self.core.acquire_lease(self.project_id, "human", "person-1")
        invalid = {"type": "create_box", "width": -1, "height": 2, "depth": 3}
        with self.assertRaises(ValidationError):
            self.core.execute_command(self.project_id, lease["lease_id"], invalid)
        detail = self.core.inspect_project(self.project_id)
        self.assertEqual(detail["document"]["revision"], 0)
        self.assertEqual(detail["history"], [])

    def test_expired_lease_requires_validated_recovery(self) -> None:
        now = [100.0]
        core = CadyxCore(
            str(Path(self.temporary_directory.name) / "recovery-projects"),
            lease_timeout_seconds=5,
            lease_clock=lambda: now[0],
        )
        project_id = core.create_project("Recovery test")["project"]["id"]
        lease = core.acquire_lease(project_id, "agent", "agent-1")
        now[0] += 6
        snapshot = core.inspect_project(project_id)["lock"]
        self.assertTrue(snapshot["recovery_required"])
        with self.assertRaises(ConflictError) as expired:
            core.execute_command(project_id, lease["lease_id"], self.create_box)
        self.assertEqual(expired.exception.code, "LEASE_EXPIRED")
        with self.assertRaises(ConflictError) as recovery_required:
            core.acquire_lease(project_id, "human", "person-1")
        self.assertEqual(recovery_required.exception.code, "LEASE_RECOVERY_REQUIRED")
        core.recover_expired_lease(project_id)
        self.assertEqual(core.inspect_project(project_id)["lock"]["state"], "FREE")
        core.acquire_lease(project_id, "human", "person-1")


if __name__ == "__main__":
    unittest.main()
