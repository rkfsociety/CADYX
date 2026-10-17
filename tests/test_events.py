import threading
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from cadyx.events import EventBroker


class EventBrokerTests(unittest.TestCase):
    def test_dashboard_elapsed_time_starts_once_freezes_and_resets_for_new_work(self):
        broker = EventBroker()
        pending = {"workflow": {"title": "Модель", "progress_percent": 0,
            "eta_seconds": None, "phases": [{"id": "build", "title": "Построение",
            "state": "pending"}]}, "tasks": []}
        active = {"workflow": {"title": "Модель", "progress_percent": 20,
            "eta_seconds": None, "phases": [{"id": "build", "title": "Построение",
            "state": "in_progress"}]}, "tasks": []}
        completed = {"workflow": {"title": "Модель", "progress_percent": 100,
            "eta_seconds": None, "phases": [{"id": "build", "title": "Построение",
            "state": "completed"}]}, "tasks": []}
        failed = {"workflow": {"title": "Модель", "progress_percent": 40,
            "eta_seconds": None, "phases": [{"id": "build", "title": "Построение",
            "state": "failed"}]}, "tasks": []}
        times = [datetime(2026, 1, 1, 0, 0, second, tzinfo=timezone.utc)
                 for second in (0, 2, 4, 7, 10, 15, 19)]

        with patch("cadyx.events.datetime") as clock:
            clock.now.side_effect = times
            clock.fromisoformat.side_effect = datetime.fromisoformat
            first = broker.publish_agent_dashboard("p", pending)
            started = broker.publish_agent_dashboard("p", active)
            progress = broker.publish_agent_dashboard("p", active)
            finished = broker.publish_agent_dashboard("p", completed)
            repeated = broker.publish_agent_dashboard("p", completed)
            resumed = broker.publish_agent_dashboard("p", active)
            failed_result = broker.publish_agent_dashboard("p", failed)

        self.assertIsNone(first["started_at"])
        self.assertIsNone(first["elapsed_seconds"])
        self.assertEqual(started["started_at"], times[1].isoformat())
        self.assertIsNone(started["elapsed_seconds"])
        self.assertEqual(progress["started_at"], started["started_at"])
        self.assertIsNone(progress["elapsed_seconds"])
        self.assertEqual(finished["elapsed_seconds"], 5)
        self.assertEqual(repeated["elapsed_seconds"], 5)
        self.assertEqual(resumed["started_at"], times[5].isoformat())
        self.assertIsNone(resumed["elapsed_seconds"])
        self.assertEqual(failed_result["elapsed_seconds"], 4)

    def test_legacy_dashboard_state_without_timer_fields_remains_valid(self):
        self.assertTrue(EventBroker._dashboard_state_is_valid({
            "workflow": None, "tasks": [], "revision": 1,
            "updated_at": "2026-01-01T00:00:00+00:00",
        }))

    def test_dashboard_persistence_does_not_hold_broker_lock(self):
        broker = EventBroker()
        persist_started = threading.Event()
        release_persist = threading.Event()
        dashboard_done = threading.Event()
        publish_done = threading.Event()

        def persist(_dashboards, _revision):
            persist_started.set()
            if not release_persist.wait(2.0):
                raise AssertionError("test did not release dashboard persistence")

        broker._persist_agent_dashboards = persist

        def publish_dashboard():
            try:
                broker.publish_agent_dashboard("project-1", {"workflow": None, "tasks": []})
            finally:
                dashboard_done.set()

        dashboard_thread = threading.Thread(target=publish_dashboard)
        dashboard_thread.start()
        self.assertTrue(persist_started.wait(1.0))

        def publish_event():
            try:
                broker.publish("probe", None, {"ok": True})
            finally:
                publish_done.set()

        event_thread = threading.Thread(target=publish_event)
        event_thread.start()
        try:
            self.assertTrue(
                publish_done.wait(0.5),
                "ordinary event publish was blocked by dashboard persistence",
            )
        finally:
            release_persist.set()

        event_thread.join(2.0)
        dashboard_thread.join(2.0)
        self.assertFalse(event_thread.is_alive())
        self.assertFalse(dashboard_thread.is_alive())
        self.assertTrue(dashboard_done.is_set())


if __name__ == "__main__":
    unittest.main()
