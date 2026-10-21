from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Sequence
from unittest.mock import patch

from cadyx.agent_runtime import (
    AgentMessage,
    AgentResponse,
    AgentRunCancelled,
    AgentRunDeadlineExceeded,
    AgentRuntime,
    AgentToolCall,
    AgentToolSpec,
)
from cadyx.capability_registry import CapabilityRegistry
from cadyx.core import CadyxCore
from cadyx.kernel import PrimitiveKernelAdapter
from cadyx.models import OwnerType


class ScriptedProvider:
    def __init__(self, responses: Sequence[AgentResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[tuple[AgentMessage, ...], tuple[AgentToolSpec, ...]]] = []

    def complete(
        self,
        messages: Sequence[AgentMessage],
        tools: Sequence[AgentToolSpec],
    ) -> AgentResponse:
        self.requests.append((tuple(messages), tuple(tools)))
        return self.responses.pop(0)


class LockCheckingProvider(ScriptedProvider):
    def __init__(self, core: CadyxCore, project_id: str, responses: Sequence[AgentResponse]) -> None:
        super().__init__(responses)
        self.core = core
        self.project_id = project_id
        self.provider_lock_states: list[str] = []

    def complete(self, messages: Sequence[AgentMessage], tools: Sequence[AgentToolSpec]) -> AgentResponse:
        self.provider_lock_states.append(self.core.inspect_project(self.project_id)["lock"]["state"])
        return super().complete(messages, tools)


class HookedProvider(ScriptedProvider):
    def __init__(self, responses: Sequence[AgentResponse], on_turn) -> None:
        super().__init__(responses)
        self.on_turn = on_turn

    def complete(self, messages: Sequence[AgentMessage], tools: Sequence[AgentToolSpec]) -> AgentResponse:
        self.on_turn(len(self.requests))
        return super().complete(messages, tools)


class FailingProvider:
    def complete(self, messages: Sequence[AgentMessage], tools: Sequence[AgentToolSpec]) -> AgentResponse:
        raise RuntimeError("provider unavailable")


class SlowProvider:
    def complete(self, messages: Sequence[AgentMessage], tools: Sequence[AgentToolSpec]) -> AgentResponse:
        time.sleep(0.75)
        return AgentResponse("Completed after a long provider call.")


class BlockingProvider:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def complete(self, messages: Sequence[AgentMessage], tools: Sequence[AgentToolSpec]) -> AgentResponse:
        self.started.set()
        self.release.wait(5.0)
        return AgentResponse("Provider eventually returned.")


class CancellingProvider:
    def __init__(self, cancel_event: threading.Event) -> None:
        self.cancel_event = cancel_event
        self.requests: list[tuple[AgentMessage, ...]] = []

    def complete(self, messages: Sequence[AgentMessage], tools: Sequence[AgentToolSpec]) -> AgentResponse:
        self.requests.append(tuple(messages))
        if len(self.requests) == 1:
            return AgentResponse(None, (AgentToolCall("inspect-1", "inspect_project", {}),))
        self.cancel_event.set()
        return AgentResponse(None, (
            AgentToolCall("command-after-cancel", "execute_command", {
                "command": {"type": "create_box", "width": 2, "height": 3, "depth": 4},
            }),
        ))


class AgentRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.core = CadyxCore(
            str(Path(self.temporary_directory.name) / "projects"),
            kernel=PrimitiveKernelAdapter(),
        )
        self.project_id = self.core.create_project("Runtime test")["project"]["id"]

    def test_run_executes_revision_checked_commands_and_releases_lease(self) -> None:
        provider = ScriptedProvider([
            AgentResponse(None, (
                AgentToolCall("inspect-1", "inspect_project", {}),
                AgentToolCall("viewport-1", "get_viewport", {}),
                AgentToolCall("command-1", "execute_command", {
                    "command": {"type": "create_box", "width": 2, "height": 3, "depth": 4},
                }),
            )),
            AgentResponse("Created a 2 by 3 by 4 box.", ()),
        ])

        result = AgentRuntime(self.core, provider, owner_id="runtime-test").run(
            self.project_id,
            "Create a 2 by 3 by 4 box.",
        )

        self.assertEqual(result.final_response, "Created a 2 by 3 by 4 box.")
        self.assertEqual(result.revision, 1)
        self.assertEqual(result.provider_turns, 2)
        self.assertEqual(result.tool_calls, 3)
        self.assertEqual(result.tool_names, ("inspect_project", "get_viewport", "execute_command"))
        self.assertEqual(self.core.inspect_project(self.project_id)["document"]["revision"], 1)
        self.assertEqual(self.core.inspect_project(self.project_id)["lock"]["state"], "FREE")
        self.assertEqual(
            tuple(spec.name for spec in provider.requests[0][1]),
            ("list_capabilities", "read_source_file", "list_source_extensions",
             "develop_source_extension", "rollback_source_extension", "develop_capability",
             "inspect_project", "get_viewport", "validate_specification", "execute_command"),
        )
        tool_messages = [message for message in provider.requests[1][0] if message.role == "tool"]
        self.assertEqual(
            [message.name for message in tool_messages],
            ["inspect_project", "get_viewport", "execute_command"],
        )
        self.assertEqual(json.loads(tool_messages[1].content)["revision"], 0)
        command_result = json.loads(tool_messages[2].content)
        self.assertEqual(command_result["document"]["revision"], 1)
        activities = self.core.events.activity_snapshot(self.project_id)
        actions = [activity["action"] for activity in activities]
        self.assertGreaterEqual(actions.count("Изучает модель"), 2)
        self.assertIn("Получает 3D-сцену", actions)
        self.assertIn("Строит модель", actions)
        self.assertTrue(all(activity["state"] == "completed" for activity in activities))

    def test_domain_errors_are_returned_to_provider_and_leave_project_unchanged(self) -> None:
        provider = ScriptedProvider([
            AgentResponse(None, (
                AgentToolCall("bad-command", "execute_command", {"command": {"type": "missing"}}),
            )),
            AgentResponse("The command was rejected; the project is unchanged.", ()),
        ])

        result = AgentRuntime(self.core, provider, owner_id="runtime-test").run(
            self.project_id,
            "Try the requested operation.",
        )

        self.assertEqual(result.revision, 0)
        self.assertEqual(self.core.inspect_project(self.project_id)["lock"]["state"], "FREE")
        tool_message = next(message for message in provider.requests[1][0] if message.role == "tool")
        self.assertEqual(json.loads(tool_message.content)["error"], "CAPABILITY_MISSING")
        self.assertEqual(json.loads(tool_message.content)["command_type"], "missing")

    def test_missing_command_is_developed_tested_registered_and_used_in_same_run(self) -> None:
        definition = {
            "id": "runtime_test_box_builder",
            "title": "Runtime test box builder",
            "description": "Creates an editable test box.",
            "version": "1.0.0",
            "input_schema": {"type": "object", "properties": {
                "width": {"type": "number", "minimum": 0.1},
            }, "required": ["width"], "additionalProperties": False},
            "output_schema": {"type": "object", "properties": {"body_id": {"type": "string"}},
                              "required": ["body_id"], "additionalProperties": False},
            "outputs": {"body_id": {"step": 0, "field": "body_id"}},
            "dependencies": [],
            "steps": [{"command": {
                "type": "create_box", "width": {"$input": "width"},
                "height": 3, "depth": 4, "name": "Runtime capability body",
            }}],
            "test_input": {"width": 2},
        }
        provider = ScriptedProvider([
            AgentResponse(None, (AgentToolCall("missing", "execute_command", {
                "command": {"type": "runtime_test_box", "width": 2},
            }),)),
            AgentResponse(None, (AgentToolCall("develop", "develop_capability", {
                "definition": definition,
            }),)),
            AgentResponse(None, (AgentToolCall("use", "cadyx_capability_runtime_test_box_builder", {
                "width": 2,
            }),)),
            AgentResponse("Built the requested model.", ()),
        ])

        result = AgentRuntime(self.core, provider, owner_id="runtime-test").run(
            self.project_id, "Build a box with the required capability.",
        )

        self.assertEqual(result.final_response, "Built the requested model.")
        self.assertEqual(result.revision, 1)
        self.assertEqual(self.core.inspect_project(self.project_id)["document"]["revision"], 1)
        self.assertIn("cadyx_capability_runtime_test_box_builder",
                      [spec.name for spec in provider.requests[2][1]])
        registry = CapabilityRegistry(self.core.workspace.root / ".cadyx-capabilities.sqlite3")
        self.assertEqual(registry.list(ready_only=True)[0]["id"], "runtime_test_box_builder")

    def test_provider_failure_without_a_command_leaves_project_free(self) -> None:
        runtime = AgentRuntime(self.core, FailingProvider(), owner_id="runtime-test")

        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            runtime.run(self.project_id, "Create a model.")

        self.assertEqual(self.core.inspect_project(self.project_id)["lock"]["state"], "FREE")

    def test_provider_waits_and_read_only_tools_never_hold_an_edit_lease(self) -> None:
        core = CadyxCore(
            str(Path(self.temporary_directory.name) / "short-lease-projects"),
            kernel=PrimitiveKernelAdapter(),
            lease_timeout_seconds=0.30,
        )
        project_id = core.create_project("Heartbeat test")["project"]["id"]

        provider = LockCheckingProvider(core, project_id, [AgentResponse("No edits needed.")])
        result = AgentRuntime(core, provider, owner_id="slow-runtime").run(project_id, "Inspect the project.")

        self.assertEqual(provider.provider_lock_states, ["FREE"])
        self.assertEqual(result.final_response, "No edits needed.")
        self.assertEqual(result.revision, 0)
        self.assertEqual(core.inspect_project(project_id)["lock"]["state"], "FREE")

    def test_each_command_gets_distinct_lease_with_free_provider_turns(self) -> None:
        provider = LockCheckingProvider(self.core, self.project_id, [
            AgentResponse(None, (
                AgentToolCall("first", "execute_command", {
                    "command": {"type": "create_box", "width": 2, "height": 3, "depth": 4},
                }),
                AgentToolCall("second", "execute_command", {
                    "command": {"type": "create_box", "width": 4, "height": 3, "depth": 2},
                }),
            )),
            AgentResponse("Both commands saved."),
        ])
        with patch.object(self.core, "stop_agent", wraps=self.core.stop_agent) as stop:
            result = AgentRuntime(self.core, provider, owner_id="two-commands").run(
                self.project_id, "Create two boxes.",
            )
        self.assertEqual(result.revision, 2)
        self.assertEqual(provider.provider_lock_states, ["FREE", "FREE"])
        lease_ids = [call.args[1] for call in stop.call_args_list]
        self.assertEqual(len(lease_ids), 2)
        self.assertNotEqual(lease_ids[0], lease_ids[1])
        self.assertEqual(self.core.inspect_project(self.project_id)["lock"]["state"], "FREE")
        activities = self.core.events.activity_snapshot(self.project_id)
        self.assertEqual(sum(a["action"] == "Строит модель" for a in activities), 2)

    def test_command_uses_agent_lease_and_preserves_atomic_revision(self) -> None:
        provider = ScriptedProvider([
            AgentResponse(None, (AgentToolCall("edit", "execute_command", {
                "command": {"type": "create_box", "width": 2, "height": 3, "depth": 4},
            }),)),
            AgentResponse("Finished"),
        ])
        original_execute = self.core.execute_command
        seen_leases = []

        def execute_while_locked(*args, **kwargs):
            snapshot = self.core.inspect_project(self.project_id)["lock"]
            seen_leases.append(snapshot)
            return original_execute(*args, **kwargs)

        with patch.object(self.core, "execute_command", side_effect=execute_while_locked):
            result = AgentRuntime(self.core, provider, owner_id="lease-check").run(
                self.project_id, "Create a box.",
            )
        self.assertEqual(result.revision, 1)
        self.assertEqual(len(seen_leases), 1)
        self.assertNotEqual(seen_leases[0]["state"], "FREE")
        self.assertEqual(seen_leases[0]["lease"]["owner_type"], "agent")
        self.assertEqual(self.core.inspect_project(self.project_id)["lock"]["state"], "FREE")

    def test_command_heartbeat_runs_only_while_lease_is_held(self) -> None:
        core = CadyxCore(
            str(Path(self.temporary_directory.name) / "heartbeat-command-projects"),
            kernel=PrimitiveKernelAdapter(),
            lease_timeout_seconds=0.3,
        )
        project_id = core.create_project("Command heartbeat")["project"]["id"]
        provider = LockCheckingProvider(core, project_id, [
            AgentResponse(None, (AgentToolCall("edit", "execute_command", {
                "command": {"type": "create_box", "width": 2, "height": 3, "depth": 4},
            }),)),
            AgentResponse("Saved."),
        ])
        original_execute = core.execute_command

        def long_command(*args, **kwargs):
            time.sleep(0.25)
            return original_execute(*args, **kwargs)

        with patch.object(core, "heartbeat", wraps=core.heartbeat) as heartbeat:
            with patch.object(core, "execute_command", side_effect=long_command):
                result = AgentRuntime(core, provider, owner_id="heartbeat").run(
                    project_id, "Create a box.",
                )
        self.assertGreater(heartbeat.call_count, 0)
        self.assertEqual(result.revision, 1)
        self.assertEqual(provider.provider_lock_states, ["FREE", "FREE"])
        self.assertEqual(core.inspect_project(project_id)["lock"]["state"], "FREE")

    def test_competing_human_lease_rejects_command_without_releasing_owner(self) -> None:
        human_lease = []
        def on_turn(turn):
            if turn == 0:
                human_lease.append(self.core.acquire_lease(self.project_id, "human", "editor")["lease_id"])

        provider = HookedProvider([
            AgentResponse(None, (AgentToolCall("edit", "execute_command", {
                "command": {"type": "create_box", "width": 2, "height": 3, "depth": 4},
            }),)),
            AgentResponse("The project is busy."),
        ], on_turn)
        try:
            result = AgentRuntime(self.core, provider, owner_id="agent").run(
                self.project_id, "Try to edit.",
            )
            response = next(msg for msg in provider.requests[1][0] if msg.role == "tool")
            self.assertEqual(json.loads(response.content)["error"], "PROJECT_LOCKED")
            self.assertEqual(result.revision, 0)
            self.assertEqual(self.core.inspect_project(self.project_id)["document"]["revision"], 0)
            self.assertEqual(self.core.inspect_project(self.project_id)["lock"]["lease"]["lease_id"], human_lease[0])
        finally:
            if human_lease:
                self.core.release_lease(self.project_id, human_lease[0])

    def test_conflict_after_provider_wait_requires_fresh_inspection(self) -> None:
        def on_turn(turn):
            if turn == 0:
                lease_id = self.core.acquire_lease(self.project_id, "human", "editor")["lease_id"]
                try:
                    self.core.execute_command(
                        self.project_id, lease_id,
                        {"type": "create_box", "width": 1, "height": 1, "depth": 1},
                        expected_owner_type=OwnerType.HUMAN,
                    )
                finally:
                    self.core.release_lease(self.project_id, lease_id)

        provider = HookedProvider([
            AgentResponse(None, (AgentToolCall("stale", "execute_command", {
                "command": {"type": "create_box", "width": 2, "height": 3, "depth": 4},
            }),)),
            AgentResponse(None, (AgentToolCall("refresh", "inspect_project", {}),)),
            AgentResponse(None, (AgentToolCall("fresh", "execute_command", {
                "command": {"type": "create_box", "width": 2, "height": 3, "depth": 4},
            }),)),
            AgentResponse("Retried after inspecting the new revision."),
        ], on_turn)
        result = AgentRuntime(self.core, provider, owner_id="agent").run(
            self.project_id, "Create a box.",
        )
        stale_response = next(msg for msg in provider.requests[1][0] if msg.role == "tool")
        self.assertEqual(json.loads(stale_response.content)["error"], "REVISION_CONFLICT")
        self.assertEqual(result.revision, 2)
        self.assertEqual(self.core.inspect_project(self.project_id)["document"]["revision"], 2)
        self.assertEqual(self.core.inspect_project(self.project_id)["lock"]["state"], "FREE")

    def test_cancellation_after_admitted_command_stops_only_its_lease(self) -> None:
        cancel_event = threading.Event()
        provider = ScriptedProvider([
            AgentResponse(None, (AgentToolCall("edit", "execute_command", {
                "command": {"type": "create_box", "width": 2, "height": 3, "depth": 4},
            }),)),
        ])
        original_execute = self.core.execute_command

        def cancel_after_save(*args, **kwargs):
            result = original_execute(*args, **kwargs)
            cancel_event.set()
            return result

        with patch.object(self.core, "execute_command", side_effect=cancel_after_save):
            with self.assertRaises(AgentRunCancelled):
                AgentRuntime(self.core, provider, owner_id="cancelled").run(
                    self.project_id, "Create a box.", cancel_event=cancel_event,
                )
        self.assertEqual(self.core.inspect_project(self.project_id)["document"]["revision"], 1)
        self.assertEqual(self.core.inspect_project(self.project_id)["lock"]["state"], "FREE")

    def test_interrupted_stop_is_recoverable_after_host_restart(self) -> None:
        provider = ScriptedProvider([
            AgentResponse(None, (AgentToolCall("edit", "execute_command", {
                "command": {"type": "create_box", "width": 2, "height": 3, "depth": 4},
            }),)),
        ])
        with patch.object(self.core, "stop_agent", side_effect=RuntimeError("host interrupted")):
            with self.assertRaisesRegex(RuntimeError, "host interrupted"):
                AgentRuntime(self.core, provider, owner_id="crash").run(
                    self.project_id, "Create a box.",
                )
        self.assertNotEqual(self.core.inspect_project(self.project_id)["lock"]["state"], "FREE")
        restarted = CadyxCore(
            str(Path(self.temporary_directory.name) / "projects"),
            kernel=PrimitiveKernelAdapter(),
        )
        self.assertEqual(restarted.inspect_project(self.project_id)["document"]["revision"], 1)
        with patch.object(restarted.leases, "_clock", return_value=time.time() + 3600):
            recovered = restarted.recover_expired_lease(self.project_id)
        self.assertTrue(recovered["recovered"])
        self.assertEqual(restarted.inspect_project(self.project_id)["lock"]["state"], "FREE")

    def test_deadline_aborts_stalled_provider_and_does_not_acquire_lease(self) -> None:
        provider = BlockingProvider()
        runtime = AgentRuntime(
            self.core,
            provider,
            owner_id="deadline-runtime",
            run_timeout_seconds=0.20,
        )
        try:
            with self.assertRaisesRegex(AgentRunDeadlineExceeded, "execution deadline"):
                runtime.run(self.project_id, "Wait for a stalled provider.")
            self.assertTrue(provider.started.is_set())
            self.assertEqual(self.core.inspect_project(self.project_id)["lock"]["state"], "FREE")
        finally:
            provider.release.set()

    def test_cancellation_between_tool_rounds_prevents_later_tool_execution(self) -> None:
        cancel_event = threading.Event()
        provider = CancellingProvider(cancel_event)
        runtime = AgentRuntime(self.core, provider, owner_id="cancel-runtime")

        with self.assertRaisesRegex(AgentRunCancelled, "cancelled"):
            runtime.run(self.project_id, "Inspect, then edit.", cancel_event=cancel_event)

        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(self.core.inspect_project(self.project_id)["document"]["revision"], 0)
        self.assertEqual(self.core.inspect_project(self.project_id)["lock"]["state"], "FREE")

    def test_command_failure_preserves_primary_error_when_cleanup_also_fails(self) -> None:
        provider = ScriptedProvider([
            AgentResponse(None, (AgentToolCall("edit", "execute_command", {
                "command": {"type": "create_box", "width": 2, "height": 3, "depth": 4},
            }),)),
        ])
        runtime = AgentRuntime(self.core, provider, owner_id="cleanup-runtime")
        with patch.object(self.core, "execute_command", side_effect=RuntimeError("command failed")):
            with patch.object(self.core, "stop_agent", side_effect=RuntimeError("cleanup failed")) as stop:
                with self.assertRaisesRegex(RuntimeError, "command failed") as error:
                    runtime.run(self.project_id, "Create a box.")
        stop.assert_called_once()
        self.assertTrue(any("cleanup failed" in note for note in error.exception.__notes__))

    def test_run_timeout_validation_and_default_unlimited_behavior(self) -> None:
        for invalid in (True, 0, -1, float("inf"), float("nan"), "1"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "run_timeout_seconds"):
                    AgentRuntime(
                        self.core,
                        ScriptedProvider([AgentResponse("unused")]),
                        owner_id="runtime-test",
                        run_timeout_seconds=invalid,
                    )
        runtime = AgentRuntime(
            self.core,
            ScriptedProvider([AgentResponse("done")]),
            owner_id="runtime-test",
        )
        self.assertIsNone(runtime.run_timeout_seconds)


if __name__ == "__main__":
    unittest.main()
