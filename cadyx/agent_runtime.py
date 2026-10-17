"""Provider-neutral orchestration for CADYX agent tool calls.

Provider adapters implement :class:`AgentProvider` and translate their native
request/response format to the small data contract in this module. The runtime
owns CADYX project lease and revision handling; document changes still go
through :class:`CadyxCore` and its shared Command System.
"""

from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

from .core import CadyxCore
from .errors import CadyxError, ConflictError, ValidationError
from .models import OwnerType


@dataclass(frozen=True)
class AgentToolCall:
    id: str
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True)
class AgentMessage:
    role: str
    content: str | None
    tool_calls: tuple[AgentToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class AgentToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True)
class AgentResponse:
    content: str | None
    tool_calls: tuple[AgentToolCall, ...] = ()


class AgentProvider(Protocol):
    """Adapter contract for any model or agent provider."""

    def complete(
        self,
        messages: Sequence[AgentMessage],
        tools: Sequence[AgentToolSpec],
    ) -> AgentResponse: ...


@dataclass(frozen=True)
class AgentRunResult:
    project_id: str
    final_response: str
    revision: int
    provider_turns: int
    tool_calls: int
    tool_names: tuple[str, ...]


class AgentRuntimeError(RuntimeError):
    """Raised when an agent provider violates the runtime contract or limits."""


class AgentRunCancelled(AgentRuntimeError):
    """Raised when cooperative cancellation aborts an agent run."""


class AgentRunDeadlineExceeded(AgentRuntimeError):
    """Raised when a configured agent-run execution deadline expires."""


@dataclass(frozen=True)
class _RegisteredTool:
    spec: AgentToolSpec
    execute: Callable[[Mapping[str, Any]], Any]


class _RunGuard:
    """Monotonic deadline and cooperative-cancellation state for one run."""

    def __init__(
        self,
        timeout_seconds: float | None,
        cancel_event: threading.Event | None,
    ) -> None:
        self._cancel_event = cancel_event
        self._deadline = (
            None if timeout_seconds is None else time.monotonic() + timeout_seconds
        )

    def check(self) -> None:
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise AgentRunCancelled("Agent run was cancelled.")
        if self._deadline is not None and time.monotonic() >= self._deadline:
            raise AgentRunDeadlineExceeded("Agent run exceeded its execution deadline.")

    def wait_seconds(self, maximum: float = 0.05) -> float:
        if self._deadline is None:
            return maximum
        return max(0.001, min(maximum, self._deadline - time.monotonic()))


class _LeaseHeartbeat:
    def __init__(self, core: CadyxCore, project_id: str, lease_id: str) -> None:
        self._core = core
        self._project_id = project_id
        self._lease_id = lease_id
        timeout = core.leases.timeout_seconds
        self._interval = max(0.001, min(timeout / 3.0, 30.0))
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self._failure: BaseException | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="cadyx-agent-heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._thread is not None:
            self._thread.join()

    def check(self) -> None:
        if self._failure is not None:
            raise AgentRuntimeError("The CADYX agent lease heartbeat failed.") from self._failure

    def _run(self) -> None:
        while not self._stopped.wait(self._interval):
            try:
                self._core.heartbeat(self._project_id, self._lease_id)
            except ConflictError as exc:
                if exc.code == "PROJECT_BUSY":
                    continue
                self._failure = exc
                return
            except BaseException as exc:
                self._failure = exc
                return


class AgentRuntime:
    """Run a provider-neutral tool loop against one existing CADYX project.

    Each command obtains a revision-protected agent lease immediately before
    mutation, maintains a heartbeat during the command, and releases it through
    the validated stop path afterward. Provider waits and read-only tools never
    hold an edit lease. A configured run timeout bounds provider waits;
    callers may also request cooperative cancellation with a ``threading.Event``.
    """

    def __init__(
        self,
        core: CadyxCore,
        provider: AgentProvider,
        *,
        owner_id: str,
        max_tool_rounds: int = 8,
        max_tool_calls: int = 32,
        run_timeout_seconds: float | None = None,
    ) -> None:
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 160:
            raise ValueError("owner_id must be a non-empty string of at most 160 characters")
        if type(max_tool_rounds) is not int or max_tool_rounds < 1:
            raise ValueError("max_tool_rounds must be a positive integer")
        if type(max_tool_calls) is not int or max_tool_calls < 1:
            raise ValueError("max_tool_calls must be a positive integer")
        if run_timeout_seconds is not None:
            if (
                isinstance(run_timeout_seconds, bool)
                or not isinstance(run_timeout_seconds, (int, float))
                or not math.isfinite(run_timeout_seconds)
                or run_timeout_seconds <= 0
            ):
                raise ValueError("run_timeout_seconds must be a positive finite number or None")
            run_timeout_seconds = float(run_timeout_seconds)
        self.core = core
        self.provider = provider
        self.owner_id = owner_id.strip()
        self.max_tool_rounds = max_tool_rounds
        self.max_tool_calls = max_tool_calls
        self.run_timeout_seconds = run_timeout_seconds

    def run(
        self,
        project_id: str,
        prompt: str,
        *,
        system_prompt: str = "You are operating a CADYX project. Inspect the project before editing it and use the provided tools for all project changes.",
        cancel_event: threading.Event | None = None,
    ) -> AgentRunResult:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if not isinstance(system_prompt, str):
            raise ValueError("system_prompt must be a string")
        if cancel_event is not None and not isinstance(cancel_event, threading.Event):
            raise ValueError("cancel_event must be a threading.Event or None")

        guard = _RunGuard(self.run_timeout_seconds, cancel_event)
        guard.check()
        current = self.core.observe_agent_operation(
            project_id, "Изучает модель", lambda: self.core.inspect_project(project_id),
        )
        revision_state = [current["document"]["revision"]]
        guard.check()
        result = self._run_loop(project_id, revision_state, prompt, system_prompt, guard)
        guard.check()
        return AgentRunResult(
            project_id=project_id,
            final_response=result[0],
            revision=revision_state[0],
            provider_turns=result[1],
            tool_calls=result[2],
            tool_names=tuple(result[3]),
        )

    def _complete_provider(
        self,
        messages: Sequence[AgentMessage],
        tools: Sequence[AgentToolSpec],
        guard: _RunGuard,
    ) -> AgentResponse:
        completed = threading.Event()
        response: list[AgentResponse] = []
        failure: list[BaseException] = []

        def invoke() -> None:
            try:
                response.append(self.provider.complete(messages, tools))
            except BaseException as exc:
                failure.append(exc)
            finally:
                completed.set()

        thread = threading.Thread(target=invoke, name="cadyx-agent-provider", daemon=True)
        thread.start()
        while not completed.wait(guard.wait_seconds()):
            guard.check()
        guard.check()
        if failure:
            raise failure[0]
        return response[0]

    def _run_loop(
        self,
        project_id: str,
        revision_state: list[int],
        prompt: str,
        system_prompt: str,
        guard: _RunGuard,
    ) -> tuple[str, int, int, list[str]]:
        def no_arguments(arguments: Mapping[str, Any]) -> None:
            if arguments:
                raise ValidationError("This tool does not accept arguments.")

        def inspect_project(arguments: Mapping[str, Any]) -> dict[str, Any]:
            no_arguments(arguments)
            detail = self.core.observe_agent_operation(
                project_id, "Изучает модель", lambda: self.core.inspect_project(project_id),
            )
            revision_state[0] = detail["document"]["revision"]
            return detail

        def get_viewport(arguments: Mapping[str, Any]) -> dict[str, Any]:
            no_arguments(arguments)
            return self.core.observe_agent_operation(
                project_id, "Получает 3D-сцену", lambda: self.core.render_project(project_id),
            )

        def execute_command(arguments: Mapping[str, Any]) -> dict[str, Any]:
            command = arguments.get("command")
            if not isinstance(command, dict):
                raise ValidationError("command must be an object.")
            if set(arguments) != {"command"}:
                raise ValidationError("Only the command field is accepted.")
            guard.check()
            # Resume checks the last observed revision while acquiring the lease
            # under Core's project guard. Another editor cannot race this command.
            resumed = self.core.resume_agent(project_id, self.owner_id, revision_state[0])
            lease_id = resumed["lease"]["lease_id"]
            heartbeat = _LeaseHeartbeat(self.core, project_id, lease_id)
            primary_error: BaseException | None = None
            try:
                heartbeat.start()
                guard.check()
                heartbeat.check()
                result = self.core.execute_command(
                    project_id,
                    lease_id,
                    command,
                    expected_owner_type=OwnerType.AGENT,
                    expected_revision=revision_state[0],
                )
                revision_state[0] = result["document"]["revision"]
                guard.check()
                heartbeat.check()
            except BaseException as exc:
                primary_error = exc
                raise
            finally:
                heartbeat.stop()
                try:
                    self.core.stop_agent(project_id, lease_id)
                except BaseException as stop_error:
                    if primary_error is None:
                        raise
                    primary_error.add_note(f"Agent lease cleanup also failed: {stop_error}")
            return result

        empty_schema = {"type": "object", "properties": {}, "additionalProperties": False}
        registered = {
            "inspect_project": _RegisteredTool(
                AgentToolSpec("inspect_project", "Read the current project, document, history and lock.", empty_schema),
                inspect_project,
            ),
            "get_viewport": _RegisteredTool(
                AgentToolSpec("get_viewport", "Read the project's current render scene.", empty_schema),
                get_viewport,
            ),
            "execute_command": _RegisteredTool(
                AgentToolSpec(
                    "execute_command",
                    "Execute one CADYX command with an immediately acquired and released agent lease.",
                    {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "object",
                                "description": "A command accepted by the CADYX Command System.",
                                "properties": {"type": {"type": "string"}},
                                "required": ["type"],
                                "additionalProperties": True,
                            }
                        },
                        "required": ["command"],
                        "additionalProperties": False,
                    },
                ),
                execute_command,
            ),
        }
        tool_specs = tuple(tool.spec for tool in registered.values())
        messages: list[AgentMessage] = []
        if system_prompt:
            messages.append(AgentMessage("system", system_prompt))
        messages.append(AgentMessage("user", prompt))

        seen_call_ids: set[str] = set()
        called_tools: list[str] = []
        tool_call_count = 0
        provider_turns = 0
        tool_rounds = 0
        while True:
            guard.check()
            response = self._complete_provider(tuple(messages), tool_specs, guard)
            guard.check()
            provider_turns += 1
            self._validate_response(response)
            messages.append(AgentMessage("assistant", response.content, response.tool_calls))
            if not response.tool_calls:
                if response.content is None:
                    raise AgentRuntimeError("Provider returned neither a final response nor tool calls.")
                return response.content, provider_turns, tool_call_count, called_tools

            tool_rounds += 1
            if tool_rounds > self.max_tool_rounds:
                raise AgentRuntimeError("Agent exceeded the maximum number of tool rounds.")
            for call in response.tool_calls:
                guard.check()
                if call.id in seen_call_ids:
                    raise AgentRuntimeError("Provider reused a tool call id within one run.")
                seen_call_ids.add(call.id)
                tool_call_count += 1
                if tool_call_count > self.max_tool_calls:
                    raise AgentRuntimeError("Agent exceeded the maximum number of tool calls.")
                tool = registered.get(call.name)
                if tool is None:
                    value: Any = {"error": "UNKNOWN_TOOL", "message": "Tool is not available."}
                else:
                    try:
                        value = tool.execute(call.arguments)
                    except CadyxError as exc:
                        value = exc.as_dict()
                    guard.check()
                    called_tools.append(call.name)
                messages.append(
                    AgentMessage(
                        "tool",
                        self._json_content(value),
                        tool_call_id=call.id,
                        name=call.name,
                    )
                )

    @staticmethod
    def _validate_response(response: AgentResponse) -> None:
        if not isinstance(response, AgentResponse):
            raise AgentRuntimeError("Provider must return an AgentResponse.")
        if response.content is not None and not isinstance(response.content, str):
            raise AgentRuntimeError("Provider response content must be text or None.")
        if not isinstance(response.tool_calls, tuple):
            raise AgentRuntimeError("Provider response tool_calls must be a tuple.")
        for call in response.tool_calls:
            if not isinstance(call, AgentToolCall):
                raise AgentRuntimeError("Provider returned an invalid tool call.")
            if not isinstance(call.id, str) or not call.id.strip():
                raise AgentRuntimeError("Provider tool call id must be non-empty text.")
            if not isinstance(call.name, str) or not call.name.strip():
                raise AgentRuntimeError("Provider tool call name must be non-empty text.")
            if not isinstance(call.arguments, Mapping):
                raise AgentRuntimeError("Provider tool call arguments must be an object.")

    @staticmethod
    def _json_content(value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise AgentRuntimeError("Tool result must be finite JSON data.") from exc
