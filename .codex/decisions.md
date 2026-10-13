# CADYX architectural decisions

## Foundation pass — 2026-10-14

- **Decision:** use Python 3.12+ standard library for Core, local HTTP API, and SSE; use browser-native HTML/CSS/JavaScript for the first UI. **Reason:** this gives a working local vertical slice without provider coupling. **Constraint:** keep the host single-process until leases have cross-process coordination. **Source:** `docs/architecture.md`.
- **Decision:** keep all mutations in the Core Command System and require an opaque edit lease; a stale lease blocks edits until saved state is reloaded and validated. **Reason:** AI and human must not mutate the same document concurrently, and timeout alone cannot prove safe recovery. **Source:** `cadyx/core.py`, `cadyx/lease.py`.
- **Decision:** the Web API accepts only human-owned leases for commands and the Agent API accepts only agent-owned leases; both delegate to the same command implementation. **Reason:** preserve one mutation path while enforcing the editor/observer boundary at each API surface. **Source:** `cadyx/web.py`, `docs/architecture.md`.
- **Decision:** store project metadata, current document, revision, and successful command history in one versioned `project.json`, replaced atomically per command; keep runtime leases out of that file. **Reason:** a command and its audit entry should become durable together, while lease state is host runtime state. **Source:** `docs/project-format-v1.md`, `cadyx/workspace.py`.
- **Decision:** isolate geometry behind `GeometryKernel`; the initial prototype used a validated primitive descriptor adapter. **Source:** `cadyx/kernel.py`.
- **Status:** the initial primitive-only implementation is superseded by the real geometry-kernel decision below.

## Real geometry kernel

- **Decision:** use Open CASCADE through the pinned `cadquery-ocp` Python bindings behind `GeometryKernel`; store box dimensions and kernel-measured volume as JSON descriptors, then rebuild and validate B-reps instead of serializing native kernel objects. Continue accepting `primitive-v1` descriptors. Include its VTK runtime for the planned shared rendering and viewport work. **Reason:** this adds real B-rep construction while preserving portable project files and existing projects, and prepares the selected visualization runtime for the next roadmap stage. **Constraint:** kernel-specific code stays inside the adapter. **Source:** `cadyx/kernel.py`, `docs/project-format-v1.md`, `requirements.txt`.
- **Status:** active.
