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
from cadyx.core import CadyxCore
from cadyx.kernel import PrimitiveKernelAdapter


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
            ("inspect_project", "get_viewport", "execute_command"),
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
        self.assertEqual(json.loads(tool_message.content)["error"], "VALIDATION_ERROR")

    def test_provider_failure_stops_agent_lease(self) -> None:
        runtime = AgentRuntime(self.core, FailingProvider(), owner_id="runtime-test")

        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            runtime.run(self.project_id, "Create a model.")

        self.assertEqual(self.core.inspect_project(self.project_id)["lock"]["state"], "FREE")

    def test_heartbeat_keeps_lease_alive_during_provider_call(self) -> None:
        core = CadyxCore(
            str(Path(self.temporary_directory.name) / "short-lease-projects"),
            kernel=PrimitiveKernelAdapter(),
            lease_timeout_seconds=0.30,
        )
        project_id = core.create_project("Heartbeat test")["project"]["id"]

        result = AgentRuntime(core, SlowProvider(), owner_id="slow-runtime").run(project_id, "Wait for the provider.")

        self.assertEqual(result.final_response, "Completed after a long provider call.")
        self.assertEqual(result.revision, 0)
        self.assertEqual(core.inspect_project(project_id)["lock"]["state"], "FREE")

    def test_deadline_aborts_stalled_provider_and_releases_lease(self) -> None:
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

    def test_deadline_preserves_primary_failure_when_cleanup_also_fails(self) -> None:
        provider = BlockingProvider()
        runtime = AgentRuntime(
            self.core,
            provider,
            owner_id="cleanup-runtime",
            run_timeout_seconds=0.20,
        )
        try:
            with patch.object(self.core, "stop_agent", side_effect=RuntimeError("cleanup failed")) as stop:
                with self.assertRaises(AgentRunDeadlineExceeded) as error:
                    runtime.run(self.project_id, "Wait for a stalled provider.")
            stop.assert_called_once()
            self.assertTrue(any("cleanup failed" in note for note in error.exception.__notes__))
        finally:
            provider.release.set()

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
