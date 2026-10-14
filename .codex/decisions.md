# CADYX architectural decisions

## Safe agent stop and resume

- **Decision:** persist per-token stop fences in a separate SQLite runtime sidecar, independently of the full-command lease transaction. Validate saved document/geometry under the existing project guard before freeing an agent lease. Resume under that guard with a revision comparison and a new token; require expected revisions for its commands. **Reason:** a stop request must block queued commands while an atomic command still holds the lease database; revisions prevent replaying a committed command after a lost response and detect intervening human edits. **Constraint:** current cooperating hosts only; legacy lease acquisition remains compatible and does not require revision arguments. No Agent Runtime or portable schema change. **Source:** `cadyx/core.py`, `cadyx/lease.py`, [API contract](../docs/architecture.md#safe-agent-stop-and-resume).
- **Status:** active.

## Foundation pass — 2026-10-14

- **Decision:** use Python 3.12+ standard library for Core, local HTTP API, and SSE; use browser-native HTML/CSS/JavaScript for the first UI. **Reason:** this gives a working local vertical slice without provider coupling. **Constraint:** the original single-process restriction is superseded by the multi-process lease decision below. **Source:** `docs/architecture.md`.
- **Decision:** keep all mutations in the Core Command System and require an opaque edit lease; a stale lease blocks edits until saved state is reloaded and validated. **Reason:** AI and human must not mutate the same document concurrently, and timeout alone cannot prove safe recovery. **Source:** `cadyx/core.py`, `cadyx/lease.py`.
- **Decision:** the Web API accepts only human-owned leases for commands and the Agent API accepts only agent-owned leases; both delegate to the same command implementation. **Reason:** preserve one mutation path while enforcing the editor/observer boundary at each API surface. **Source:** `cadyx/web.py`, `docs/architecture.md`.
- **Decision:** store project metadata, current document, revision, and successful command history in one versioned `project.json`, replaced atomically per command; keep runtime leases out of that file. **Reason:** a command and its audit entry should become durable together, while lease state is host runtime state. **Source:** `docs/project-format-v1.md`, `cadyx/workspace.py`.
- **Decision:** isolate geometry behind `GeometryKernel`; the initial prototype used a validated primitive descriptor adapter. **Source:** `cadyx/kernel.py`.
- **Status:** the initial primitive-only implementation is superseded by the real geometry-kernel decision below.

## Real geometry kernel

- **Decision:** use current stable native OCCT through the small project-built C ABI bridge and Python `ctypes`, rather than a Python-version-bound third-party extension. Keep `GeometryKernel`, JSON descriptors, `occt-v1` and `primitive-v1` compatibility unchanged. **Reason:** the user requires absolute newest stable versions; latest cadquery-ocp excludes Python 3.15 and pins older VTK. The browser renderer requires no VTK. **Constraint:** verified setup is Windows x64 with CMake/MSVC and a modeling-only OCCT SDK; downloads/build artifacts stay local. **Source:** `native/`, `cadyx/native_kernel.py`, [setup](../README.md).
- **Status:** active; supersedes the previous cadquery-ocp/VTK runtime decision.

## Shared rendering and viewport

- **Decision:** Core produces a versioned read-only JSON scene under the existing project guard, with kernel-derived body triangles, stable object ids and sampled XY sketch primitives. Browser and Agent API expose identical scenes. One browser canvas renderer serves these scenes and PLY previews; camera state is client-local and Z-up. **Reason:** keep authoritative geometry in Core and remove browser box-specific geometry duplication without changing portable project data or requiring edit leases for observation. **Constraint:** orthographic preview, 64-segment circle sampling and sketch overlays; no selection picking, visibility persistence, perspective or sketch editor. **Source:** [viewport contract](../docs/viewport-api.md), `cadyx/rendering.py`, `web/viewport.js`.
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
