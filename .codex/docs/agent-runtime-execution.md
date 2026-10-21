# Agent Runtime execution budgets and cancellation

[Полный русский перевод](agent-runtime-execution.ru.md)

[Русский обзор](#русский-обзор) · [English reference](#english-reference)

## Русский обзор

### Выполнение и отмена Agent Runtime

`run_timeout_seconds` задаёт необязательный положительный бюджет времени, `cancel_event` — кооперативную отмену. Отмена и дедлайн проверяются до/после вызовов провайдера и инструментов; уже принятая атомарная команда может завершиться. Рассуждение провайдера и read-only инспекция не удерживают блокировку. Каждая команда получает отдельную блокировку с проверкой ревизии и heartbeat, затем освобождает её в `finally`. Ошибка команды остаётся основной, ошибка очистки добавляется примечанием. Аварийно оставленную блокировку восстанавливают через проверенный механизм Core.

Это обзор, не полный перевод. Полные схемы, примеры и подробности контракта приведены на английском ниже.

## English reference

`AgentRuntime` keeps provider execution limits separate from project edit-lease expiry. `run_timeout_seconds` is an optional positive finite monotonic budget; its default `None` permits an unlimited provider run. `run(..., cancel_event=...)` optionally accepts a `threading.Event` for cooperative cancellation.

A deadline or cancellation raises `AgentRunDeadlineExceeded` or `AgentRunCancelled`, both subclasses of `AgentRuntimeError`. The guard is checked before and after provider turns, before tool calls, and while a command lease is held. An already admitted atomic Core command may finish; no subsequent command is accepted after cancellation.

## Provider waits and command leases

Provider calls execute on daemon workers so the guarded caller can stop waiting on a stalled synchronous provider. Neither provider reasoning nor read-only inspect/viewport tools hold an edit lease or need a heartbeat. A late provider response has no executable CADYX callbacks and cannot mutate the project.

For each `execute_command`, the runtime resumes a *new* revision-protected agent lease immediately before the command, starts a heartbeat for its duration, executes under the expected revision and agent ownership, then stops the heartbeat and uses `CadyxCore.stop_agent` to validate and release that lease. Consecutive commands get distinct leases, including within one provider response. A failed resume leaves other owners' leases untouched. Explicit project inspection refreshes the runtime's expected revision; if another editor changes it after inspection, resume returns `REVISION_CONFLICT` and no agent command is admitted.

## Cleanup and error precedence

Every *acquired* command lease is stopped in a `finally` block on success, command rejection, exception, cancellation or deadline. If cleanup alone fails, its error is raised. If a command already failed, its exception remains primary and the cleanup error is attached as a note. Provider failures, cancellation and deadlines **outside** commands require no lease cleanup because the project is free (unless owned by another editor). A crashed process still leaves its current edit lease for validated Core stop/expired-lease recovery.

Tests in `tests/test_agent_runtime.py` cover provider-time unlock, independent per-command leases, competing editors and stale revisions, cancellation during a command, cleanup-error precedence and recovery after an interrupted stop.
