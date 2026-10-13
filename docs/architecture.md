# CADYX architecture foundation

## Product boundary

CADYX is AI-first: agents use a versioned API and the same Core command system as the browser. The browser observes authoritative project state and can issue commands only while its human edit lease is active. It does not call a geometry kernel or own CAD state.

```text
AI provider (future) -> Agent API -> CADYX Core -> Command System -> Document
Browser ----------------> Web API -> CADYX Core -> Project Workspace
                                      |            Geometry Kernel adapter
                                      +-> event broker -> SSE -> Browser
```

The first pass is a local single-process prototype. It uses Python 3.12+ standard-library modules for the Core and HTTP server, with plain browser HTML/CSS/JavaScript. Open CASCADE is provided through the pinned `cadquery-ocp` bindings, including VTK for the planned shared rendering and viewport API; there are no AI-provider integrations. The browser displays supported CADYX box geometry as an isometric SVG preview and can preview binary little-endian PLY meshes and point clouds from folder projects in a rotatable canvas. PLY rendering samples large inputs to keep the browser responsive. The project stream is an internal synchronization mechanism; the UI does not expose an agent command feed. There is no embedded Agent Runtime yet, so an external agent must use the Agent API.

## Core and storage

`cadyx/core.py` is the application boundary used by both API surfaces. `ProjectWorkspace` stores one versioned `project.json` per UUID project directory. The project file contains metadata, document state, revision, and command history. Loading applies explicit sequential migrations from `cadyx/migrations.py` to an in-memory copy, independently for the project envelope and document; reads never rewrite files. See the [migration contract](project-format-v1.md#migration-contract). Each accepted command updates a copied in-memory project and atomically replaces `project.json`; document revision and successful command history are committed together. Both project-list endpoints also report immediate directories without a CADYX `project.json` in `folders`. The browser treats these as selectable projects. Its folder file routes list and serve only direct regular files from a workspace directory; the browser preview reads PLY files without modifying source data.

The `projects/` directory is runtime data. Its contents are ignored by Git apart from `.gitkeep`. Source scans, meshes, CAD files, images, measurements, and references must be treated as user data and must not be overwritten or removed as part of routine development.

## Edit leases

Each process has one in-memory lease per project. A lease records `project_id`, `owner_type`, `owner_id`, acquisition and heartbeat times, and an opaque `lease_id`. Owners are `agent`, `human`, or `system`; states include `FREE`, `AI_LOCKED`, `HUMAN_LOCKED`, and `SYSTEM_LOCKED`.

All document writes pass through `CadyxCore.execute_command`, which requires the current lease id and, for Agent API commands, an agent-owned lease. A heartbeat timeout marks the lease stale but does not free the project. Recovery reloads the last atomically saved project file and validates its document and geometry before releasing the stale lease. A server restart clears in-memory leases; because every successful command is saved atomically before returning, the on-disk document remains the recoverable state.

This initial lease manager is process-local. Run only one CADYX host against a given project workspace; multi-process locking and durable leases are future work.

## Commands and geometry

The initial command registry supports `create_box` and `set_parameter` for box dimensions. Object ids use typed UUIDs such as `body:<uuid>` and `feature:<uuid>`. The `GeometryKernel` protocol isolates CADYX Core from a specific kernel. The default `OCPKernelAdapter` creates box B-reps with Open CASCADE, checks topology with `BRepCheck_Analyzer`, and gets volume from kernel mass properties. Project files store JSON-safe dimensions and measured volume; the kernel shape is regenerated and checked instead of serializing a native object. The adapter continues to validate existing `primitive-v1` descriptors. `PrimitiveKernelAdapter` remains available for demos and does not create B-rep geometry.

## APIs and events

Document Model v2 adds independent XY sketches, points, lines, circles and
checked geometric/dimensional constraints. Sketch references use stable typed
UUIDs and are validated on load and after every command. Coordinate updates can
be batched atomically; deletions cannot leave dangling references. Constraints
check the supplied geometry without solving it. The existing command endpoints
support these objects under the same leases and atomic save path. Document v1
is upgraded in memory; the project envelope stays at version 1. See
[Document Model v2](document-model-v2.md) for the supported commands, migration
policy and exact semantics. Sketch rendering/editing in the browser remains
future work.

The HTTP API uses JSON under `/api/v1`. The Agent API is under `/api/v1/agent`; it shares Core operations and requires an agent lease for commands. Both `GET /api/v1/projects` and `GET /api/v1/agent/projects` return persisted CADYX projects in `projects` and immediate project-directory names in `folders`. `GET /api/v1/workspace/folders/{folder}/files` lists direct regular files; the corresponding `/files/{name}` route serves an individual file for browser preview. Folder entries are available for discovery and preview; project inspection, leases, and CADYX document commands continue to apply to persisted CADYX project records. The browser API is under `/api/v1/projects` and requires a human lease for commands. Both command routes enter the same Command System. Project updates are broadcast in-process over server-sent events at `/api/v1/events`; event history is not replayed after reconnect, so clients refresh project state through the API.

The default host is `127.0.0.1`. No authentication or LAN deployment hardening is implemented in this foundation pass.

## Foundation roadmap

The proposed development sequence, including the authentication gate for LAN access and the dependencies for multi-process hosting and Scan-to-CAD, is maintained in the [CADYX project roadmap](../.codex/roadmap.md). Inspect the current implementation before treating a proposed milestone as unfinished or complete.
