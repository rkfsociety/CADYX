# CADYX architecture foundation

## Product boundary

CADYX is AI-first: agents use a versioned API and the shared Core command system. The browser is a read-only, full-window detail viewer; project creation and document writes go through the Agent API. Model and property docks start collapsed. The browser does not call a geometry kernel or own CAD state. It renders the live model and compact current-action status without a raw command or transcript feed.

```text
Provider adapter (future) -> Agent Runtime -> CADYX Core -> Command System -> Document
External agent -----------------------> Agent API -> CADYX Core -> Project Workspace
Browser --------------------------------> Web API -> CADYX Core -> Project Workspace
                                                      |            Geometry Kernel adapter
                                                      +-> event broker -> SSE -> Browser
```

The local prototype uses Python standard-library modules for the Core, Agent Runtime and HTTP server, with plain browser HTML/CSS/JavaScript. The current Windows x64 runtime uses Python 3.15, native OCCT 8.0.1, and the CPU build of Open3D 0.20.0 through project-built C ABI bridges (`native/`, `cadyx/native_kernel.py`, and `cadyx/native_scan.py`), independent of Python extension ABIs. Open3D handles PLY point-cloud cleanup, screened Poisson surface reconstruction, and mesh simplification. Its processor caps work at four threads and downsamples to 100,000 points; the implementation uses tensor PLY IO and point preprocessing, then converts point clouds to the legacy API for screened Poisson, which has no tensor equivalent. Triangle mesh cleanup and simplification use Open3D's legacy mesh API. The browser consumes Core-generated meshes and XY/XZ sketch primitives using the same Z-up orthographic canvas viewport as folder PLY previews. PLY rendering samples large inputs to keep the browser responsive. Core activity snapshots and SSE events retain actual command, solid-feature, camera and native scan phases for Agent API clients; the compact user-facing activity card shows only the current action/result or idle thinking state. Activity state is process-local. External agents can continue to use the Agent API directly. See [Agent Runtime](#agent-runtime), [viewport API](viewport-api.md), [scan command contract](document-model-v3.md#scan-import-and-editable-triangle-meshes) and [setup](../README.md).


## Core and storage

`cadyx/core.py` is the application boundary used by both API surfaces. `ProjectWorkspace` stores one versioned `project.json` per UUID project directory. The project file contains metadata, document state, revision, and command history. Loading applies explicit sequential migrations from `cadyx/migrations.py` to an in-memory copy, independently for the project envelope and document; reads never rewrite files. See the [migration contract](project-format-v1.md#migration-contract). Each accepted command updates a copied in-memory project and atomically replaces `project.json`; document revision and successful command history are committed together. Both project-list endpoints also report immediate directories without a CADYX `project.json` in `folders`. The browser treats these as selectable projects. Its folder file routes list and serve only direct regular files from a workspace directory; the browser preview reads PLY files without modifying source data.

The `projects/` directory is runtime data. Its contents are ignored by Git apart from `.gitkeep`. Source scans, meshes, CAD files, images, measurements, and references must be treated as user data and must not be overwritten or removed as part of routine development.

## Edit leases

Each project has one durable lease in its `.cadyx-leases.sqlite3` sidecar, shared by all local host processes using the same workspace. A lease records `project_id`, `owner_type`, `owner_id`, acquisition and heartbeat times, an opaque `lease_id`, and the timeout chosen on acquisition. Owners are `agent`, `human`, or `system`; states include `FREE`, `AI_LOCKED`, `HUMAN_LOCKED`, and `SYSTEM_LOCKED`. A heartbeat from a host with a different configured timeout does not change the lease's original timeout.

All document writes pass through `CadyxCore.execute_command`, which requires the current lease id and the appropriate human/agent owner for its API surface. A SQLite `BEGIN IMMEDIATE` transaction holds a per-project OS-backed lock throughout lease validation, document load, command execution, validation and atomic `project.json` replacement. Acquire, heartbeat, release, inspection and recovery use the same lock. Different projects use separate databases and remain independent. After five seconds waiting for an occupied project, an operation returns `PROJECT_BUSY` (HTTP 409); it may be retried, and never interrupts the running command.

A heartbeat timeout marks the lease stale but does not free the project. Recovery obtains the same lock, reloads the last atomically saved project and validates its document and geometry before deleting the stale lease. An in-flight command that started with a valid lease may finish after its heartbeat expires; recovery cannot overlap its save. A process crash releases the SQLite lock but preserves the committed lease, including across server restarts. Recovery remains required after expiry. A crash before the atomic replacement leaves the previous revision; a crash after it leaves the complete new revision and history. The client may not have received a response, so inspect saved history before retrying a command after a connection loss.

This supports cooperating current CADYX hosts on one local machine and local filesystem. Stop older hosts before upgrading: earlier hosts do not participate in the current agent stop protocol, and the earliest hosts also lack shared lease locks. Do not remove or replace coordination databases/journals while hosts are running. Back up or move projects with hosts stopped; coordination sidecars are runtime data, separate from the portable `project.json`. Direct file writers and network filesystems are outside this coordination contract. SSE delivery remains process-local; the browser's lightweight update polling makes committed project and workspace-file changes from another cooperating host appear automatically.

## Safe agent stop and resume

External agents use `POST /api/v1/agent/projects/{id}/stop` with `lease_id`.
The Core first commits a fence for that token in `.cadyx-agent.sqlite3`,
independently of the document/lease transaction. A command starts when it checks
this fence under the project guard: commands admitted before the fence may finish;
later commands using that token return `AGENT_STOPPING`. Stop then obtains the
same project guard, checks that the token is the current agent lease, reloads
and validates the saved document and geometry, and releases the lease. This also
permits validated stopping of an expired agent lease. Human and replacement
leases cannot be released by an old stop request. No command is forcibly interrupted.

The successful response contains `stopped: true`, `project`, `document`, `history`
and the now-free `lock`; revision and history reflect the last complete atomic
save. Failed validation retains the lease and the durable stop fence. A busy
project can return `PROJECT_BUSY` after five seconds while its fence remains
active; retry stop with the same token. A host crash preserves the fence. If the
stop response is lost, inspect the project: a free or replacement lease means
the old token no longer owns it; retrying stop cannot release the replacement.
Expired-lease recovery remains available and still validates saved state.

To continue (or begin work with revision protection), inspect the project and
call `POST /api/v1/agent/projects/{id}/resume` with `owner_id` and integer
`expected_revision`. Under one project guard, Core validates saved state,
compares its revision, acquires a fresh agent lease, and returns `resumed: true`,
`lease` and the complete inspection snapshot. Active leases are never stolen;
expired leases require stop/recovery first. A human edit since the inspected
revision returns `REVISION_CONFLICT`; inspect again and reconsider the next
command. Resume does not run commands or restore a previous model snapshot.

Every command on a resumed lease requires `expected_revision`; absent/invalid
values return `VALIDATION_ERROR`, and changed revisions return `REVISION_CONFLICT`
before execution. Increment the expected revision from each successful response.
After a lost command response, inspect revision and saved history to establish
whether the command was saved before deciding to submit another command; do not
automatically replay it. Retrying with the same expected revision cannot create
a second successful save. The existing `/leases` acquisition and commands remain
compatible: those leases accept optional `expected_revision`, but do not enforce
its presence. New agents should use `/resume` for guarded work.

The control sidecar records only token fences and revision-protection flags;
new resumed leases prune previous tokens. It does not change the portable project
or document schema, so no format migration is needed. Preserve both SQLite
sidecars while hosts run. UI stop controls remain outside this API milestone.

## Agent Runtime

`cadyx.agent_runtime.AgentRuntime` runs a provider-neutral completion and tool
loop for one existing project. A provider adapter implements the small
`AgentProvider.complete(messages, tools)` protocol and translates its native
request/response format to CADYX's `AgentMessage`, `AgentToolSpec` and
`AgentResponse` values. CADYX has no provider SDK dependency or provider-specific
configuration in Core.

The runtime exposes three tools to the provider: inspect the current project,
read its viewport scene, and execute one Command System command. Each run
compares the inspected revision while acquiring a fresh agent lease, checks
that revision for every command, refreshes the lease heartbeat while the
provider is working, and uses the validated stop path to release the lease on
success or failure. Provider tool rounds and total calls are bounded. Tool
validation and conflict errors are returned as structured tool results so the
provider can respond; unexpected provider/runtime failures end the run and
still attempt validated lease cleanup.

The runtime is an in-process Python API exported as `cadyx.AgentRuntime`; it is
not a provider integration, HTTP endpoint or background service. Projects are
created through the existing Core or Agent API before a runtime run. See the
[Agent Runtime module](../cadyx/agent_runtime.py) and
[focused tests](../tests/test_agent_runtime.py).

## Commands and geometry

The command registry supports `create_box`/`set_parameter`, `import_scan`/`set_scan_parameter`, XY/XZ sketches and closed-profile `extrude_sketch`/`revolve_sketch` features. Profile features keep their source sketch and operation parameters and rebuild atomically when source points or feature dimensions change. Scan commands resolve direct workspace PLY files through `ProjectWorkspace`, run inside the same Core lease and atomic-save path, and never write to the source. Each output is an atomic, content-addressed binary PLY beside its source; matching files are reused, conflicting files are never overwritten. The `reconstruct_scan` feature stores its source hash, output-file metadata, and processing parameters; its body's `cadyx-mesh-v1` geometry stores bounded compressed float32 vertices and uint32 faces in `project.json`. The Core retains the latest 128 in-memory visual activity snapshots per project and publishes each update as `agent.activity` SSE; snapshots cover Agent Runtime/API model inspections, render requests, document commands, solid features, camera orbits and actual native scan phases. The `GeometryKernel` protocol isolates CADYX Core from a specific kernel. `OCCTKernelAdapter` creates and validates box, profile extrusion and revolution B-reps with Open CASCADE and validates legacy `primitive-v1` descriptors; it also validates/tessellates the portable triangle-mesh format without creating native kernel objects for it. `PrimitiveKernelAdapter` remains available for demos and does not create B-rep geometry.

## HTTP authentication and LAN bind gate

The CLI host keeps `127.0.0.1` as its default bind address. Loopback mode is backward-compatible and can run without HTTP authentication. Before constructing the server, `cadyx.auth.validate_bind_auth` rejects any host that is not explicitly loopback unless a valid authentication token has been loaded. Hostnames that cannot be proven loopback are treated as non-loopback and therefore require authentication. This makes an accidental `--host 0.0.0.0` or LAN-address bind fail closed.

A token is supplied through exactly one runtime source: `CADYX_AUTH_TOKEN`, or a UTF-8 token file selected with `--auth-token-file` / `CADYX_AUTH_TOKEN_FILE`. Tokens must contain at least 32 characters and no whitespace. They are not stored in `project.json`, lease databases, command history or any portable schema, so enabling authentication needs no project-format migration. The CLI deliberately does not accept a raw token argument, avoiding routine exposure through command-line process listings.

When a token is configured, `cadyx.web` authenticates every application request before route handling. The browser UI and static assets, Web API, Agent API, workspace-file previews/downloads, viewport JSON/PNG, lease and command routes, and the SSE stream all share the same gate. `GET /api/v1/health` is the only public endpoint and returns only readiness status/API version. Rejected requests receive HTTP 401 and the connection is closed before any unread POST body can be reused as another HTTP/1.1 request.

Agent and API clients use `Authorization: Bearer <token>`. Browser navigation uses standard HTTP Basic authentication with username `cadyx` and the same token as the password; the browser then reuses the same-origin HTTP credentials for its fetches and event stream. Credential comparison uses `secrets.compare_digest`. The token is not accepted in a URL or query parameter.

This is an authentication boundary, not transport encryption. The built-in server remains plain HTTP. Non-loopback operation is therefore limited to a trusted private LAN unless a TLS-terminating reverse proxy or trusted VPN protects the transport. Direct Internet exposure of the built-in HTTP listener is outside the supported security boundary. The persistent Windows task remains loopback-only by default.

## APIs and events

Document Model v3 supports XY/XZ sketches, points, lines, circles, checked
geometric/dimensional constraints, and OCCT extrusion/revolution features.
Sketch references use stable typed
UUIDs and are validated on load and after every command. Coordinate updates can
be batched atomically; deletions cannot leave dangling references. Constraints
check the supplied geometry without solving it. The existing command endpoints
support these objects under the same leases and atomic save path. Document v1
is upgraded in memory; the project envelope stays at version 1. See
[Document Model v3](document-model-v3.md) for the supported commands, migration
policy and exact semantics. Sketch points, lines and circles now render in the
shared viewport. Project creation, model creation and editing stay on the Agent
API; the browser provides no project or document mutation controls. Agents can
fetch a server-rendered PNG snapshot through the Agent API without a browser.

The HTTP API uses JSON under `/api/v1`. The Agent API is under `/api/v1/agent`; it shares Core operations and requires an agent lease for commands. Agents create projects through `/api/v1/agent/projects` and edit supported bodies and sketches through `/api/v1/agent/projects/{id}/commands`, using the existing Command System and agent lease. `POST /api/v1/agent/projects/{id}/viewport/camera` publishes a transient animated orbit to the selected browser viewport; it does not edit the document or require a lease. Both Agent API and web API expose `GET .../viewport` for a JSON scene and `GET .../viewport.png` for a server-rendered image; image retrieval is read-only and requires no browser or edit lease. Both `GET /api/v1/projects` and `GET /api/v1/agent/projects` return persisted CADYX projects in `projects` and immediate project-directory names in `folders`. `GET /api/v1/workspace/folders/{folder}/files` lists direct regular files; the corresponding `/files/{name}` route serves an individual file for browser preview. Folder entries are available for discovery and preview; project inspection, leases, and CADYX document commands continue to apply to persisted CADYX project records. The browser reads `/api/v1/projects` snapshots and viewport scenes; it does not issue project or model writes. The HTTP Web command route remains available to human API clients. Its only default visible content is the detail; model and property docks start collapsed. Project updates are broadcast in-process over server-sent events at `/api/v1/events`; event history is not replayed after reconnect, so clients refresh project state through the API.

The default host is `127.0.0.1`. Non-loopback binds require the host authentication gate described above; the built-in server still relies on a trusted LAN or external TLS/VPN for transport confidentiality.

## Foundation roadmap

The development sequence, including authenticated LAN access and the prerequisites for Scan-to-CAD, is maintained in the [CADYX project roadmap](../.codex/roadmap.md). Inspect the current implementation before treating a milestone as unfinished or complete.
