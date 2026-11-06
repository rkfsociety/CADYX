from __future__ import annotations

import tempfile
import unittest

from cadyx.core import CadyxCore
from cadyx.errors import ConflictError, ValidationError
from cadyx.kernel import PrimitiveKernelAdapter


BOX = {"type": "create_box", "width": 2, "height": 3, "depth": 4}


class AgentControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.core = CadyxCore(self.temp.name, kernel=PrimitiveKernelAdapter())
        self.project = self.core.create_project("Resume")["project"]["id"]
        self.token = self.core.acquire_lease(self.project, "agent", "agent")["lease_id"]

    def test_stop_resume_and_unknown_command_result(self):
        self.core.execute_command(self.project, self.token, BOX)
        stopped = self.core.stop_agent(self.project, self.token)
        self.assertEqual(stopped["document"]["revision"], 1)
        self.assertEqual(len(stopped["history"]), 1)
        self.assertEqual(stopped["lock"]["state"], "FREE")
        resumed = self.core.resume_agent(self.project, "agent", 1)
        token = resumed["lease"]["lease_id"]
        self.assertNotEqual(token, self.token)
        with self.assertRaises(ConflictError):
            self.core.execute_command(self.project, self.token, BOX)
        with self.assertRaises(ValidationError):
            self.core.execute_command(self.project, token, BOX)
        self.core.execute_command(self.project, token, BOX, expected_revision=1)
        # Lost response: retrying the old revision cannot duplicate the command.
        with self.assertRaises(ConflictError) as error:
            self.core.execute_command(self.project, token, BOX, expected_revision=1)
        self.assertEqual(error.exception.code, "REVISION_CONFLICT")
        self.assertEqual(len(self.core.inspect_project(self.project)["history"]), 2)

    def test_human_change_requires_reinspection(self):
        self.core.stop_agent(self.project, self.token)
        human = self.core.acquire_lease(self.project, "human", "human")["lease_id"]
        self.core.execute_command(self.project, human, BOX)
        self.core.release_lease(self.project, human)
        with self.assertRaises(ConflictError) as error:
            self.core.resume_agent(self.project, "agent", 0)
        self.assertEqual(error.exception.code, "REVISION_CONFLICT")
        self.assertEqual(self.core.inspect_project(self.project)["lock"]["state"], "FREE")
        self.assertEqual(self.core.resume_agent(self.project, "agent", 1)["document"]["revision"], 1)

    def test_validation_failure_keeps_lease_and_stop_fence_across_hosts(self):
        path = self.core.workspace._project_dir(self.project) / "project.json"
        original = path.read_bytes()
        path.write_text("{}")
        with self.assertRaises(ValidationError):
            self.core.stop_agent(self.project, self.token)
        path.write_bytes(original)
        other = CadyxCore(self.temp.name, kernel=PrimitiveKernelAdapter())
        with self.assertRaises(ConflictError) as error:
            other.execute_command(self.project, self.token, BOX)
        self.assertEqual(error.exception.code, "AGENT_STOPPING")
        self.assertEqual(other.leases.get(self.project).lease_id, self.token)
        other.stop_agent(self.project, self.token)

    def test_invalid_geometry_prevents_stop_and_resume(self):
        self.core.execute_command(self.project, self.token, BOX)
        original = self.core.kernel.validate
        def reject(geometry):
            raise ValidationError("Invalid geometry")
        self.core.kernel.validate = reject
        with self.assertRaises(ValidationError):
            self.core.stop_agent(self.project, self.token)
        self.assertEqual(self.core.leases.get(self.project).lease_id, self.token)
        self.core.kernel.validate = original
        self.core.stop_agent(self.project, self.token)
        self.core.kernel.validate = reject
        with self.assertRaises(ValidationError):
            self.core.resume_agent(self.project, "agent", 1)
        self.assertIsNone(self.core.leases.get(self.project))

    def test_stop_cannot_release_human_or_replacement_lease(self):
        self.core.release_lease(self.project, self.token)
        human = self.core.acquire_lease(self.project, "human", "human")["lease_id"]
        for token in (self.token, human):
            with self.assertRaises(ConflictError):
                self.core.stop_agent(self.project, token)
        self.core.execute_command(self.project, human, BOX)

    def test_idempotency_key_replay_returns_cached_result(self):
        result1 = self.core.execute_command(self.project, self.token, BOX, expected_revision=0, request_id="req-123")
        self.assertEqual(result1["document"]["revision"], 1)
        self.assertEqual(len(self.core.inspect_project(self.project)["history"]), 1)

        # Re-sending the exact same command with the same request_id retrieves stored result
        result2 = self.core.execute_command(self.project, self.token, BOX, expected_revision=0, request_id="req-123")
        self.assertEqual(result2["document"]["revision"], 1)
        self.assertEqual(result2["history_entry"]["revision"], 1)
        self.assertEqual(result2["history_entry"]["request_id"], "req-123")
        # History is never duplicated, revision never increments
        self.assertEqual(len(self.core.inspect_project(self.project)["history"]), 1)
        self.assertEqual(self.core.inspect_project(self.project)["document"]["revision"], 1)

    def test_idempotency_key_mismatched_payload_conflict(self):
        self.core.execute_command(self.project, self.token, BOX, expected_revision=0, request_id="req-conflict")
        # Reusing the request_id with different command data conflicts
        different_box = {"type": "create_box", "width": 5, "height": 5, "depth": 5}
        with self.assertRaises(ConflictError) as err1:
            self.core.execute_command(self.project, self.token, different_box, expected_revision=0, request_id="req-conflict")
        self.assertEqual(err1.exception.code, "IDEMPOTENCY_CONFLICT")

        # Reusing the request_id with different expected_revision conflicts
        with self.assertRaises(ConflictError) as err2:
            self.core.execute_command(self.project, self.token, BOX, expected_revision=1, request_id="req-conflict")
        self.assertEqual(err2.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_simulated_dropped_response_and_retry_loop(self):
        self.core.release_lease(self.project, self.token)
        # Initial call commits and records idempotency
        res1 = self.core.execute_agent_command(self.project, "agent", 0, BOX, request_id="req-dropped")
        self.assertEqual(res1["document"]["revision"], 1)
        self.assertTrue(res1["lease_released"])

        # Agent connection dropped right after commit; agent retries with original expected_revision 0
        res2 = self.core.execute_agent_command(self.project, "agent", 0, BOX, request_id="req-dropped")
        self.assertEqual(res2["document"]["revision"], 1)
        self.assertTrue(res2["lease_released"])
        self.assertEqual(res2["history_entry"]["request_id"], "req-dropped")
        self.assertEqual(len(self.core.inspect_project(self.project)["history"]), 1)

    def test_restart_preserves_idempotency_lookup(self):
        self.core.release_lease(self.project, self.token)
        res1 = self.core.execute_agent_command(self.project, "agent", 0, BOX, request_id="req-restart")
        self.assertEqual(res1["document"]["revision"], 1)

        # Fresh core instance simulating host restart
        other = CadyxCore(self.temp.name, kernel=PrimitiveKernelAdapter())
        res2 = other.execute_agent_command(self.project, "agent", 0, BOX, request_id="req-restart")
        self.assertEqual(res2["document"]["revision"], 1)
        self.assertEqual(len(other.inspect_project(self.project)["history"]), 1)

    def test_invalid_request_id_rejected(self):
        with self.assertRaises(ValidationError):
            self.core.execute_command(self.project, self.token, BOX, request_id="")
        with self.assertRaises(ValidationError):
            self.core.execute_command(self.project, self.token, BOX, request_id=123)  # type: ignore[arg-type]
        with self.assertRaises(ValidationError):
            self.core.execute_command(self.project, self.token, BOX, request_id="x" * 129)

    def test_command_burst_executes_sequentially_and_advances_revisions(self):
        self.core.release_lease(self.project, self.token)
        commands = [
            {"type": "create_box", "width": 1, "height": 2, "depth": 3},
            {"type": "create_box", "width": 4, "height": 5, "depth": 6},
            {"type": "create_box", "width": 7, "height": 8, "depth": 9},
        ]
        burst_result = self.core.execute_agent_command_burst(
            self.project, "agent", expected_revision=0, commands=commands,
        )
        self.assertEqual(burst_result["completed_count"], 3)
        self.assertEqual(burst_result["total_count"], 3)
        self.assertEqual(burst_result["initial_revision"], 0)
        self.assertEqual(burst_result["final_revision"], 3)
        self.assertTrue(burst_result["lease_released"])
        self.assertEqual(len(burst_result["results"]), 3)
        self.assertEqual([r["revision"] for r in burst_result["results"]], [1, 2, 3])
        self.assertEqual(burst_result["document"]["revision"], 3)

        project_state = self.core.inspect_project(self.project)
        self.assertEqual(project_state["document"]["revision"], 3)
        self.assertEqual(len(project_state["history"]), 3)
        self.assertEqual(project_state["lock"]["state"], "FREE")

    def test_command_burst_emits_distinct_events(self):
        import queue
        self.core.release_lease(self.project, self.token)
        commands = [
            {"type": "create_box", "width": 1, "height": 1, "depth": 1},
            {"type": "create_box", "width": 2, "height": 2, "depth": 2},
        ]
        events_received = []
        sub_id, sub_queue = self.core.events.subscribe(self.project)
        try:
            self.core.execute_agent_command_burst(
                self.project, "agent", expected_revision=0, commands=commands,
            )
            while True:
                try:
                    ev = sub_queue.get(timeout=0.1)
                    events_received.append(ev)
                except queue.Empty:
                    break
        finally:
            self.core.events.unsubscribe(sub_id)

        event_names = [e.event for e in events_received]
        self.assertEqual(event_names.count("command.started"), 2)
        self.assertEqual(event_names.count("document.changed"), 2)
        self.assertEqual(event_names.count("command.completed"), 2)

    def test_command_burst_failure_commits_prefix_and_halts(self):
        self.core.release_lease(self.project, self.token)
        commands = [
            {"type": "create_box", "width": 1, "height": 2, "depth": 3},
            {"type": "create_box", "width": -5, "height": 2, "depth": 3},
            {"type": "create_box", "width": 7, "height": 8, "depth": 9},
        ]
        with self.assertRaises(ValidationError) as ctx:
            self.core.execute_agent_command_burst(
                self.project, "agent", expected_revision=0, commands=commands,
            )
        diag = ctx.exception.diagnostics
        self.assertEqual(diag.get("burst_failed_index"), 1)
        self.assertEqual(diag.get("burst_completed_count"), 1)
        self.assertEqual(diag.get("burst_total_count"), 3)
        self.assertEqual(diag.get("burst_committed_revision"), 1)

        project_state = self.core.inspect_project(self.project)
        self.assertEqual(project_state["document"]["revision"], 1)
        self.assertEqual(len(project_state["history"]), 1)
        self.assertEqual(project_state["lock"]["state"], "FREE")

    def test_command_burst_stop_fence_halts_execution(self):
        self.core.release_lease(self.project, self.token)
        commands = [
            {"type": "create_box", "width": 1, "height": 1, "depth": 1},
            {"type": "create_box", "width": 2, "height": 2, "depth": 2},
        ]
        original_execute = self.core._command_activity.execute_command

        def intercept_and_fence(*args, **kwargs):
            result = original_execute(*args, **kwargs)
            with self.core._server_agent_lease_guard:
                active = self.core._server_agent_leases.get(self.project)
            if active is not None:
                self.core.leases.request_agent_stop(self.project, active[0])
            return result

        object.__setattr__(self.core._command_activity, "execute_command", intercept_and_fence)
        try:
            with self.assertRaises(ConflictError) as ctx:
                self.core.execute_agent_command_burst(
                    self.project, "agent", expected_revision=0, commands=commands,
                )
            self.assertEqual(ctx.exception.code, "AGENT_STOPPING")
        finally:
            object.__setattr__(self.core._command_activity, "execute_command", original_execute)

        project_state = self.core.inspect_project(self.project)
        self.assertEqual(project_state["document"]["revision"], 1)
        self.assertEqual(len(project_state["history"]), 1)
        self.assertEqual(project_state["lock"]["state"], "FREE")

    def test_command_burst_idempotency_retry(self):
        self.core.release_lease(self.project, self.token)
        commands = [
            {"type": "create_box", "width": 1, "height": 1, "depth": 1},
            {"type": "create_box", "width": 2, "height": 2, "depth": 2},
        ]
        burst1 = self.core.execute_agent_command_burst(
            self.project, "agent", expected_revision=0, commands=commands, request_id="burst-idem-1",
        )
        self.assertEqual(burst1["final_revision"], 2)
        self.assertEqual(len(self.core.inspect_project(self.project)["history"]), 2)

        burst2 = self.core.execute_agent_command_burst(
            self.project, "agent", expected_revision=0, commands=commands, request_id="burst-idem-1",
        )
        self.assertEqual(burst2["final_revision"], 2)
        self.assertEqual(burst2["completed_count"], 2)
        self.assertEqual(len(self.core.inspect_project(self.project)["history"]), 2)

        diff_commands = [
            {"type": "create_box", "width": 10, "height": 10, "depth": 10},
        ]
        with self.assertRaises(ConflictError) as ctx:
            self.core.execute_agent_command_burst(
                self.project, "agent", expected_revision=0, commands=diff_commands, request_id="burst-idem-1",
            )
        self.assertEqual(ctx.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_command_burst_per_command_request_ids(self):
        self.core.release_lease(self.project, self.token)
        commands = [
            {"type": "create_box", "width": 1, "height": 1, "depth": 1},
            {"type": "create_box", "width": 2, "height": 2, "depth": 2},
        ]
        request_ids = ["cmd-id-1", "cmd-id-2"]
        burst = self.core.execute_agent_command_burst(
            self.project, "agent", expected_revision=0, commands=commands, request_ids=request_ids,
        )
        self.assertEqual(burst["final_revision"], 2)
        history = self.core.inspect_project(self.project)["history"]
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["request_id"], "cmd-id-1")
        self.assertEqual(history[1]["request_id"], "cmd-id-2")
