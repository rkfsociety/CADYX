# Agent Runtime execution budgets and cancellation

`AgentRuntime` keeps provider execution limits separate from project edit-lease expiry. `run_timeout_seconds` is an optional positive finite monotonic budget configured on the runtime. Its default is `None`, which preserves the previous unlimited run duration. `run(..., cancel_event=...)` optionally accepts a `threading.Event` for cooperative caller cancellation.

A configured deadline or cancellation request raises a typed `AgentRunDeadlineExceeded` or `AgentRunCancelled`, both subclasses of `AgentRuntimeError`. The runtime checks the run guard before and after provider turns and before each tool call. A tool or atomic Core command already admitted when cancellation arrives is allowed to finish; no later provider response or tool call is accepted.

## Provider waits

The provider protocol remains synchronous, so a blocking provider call cannot be forcibly terminated safely. CADYX invokes each `AgentProvider.complete` call on a daemon worker and waits for its result through the run guard. While the run remains active, the existing lease heartbeat continues. When a deadline or cancellation wins the race, the runtime stops accepting the provider result, stops the heartbeat and proceeds to validated lease cleanup. A provider worker that eventually returns has only messages and tool specifications, not executable CADYX tool callbacks, so its late response cannot mutate the project or resume the tool loop.

With no execution budget and no cancellation request, provider calls retain the previous unlimited-duration behavior. The guarded wait still observes heartbeat failures while the provider is running.

## Cleanup and error precedence

After an agent lease has been acquired, every run exit path attempts `CadyxCore.stop_agent(project_id, lease_id)`, including provider failure, deadline expiry, cancellation, heartbeat failure and heartbeat startup failure. This keeps the existing validated stop path authoritative for saved-document and geometry checks.

If cleanup alone fails, that cleanup error is raised. If the run already has a primary failure, the primary exception is preserved and the cleanup failure is attached as an exception note rather than replacing it. This applies to typed deadline/cancellation failures as well as provider and runtime failures.

Tests in `tests/test_agent_runtime.py` cover the unchanged unlimited heartbeat behavior, a stalled provider deadline, cancellation between tool rounds with no subsequent command execution, and primary-error preservation when cleanup also fails.
