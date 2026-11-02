"""Regression tests for the lifetime of the agent lease renewal worker."""

from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cadyx.lease_heartbeat import LeaseHeartbeat


class _CoreStub:
    def __init__(self) -> None:
        self.leases = SimpleNamespace(timeout_seconds=0.03)
        self.renewed = threading.Event()
        self.renewal_threads: list[threading.Thread] = []

    def heartbeat(self, project_id: str, lease_id: str) -> None:
        self.renewal_threads.append(threading.current_thread())
        self.renewed.set()


class LeaseHeartbeatLifecycleTests(unittest.TestCase):
    def test_start_renews_synchronously_before_returning(self) -> None:
        core = _CoreStub()
        heartbeat = LeaseHeartbeat(core, "project", "lease")
        try:
            heartbeat.start()
            self.assertIn(threading.current_thread(), core.renewal_threads)
        finally:
            heartbeat.stop()

    def test_initial_renewal_failure_does_not_start_a_worker(self) -> None:
        core = _CoreStub()
        heartbeat = LeaseHeartbeat(core, "project", "lease")
        with patch.object(core, "heartbeat", side_effect=RuntimeError("renewal failed")):
            with self.assertRaisesRegex(RuntimeError, "renewal failed"):
                heartbeat.start()
        self.assertIsNone(heartbeat._thread)
        heartbeat.stop()

    def test_duplicate_start_does_not_orphan_a_renewal_thread(self) -> None:
        core = _CoreStub()
        heartbeat = LeaseHeartbeat(core, "project", "lease")
        heartbeat.start()
        worker = heartbeat._thread
        try:
            self.assertTrue(core.renewed.wait(1.0))
            with self.assertRaisesRegex(RuntimeError, "already started"):
                heartbeat.start()
            self.assertIs(heartbeat._thread, worker)
        finally:
            heartbeat.stop()
        self.assertIsNotNone(worker)
        self.assertFalse(worker.is_alive())

    def test_start_after_stop_fails_instead_of_silently_disabling_renewals(self) -> None:
        core = _CoreStub()
        heartbeat = LeaseHeartbeat(core, "project", "lease")
        heartbeat.stop()
        heartbeat.stop()
        with self.assertRaisesRegex(RuntimeError, "stopped"):
            heartbeat.start()

        active = LeaseHeartbeat(core, "project", "lease")
        active.start()
        active.stop()
        with self.assertRaisesRegex(RuntimeError, "stopped"):
            active.start()


if __name__ == "__main__":
    unittest.main()
