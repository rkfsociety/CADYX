import threading
import unittest

from cadyx.events import EventBroker


class EventBrokerTests(unittest.TestCase):
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
