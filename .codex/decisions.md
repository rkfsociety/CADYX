# CADYX architectural decisions

## Foundation pass — 2026-10-14

- **Decision:** use Python 3.12+ standard library for Core, local HTTP API, and SSE; use browser-native HTML/CSS/JavaScript for the first UI. **Reason:** this gives a working local vertical slice without provider coupling. **Constraint:** the original single-process restriction is superseded by the multi-process lease decision below. **Source:** `docs/architecture.md`.
- **Decision:** keep all mutations in the Core Command System and require an opaque edit lease; a stale lease blocks edits until saved state is reloaded and validated. **Reason:** AI and human must not mutate the same document concurrently, and timeout alone cannot prove safe recovery. **Source:** `cadyx/core.py`, `cadyx/lease.py`.
- **Decision:** the Web API accepts only human-owned leases for commands and the Agent API accepts only agent-owned leases; both delegate to the same command implementation. **Reason:** preserve one mutation path while enforcing the editor/observer boundary at each API surface. **Source:** `cadyx/web.py`, `docs/architecture.md`.
- **Decision:** store project metadata, current document, revision, and successful command history in one versioned `project.json`, replaced atomically per command; keep runtime leases out of that file. **Reason:** a command and its audit entry should become durable together, while lease state is host runtime state. **Source:** `docs/project-format-v1.md`, `cadyx/workspace.py`.
- **Decision:** isolate geometry behind `GeometryKernel`; the initial prototype used a validated primitive descriptor adapter. **Source:** `cadyx/kernel.py`.
- **Status:** the initial primitive-only implementation is superseded by the real geometry-kernel decision below.

## Real geometry kernel

- **Decision:** use Open CASCADE through the pinned `cadquery-ocp` Python bindings behind `GeometryKernel`; store box dimensions and kernel-measured volume as JSON descriptors, then rebuild and validate B-reps instead of serializing native kernel objects. Continue accepting `primitive-v1` descriptors. Include its VTK runtime for the planned shared rendering and viewport work. **Reason:** this adds real B-rep construction while preserving portable project files and existing projects, and prepares the selected visualization runtime for the next roadmap stage. **Constraint:** kernel-specific code stays inside the adapter. **Source:** `cadyx/kernel.py`, `docs/project-format-v1.md`, `requirements.txt`.
- **Status:** active.

## Document Model v2

- **Decision:** represent XY sketches, points, lines, circles and constraints as document objects with stable typed UUID references. Membership has one source (`sketch_id`); constraints validate geometry with absolute tolerance 1e-7 instead of implying a solver. Batch coordinate updates use the existing atomic Core command path. **Reason:** add editable, persistent relationships with explicit invariant checks without introducing a separate mutation system. **Source:** `docs/document-model-v2.md`, `cadyx/sketches.py`, `cadyx/commands.py`.
- **Decision:** explicitly upgrade document v1 to v2 in memory and save it only with the next successful command; keep project envelope v1. **Reason:** legacy bodies, features and history remain intact and read-only inspection does not rewrite user data. Older hosts reject v2; sequential migrations are now centralized in `cadyx/migrations.py`. **Source:** `cadyx/models.py`, `docs/document-model-v2.md`, `tests/test_document.py`.
- **Status:** active.

## Project Format migrations

- **Decision:** centralize independent project/document schema versions and consecutive migration steps in `cadyx/migrations.py`. Migrate copies on load, validate through the existing domain/workspace checks, and persist through the existing leased Core command and atomic save path. Keep envelope v1 and document v2 until an actual schema change requires another version. **Reason:** future schema evolution needs explicit transitions without rewriting user data on reads or introducing a second mutation path. **Source:** `docs/project-format-v1.md`, `cadyx/migrations.py`, `tests/test_migrations.py`.
- **Status:** active.

## Multi-process edit leases

- **Decision:** use a per-project SQLite runtime sidecar and `BEGIN IMMEDIATE` for shared leases and the complete Core command/recovery critical section; keep `project.json` and its atomic replacement unchanged. **Reason:** standard-library OS-backed coordination releases locks on process death while retaining lease ownership for validated recovery, without blocking unrelated projects. **Constraint:** cooperating current hosts on local filesystems only; SSE remains process-local. Sidecars must not be removed while hosts are active. **Source:** `cadyx/lease.py`, `cadyx/core.py`, [lease contract](../docs/architecture.md#edit-leases), `tests/test_multiprocess_leases.py`.
- **Status:** active; supersedes the foundation's process-local lease restriction.
