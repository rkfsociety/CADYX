from __future__ import annotations

import multiprocessing
import queue
import tempfile
import time
import unittest
from pathlib import Path

from cadyx.core import CadyxCore
from cadyx.errors import CadyxError, ValidationError
from cadyx.kernel import PrimitiveKernelAdapter


BOX = {"type": "create_box", "width": 2, "height": 3, "depth": 4}


def worker(root, project_id, action, args, now, output, start=None, entered=None, finish=None,
           attempted=None, timeout_seconds=5):
    core = CadyxCore(root, kernel=PrimitiveKernelAdapter(),
                     lease_timeout_seconds=timeout_seconds, lease_clock=lambda: now)
    if start is not None:
        start.wait(10)
    if entered is not None:
        original = core.workspace.save

        def save(project, **kwargs):
            if action == "saved_crash":
                original(project, **kwargs)
            entered.set()
            if not finish.wait(10):
                raise RuntimeError("Test command was not released")
            if action == "failed_save":
                raise ValidationError("Save rejected by test")
            if action != "saved_crash":
                original(project, **kwargs)

        core.workspace.save = save
    try:
        method = "execute_command" if action in {"saved_crash", "failed_save"} else action
        if attempted is not None:
            attempted.set()
        if action == "guarded_command":
            result = core.execute_command(project_id, args[0], args[1], expected_revision=args[2])
        else:
            result = getattr(core, method)(project_id, *args)
        output.put(("ok", result))
    except CadyxError as exc:
        output.put(("error", exc.code))


class MultiprocessLeaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.context = multiprocessing.get_context("spawn")
        self.core = CadyxCore(self.root, kernel=PrimitiveKernelAdapter(),
                              lease_timeout_seconds=5, lease_clock=lambda: 100)
        self.project_id = self.core.create_project("Concurrent project")["project"]["id"]

    def launch(self, action, args=(), now=100, **kwargs):
        output = self.context.Queue()
        process = self.context.Process(target=worker, args=(
            self.root, self.project_id, action, args, now, output), kwargs=kwargs)
        process.start()

        def cleanup():
            if process.is_alive():
                process.terminate()
            process.join(10)
            output.close()

        self.addCleanup(cleanup)
        return process, output

    def result(self, job):
        process, output = job
        result = output.get(timeout=15)
        process.join(10)
        self.assertEqual(process.exitcode, 0)
        return result

    def call(self, action, args=(), now=100):
        return self.result(self.launch(action, args, now))

    def test_competing_acquisitions_have_exactly_one_owner(self):
        start = self.context.Event()
        jobs = [self.launch("acquire_lease", (owner, owner), start=start)
                for owner in ("human", "agent")]
        start.set()
        results = [self.result(job) for job in jobs]
        self.assertEqual(sorted(result[0] for result in results), ["error", "ok"])
        self.assertIn(("error", "PROJECT_LOCKED"), results)
        lease = next(result[1] for result in results if result[0] == "ok")
        self.assertEqual(self.core.inspect_project(self.project_id)["lock"]["lease"]["lease_id"],
                         lease["lease_id"])

    def test_heartbeat_release_and_stale_token_across_processes(self):
        lease = self.core.acquire_lease(self.project_id, "agent", "agent")
        token = lease["lease_id"]
        self.assertEqual(self.call("heartbeat", (token,), 104)[0], "ok")
        self.assertFalse(self.call("inspect_project", now=108)[1]["lock"]["recovery_required"])
        self.assertEqual(self.call("release_lease", (token,), 108)[0], "ok")
        new = self.call("acquire_lease", ("human", "human"), 108)[1]
        self.assertNotEqual(new["lease_id"], token)
        self.assertEqual(self.call("execute_command", (token, BOX), 108), ("error", "PROJECT_LOCKED"))
        self.assertEqual(self.call("heartbeat", (token,), 108), ("error", "PROJECT_LOCKED"))
        self.assertEqual(self.call("release_lease", (token,), 108), ("error", "PROJECT_LOCKED"))

    def test_concurrent_commands_preserve_both_revisions(self):
        token = self.core.acquire_lease(self.project_id, "agent", "agent")["lease_id"]
        start = self.context.Event()
        jobs = [self.launch("execute_command", (token, BOX), start=start) for _ in range(2)]
        start.set()
        results = [self.result(job) for job in jobs]
        self.assertTrue(all(result[0] == "ok" for result in results))
        self.assertEqual(sorted(result[1]["document"]["revision"] for result in results), [1, 2])
        detail = self.core.inspect_project(self.project_id)
        self.assertEqual(len(detail["history"]), 2)
        self.assertEqual(len(detail["document"]["objects"]), 4)

    def test_hosts_use_timeout_from_acquisition(self):
        token = self.core.acquire_lease(self.project_id, "agent", "agent")["lease_id"]
        self.assertEqual(self.result(self.launch("heartbeat", (token,), now=104,
                                                timeout_seconds=1))[0], "ok")
        detail = self.result(self.launch("inspect_project", now=108, timeout_seconds=1))[1]
        self.assertFalse(detail["lock"]["recovery_required"])
        self.assertEqual(self.call("execute_command", (token, BOX), 110), ("error", "LEASE_EXPIRED"))

    def test_recovery_waits_for_command_even_after_expiry(self):
        token = self.core.acquire_lease(self.project_id, "agent", "agent")["lease_id"]
        entered, finish = self.context.Event(), self.context.Event()
        command = self.launch("execute_command", (token, BOX), entered=entered, finish=finish)
        self.assertTrue(entered.wait(10))
        attempted = self.context.Event()
        recovery = self.launch("recover_expired_lease", now=106, attempted=attempted)
        self.assertTrue(attempted.wait(10))
        with self.assertRaises(queue.Empty):
            recovery[1].get(timeout=0.2)
        # A different project remains usable while this command holds its lock.
        other = self.core.create_project("Independent", select=False)["project"]["id"]
        self.core.acquire_lease(other, "human", "human")
        finish.set()
        self.assertEqual(self.result(command)[0], "ok")
        self.assertEqual(self.result(recovery)[0], "ok")
        detail = self.core.inspect_project(self.project_id)
        self.assertEqual(detail["document"]["revision"], 1)
        self.assertEqual(detail["lock"]["state"], "FREE")
        self.assertEqual(self.call("execute_command", (token, BOX), 106), ("error", "EDIT_LOCK_REQUIRED"))

    def test_busy_project_rejects_recovery_without_interrupting_command(self):
        token = self.core.acquire_lease(self.project_id, "agent", "agent")["lease_id"]
        entered, finish = self.context.Event(), self.context.Event()
        command = self.launch("execute_command", (token, BOX), entered=entered, finish=finish)
        self.assertTrue(entered.wait(10))
        self.assertEqual(self.call("recover_expired_lease", now=106), ("error", "PROJECT_BUSY"))
        finish.set()
        self.assertEqual(self.result(command)[0], "ok")
        self.assertEqual(self.call("recover_expired_lease", now=106)[0], "ok")
        self.assertEqual(self.core.inspect_project(self.project_id)["document"]["revision"], 1)

    def test_crash_before_and_after_atomic_save_preserves_recoverable_state(self):
        for action, revision in (("execute_command", 0), ("saved_crash", 1)):
            with self.subTest(action=action):
                self.project_id = self.core.create_project(action, select=False)["project"]["id"]
                token = self.core.acquire_lease(self.project_id, "agent", "agent")["lease_id"]
                entered, finish = self.context.Event(), self.context.Event()
                process, _ = self.launch(action, (token, BOX), entered=entered, finish=finish)
                self.assertTrue(entered.wait(10))
                process.terminate()
                process.join(10)
                self.assertIsNotNone(process.exitcode)
                self.assertEqual(self.call("acquire_lease", ("human", "human"), 106),
                                 ("error", "LEASE_RECOVERY_REQUIRED"))
                self.assertEqual(self.call("recover_expired_lease", now=106)[0], "ok")
                detail = self.core.inspect_project(self.project_id)
                self.assertEqual(detail["document"]["revision"], revision)
                self.assertEqual(len(detail["history"]), revision)
                self.assertEqual(detail["lock"]["state"], "FREE")

    def test_invalid_saved_state_prevents_recovery(self):
        self.core.acquire_lease(self.project_id, "agent", "agent")
        path = self.core.workspace._project_dir(self.project_id) / "project.json"
        saved = path.read_bytes()
        path.write_text("{}")
        self.assertEqual(self.call("recover_expired_lease", now=106), ("error", "VALIDATION_ERROR"))
        path.write_bytes(saved)
        self.assertEqual(self.call("acquire_lease", ("human", "human"), 106),
                         ("error", "LEASE_RECOVERY_REQUIRED"))
        self.assertEqual(self.call("recover_expired_lease", now=106)[0], "ok")

    def wait_for_stop_fence(self, token):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with self.core.leases.agent_control(self.project_id) as connection:
                row = connection.execute("SELECT stopped FROM control WHERE lease_id = ?", (token,)).fetchone()
            if row and row[0]:
                return
            time.sleep(0.02)
        self.fail("Stop fence was not persisted")

    def test_stop_fences_queued_commands_and_waits_for_atomic_save(self):
        token = self.core.acquire_lease(self.project_id, "agent", "agent")["lease_id"]
        entered, finish = self.context.Event(), self.context.Event()
        command = self.launch("execute_command", (token, BOX), entered=entered, finish=finish)
        self.assertTrue(entered.wait(10))
        stop = self.launch("stop_agent", (token,))
        self.wait_for_stop_fence(token)
        queued = self.launch("execute_command", (token, BOX))
        with self.assertRaises(queue.Empty):
            stop[1].get(timeout=0.2)
        finish.set()
        self.assertEqual(self.result(command)[0], "ok")
        stopped = self.result(stop)
        self.assertEqual(stopped[0], "ok")
        self.assertEqual(stopped[1]["document"]["revision"], 1)
        rejected = self.result(queued)
        self.assertEqual(rejected[0], "error")
        self.assertIn(rejected[1], ("AGENT_STOPPING", "EDIT_LOCK_REQUIRED"))
        resumed = self.call("resume_agent", ("agent", 1))[1]
        self.assertNotEqual(resumed["lease"]["lease_id"], token)
        self.assertEqual(len(resumed["history"]), 1)

    def test_stop_after_command_process_crash_returns_known_revision(self):
        for action, revision in (("execute_command", 0), ("saved_crash", 1)):
            with self.subTest(action=action):
                self.project_id = self.core.create_project(action, select=False)["project"]["id"]
                token = self.core.acquire_lease(self.project_id, "agent", "agent")["lease_id"]
                entered, finish = self.context.Event(), self.context.Event()
                command = self.launch(action, (token, BOX), entered=entered, finish=finish)
                self.assertTrue(entered.wait(10))
                stop = self.launch("stop_agent", (token,), now=106)
                self.wait_for_stop_fence(token)
                command[0].terminate()
                command[0].join(10)
                stopped = self.result(stop)
                self.assertEqual(stopped[0], "ok")
                self.assertEqual(stopped[1]["document"]["revision"], revision)
                self.assertEqual(len(stopped[1]["history"]), revision)
                self.assertEqual(self.call("resume_agent", ("agent", revision), 106)[0], "ok")

    def test_stop_returns_prior_revision_after_failed_command(self):
        token = self.core.acquire_lease(self.project_id, "agent", "agent")["lease_id"]
        entered, finish = self.context.Event(), self.context.Event()
        command = self.launch("failed_save", (token, BOX), entered=entered, finish=finish)
        self.assertTrue(entered.wait(10))
        stop = self.launch("stop_agent", (token,))
        self.wait_for_stop_fence(token)
        finish.set()
        self.assertEqual(self.result(command), ("error", "VALIDATION_ERROR"))
        stopped = self.result(stop)
        self.assertEqual(stopped[0], "ok")
        self.assertEqual(stopped[1]["document"]["revision"], 0)
        self.assertEqual(stopped[1]["history"], [])

    def test_stop_request_survives_stopper_process_crash(self):
        token = self.core.acquire_lease(self.project_id, "agent", "agent")["lease_id"]
        entered, finish = self.context.Event(), self.context.Event()
        command = self.launch("execute_command", (token, BOX), entered=entered, finish=finish)
        self.assertTrue(entered.wait(10))
        stop = self.launch("stop_agent", (token,))
        self.wait_for_stop_fence(token)
        stop[0].terminate()
        stop[0].join(10)
        finish.set()
        self.assertEqual(self.result(command)[0], "ok")
        self.assertEqual(self.call("execute_command", (token, BOX)), ("error", "AGENT_STOPPING"))
        self.assertEqual(self.call("stop_agent", (token,))[1]["document"]["revision"], 1)

    def test_resumed_revision_check_serializes_competing_commands(self):
        token = self.core.resume_agent(self.project_id, "agent", 0)["lease"]["lease_id"]
        start = self.context.Event()
        jobs = [self.launch("guarded_command", (token, BOX, 0), start=start) for _ in range(2)]
        start.set()
        results = [self.result(job) for job in jobs]
        self.assertEqual(sorted(result[0] for result in results), ["error", "ok"])
        self.assertIn(("error", "REVISION_CONFLICT"), results)
        self.assertEqual(len(self.core.inspect_project(self.project_id)["history"]), 1)


if __name__ == "__main__":
    unittest.main()
