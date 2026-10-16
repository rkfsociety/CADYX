from __future__ import annotations

import unittest
from uuid import uuid4

from cadyx.events import EventBroker
from cadyx.models import Document


class _MembershipTrap(list):
    def __contains__(self, value):
        raise AssertionError(f"linear feature-id membership scan used for {value!r}")


class _CountingEventBroker(EventBroker):
    def __init__(self) -> None:
        self.active_project_scan_calls = 0
        super().__init__()

    def _agent_work_project_ids_locked(self) -> list[str]:
        self.active_project_scan_calls += 1
        return super()._agent_work_project_ids_locked()

    def _persist_agent_dashboards(self, dashboards, revision) -> None:
        return None


class OptimizationRegressionTests(unittest.TestCase):
    def test_document_feature_membership_uses_prebuilt_index(self):
        body_id = f"body:{uuid4()}"
        feature_ids = _MembershipTrap(f"feature:{uuid4()}" for _ in range(64))
        document = Document(
            id=f"document:{uuid4()}",
            objects=[
                {"id": body_id, "type": "body", "feature_ids": feature_ids},
                *(
                    {"id": feature_id, "type": "feature", "body_id": body_id, "operation": "noop"}
                    for feature_id in feature_ids
                ),
            ],
        )

        document.validate()

    def test_dashboard_publish_scans_active_projects_once_and_keeps_input_isolated(self):
        broker = _CountingEventBroker()
        payload = {
            "workflow": None,
            "tasks": [{"id": "task-1", "title": "Original", "state": "pending"}],
        }

        published = broker.publish_agent_dashboard("project-1", payload)

        self.assertEqual(broker.active_project_scan_calls, 1)
        self.assertTrue(broker.agent_work_snapshot()["active"])
        payload["tasks"][0]["title"] = "Mutated"
        self.assertEqual(published["tasks"][0]["title"], "Original")
        self.assertEqual(broker.agent_dashboard_snapshot("project-1")["tasks"][0]["title"], "Original")


if __name__ == "__main__":
    unittest.main()
