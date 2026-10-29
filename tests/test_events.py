import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
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

    def test_malformed_dashboard_fields_fail_before_publishing(self):
        broker = EventBroker()
        subscriber_id, events = broker.subscribe()
        workflow = {
            "title": "Build", "progress_percent": 25, "eta_seconds": 10,
            "phases": [{"id": "build", "title": "Build", "state": "in_progress"}],
        }
        task = {"id": "test", "title": "Test", "state": "running"}
        invalid_reports = [
            {"workflow": {**workflow, "eta_seconds": -1}, "tasks": []},
            {"workflow": {**workflow, "phases": [{**workflow["phases"][0], "id": []}]}, "tasks": []},
            {"workflow": {**workflow, "phases": [{**workflow["phases"][0], "title": 42}]}, "tasks": []},
            {"workflow": {**workflow, "phases": [{**workflow["phases"][0], "state": []}]}, "tasks": []},
            {"workflow": None, "tasks": [{**task, "id": []}]},
            {"workflow": None, "tasks": [{**task, "title": 42}]},
            {"workflow": None, "tasks": [{**task, "state": []}]},
        ]
        try:
            for report in invalid_reports:
                with self.subTest(report=report):
                    with self.assertRaises(ValueError):
                        broker.publish_agent_dashboard("p", report)
                    self.assertEqual(broker.agent_dashboard_revision("p"), 0)
                    self.assertEqual(broker.agent_work_snapshot()["revision"], 0)
                    self.assertTrue(events.empty())
        finally:
            broker.unsubscribe(subscriber_id)

    def test_legacy_dashboard_state_without_timer_fields_remains_valid(self):
        self.assertTrue(EventBroker._dashboard_state_is_valid({
            "workflow": None, "tasks": [], "revision": 1,
            "updated_at": "2026-01-01T00:00:00+00:00",
        }))

    def test_malformed_persisted_dashboard_state_fails_closed(self):
        valid_dashboard = {
            "workflow": None,
            "tasks": [],
            "revision": 1,
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        invalid_states = [
            {"version": 2, "agent_work_revision": 0, "dashboards": {}},
            {"version": 1, "agent_work_revision": -1, "dashboards": {}},
            {"version": 1, "agent_work_revision": 0, "dashboards": []},
            {"version": 1, "agent_work_revision": 0, "dashboards": {"p": {**valid_dashboard, "revision": 0}}},
            {"version": 1, "agent_work_revision": 0, "dashboards": {"p": {**valid_dashboard, "elapsed_seconds": 1}}},
            {"version": 1, "agent_work_revision": 0, "dashboards": {"p": {**valid_dashboard, "started_at": "bad"}}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dashboards.json"
            for index, state in enumerate(invalid_states):
                with self.subTest(index=index):
                    path.write_text(json.dumps(state), encoding="utf-8")
                    with self.assertRaisesRegex(RuntimeError, "could not be loaded"):
                        EventBroker(dashboard_state_path=path)

    def test_legacy_dashboard_json_migrates_to_transactional_store(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy_path = Path(directory) / "dashboards.json"
            database_path = Path(directory) / "dashboards.sqlite3"
            legacy_path.write_text(json.dumps({
                "version": 1,
                "agent_work_revision": 7,
                "dashboards": {"project-a": {
                    "workflow": None, "tasks": [], "revision": 3,
                    "updated_at": "2026-01-01T00:00:00+00:00",
                }},
            }), encoding="utf-8")

            broker = EventBroker(dashboard_state_path=legacy_path,
                                 dashboard_database_path=database_path)

            self.assertEqual(broker.agent_dashboard_revision("project-a"), 3)
            self.assertEqual(broker.agent_work_snapshot()["revision"], 7)
            self.assertTrue(database_path.is_file())
            restarted = EventBroker(dashboard_state_path=legacy_path,
                                    dashboard_database_path=database_path)
            self.assertEqual(restarted.agent_dashboard_snapshot("project-a"),
                             broker.agent_dashboard_snapshot("project-a"))

    def test_dashboard_persistence_updates_only_the_reported_project_row(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "dashboards.sqlite3"
            broker = EventBroker(dashboard_database_path=database_path)
            first = broker.publish_agent_dashboard("project-a", {
                "workflow": None,
                "tasks": [{"id": "x", "title": "first", "state": "completed"}],
            })
            other = broker.publish_agent_dashboard("project-b", {
                "workflow": None,
                "tasks": [{"id": "x", "title": "other", "state": "completed"}],
            })
            updated = broker.publish_agent_dashboard("project-a", {
                "workflow": None,
                "tasks": [{"id": "x", "title": "updated", "state": "completed"}],
            })

            with closing(sqlite3.connect(database_path)) as connection:
                rows = dict(connection.execute(
                    "SELECT project_id, payload FROM dashboards"
                ))
                revision = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'agent_work_revision'"
                ).fetchone()[0]

            self.assertEqual(set(rows), {"project-a", "project-b"})
            self.assertEqual(json.loads(rows["project-a"])["tasks"][0]["title"], "updated")
            self.assertEqual(json.loads(rows["project-b"])["tasks"][0]["title"], "other")
            self.assertEqual((first["revision"], other["revision"], updated["revision"]), (1, 1, 2))
            self.assertEqual(revision, 3)
            self.assertEqual(EventBroker(dashboard_database_path=database_path)
                             .agent_work_snapshot()["revision"], 3)

    def test_prune_missing_project_dashboards_persists_and_publishes_active_work(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "dashboards.sqlite3"
            broker = EventBroker(dashboard_database_path=database_path)
            subscriber_id, events = broker.subscribe()
            active = {
                "workflow": {"title": "Работа", "progress_percent": 20,
                             "eta_seconds": None, "phases": [
                                 {"id": "build", "title": "Сборка", "state": "in_progress"}]},
                "tasks": [],
            }
            broker.publish_agent_dashboard("missing-project", active)
            broker.publish_agent_dashboard("live-project", active)
            try:
                self.assertEqual(broker.prune_agent_dashboards({"live-project"}), ["missing-project"])
                self.assertEqual(broker.agent_work_snapshot(), {
                    "active": True, "active_project_ids": ["live-project"], "revision": 3,
                })
                self.assertEqual(broker.agent_dashboard_snapshot("missing-project")["revision"], 0)
                self.assertEqual(EventBroker(dashboard_database_path=database_path)
                                 .agent_work_snapshot(), broker.agent_work_snapshot())
                with closing(sqlite3.connect(database_path)) as connection:
                    self.assertEqual(connection.execute(
                        "SELECT project_id FROM dashboards ORDER BY project_id"
                    ).fetchall(), [("live-project",)])
                workspace_event = list(events.queue)[-1]
                self.assertEqual(workspace_event.event, "workspace.agent-work")
                self.assertEqual(workspace_event.data["project_ids"], ["live-project"])
            finally:
                broker.unsubscribe(subscriber_id)

    def test_dashboard_database_failure_does_not_publish_uncommitted_work(self):
        with tempfile.TemporaryDirectory() as directory:
            broker = EventBroker(dashboard_database_path=Path(directory) / "dashboards.sqlite3")
            subscriber_id, events = broker.subscribe()
            try:
                with patch.object(broker, "_persist_agent_dashboard",
                                  side_effect=sqlite3.OperationalError("disk failure")):
                    with self.assertRaises(sqlite3.OperationalError):
                        broker.publish_agent_dashboard("project-a", {
                            "workflow": None, "tasks": [],
                        })

                self.assertEqual(broker.agent_dashboard_revision("project-a"), 0)
                self.assertEqual(broker.agent_work_snapshot()["revision"], 0)
                self.assertTrue(events.empty())
            finally:
                broker.unsubscribe(subscriber_id)

    def test_failed_event_encoding_does_not_consume_ids_or_activity_revisions(self):
        broker = EventBroker()
        subscriber_id, events = broker.subscribe()
        try:
            with self.assertRaises(TypeError):
                broker.publish("agent.activity", "project-a", {
                    "operation_id": "invalid", "payload": object(),
                })
            self.assertEqual(broker.activity_state_snapshot("project-a"), ([], 0))
            self.assertTrue(events.empty())

            with self.assertRaises(TypeError):
                broker.publish("probe", "project-a", {"payload": object()})
            self.assertTrue(events.empty())

            published = broker.publish("agent.activity", "project-a", {
                "operation_id": "valid", "state": "running",
            })
            self.assertEqual((published.id, published.data["revision"]), (1, 1))
            self.assertEqual(broker.activity_revision("project-a"), 1)
            self.assertEqual(broker.activity_snapshot("project-a")[0]["operation_id"], "valid")
            self.assertEqual(events.get_nowait().id, 1)
            self.assertTrue(events.empty())
        finally:
            broker.unsubscribe(subscriber_id)

    def test_replay_filters_project_and_keeps_order_without_duplicates(self):
        broker = EventBroker()
        first = broker.publish("probe", "a", {"n": 1})
        broker.publish("probe", "b", {"n": 2})
        last = broker.publish("probe", "a", {"n": 3})
        subscriber_id, events = broker.subscribe("a", last_event_id=broker.event_cursor(first.id))
        try:
            self.assertEqual(events.get_nowait().id, last.id)
            self.assertTrue(events.empty())
            following = broker.publish("probe", "a", {"n": 4})
            self.assertEqual(events.get_nowait().id, following.id)
            self.assertTrue(events.empty())
        finally:
            broker.unsubscribe(subscriber_id)

    def test_replay_rejects_expired_future_invalid_and_restarted_cursors(self):
        broker = EventBroker(replay_capacity=2)
        first = broker.publish("probe", "a", {"n": 1})
        broker.publish("probe", "a", {"n": 2})
        broker.publish("probe", "a", {"n": 3})
        for cursor, reason in (
            (broker.event_cursor(first.id), "history_expired"),
            (broker.event_cursor(999), "invalid_cursor"),
            ("not-a-cursor", "invalid_cursor"),
            (EventBroker().event_cursor(first.id), "stream_changed"),
        ):
            with self.subTest(reason=reason):
                subscription_id, events = broker.subscribe("a", last_event_id=cursor)
                try:
                    gap = events.get_nowait()
                    self.assertEqual((gap.event, gap.data["reason"]), ("stream.gap", reason))
                    self.assertEqual(gap.id, 3)
                    self.assertTrue(events.empty())
                finally:
                    broker.unsubscribe(subscription_id)

    def test_replay_byte_and_subscriber_budgets_fail_without_partial_replay(self):
        broker = EventBroker(subscriber_capacity=2, replay_capacity=10, replay_max_bytes=2048)
        first = broker.publish("probe", None, {"n": 1})
        for n in (2, 3, 4):
            broker.publish("probe", None, {"n": n})
        subscription_id, events = broker.subscribe(last_event_id=broker.event_cursor(first.id))
        try:
            self.assertEqual(events.get_nowait().data["reason"], "subscriber_capacity")
            self.assertTrue(events.empty())
        finally:
            broker.unsubscribe(subscription_id)
        oversized = broker.publish("probe", None, {"payload": "x" * 4096})
        next_event = broker.publish("probe", None, {"n": 5})
        self.assertLessEqual(broker._replay_bytes, broker._replay_max_bytes)
        subscription_id, events = broker.subscribe(last_event_id=broker.event_cursor(oversized.id - 1))
        try:
            self.assertEqual(events.get_nowait().data["reason"], "history_expired")
        finally:
            broker.unsubscribe(subscription_id)
        subscription_id, events = broker.subscribe(last_event_id=broker.event_cursor(oversized.id))
        try:
            self.assertEqual(events.get_nowait().id, next_event.id)
        finally:
            broker.unsubscribe(subscription_id)

    def test_live_events_keep_published_payload_when_caller_mutates_it(self):
        broker = EventBroker()
        subscriber_id, events = broker.subscribe("a")
        try:
            source = {"child": {"n": 1}, "values": [1]}
            published = broker.publish("probe", "a", source)
            source["child"]["n"] = 999
            source["values"].append(2)
            source["new"] = "late"

            live = events.get_nowait()
            expected = {"child": {"n": 1}, "values": [1]}
            self.assertEqual(published.data, expected)
            self.assertEqual(live.data, expected)
            self.assertTrue(events.empty())

            replay_id, replay = broker.subscribe(
                "a", last_event_id=broker.event_cursor(0),
            )
            try:
                self.assertEqual(replay.get_nowait().data, expected)
            finally:
                broker.unsubscribe(replay_id)
        finally:
            broker.unsubscribe(subscriber_id)

    def test_live_subscribers_and_publish_result_have_independent_payloads(self):
        broker = EventBroker()
        first_id, first_events = broker.subscribe("a")
        second_id, second_events = broker.subscribe("a")
        try:
            expected = {"child": {"n": 1}, "values": [1]}
            published = broker.publish("probe", "a", expected)
            first = first_events.get_nowait()
            second = second_events.get_nowait()
            self.assertIsNot(first, second)
            self.assertIsNot(first, published)

            first.data["child"]["n"] = 10
            first.data["values"].append(2)
            self.assertEqual(second.data, expected)
            self.assertEqual(published.data, expected)

            published.data["child"]["n"] = 20
            self.assertEqual(second.data, expected)
            serialized = second.as_dict()
            serialized["data"]["child"]["n"] = 30
            self.assertEqual(second.data, expected)

            replay_id, replay = broker.subscribe(
                "a", last_event_id=broker.event_cursor(0),
            )
            try:
                self.assertEqual(replay.get_nowait().data, expected)
            finally:
                broker.unsubscribe(replay_id)
        finally:
            broker.unsubscribe(first_id)
            broker.unsubscribe(second_id)

    def test_replay_keeps_immutable_encoded_event_data(self):
        broker = EventBroker()
        source = {"child": {"n": 1}}
        broker.publish("probe", "a", source)
        source["child"]["n"] = 999
        subscription_id, events = broker.subscribe("a", last_event_id=broker.event_cursor(0))
        try:
            self.assertEqual(events.get_nowait().data["child"]["n"], 1)
        finally:
            broker.unsubscribe(subscription_id)

    def test_dashboard_revisions_and_event_ids_are_monotonic(self):
        broker = EventBroker()
        subscriber_id, events = broker.subscribe()
        try:
            first = broker.publish_agent_dashboard("p", {"workflow": None, "tasks": []})
            second = broker.publish_agent_dashboard("p", {"workflow": None, "tasks": []})
            self.assertEqual((first["revision"], second["revision"]), (1, 2))
            self.assertEqual(broker.agent_work_snapshot()["revision"], 2)
            published = [events.get_nowait() for _ in range(4)]
            self.assertEqual([item.id for item in published], [1, 2, 3, 4])
            self.assertEqual(
                [item.event for item in published],
                ["agent.dashboard", "workspace.agent-work", "agent.dashboard", "workspace.agent-work"],
            )
        finally:
            broker.unsubscribe(subscriber_id)

    def test_project_filter_and_drop_oldest_delivery_are_shared(self):
        broker = EventBroker(subscriber_capacity=2)
        project_subscriber_id, project_events = broker.subscribe("project-a")
        global_subscriber_id, global_events = broker.subscribe()
        try:
            broker.publish("probe", "project-b", {"n": 1})
            broker.publish("probe", "project-a", {"n": 2})
            broker.publish("probe", "project-a", {"n": 3})
            self.assertEqual([project_events.get_nowait().data["n"] for _ in range(2)], [2, 3])
            self.assertEqual([global_events.get_nowait().data["n"] for _ in range(2)], [2, 3])

            broker.publish_agent_dashboard("project-a", {
                "workflow": {"title": "Работа", "progress_percent": 0, "eta_seconds": None,
                             "phases": [{"id": "build", "title": "Сборка", "state": "pending"}]},
                "tasks": [],
            })
            dashboard_event = project_events.get_nowait()
            self.assertEqual((dashboard_event.event, dashboard_event.project_id),
                             ("agent.dashboard", "project-a"))
            self.assertTrue(project_events.empty(), "project filter must exclude workspace-global events")
            global_after = [global_events.get_nowait(), global_events.get_nowait()]
            self.assertEqual([item.event for item in global_after],
                             ["agent.dashboard", "workspace.agent-work"])
        finally:
            broker.unsubscribe(project_subscriber_id)
            broker.unsubscribe(global_subscriber_id)

    def test_active_work_transitions_preserve_global_lock_semantics(self):
        broker = EventBroker()
        pending = {
            "workflow": {"title": "Работа", "progress_percent": 0, "eta_seconds": None,
                         "phases": [{"id": "build", "title": "Сборка", "state": "pending"}]},
            "tasks": [],
        }
        running = {
            "workflow": {"title": "Работа", "progress_percent": 50, "eta_seconds": None,
                         "phases": [{"id": "build", "title": "Сборка", "state": "in_progress"}]},
            "tasks": [],
        }
        completed = {
            "workflow": {"title": "Работа", "progress_percent": 100, "eta_seconds": None,
                         "phases": [{"id": "build", "title": "Сборка", "state": "completed"}]},
            "tasks": [],
        }
        broker.publish_agent_dashboard("p", pending)
        self.assertEqual(broker.agent_work_snapshot(), {
            "active": True, "active_project_ids": ["p"], "revision": 1,
        })
        broker.publish_agent_dashboard("p", running)
        self.assertTrue(broker.agent_dashboard_is_active("p"))
        broker.publish_agent_dashboard("p", completed)
        self.assertEqual(broker.agent_work_snapshot(), {
            "active": False, "active_project_ids": [], "revision": 3,
        })

    def test_dashboard_persistence_does_not_hold_broker_lock(self):
        broker = EventBroker()
        persist_started = threading.Event()
        release_persist = threading.Event()
        dashboard_done = threading.Event()
        publish_done = threading.Event()

        def persist(_project_id, _dashboard, _revision):
            persist_started.set()
            if not release_persist.wait(2.0):
                raise AssertionError("test did not release dashboard persistence")

        broker._persist_agent_dashboard = persist

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
