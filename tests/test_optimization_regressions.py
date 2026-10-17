from __future__ import annotations

import unittest
from types import SimpleNamespace
from uuid import uuid4

from cadyx.curve_editing import _latest_version
from cadyx.errors import ValidationError
from cadyx.events import EventBroker
from cadyx.models import Document


class _MembershipTrap(list):
    def __contains__(self, value):
        raise AssertionError(f"linear feature-id membership scan used for {value!r}")


class _UnexpectedTraceLookup(dict):
    def get(self, key, default=None):
        raise AssertionError(f"trace-version scan continued after invalid metadata: {key!r}")


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

    def test_trace_version_lookup_handles_large_unrelated_documents_in_one_scan(self):
        trace_id = "trace-target"
        document = SimpleNamespace(
            objects=[
                *({"id": f"body:{index}", "type": "body"} for index in range(10_000)),
                {"type": "sketch", "trace_id": trace_id, "trace_version": 3},
                {"type": "sketch", "trace_id": "other", "trace_version": 99},
                {"type": "sketch", "trace_id": trace_id, "trace_version": 9},
                {"type": "sketch", "trace_id": trace_id, "trace_version": 4},
            ]
        )

        self.assertEqual(_latest_version(document, trace_id), 9)

    def test_trace_version_lookup_validates_immediately_without_materializing_matches(self):
        trace_id = "trace-target"
        document = SimpleNamespace(
            objects=[
                {"type": "sketch", "trace_id": trace_id, "trace_version": 0},
                _UnexpectedTraceLookup(),
            ]
        )

        with self.assertRaisesRegex(ValidationError, "Existing trace version metadata is invalid"):
            _latest_version(document, trace_id)


if __name__ == "__main__":
    unittest.main()
