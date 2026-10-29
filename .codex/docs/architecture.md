# CADYX architecture foundation

[Русский перевод (в процессе)](architecture.ru.md)

[Полный русский перевод](architecture.ru.md) · [English reference](#english-reference)

## Русский обзор

### Архитектура CADYX

Core владеет состоянием проекта, общий Command System проверяет и атомарно сохраняет изменения. Агент использует Agent API/MCP; браузер показывает модель, прогресс и события. Геометрическое ядро скрыто за GeometryKernel. Защита проекта объединяет edit lease, ожидаемую ревизию и контроль активности. Ниже описаны HTTP-аутентификация, SSE, хранение, композиция расширений, нативный ABI, инспекция и механизм проверяемых расширений. Для запуска см. [русское руководство](../../docs/operations.ru.md). Точные поля и ограничения приведены в английском контракте ниже.

Это обзор, не полный перевод. Полные схемы, примеры и подробности контракта приведены на английском ниже.

## English reference

## Product boundary

CADYX is AI-first: agents use a versioned API and the shared Core command system. The desktop browser is a full-window viewer with a human assembly editor; agents still create projects and edit models through the Agent API. Browser assembly edits use the Web API's human lease and expected-revision command route, while the Core remains the only owner of saved CAD state and geometry. The object tree is in Geometry; project-file browsing is gated by project availability. The browser does not call a geometry kernel. It renders the live model and a single-line current-action/result status without a raw command or transcript feed.

The embedded Revopoint panel can display either the live depth frame or a saved ASCII point-only PLY from a workspace folder. Saved clouds are rendered directly as points through the read-only `/api/v1/scanner/point-cloud` endpoint; this preview does not convert them into a triangle mesh or claim metric scale.

```text
Provider adapter (future) -> Agent Runtime -> CADYX Core -> Command System -> Document
External agent -----------------------> Agent API -> CADYX Core -> Project Workspace
Browser --------------------------------> Web API -> CADYX Core -> Project Workspace
                                                      |            Geometry Kernel adapter
                                                      +-> event broker -> SSE -> Browser
```

The local prototype uses Python standard-library modules for the Core, Agent Runtime and HTTP server, with plain browser HTML/CSS/JavaScript. The current Windows x64 runtime uses Python 3.15, native OCCT 8.0.1, and the CPU build of Open3D 0.20.0 through project-built C ABI bridges (`native/`, `cadyx/native_kernel.py`, and `cadyx/native_scan.py`), independent of Python extension ABIs. The OCCT STEP reader is linked through `TKDESTEP`; its additional runtime DLL closure is hash-pinned in `scripts/occt-runtime-lock.json` and installed by `scripts/setup-runtime.ps1`. Open3D handles PLY point-cloud cleanup, screened Poisson surface reconstruction, and mesh simplification. Its processor caps work at four threads and downsamples to 100,000 points; the implementation uses tensor PLY IO and point preprocessing, then converts point clouds to the legacy API for screened Poisson, which has no tensor equivalent. Triangle mesh cleanup and simplification use Open3D's legacy mesh API. The browser consumes Core-generated meshes and sketch primitives on named XY/XZ/YZ and custom/face-attached work planes using the same Z-up orthographic canvas viewport as folder PLY previews. PLY rendering samples large inputs to keep the browser responsive. Core activity snapshots and SSE events retain actual command, solid-feature, camera and native scan phases for Agent API clients; the UI shows one progress card with the current status, progress/ETA, the current or last actual agent action, its current or most recently finished task, and a newest-first history of completed/failed actions with local completion times. Planned phases and future queue entries remain available through the Agent API but are hidden in the browser. Activity remains process-local; dashboard reports are stored as per-project rows in the workspace SQLite runtime database, with each report and the global work revision committed in one transaction. Existing JSON dashboard state is imported on first startup when the database is absent. External agents can report dashboard state through the Agent API; hardware telemetry is not currently available. See [Agent Runtime](#agent-runtime), [viewport API](viewport-api.md), [scan command contract](document-model-v7.md#scan-import-and-editable-triangle-meshes), [STEP import contract](document-model-v7.md#step-import-and-editable-b-reps) and [setup](../../README.md).


## Core and storage

For the current project and document versions, workspace reads validate the parsed snapshot directly without making a migration copy. Imported B-rep payloads shared by body, feature, and history records are stored once in content-addressed hidden project files, and references are expanded before validation or API responses; see the [project format](project-format-v1.md). Command and preview loads use the committed history count and byte offset without reparsing the journal; saves append only new history entries, while full project inspections validate the committed journal. Each command works on an isolated document and appends only its new history entries. The already-validated document is not validated a second time at the save boundary.

`cadyx/core.py` is the application boundary used by both API surfaces. `ProjectWorkspace` stores one versioned `project.json` per human-readable project directory; new project names are sanitized for the filesystem and collisions receive a numeric suffix; adopted directories keep their existing names. A local `.cadyx-project-id` marker keeps the UUID addressable even if `project.json` is damaged. The stable UUID remains the API and lease identity, and legacy UUID-named directories remain readable. The project file contains metadata, document state, revision, and command history. Loading applies explicit sequential migrations from `cadyx/migrations.py` to an isolated copy only when the project envelope or document is older; current snapshots are validated without a migration copy, and reads never rewrite files. See the [migration contract](project-format-v1.md#migration-contract). Each accepted command updates an isolated document and atomically replaces `project.json`; document revision and successful command history are committed together. Read-only listing, document inspection, and viewport rendering use the last atomically saved snapshot, so a page reload can restore a model while an agent holds the edit lease for its next command. Both project-list endpoints adopt each immediate non-symlink directory without a project record in place by publishing a stable UUID marker and an empty `project.json`; existing user files remain untouched. Adopted directories appear in `projects`, while directories with conflicting reserved metadata remain in `folders`. The browser restores its previous project selection and lets the user select a listed project through Projects (07) when its lock is free. Folder file routes list and serve only direct regular files from a workspace directory. Project file routes list and serve only direct user files; project metadata, `.cadyx-*` sidecars, nested files and symlinks are excluded. `GET /api/v1/projects/{id}/files/{name}/preview` builds an in-memory viewport scene for a staged STEP/STP through the existing OCCT import path; it does not change the saved document or history. Viewport reads check a stat-only project-file token before loading and validating the full document, then reuse a cached scene for an unchanged snapshot. Geometry-validation results are keyed by the atomically updated document revision, avoiding repeated serialization and hashing of large scan meshes. Mesh tessellation validates the compressed payload as it decodes it, without a second validation pass. PNG snapshots project from the flat vertex buffer and reuse the projection arrays to limit peak memory.

The `projects/` directory is runtime data. Its contents are ignored by Git apart from `.gitkeep`. Source scans, meshes, CAD files, images, measurements, and references must be treated as user data and must not be overwritten or removed as part of routine development. The Agent API and Files view can upload user files with `POST /api/v1/agent/projects/{id}/files/{name}` (`application/octet-stream`, up to 64 MiB per file) into the project root. Uploads require a free project, use a direct filename, and never replace existing files; STEP/STP and PLY retain specialized staging paths, while other file types are stored as attachments and served as downloads. `POST /api/v1/agent/projects/{id}/files/rename` renames an unused direct project file (optionally within a project subfolder) without replacing an existing target. `import_project_scan` processes a staged PLY into an editable triangle-mesh reference and records its filename and hash so parameter edits reprocess the unchanged source. The local MCP adapter exposes STEP and PLY upload as `cadyx_upload_project_step` and `cadyx_upload_project_scan`; both read a local regular file and send its bytes, while the HTTP host never resolves arbitrary client paths. For temporary reconstruction references, `DELETE /api/v1/agent/projects/{id}/files/{name}` removes only a direct STEP file from a free project and refuses while a current `import_step` feature still uses it. The local MCP adapter exposes this as `cadyx_remove_staged_step`; the original local source remains untouched. `POST /api/v1/agent/projects/{id}/copy-files` copies user files recursively from another free project, omits project metadata and `.cadyx-*` runtime state, preflights collisions, and never overwrites. `cadyx_copy_project_files` exposes this through the local MCP adapter. `POST /api/v1/agent/projects/{id}/prune-files` removes user files recursively while retaining explicit direct filenames; it requires a free project with an empty document, preserves project metadata and `.cadyx-*` state, and reports hashes for retained files. `cadyx_prune_project_files` exposes this through the local MCP adapter. Host authentication policy applies to file routes. `POST /api/v1/agent/projects` accepts `select: false` to create a project without changing the current viewer selection, allowing file staging while a different project has active work.

## Project availability policies

`CadyxCore._project_availability` combines the lease snapshot and running
operation state. Persisted dashboard activity remains a separate project-scoped
UI/reporting signal and does not gate Core command admission. Availability policy checks are the
single Core boundary used by project reads, writes, file management, resource
selection and destructive lifecycle operations. A lock snapshot that is
missing, busy or otherwise unknown does not count as free.

Nonblocking availability reads also avoid waiting on the scanner controller's
device-I/O guard. While that guard is held, scan state is conservatively treated
as running; authoritative mutation checks still wait for the actual state.

Read-only inspection and listings use the nonblocking lease snapshot and
continue to return the last atomically saved project state while a writer is
active. A new write lease requires a free project with no recovery requirement
or running operation; commands still require their validated owner lease and
revision. A command admitted with a valid lease remains atomic if that lease
expires while the command is running.

File management and destructive actions require `FREE`, no lease recovery and
no running operation, preserving their established `PROJECT_BUSY` or
`PROJECT_LOCKED` errors. Workspace selection is a process-global viewer hint,
not an ownership or command-routing mechanism. Core validates only the target:
it must be `FREE`, have no recovery requirement or running operation, and an
unfinished workflow may be selected when the target is between commands.
Switching away from a busy current project and selecting a project while other
projects have active workflows are allowed. Creating a selected project does
not wait for other projects' workflows; `select: false` still leaves the viewer
selection unchanged. Separate chat sessions must pass explicit `project_id`
values to project operations. The per-project lease remains the authority that
prevents two sessions from editing the same project at once.

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
read its viewport scene, and execute one Command System command. Each command
acquires its own fresh agent lease, checks the most recently inspected revision,
maintains a heartbeat only during the command, and releases the lease through
validated agent stop in a `finally` block. Provider waits and read-only tools
do not hold a lease; explicit inspections refresh the expected revision.
A competing editor or changed revision produces a structured conflict result
without releasing someone else's lease. Provider tool rounds and total calls
are bounded. Unexpected command/cleanup failures preserve the original error
and attempt validated lease cleanup; provider-only failures have no lease to clean.

The runtime is an in-process Python API exported as `cadyx.AgentRuntime`; it is
not a provider integration, HTTP endpoint or background service. Projects are
created through the existing Core or Agent API before a runtime run. See the
[Agent Runtime module](../../cadyx/agent_runtime.py) and
[focused tests](../../tests/test_agent_runtime.py).

## Self-Evolving Capability Engine

The provider-neutral Agent Runtime and local MCP surface now detect an unknown
Command System command and report `CAPABILITY_MISSING`. Agents can inspect the
workspace-wide persistent registry, propose a capability, run it through an
isolated `Document` and the active geometry kernel, register a passing result,
execute it in the requested model, and restore the previous registry
checkpoint. The Agent API surfaces these operations under
`/api/v1/agent/projects/{id}/capabilities`; command contracts and registered
capabilities are returned by `GET /api/v1/agent/capabilities`.

The registry lives at workspace-root `.cadyx-capabilities.sqlite3`. A
capability stores a stable id, title/description, MAJOR.MINOR.PATCH version,
closed input/output schemas, command-result output bindings, dependencies,
declarative Command System steps, test input/evidence, the current command
contract hash, and its MCP invocation name. SQLite checkpoints are captured on
replacement and `cadyx_rollback_capability` restores the previous version.
Only compatible `ready` entries are exposed for reuse. Each live command in a
capability obtains and releases its own revision-protected agent lease, so
completed steps remain saved and visible if a later step fails.

The default model-agent instructions first try a tested composition, then expose
a source-development path when a missing algorithm needs code. The agent can read
only approved `cadyx/*.py`, `tests/test_*.py`, and Markdown files. A proposal is
revision-bound by each file's SHA-256, limited to 12 files, 256 KiB per file,
2 MiB total, and eight existing `tests.test_*` modules. It cannot add
dependencies or modify runtime/native binaries, project data, or configuration.

Candidate files and selected tests are copied into a disposable tree. Windows
`CreateProcessW` with `PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES` runs the
isolated Python interpreter in a uniquely named AppContainer with no network
capabilities. CADYX grants that package SID read-only ACLs on candidate/runtime
directories and uses its AppContainer profile `Temp` directory as the sole
writeable location, then removes the temporary ACL grants and profile after the
process exits. The process inherits only a write-only diagnostics file handle
through an explicit handle list, has a one-process Job Object, a 180-second
wall timeout, and a 2 GiB process-memory limit. The environment is allow-listed
to standard Windows runtime variables. Python temporary directories use
inherited AppContainer profile ACLs so nested test scratch remains writable
without granting access to candidate files. If AppContainer creation,
ACL setup, process limits, or cleanup fails, the operation fails closed and
reports `SOURCE_SANDBOX_UNAVAILABLE`; it never runs the candidate normally.
The Windows security regression verifies scratch writes work while a host
sentinel cannot be read or changed and outbound networking fails.

Only after at least one selected test runs successfully with no failures or
errors does CADYX write a durable checkpoint in `.cadyx-source-extensions.sqlite3`
and atomically apply the exact base-hash-checked patch. Partial application is
restored on error. A rollback restores only when every current file still matches
the applied hash; human changes yield `SOURCE_REVISION_CONFLICT`. The Agent API
exposes read, list, develop, and rollback routes under
`/api/v1/agent/projects/{id}/source-extensions` and
`/api/v1/agent/source-files/read`; the Agent Runtime and local MCP expose matching
tools. A successful HTTP source integration schedules the managed host restart
after returning its result. The saved model remains in the project and can be
inspected after restart. This is a bounded trusted-source update path, not an
arbitrary process/shell tool; source changes that need a new external dependency
or native binary are out of scope.

Capability lifecycle activities use the existing `agent.activity` SSE stream.
The browser receives the capability and source-development phase changes through
the existing `agent.activity` SSE stream, including the function title, test
result, and integration outcome. Current source-extension versions and lifecycle
status persist across host restart.
See [persistent capability tests](../../tests/test_self_evolving.py),
[Agent Runtime lifecycle tests](../../tests/test_agent_runtime.py), and
[Agent API tests](../../tests/test_web_api.py).

### Internal involute gear profile

`add_sketch_internal_involute_gear` writes the closed internal-tooth cavity
boundary as an editable closed periodic XY sketch spline through generated
points, with the module, tooth count, pressure angle, center, rotation and
backlash returned in `gear_parameters`. External and internal gear extrusion use
native periodic OCCT B-spline boundary edges interpolating those same points.
The sketch stores one closed spline and its source points, not a polygon chain;
parameter edits regenerate the spline, and legacy line-based gear sketches
convert to the spline form on their next parameter rebuild.
The command requires an existing outer circle and no other profiles in the same
sketch, so `extrude_sketch` can preserve the ring as one outer contour plus one
inner tooth contour. It uses the standard unshifted internal radii
`pitch = module * teeth / 2`, `tip = pitch - module`, and
`root = pitch + 1.25 * module`; the generated control points sample the
mathematical involute flank and circular tip/root segments. The OCCT edge is a
periodic interpolating B-spline through those points, not an exact analytic
involute curve or a certified tolerance fit. The same undercut-count check as
the external generator applies. Extrusion produces a validated editable OCCT
B-rep with a periodic B-spline cavity edge.

### Planetary gear kinematics and viewport motion

`POST /api/v1/agent/projects/{id}/validations/planetary-kinematics` reads
generated external/internal gear profile metadata at an exact saved revision.
It checks matching module/pressure angle, equal planet tooth counts, sun/planet
and ring/planet pitch center distances, equal-spacing tooth indexing, and the
saved angular phases at both meshes. The ideal rigid-body speed solver uses
`(Ns + Nr) * planet_spacing / 360 = integer` for planet indexing and
`wc = (Ns*ws + Nr*wr) / (Ns + Nr)` for carrier speed. It reports planet spin,
pitch center distances, phase residuals, and whether planet spacing is balanced.
This is an ideal kinematic constraint check; it does not solve elastic contact,
backlash under load, force balance or manufacturing tolerances.

The optional `animate_duration_ms` sends `viewport.body-motion` to the selected
browser viewer. It temporarily rotates/orbits the existing saved B-rep bodies
according to the solver tracks, then restores their saved pose when the bounded
animation ends. It does not change document geometry or revision and is not a
persistent assembly-joint record. Use `carrier_body_id` to animate the saved
carrier with its computed speed. The operation contract and local MCP tool are
generated from `cadyx/api_contracts.py`.

## Commands and geometry

Polygonal multi-contour extrusions add each hole contour as an inner wire to the
same OCCT face before one prism operation. Curved or analytic-segment contours
retain the existing validated sequential Boolean path. Both paths persist the
editable source contours and rebuild from them.

The shared Command System supports box and parameter edits, workspace and project-file PLY scan import and parameter edits, persistent staged point-only PLY import, direct single-solid STEP import, XY/XZ/YZ/custom/face-attached sketches and constraints, profile/path replacement, and linked solid features. Point-only imports stay as persistent point clouds with no faces; they cannot be used as a triangle mesh for scan-section extraction. Profile extrusion/revolution, circular-section path sweeps, connected Boolean fuses, and topology-selected edge fillets all create editable OCCT B-reps. STEP import stores the transferred OCCT B-rep with the source hash and geometry metrics; invalid source shapes remain marked invalid if OCCT cannot repair them within the documented tolerance. Feature descriptors retain their source sketches or bodies and parameters; supported upstream edits rebuild dependent features in document order inside the same atomic command. Fusion accepts intersecting B-rep solids, including swept pipes and prior fuse/fillet results, and must produce one connected solid. Fillet selection uses current unique B-rep edge indices exposed by the Agent topology endpoint; indices are traversal results, not persistent names. Scan commands resolve direct workspace or project PLY files through `ProjectWorkspace`, run inside the same Core lease and atomic-save path, and never write to the source. Each processed mesh is published as an atomic, content-addressed binary PLY beside its source; matching files are reused, and conflicts are never overwritten. The `reconstruct_scan` feature stores its source location and hash, output-file metadata, and processing parameters; its body's `cadyx-mesh-v1` geometry stores bounded compressed float32 vertices and uint32 faces in `project.json`. The Core retains the latest 128 in-memory activity snapshots per project and publishes updates as `agent.activity` SSE. Snapshots include Agent Runtime/API inspections, render requests, commands, solid features, camera orbits, and native scan phases; the browser shows the current action/task and a newest-first timestamped list of completed/failed action snapshots, without exposing the full journal. The `GeometryKernel` protocol isolates CADYX Core from a specific kernel. `OCCTKernelAdapter` creates and validates boxes, profile extrusions/revolutions, swept pipes, fused solids, filleted solids, and imported STEP solids, and validates legacy `primitive-v1` descriptors. It also validates and tessellates portable triangle meshes without turning them into B-reps. `PrimitiveKernelAdapter` remains available for demos and does not create B-rep geometry. See the [Document Model v7 contract](document-model-v7.md#sketch-driven-solid-features) for exact commands and limits.

### Geometry descriptor ownership

`cadyx/kernel.py` remains the public `GeometryKernel` boundary and OCCT adapter;
`OCPKernelAdapter` and `PrimitiveKernelAdapter` remain import-compatible aliases.
Pure numeric/profile normalization and loft matching live in `kernel_profiles.py`;
the demo adapter lives in `kernel_primitive.py`.
`kernel_descriptors.py` owns canonical JSON serialization, the bounded validation
cache, existing kind-specific descriptor/native-volume checks, and discriminated
`TypedDict` contracts for boxes, profiles, lofts, Booleans, pipes, threads, imported
B-reps, fillets and triangle meshes. Mesh payload validation/decoding remains owned
by `mesh_geometry.py`.

Raw saved geometry enters as recursive JSON values. `normalize_geometry` validates
that descriptor using the same cache as `validate`, then returns an independent
JSON snapshot typed as `CheckedGeometry`; mutating it cannot alter the source or
poison cached objects. OCCT operations that previously validated input descriptors
now use this checked snapshot. Geometry constructors expose kind-specific return
types. Native shape checks remain authoritative; these annotations do not imply
that the whole adapter implementation is covered by the incremental mypy gate.
Persisted JSON, legacy `primitive-v1`/spline descriptors, validation errors and
native reconstruction algorithms remain unchanged; no migration is required.

Viewport tessellation uses a process-wide bounded LRU keyed by canonical geometry
descriptor SHA-256 and the explicit `occt-viewport-v1-linear-0.01-display-mesh-v1`
contract. The cache is capped at 128 meshes and 64 MiB, returns independent copies,
and excludes portable triangle meshes, which are decoded and validated from their
own content-addressed descriptors. A second 128-entry/64 MiB LRU caches transformed
assembly-instance vertex positions by source geometry hash and placement transform.
Scene snapshots remain revision-bound for the browser protocol; unchanged body and
assembly-instance meshes survive scene rebuilds.

### OCCT C ABI ownership

`native/kernel_abi.hpp` is the canonical OCCT C ABI declaration and export list.
`native/kernel.cpp` is its only exported facade; implementations live in the
shared, primitive/profile, topology, delivery, validation, advanced-feature and
agent-feature modules. `kernel_common.cpp` owns shape JSON serialization, error
buffer copying and the only `new[]` result allocators. Every returned allocation
is released through `cadyx_free`; the Open3D scan bridge remains separate.

The facade reports an ABI version and advertised export list through
`cadyx_kernel_abi_manifest`. Before binding any other symbol,
`cadyx/native_kernel.py` checks the ABI version, required exports and the presence
of every advertised symbol. Optional capabilities are bound only when the
manifest lists them. A bridge without a supported manifest fails at startup with
a rebuild instruction. Python signatures remain centralized in
`_NATIVE_FUNCTION_SIGNATURES`; modeling modules use typed `NativeKernel` methods
without accessing the DLL or configuring `ctypes` signatures.


`export_brep` requires `expected_revision`, validates one current single-solid B-rep, exports STEP and re-imports it to check validity, solid count, bounds and volume before publishing. Optional binary STL output is checked for watertightness and orientation, with mesh volume error and a bounded sampled surface-deviation measurement recorded in a revision-bound manifest. Export files use no-replace atomic publication in the project folder; the manifest is published last. STEP is authoritative; STL sampling does not claim exhaustive surface equivalence. See the [verified delivery contract](document-model-v7.md#verified-cad-delivery).

## HTTP authentication and LAN bind gate

The CLI host keeps `127.0.0.1` as its default bind address. Loopback mode is backward-compatible and can run without HTTP authentication. Before constructing the server, `cadyx.auth.validate_bind_auth` rejects any host that is not explicitly loopback unless a valid authentication token has been loaded. Hostnames that cannot be proven loopback are treated as non-loopback and therefore require authentication. This makes an accidental `--host 0.0.0.0` or LAN-address bind fail closed.

A token is supplied through exactly one runtime source: `CADYX_AUTH_TOKEN`, or a UTF-8 token file selected with `--auth-token-file` / `CADYX_AUTH_TOKEN_FILE`. Tokens must contain at least 32 characters and no whitespace. They are not stored in `project.json`, lease databases, command history or any portable schema, so enabling authentication needs no project-format migration. The CLI deliberately does not accept a raw token argument, avoiding routine exposure through command-line process listings.

When a token is configured, `cadyx.web` authenticates every application request before route handling. The browser UI and static assets, Web API, Agent API, workspace-file previews/downloads, viewport JSON/PNG, lease and command routes, and the SSE stream all share the same gate. `GET /api/v1/health` is the only public endpoint and returns only readiness status/API version. Rejected requests receive HTTP 401 and the connection is closed before any unread POST body can be reused as another HTTP/1.1 request.

Agent and API clients use `Authorization: Bearer <token>`. Browser navigation uses standard HTTP Basic authentication with username `cadyx` and the same token as the password; the browser then reuses the same-origin HTTP credentials for its fetches and event stream. Credential comparison uses `secrets.compare_digest`. The token is not accepted in a URL or query parameter.

This is an authentication boundary, not transport encryption. The built-in server remains plain HTTP. Non-loopback operation is therefore limited to a trusted private LAN unless a TLS-terminating reverse proxy or trusted VPN protects the transport. Direct Internet exposure of the built-in HTTP listener is outside the supported security boundary. The persistent Windows task remains loopback-only by default.

## APIs and events

Document Model v8 extended v7 with named numeric parameters and expression bindings;
current Document v17 retains point/line/circle/ellipse/spline primitives and
adds YZ, custom and face-attached sketch work planes to the earlier XY/XZ planes,
atomic builders for polylines/polygons, rectangles, and circles, checked
geometric/dimensional constraints, and linked OCCT extrusion, revolution, sweep,
fuse, fillet, and external metric-thread features. The Command System also builds
sampled involute spur-gear outlines from design parameters as editable closed
periodic XY sketch splines; both operations use the existing Agent API and create no browser
modeling controls. Their exact supported ranges and geometric approximations are
documented in [Document Model v7](document-model-v7.md#engineering-profiles).
Sketch references use stable typed
UUIDs and are validated on load and after every command. Coordinate updates can
be batched atomically; deletions cannot leave dangling references. Constraint edits use the saved sketch solver and apply geometry changes atomically.
The existing command endpoints support these objects under the same leases and
atomic save path. Documents v1–v11 migrate in memory to v12; the project
envelope stays at version 1. See
[Document Model v7](document-model-v7.md) for the base object graph and
[Document Model v8](document-model-v8.md) for named parameters and atomic rebuilds,
and [work planes v12](../../docs/sketch-workplanes.md) for current sketch placement. Sketch points, lines, circles, full ellipses,
elliptical arcs and interpolating splines now render in the
shared viewport. Agents create projects and edit models through the Agent API;
human API clients may write through the Web API, while the browser provides no
project or document mutation controls. Agents fetch the browser's exact main
viewport PNG from the Agent API when a current frame is available; standalone
API requests retain a server-rendered fallback. Browser frames are revision
tagged and held in memory only.

The HTTP API uses JSON under `/api/v1`. The Agent API is under `/api/v1/agent`; it shares Core operations and requires an agent lease for commands. Agents create projects through `/api/v1/agent/projects` and edit supported bodies and sketches through `/api/v1/agent/projects/{id}/commands`, using the existing Command System and agent lease. Human API clients can use the Web API command route with a human lease. `GET /api/v1/agent/projects/{id}/topology?body_id=...` reports current B-rep edges for fillet selection. `POST /api/v1/agent/projects/{id}/viewport/camera` publishes a transient animated orbit to the selected browser viewport; it does not edit the document or require a lease. The browser posts the rendered main viewport to `POST /api/v1/agent/projects/{id}/viewport/snapshot` with its document revision; matching `GET .../viewport.png` responses return those exact PNG bytes, marked by `X-CADYX-Viewport-Source: browser`. If no current browser image exists, including when the stored image belongs to an older revision, the API returns its server-rendered fallback, marked `server-fallback`. Snapshot publication is read-only, requires no edit lease, and retains images in memory only. `GET /api/v1/projects/{id}/dashboard` and its Agent API equivalent return the persisted workflow report and task queue; `POST /api/v1/agent/projects/{id}/dashboard` replaces the persisted report without acquiring a document edit lease. Reports emit `agent.dashboard` and global work-lock state over SSE. `GET /api/v1/updates` reports whether any workflow, phase, or task remains unfinished. The browser disables mutations only for the project whose own lease, running operation, or unfinished workflow makes it busy; other projects remain available, and workflow reports persist across host restarts. Its single progress card shows the current or last actual action, the current or most recently finished task, and a newest-first history of completed/failed actions with local completion times; future plan items remain hidden. Both APIs expose `GET .../viewport` for a JSON scene; PNG image retrieval is read-only and requires no edit lease. Both `GET /api/v1/projects` and `GET /api/v1/agent/projects` return persisted CADYX projects in `projects` and immediate project-directory names in `folders`. `GET /api/v1/workspace/folders/{folder}/files` lists direct regular files; the corresponding `/files/{name}` route serves an individual file. The browser lets users select a CAD project through Projects (07) using the existing Agent API selection route, and the agent remains responsible for workspace-folder selection. The browser reflects selection through `workspace.selection.changed` and keeps a shared workspace shell for both resource types. Its left navigation provides Overview, Geometry, Analysis, Manufacturing, Activity, Files (06), and Projects (07). Projects lists saved CADYX projects and allows selection only when the current resource is confirmed free and the target project lock is FREE with no running operation. For a selected CAD project, the browser locks that project while its own lease is not FREE, its operation is running, its workflow or queue is unfinished, or availability is unknown or stale; activity in other projects does not block it. The progress block remains readable and shows only actual current or last work, not future plan items. The browser reads `/api/v1/projects` snapshots and viewport scenes but does not create projects or write model documents. A project's model viewport, object tree, comparison state, manufacturing-data empty state, orthographic previews, and agent dashboard remain inside the shared shell. Model and property docks start collapsed. Project updates are broadcast in-process over server-sent events at `/api/v1/events`. SSE `id` values are opaque `server-epoch:event-number` cursors. `Last-Event-ID` resumes from an immutable in-memory ring bounded by 256 events and 1 MiB of serialized payload, with at most 128 pending events per subscriber. Replay and subscriber enrollment share the broker publication lock, preserving order across concurrent publishes. Project-filtered streams receive only their project events, excluding other projects and global events. Invalid/stale/restarted cursors, unbufferable events or a replay larger than the subscriber queue emit `stream.gap` (reason and latest event ID) instead of silently returning incomplete history. Clients must immediately resynchronize authoritative saved project/lock/viewport state after a gap; the browser also refreshes on reconnect. Five-second `/updates` polling still handles cross-process changes and live subscriber overflow. Replay is neither durable across restarts nor a replacement for the authoritative project API. On a committed command, `document.changed` announces the new revision and `command.completed` carries a bounded **v2 receipt** (`schema_version: 2`, `operation_id`, `revision`, `command`, `outcome: "completed"`, optional `feature_id`). This completion event never embeds the command input or its potentially large geometry-bearing result. `command.started` retains the same `operation_id` for correlation; full immutable command results and history remain available in the command response and the project read APIs. Agents and viewers must not treat a receipt as the full history entry.

### Agent-controlled workspace selection

`GET /api/v1/agent/workspace/selection` returns the process-local active project or
workspace folder. Agents select an existing CAD project with
`POST /api/v1/agent/workspace/selection` and `{"type":"project","project_id":"..."}`;
they select a workspace folder with `{"type":"folder","folder_name":"..."}`.
Selection is a process-global viewer hint, so concurrent chat sessions may
change it; it must never be used to infer which project a command targets.
Every project operation uses its explicit `project_id`. The Core validates only
the target project and publishes `workspace.selection.changed` over SSE. The
target must be free, recovered and have no running operation; its own unfinished
workflow is allowed between commands. A busy current selection or other active
project workflows do not block switching. Creating a selected project is also
allowed while other project workflows are active; `select: false` creates a
project without changing the viewer selection. MCP delegates target validation
to Core through the Agent API. The browser's Projects (07) view lists saved projects and opens each
project in a separate tab; this does not change the process-local Agent API
selection. Workspace-folder selection remains Agent API-only.
The browser loads a model from a readable `/p/<project-slug>` address. Slugs
derive from the display name; duplicate or colliding slugs receive an ID suffix.
Existing `?project_id=<UUID>` links remain supported and canonicalize to the
readable path after the project list loads. Renaming a project changes its
canonical slug. An
unqualified root address opens Projects without restoring the current host
selection; `?view=projects` has the same behavior. A project or folder is shown
only when its address explicitly contains a project slug, `project_id`, or
`folder`. Explicit project, `folder`, and `view=projects` routes remain pinned across
SSE workspace-selection events; only an explicit navigation action or a newly
created project can change the address.
Opening a project from Projects creates a tab at `/p/<project-slug>`;
folder selection uses `?folder=<folder name>`. A resource address pins the tab's
displayed project or folder, so separate tabs can keep different resources
visible. Project cards open their URL in a new tab even when the project is
locked; the new tab can observe the last committed model while its controls
remain disabled until the project is free. Project data and leases remain shared
by project ID; the address isolates the viewer selection, not the edit lock. A
tab opened at `?view=projects` remains available for navigation while agent work
is active. A tab pinned to a project continues to disable controls while that
project's lock or activity is not free.

The Projects view also supports logical project groups. Group definitions and
project-to-group assignments are stored in `.cadyx-project-groups.sqlite3` in
the sibling `.<workspace-name>-state/` directory, separate from project documents and physical workspace
folders. `GET /api/v1/projects` includes `groups` and each entry's `group_id`;
`POST /api/v1/project-groups` creates a group, and
`POST /api/v1/projects/{id}/group` assigns a group or accepts `group_id: null`
to return a project to the ungrouped section. Assignment requires the project
to be free with no running operation. Group creation and assignment do not
move or rewrite project directories or documents.

Project deletion is available through authenticated `DELETE /api/v1/projects/{id}`.
The Projects view asks for confirmation that the model, history and all files
in that project will be permanently removed. Core refuses deletion while the
project is locked, requires lease recovery, or has a running operation. It
fences new lease requests before detaching the project directory, clears its
group assignment and active Core selection, then publishes `project.deleted`.
If a viewer still holds files open, the directory remains in a hidden deletion
tombstone and workspace listing retries cleanup; the API reports
`cleanup_pending: true` in that case.

The default host is `127.0.0.1`. Non-loopback binds require the host authentication gate described above; the built-in server still relies on a trusted LAN or external TLS/VPN for transport confidentiality.

### Explicit built-in extension composition

`cadyx/extensions.py` declares the ordered built-ins: advanced features, agent
authoring, manufacturing features, semantic selection, selector-contract
refinements, and scan reverse engineering. `capabilities.py` constructs one
`COMMAND_REGISTRY` from `BASE_COMMAND_CAPABILITIES` plus these definitions and
publishes its final contract snapshot once. Composition copies input metadata
and rejects duplicate command/extension names, missing handlers, and refinements
that change registered command names. Rebuilding a registry is deterministic
and does not mutate the base or a previous registry.

Each `CommandSystem` binds the registered handlers and middleware to its base
executor during initialization. Semantic selection wraps dispatch; scan alignment
wraps that pipeline and reapplies the saved transform after `set_scan_parameter`.
The last registered middleware is outermost. Progress and project context pass
through the same pipeline, inside the existing Core lease/revision/atomic-save
boundary. Base handlers are supplied by `base_command_handlers()` and must
exactly match the base capability names before extensions are composed. Production
dispatch has a registered handler for every discovered command.

Extension modules define ordinary functions and metadata, without `install()` or
method/global-contract reassignment. `OCCTKernelAdapter.topology` explicitly calls
analytic topology enrichment, and the shared renderer explicitly filters consumed
source bodies while preserving document metadata. Face selectors resolve against
faces and do not capture edge references. `tests/test_extensions.py` covers
composition, collisions, middleware order, isolated handlers and import order;
native authoring and rendering tests cover the adapter/scene integration.

### Command families and atomic execution

`commands.py` retains the `CommandSystem` constructor, shared execution context,
and public request/type validation. Its implementation methods live in focused
`command_*` modules: sketch editing/common helpers, sketch profiles, sketch curves,
body features, history/deletion, parameters, dependent-feature rebuilding, scan
processing, and STEP import/verified delivery. The classes are composed by ordinary
inheritance so existing extension calls to shared helper methods continue to use
the same kernel, resolvers, artifact writers and staged document. Helpers have
unique names across families; no class methods are assigned after definition.

Each family declares its named `COMMAND_HANDLERS`; `base_commands.py` combines
these tables with duplicate detection. `capabilities.py` pairs them with the
canonical metadata through the same registry used for extensions. Missing,
extra or non-callable base bindings fail during registry construction. Alias
handlers retain their original parameter mapping, feature operation names and
progress phases. Command lookup does not depend on an ordered `if` chain.

Handlers neither acquire leases nor save project state. Core continues to copy
and validate the document, enforce lease/revision/stop fences, record history and
commit once through the existing atomic workspace path. An exception after a
partial staged edit leaves the saved document, revision and history unchanged.
`tests/test_command_families.py` covers isolated family behavior, registration
parity and that atomic-failure invariant without loading native libraries.

### Agent API discovery and local Codex MCP adapter

The MCP adapter's default `cadyx_execute_command` uses the single-request
`POST /api/v1/agent/projects/{id}/commands/execute` route. The Core holds the
project guard while it acquires the agent lease, checks the exact revision,
executes one command, creates a bounded receipt, and releases the lease. A
resumed lease may be passed to this route and is checked against the MCP owner.
The receipt includes revision and object counts plus the compact command result;
it does not serialize saved objects or full command history. The legacy
lease-explicit `/commands` route remains available to other clients. MCP's
`cadyx_inspect_project` reads `inspection?section=summary`; use
`cadyx_inspect_compact` for bounded details and `cadyx_inspect_project_full`
only when full geometry or history is needed.
The built-in `AgentRuntime` also starts with this compact summary; its
`inspect_project` tool exposes bounded pages and requires `full=true` for the
explicit large-payload path.

`GET /api/v1/agent/capabilities` is a read-only machine-readable inventory of Agent API routes, supported Command System names, required/optional parameter names, optional JSON Schema hints, known error codes and documented limitations. Parameter metadata is an operator guide; Core and command validators plus the linked model contracts remain authoritative. `CommandSystem.execute` rejects command types absent from the discovery registry, so a new operation is not exposed until its contract is registered.

Typed Agent API errors preserve the top-level `error` and `message` fields and add a `diagnostics` object containing the error category and failed precondition, with command type, saved revision, feature/body/sketch/entity IDs, measured values, and a descriptive `correction_hint` when available. Measurements are `null` when the failing operation produced no measurement; CADYX does not invent them or choose a repair. Kernel, topology-selection, sketch-constraint, scan, export, resource-limit and unsupported-operation failures have stable codes listed by capability discovery. The MCP bridge reports read-side transport failures as `TRANSIENT_TRANSPORT` and ambiguous mutations as `OPERATION_OUTCOME_UNCERTAIN`; inspect revision/history before deciding whether to retry. Unexpected HTTP and MCP failures use a generic `INTERNAL_ERROR` response; public errors redact bearer credentials and absolute host paths.

Command value hints live on the corresponding `COMMAND_CAPABILITIES` entry. MCP command branches derive their fields and schemas from the final registry, including extension entries; fields without a confirmed value schema remain permissive. The shared validation-operation registry in `cadyx/api_contracts.py` supplies discovery's `operations` and validation route descriptions, MCP tool schemas/request projection/defaults, and HTTP required/allowed-field checks for dimensions, clearance, preservation and thickness. `operations` is additive discovery metadata, not a second execution API. Core retains value, revision and geometry validation, and HTTP retains its existing error messages and progress wrappers. Source-contract tests verify registry parity, request projection and documentation paths/current document version; Web API tests verify the field gates and Core delegation.

`cadyx/mcp_server.py` implements a local stdio MCP adapter using only Python's standard library. It calls the protected CADYX HTTP Agent API; it does not import Core or access project files directly. Tools cover workspace selection, project/file/model inspection, capabilities, current B-rep topology, scene and PNG image retrieval, camera, dashboard, commands, safe project-file copying/deletion, stop/resume, and validated expired-lease recovery. `cadyx_get_project_thumbnail_batch` returns server-rendered 480×270 PNG thumbnails for up to eight project IDs in one MCP result, with each image labeled by project ID, document revision, and source. This lets the calling AI agent compare a reference image it read through the existing project/folder file tools against candidate CAD projects; ranking and model-generation decisions remain with that agent. It has no shell, generic HTTP, or model-file write tool. Each model command requires the revision observed by the caller; the single-request execute route acquires and releases the lease inside Core. Lock and revision preflights read compact inspection summaries, not full geometry/history snapshots. A dropped command response is never replayed automatically. A concurrent stop tool sets Core's durable stop fence, waits for an admitted command to finish, and returns Core's validated saved state. MCP workspace selection is a process-global viewer hint and may be changed by another chat session. Core validates only the selected target; active workflows in other projects do not block selection. Project operation tools always use their explicit `project_id`, and each project-specific durable lease preserves same-project exclusivity. `/api/v1/updates` lists active workflow project IDs for project-specific UI locking.
The MCP command response intentionally omits the saved document's full object array and large geometry payloads; it returns the revision, object counts, and compact command/history results. The built-in AgentRuntime follows the same compact-summary default and exposes full document inspection explicitly.

The local MCP adapter exposes `cadyx_start_scan_360` and `cadyx_get_scan_360_status` over the existing Agent API routes. Capture starts through Core's direct-UVC controller; status includes the latest captured point-cloud file and the project viewer polls the same route to refresh its live scan overlay.

The MCP HTTP bridge reads responses in bounded 64 KiB chunks and rejects oversized `Content-Length` before reading the body; missing/incorrect length does not bypass the streaming cap. The wire-body budgets are **16 MiB for normal JSON**, **64 MiB for full project/scene/topology/command JSON**, **8 MiB for paged compact inspection JSON**, **24 MiB for viewport PNG**, **8 MiB per thumbnail**, **64 MiB for project/folder files**, and **64 KiB for HTTP error bodies**. These limits apply before decoding or base64 encoding, and do not imply that downstream JSON decoding or MCP serialization consumes no additional memory. Read-side excess returns `RESPONSE_TOO_LARGE` with a byte limit and guidance: page `cadyx_inspect_compact` via `section`/`limit`/`offset`, use a thumbnail in place of an oversized viewport PNG, or use the supported staged STEP/PLY import command rather than transferring a large source through MCP text. Oversized HTTP error bodies return `HTTP_ERROR_BODY_TOO_LARGE` with the original HTTP status (including the definitive/ambiguous mutation distinction). Oversized successful **mutation** responses remain outcome-uncertain, are fenced and inspected, and are never retried automatically. This is a transport memory guard, not server-side pagination or proof of unchanged geometry.

The repository entry point is [`AGENTS.md`](../../AGENTS.md), which points to the repo-local skill at `.agents/skills/cadyx-operator/SKILL.md`. `.codex/config.toml` registers the stdio process at project scope after this trusted checkout is opened in Codex; see [README](../../docs/operations.md#codex-operator-integration). The client accepts a token from process environment or `CADYX_API_TOKEN_FILE`; token contents are not passed as process arguments or logged. No MCP package or provider SDK is required.

## Foundation roadmap

The development sequence, including authenticated LAN access and the prerequisites for Scan-to-CAD, is maintained in the [CADYX project roadmap](../roadmap.md). Inspect the current implementation before treating a milestone as unfinished or complete.


### Scan buffer and PLY preview performance

Packed scan geometry compression and decoding use buffer views to avoid intermediate full-payload byte copies; decoded arrays remain independently owned. Reusing a generated PLY hashes the existing file incrementally rather than loading it into memory. Browser PLY previews resolve coordinate readers once, skip unsampled polygon and unused list payloads by byte length, and verify that skipped payloads fit the input. Vertex data is checked before allocating its preview array. Sampling and triangulation remain unchanged; this preview is not a full validator of unsampled face indices. Regression coverage lives in `tests/test_ply_preview.cjs` and the portable scan tests.

### Revision-bound geometric topology queries

External agents may use POST `/api/v1/agent/projects/{id}/topology/query`
(MCP `cadyx_query_topology`) with a `body_id`, exact `expected_revision`
and typed `query`:

```json
{"body_id":"body:...","expected_revision":4,"query":{
  "entity":"face","surface":"cylinder",
  "radius":{"value":2.5,"tolerance":0.01},
  "axis":{"origin":[0,0,0],"direction":[0,0,1],"tolerance":0.01}
}}
```

`entity` must be `face` or `edge`. Supported filters include `surface`,
`curve`, `radius`, `diameter`, `area`, `length`, `orientation`,
`normal`, `position`, `center_in_box` and `axis`. Scalar filters use
`value` and absolute `tolerance` in model units (area uses square units).
Direction filters use `vector` and optional `mode` (`aligned`, `parallel`,
`perpendicular`) and `angle_tolerance_degrees` (default 1 degree).
`position` uses a `point` and `tolerance` to match the entity's centroid;
`center_in_box` tests only the centroid and does not certify that the
whole face/edge is enclosed. `axis` matches analytic axis position and
orientation using `origin`, `direction` and distance `tolerance`.
Missing analytic descriptors do not match. `max_results` limits the returned
prefix (default 100, maximum 500), not the counted matches.

The read-only result includes saved document `revision`, deterministic
index-ordered `candidates`, `match_count`, `truncated` and `status`
(`no_match`, `unique`, `ambiguous`). Candidate confidence is descriptive
(`descriptor_within_tolerance`), not a measured probability.
Agents must explicitly choose an index or a uniquely resolving semantic key;
fingerprints are not permanent topology IDs. For face/edge mutation commands,
pass `topology_revision` equal to the inspected saved revision along with
the usual command `expected_revision` and a fresh edit lease. A stale topology
revision returns `TOPOLOGY_CHANGED`; a duplicated semantic fingerprint
returns `TOPOLOGY_AMBIGUOUS`, without applying the command.
