# CADYX architectural decisions

## Host authentication and LAN bind gate

- **Decision:** keep HTTP authentication as host runtime configuration and refuse any non-loopback bind unless a reusable authentication token is configured. Protect the browser UI, Web API, Agent API, workspace file routes, viewport routes and SSE with the same token. Agents use Bearer authentication; browser navigation uses HTTP Basic with fixed username `cadyx` and the token as password. Keep only `GET /api/v1/health` public with minimal readiness data. **Reason:** LAN access must fail closed before CADYX exposes project data or mutation routes, while the existing browser can authenticate without adding a separate credential UI or a second application session mechanism. **Constraint:** the built-in server remains plain HTTP; authenticated non-loopback mode is for trusted private LANs only. Use TLS termination or a trusted VPN before crossing an untrusted network. The token is runtime state and does not change portable project/document schemas. The persistent Windows task remains loopback-only by default. **Source:** `cadyx/auth.py`, `cadyx/web.py`, `cadyx/__main__.py`, [authentication contract](docs/architecture.md#http-authentication-and-lan-bind-gate), and [run instructions](../docs/operations.md#authentication-and-lan-access).
- **Status:** active.

## Safe agent stop and resume

- **Decision:** persist per-token stop fences in a separate SQLite runtime sidecar, independently of the full-command lease transaction. Validate saved document/geometry under the existing project guard before freeing an agent lease. Resume under that guard with a revision comparison and a new token; require expected revisions for its commands. **Reason:** a stop request must block queued commands while an atomic command still holds the lease database; revisions prevent replaying a committed command after a lost response and detect intervening human edits. **Constraint:** current cooperating hosts only; legacy lease acquisition remains compatible and does not require revision arguments. This mechanism adds no portable schema change or provider-specific runtime dependency. **Source:** `cadyx/core.py`, `cadyx/lease.py`, [API contract](docs/architecture.md#safe-agent-stop-and-resume).
- **Status:** active.

## Project availability and lifecycle policy

- **Decision:** make `CadyxCore` the authority for read, write, file-management, selection and destructive-operation availability. Read paths use nonblocking snapshots of the last atomic save; writes keep the existing lease/revision boundary; file and destructive actions require `FREE`, no recovery and no running operation. Selection checks current/target project state and active workflows in Core, including the sole-active-project restore exception; MCP delegates to the same Agent API instead of preflighting separately. **Reason:** multiple clients must apply the same fail-closed project lifecycle rules, and read-only inspection must remain available during a write. **Constraint:** active workflow status can be published without a lease, so selection must also consult the persisted dashboard snapshot; `select: false` project creation does not change selection. **Source:** `cadyx/core.py`, [availability contract](docs/architecture.md#project-availability-policies), [workspace selection](docs/architecture.md#agent-controlled-workspace-selection).
- **Status:** active.

## Provider-neutral Agent Runtime

- **Decision:** keep provider adapters behind the `AgentProvider` completion protocol; let the in-process Agent Runtime own bounded tool orchestration and use the existing Core lease, revision and Command System paths. **Reason:** CADYX can support different providers without importing a provider SDK into Core or creating another document mutation path. **Constraint:** one existing project per run; inspection, viewport and command tools only; no bundled provider adapter or HTTP runtime endpoint. **Source:** `cadyx/agent_runtime.py`, [runtime contract](docs/architecture.md#agent-runtime).
- **Status:** active.

## Foundation pass — 2026-10-14

- **Decision:** use Python standard-library modules for Core, local HTTP API, and SSE; use browser-native HTML/CSS/JavaScript for the first UI. Python 3.12+ was the initial baseline; the current Windows runtime version is maintained in `scripts/runtime-versions.json`. **Reason:** this gives a working local vertical slice without provider coupling. **Constraint:** the original single-process restriction is superseded by the multi-process lease decision below. **Source:** `docs/architecture.md` and `scripts/runtime-versions.json`.
- **Decision:** keep all mutations in the Core Command System and require an opaque edit lease; a stale lease blocks edits until saved state is reloaded and validated. **Reason:** AI and human must not mutate the same document concurrently, and timeout alone cannot prove safe recovery. **Source:** `cadyx/core.py`, `cadyx/lease.py`.
- **Decision:** the Web API accepts only human-owned leases for commands and the Agent API accepts only agent-owned leases; both delegate to the same command implementation. **Reason:** preserve one mutation path while enforcing the editor/observer boundary at each API surface. **Source:** `cadyx/web.py`, `docs/architecture.md`.
- **Decision:** store project metadata, current document, revision, and successful command history in one versioned `project.json`, replaced atomically per command; keep runtime leases out of that file. **Reason:** a command and its audit entry should become durable together, while lease state is host runtime state. **Source:** `docs/project-format-v1.md`, `cadyx/workspace.py`.
- **Decision:** isolate geometry behind `GeometryKernel`; the initial prototype used a validated primitive descriptor adapter. **Source:** `cadyx/kernel.py`.
- **Status:** the initial primitive-only implementation is superseded by the real geometry-kernel decision below.

## Real geometry kernel

- **Decision:** use current stable native OCCT through the small project-built C ABI bridge and Python `ctypes`, rather than a Python-version-bound third-party extension. Keep `GeometryKernel`, JSON descriptors, `occt-v1` and `primitive-v1` compatibility unchanged. **Reason:** the user requires absolute newest stable versions; latest cadquery-ocp excludes Python 3.15 and pins older VTK. The browser renderer requires no VTK. **Constraint:** verified setup is Windows x64 with CMake/MSVC and a modeling-only OCCT SDK; downloads/build artifacts stay local. **Source:** `native/`, `cadyx/native_kernel.py`, [setup](../README.md).
- **Status:** active; supersedes the previous cadquery-ocp/VTK runtime decision.

## Native OCCT C ABI and implementation modules

- **Decision:** keep the OCCT exports and their versioned symbol manifest in one C ABI facade declared by `native/kernel_abi.hpp`; place native implementation code in focused modules and centralize result-buffer allocation/error copying in `kernel_common.cpp`. Verify the ABI version and advertised symbols before Python binds any bridge functions. **Reason:** module decomposition must not leave `ctypes` signatures, optional capabilities, or allocator ownership implicit across translation units. **Constraint:** unsupported/missing manifests fail at native startup and require rebuilding the runtime; the scan bridge has its own ABI and remains separate. **Source:** `native/kernel_abi.hpp`, `native/kernel.cpp`, `native/kernel_common.cpp`, `cadyx/native_kernel.py`, and [OCCT C ABI ownership](docs/architecture.md#occt-c-abi-ownership).
- **Status:** active.

## Shared rendering and viewport

- **Decision (updated by assembly editing):** Core produces a versioned read-only JSON scene under the existing project guard, with kernel-derived body triangles, stable object ids and sampled XY/XZ sketch primitives. Browser and Agent API expose identical scenes. The human assembly editor picks rendered occurrences and submits changes through human leases and the existing command API; the browser renderer still does not own CAD state or call the geometry kernel. **Reason:** provide spatial assembly manipulation while preserving Core ownership and revision checks. **Constraint:** the current schema supports occurrence visibility/grounding and rigid joints; geometry-face references and a general nonlinear constraint solver remain separate work. **Source:** [viewport contract](docs/viewport-api.md), `cadyx/rendering.py`, `web/viewport.js`, `web/app.js`, `cadyx/command_assembly.py`.
- **Status:** active.

## Sketch-driven solid features and camera observation

- **Decision:** build closed sketch profiles as validated OCCT extrusion or revolution B-reps through the shared Command System and preserve source references and parameters. Add path-based circular-section sweeps, connected Boolean fusion of intersecting B-reps, and rolling-edge fillets selected from inspected current topology; retain dependencies so supported edits rebuild downstream features atomically. Expose camera orbit as a transient Agent API/SSE event that animates only the open viewport and never changes the saved document. **Reason:** agents need editable solids and a visible modeling sequence without a second mutation path. **Constraint:** profile extrusion/revolution use one closed contour on the documented planes; sweep paths are open XY/XZ chains; fuses must yield one connected solid; edge indices are transient and must be re-inspected after upstream edits. Imported triangle meshes are not B-rep inputs. Camera state stays local to the browser. **Source:** `cadyx/commands.py`, `cadyx/native_kernel.py`, [Document Model v4](docs/document-model-v4.md), and [viewport contract](docs/viewport-api.md).
- **Status:** active.

## Automatic browser synchronization

- **Decision:** keep SSE for immediate same-process changes and poll a lightweight `/api/v1/updates` endpoint every five seconds for workspace/project/folder tokens, in-memory activity, persisted dashboard/work-lock revisions, and content-hashed UI assets. Refresh API state only when its token changes; automatically reload the page when served frontend assets change. **Reason:** keep the visible workspace current after missed events, external cooperating-host commits, folder-file changes, or frontend edits without repeatedly loading large documents or asking the user to refresh. **Constraint:** activity history remains process-local; dashboard reports and global work state persist atomically, and the browser fails closed until the global work revision confirms every workflow and queue is complete. **Source:** `cadyx/web.py`, `cadyx/workspace.py`, `cadyx/events.py`, `web/app.js`, [viewport contract](docs/viewport-api.md), and [architecture](docs/architecture.md).
- **Status:** active.

## Scan-to-CAD

- **Decision:** use the current stable CPU Open3D C++ SDK through a project-built C ABI bridge for PLY cleanup, point-cloud surface reconstruction, and mesh simplification. Store the editable result as bounded, compressed `cadyx-mesh-v1` geometry in the document, and atomically publish a content-addressed PLY beside its unchanged source. Publish real operation phases in Core activity snapshots and Agent API/SSE data; show only one compact action/result status in the browser. **Reason:** provide useful mesh and point-cloud reconstruction on modest hardware without binding the processing library to CADYX's Python ABI or replacing the source scan. **Constraint:** Windows x64 runtime currently pins Open3D 0.20.0, caps scan processing at four CPU threads and one hundred thousand downsampled points, and keeps live activity snapshots process-local. **Source:** `cadyx/scan.py`, `native/scan.cpp`, `cadyx/mesh_geometry.py`, [scan contract](docs/document-model-v4.md#scan-import-and-editable-triangle-meshes), [runtime setup](../scripts/setup-runtime.ps1), and [Open3D releases](https://github.com/isl-org/Open3D/releases).
- **Status:** active.

## STEP B-rep import

- **Decision:** import one-solid STEP files through the OCCT 8 `TKDESTEP` reader and store the serialized native B-rep in the document with the source filename, hash, and geometry metrics; render a tessellation only for the viewport. Preserve an invalid transferred shape when OCCT cannot repair it within the configured tolerance, mark its validity, and reject downstream operations that require a valid B-rep. **Reason:** imported CAD must retain analytic/native geometry rather than becoming a triangle-mesh approximation, and repair must not silently alter a user's reference. **Constraint:** source and serialized payloads are limited to 64 MiB, the source is a direct regular file in the project folder, and invalid imports may be displayed but are not valid Boolean/fillet inputs. `scripts/occt-runtime-lock.json` pins the additional OCCT STEP runtime closure. **Source:** `native/kernel.cpp`, `cadyx/commands.py`, `cadyx/kernel.py`, [STEP import contract](docs/document-model-v4.md#step-import-and-editable-b-reps), and [runtime setup](../scripts/setup-runtime.ps1).
- **Status:** active.

## Document Models v2 and v3

- **Decision:** represent XY/XZ sketches, points, lines, circles and constraints as document objects with stable typed UUID references. Membership has one source (`sketch_id`); constraints validate geometry with absolute tolerance 1e-7 instead of implying a solver. Batch coordinate updates use the existing atomic Core command path. **Reason:** add editable, persistent relationships with explicit invariant checks without introducing a separate mutation system. **Source:** `docs/document-model-v4.md`, `cadyx/sketches.py`, `cadyx/commands.py`.
- **Decision:** migrate document v1 through v2 to v3 in memory and save it only with the next successful command; keep project envelope v1. **Reason:** legacy bodies, features and history remain intact and read-only inspection does not rewrite user data. Older hosts reject v3; sequential migrations are centralized in `cadyx/migrations.py`. **Source:** `cadyx/models.py`, `docs/document-model-v4.md`, `tests/test_document.py`.
- **Status:** active.

## CAD workspace and API-first editing

- **Decision (updated by the assembly-editor request):** keep the desktop detail workspace centered on a dominant viewport, with left section navigation and one progress block. Agents continue to create projects and modify documents through the Agent API; humans can also select and edit assembly occurrences in the browser through the same Core command system, human lease, and expected-revision check. Read-only inspection remains available while a project is busy. The full AI plan and future queue remain hidden in the browser. **Reason:** assembly placement and joint edits need direct spatial feedback while preserving one authoritative Core transaction path. **Constraint:** the editor must fail closed on busy/unknown availability, preserve all solved mates, and never let an older browser or AI revision overwrite newer state. **Source:** `web/app.js`, `web/viewport.js`, `cadyx/command_assembly.py`, `cadyx/core_services.py`, and [assembly editor contract](document-model-v14.md).
- **Status:** active.

## Persistent Windows host

- **Decision:** run the local host as a hidden Task Scheduler task when the current Windows account signs in, bound to loopback port 8888. Launch `pythonw.exe` as the tracked task process, capture its output to local logs, and restart it after failures so it remains independent of terminal/Codex lifetime. **Reason:** registering a pre-logon startup task was denied without elevation; an interactive logon task gives the requested Codex-independent lifetime without changing machine-wide permissions. **Constraint:** the Windows account must remain signed in; local checkout and `.runtime` paths must remain in place; rerun the installer after moving the checkout or rebuilding the runtime. **Source:** `scripts/manage-host-task.ps1`, `scripts/run-host-background.py`, [host lifecycle](../docs/operations.md#keep-the-windows-host-running).
- **Status:** active.

## Project Format migrations

- **Decision:** centralize independent project/document schema versions and consecutive migration steps in `cadyx/migrations.py`. Migrate copies on load, validate through the existing domain/workspace checks, and persist through the existing leased Core command and atomic save path. Keep envelope v1 and document v3 current. **Reason:** future schema evolution needs explicit transitions without rewriting user data on reads or introducing a second mutation path. **Source:** `docs/project-format-v1.md`, `cadyx/migrations.py`, `tests/test_migrations.py`.
- **Status:** active.

## Multi-process edit leases

- **Decision:** use a per-project SQLite runtime sidecar and `BEGIN IMMEDIATE` for shared leases and the complete Core command/recovery critical section; keep `project.json` and its atomic replacement unchanged. **Reason:** standard-library OS-backed coordination releases locks on process death while retaining lease ownership for validated recovery, without blocking unrelated projects. **Constraint:** cooperating current hosts on local filesystems only; SSE remains process-local. Sidecars must not be removed while hosts are active. **Source:** `cadyx/lease.py`, `cadyx/core.py`, [lease contract](docs/architecture.md#edit-leases), `tests/test_multiprocess_leases.py`.
- **Status:** active; supersedes the foundation's process-local lease restriction.

## Engineering profiles

- **Decision:** store generated external and internal gear contours as editable closed periodic sketch splines through sampled profile points, and use periodic OCCT B-spline boundaries through the same points. Parameter edits regenerate the curve; legacy line-based gear sketches convert on their next parameter rebuild. Keep single-start external ISO metric threads as editable OCCT B-rep features. **Reason:** preserve parametric sketch history while removing polygon edges from saved gear sketches and solid boundaries. **Constraint:** the spline interpolates sampled involute points; it is not an exact analytic involute or a certified deviation fit. Profile shift, helical geometry and cutter-generated root fillets remain unsupported. Thread flanks/root rounds remain approximations; units remain document model units without a global physical-unit setting. **Source:** `cadyx/engineering.py`, `cadyx/sketches.py`, `cadyx/command_sketch_profiles.py`, `native/kernel.cpp`, and [engineering profile contract](docs/document-model-v7.md#engineering-profiles).
- **Status:** active.

## Automated source extensions

- **Decision:** allow source proposals only through a bounded Windows AppContainer test path; stage changes in a disposable candidate tree, require exact source hashes, persist the full rollback checkpoint before atomic application, and refuse rollback when patched files have since changed. **Reason:** complete the capability development cycle without running candidate tests with host filesystem/network access or silently replacing concurrent human work. **Constraint:** depends on the experimental Windows `processmodel.dll` sandbox API; unsupported hosts fail closed. Candidate tests run with one process, fixed time/memory limits, no new dependencies/native binaries, and the managed host restarts after successful HTTP integration. **Source:** `cadyx/source_extension.py`, `cadyx/source_sandbox.py`, [Self-Evolving Capability Engine](docs/architecture.md#self-evolving-capability-engine), `tests/test_source_extension.py`.
- **Status:** active.

## Verified CAD delivery

- **Decision:** export only valid single-solid OCCT B-reps through the existing Core/Command System and native kernel boundary. Always write STEP, round-trip it through the existing reader, and compare solid count, validity, bounds and volume. Optionally write binary STL and record full-mesh watertight/orientation and volume checks plus a bounded one-sided sampled surface-deviation measurement. Bind a JSON manifest to the required source revision and canonical geometry SHA-256; atomically publish content-addressed artifacts without replacing existing files, with the manifest last. **Reason:** preserve exact editable B-reps as authoritative delivery while making mesh approximation and verification scope explicit. **Constraint:** STEP and STL outputs are each limited to 64 MiB; triangle meshes and invalid B-reps are not export inputs. STL sampling is not exhaustive and neither format establishes physical fit or printability. **Source:** `cadyx/delivery.py`, `cadyx/native_kernel.py`, `native/kernel.cpp`, `cadyx/commands.py`, `cadyx/workspace.py`, and [verified delivery contract](docs/document-model-v4.md#verified-cad-delivery).
- **Status:** active.
