# CADYX architectural decisions

## Foundation pass — 2026-10-14

- **Decision:** use Python 3.12+ standard library for Core, local HTTP API, and SSE; use browser-native HTML/CSS/JavaScript for the first UI. **Reason:** the repository was empty and this gives a working local vertical slice without production dependencies or provider coupling. **Constraint:** keep the host single-process until leases have cross-process coordination. **Source:** `docs/architecture.md`.
- **Decision:** keep all mutations in the Core Command System and require an opaque edit lease; a stale lease blocks edits until saved state is reloaded and validated. **Reason:** AI and human must not mutate the same document concurrently, and timeout alone cannot prove safe recovery. **Source:** `cadyx/core.py`, `cadyx/lease.py`.
- **Decision:** the Web API accepts only human-owned leases for commands and the Agent API accepts only agent-owned leases; both delegate to the same command implementation. **Reason:** preserve one mutation path while enforcing the editor/observer boundary at each API surface. **Source:** `cadyx/web.py`, `docs/architecture.md`.
- **Decision:** store project metadata, current document, revision, and successful command history in one versioned `project.json`, replaced atomically per command; keep runtime leases out of that file. **Reason:** a command and its audit entry should become durable together, while lease state is host runtime state. **Source:** `docs/project-format-v1.md`, `cadyx/workspace.py`.
- **Decision:** isolate geometry behind `GeometryKernel`; start with a validated primitive descriptor adapter. **Reason:** the initial pass needs a runnable seam without building or pretending to provide a B-Rep kernel. **Source:** `cadyx/kernel.py`.
- **Status:** active for the foundation prototype; revisit process coordination, CAD-kernel choice, and renderer when those capabilities enter scope.
