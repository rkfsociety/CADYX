from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from typing import Sequence

from cadyx.agent_runtime import (
    AgentMessage,
    AgentResponse,
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


if __name__ == "__main__":
    unittest.main()
