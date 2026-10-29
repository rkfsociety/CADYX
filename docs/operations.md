# CADYX setup and operations

[Русский](operations.ru.md) · [English](operations.md) · [Documentation map](README.md)

All commands below run from the repository root.

CADYX is an AI-first CAD foundation prototype. Agents create projects and modify models through the Agent API and shared Core Command System; the desktop browser also provides lease-protected manual editing for assembly occurrences and mates. A per-project edit lease prevents concurrent writes. The provider-neutral in-process Agent Runtime can orchestrate a provider adapter and project tools without adding a provider dependency. The browser keeps inspection and editing in the same full-window detail view, with a dominant CAD viewport, assembly tree and properties, one live progress card with the current action and timestamped action history, and front/right/top projections. The full workflow plan and queue remain available to agents through the API. Agents can fetch the exact browser viewport PNG when a live browser frame is available; standalone API use retains a server-rendered fallback.

## Run locally

The verified Windows x64 runtime uses Python 3.15.0, OCCT 8.0.1, and the CPU build of Open3D 0.20.0. Setup downloads hash-checked runtime/SDK archives into the ignored `.runtime/` directory and builds native geometry and scan bridges with installed CMake and MSVC C++ Build Tools (run setup in PowerShell 7+). Python production code needs no pip packages. Open3D handles PLY scan cleanup, point-cloud surface reconstruction, and mesh simplification on CPU; the defaults cap processing at four threads and 100,000 downsampled points. Review `scripts/runtime-versions.json` against the newest stable upstream releases when choosing versions; do not downgrade dependencies to work around compatibility.

```powershell
.\scripts\setup-runtime.ps1
.\.runtime\python\python.exe -m cadyx
```

Run setup again after moving the checkout (the embedded Python import path is local to it). Stop running hosts before rebuilding the native DLL. This setup targets Windows x64; other platforms need their own OCCT SDK and bridge build. CMake and MSVC are build prerequisites, not downloaded by the setup script.

Open <http://127.0.0.1:8888>. Port `8888` is the default; to use another port or workspace, pass `--port` / `--projects-dir`, or set `CADYX_PORT`, `CADYX_HOST`, and `CADYX_PROJECTS_DIR`. The default host is loopback and remains usable without HTTP authentication. A non-loopback bind is refused unless an authentication token is configured.

The home page opens the project list. Click “Новый проект”, enter a name and click “Создать” to save and open an empty project. Errors keep the entered name in the dialog and display the failure. Direct `/p/<slug>` links still open the selected model.

The workspace root (`projects/` by default) contains only project directories. Workspace databases, agent reports and scanner preview/turntable runtime artifacts live in the sibling `.<workspace-name>-state/` directory (`.projects-state/` by default). Back up this directory as well to preserve workspace groups and agent state. Stop all workspace writers before upgrading: startup moves legacy `.cadyx-*` and `.revopoint-*` artifacts to the sibling directory, preserving their names and contents; an existing destination causes an explicit error instead of overwriting data. Other loose user files are left untouched.

Checkboxes in the project cards support bulk deletion. The bulk action bar appears after the first selection and hides when all selections are cleared. “Выбрать все в поиске” selects available projects matching the current search; earlier selections remain selected when the search changes. Confirmation lists the count and names of all selected projects before permanent deletion. Busy projects cannot be selected. Partial failures leave unsuccessful projects selected and report the deletion count and failure reasons.

## Authentication and LAN access

CADYX treats LAN exposure as an explicit authenticated mode. Configure one reusable token of at least 32 non-whitespace characters through `CADYX_AUTH_TOKEN`, or store it in a UTF-8 file and pass `--auth-token-file` (or set `CADYX_AUTH_TOKEN_FILE`). Do not configure both sources. For example, in PowerShell:

```powershell
$env:CADYX_AUTH_TOKEN = & .\.runtime\python\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
.\.runtime\python\python.exe -m cadyx --host 0.0.0.0
```

API and agent clients authenticate with `Authorization: Bearer <token>`. A browser can open the protected host using HTTP Basic authentication: username `cadyx`, password equal to the same token. Browser authentication then applies to the same-origin API requests and event stream. The token is never accepted in a URL or query string. `GET /api/v1/health` is intentionally unauthenticated and returns only minimal readiness metadata; all application UI, project APIs, workspace files, viewport data/images, commands, leases and SSE require credentials when authentication is enabled.

This gate prevents accidental unauthenticated LAN exposure, but it does not add transport encryption. Use LAN mode only on a trusted private network. Put CADYX behind TLS termination or a trusted VPN before using it across an untrusted network, and do not expose the plain-HTTP host directly to the Internet.

## Keep the Windows host running

To keep the host independent of a terminal or Codex session, register its Windows startup task:

```powershell
.\scripts\manage-host-task.ps1 -Action Install
```

The task starts when the current Windows account signs in, runs separately from terminals and Codex, binds only to `127.0.0.1:8888`, and asks Task Scheduler to restart it after a failure. It launches `pythonw.exe` directly, so no console window appears and Task Scheduler tracks the host process. Standard output and errors are captured in `%LOCALAPPDATA%\CADYX\host.stdout.log` and `host.stderr.log`, with previous logs rotated on startup. The host uses this checkout's `projects/` workspace.

Agents and operators can restart the host without browser interaction by running this PowerShell command from the repository root:

```powershell
.\scripts\manage-host-task.ps1 -Action Restart
```

Use `-Action Status`, `-Action Stop`, or `-Action Uninstall` to manage the task. After moving the checkout or rebuilding its local runtime, run `-Action Install` again to refresh the registered paths and restart the host.

## Codex operator integration

The repository root [`AGENTS.md`](../AGENTS.md) points Codex to the project rules and the repository skill at `.agents/skills/cadyx-operator/SKILL.md`. The skill uses the local stdlib MCP adapter, which calls only CADYX's existing HTTP Agent API and exposes no shell, arbitrary HTTP, or model-file write tools.

After setting up the runtime and starting the local host, open this trusted checkout in Codex. The repository-scoped [`.codex/config.toml`](../.codex/config.toml) registers the adapter without changing your user-wide MCP configuration and contains no checkout-specific paths. Codex launches it from the opened project directory, so the same config works when the checkout is stored in a different location. The adapter uses the project's bundled Python runtime and only the standard library; run setup before enabling the project MCP config. Its approval setting is scoped to the CADYX server so its Agent API tools remain callable under non-interactive Codex policies; the adapter exposes no shell, generic HTTP, or direct model-file write tool. To check the MCP registration from this directory:

```powershell
codex mcp list
```

The project config launches the adapter with `.runtime/python/python.exe -m cadyx.mcp_server` and forces Python UTF-8 mode so Russian and other Unicode names survive Windows MCP stdio transport. Using the bundled runtime avoids Windows Python execution aliases that can exist on `PATH` without a working interpreter. If CADYX authentication is enabled, provide `CADYX_API_TOKEN_FILE` as an MCP environment value pointing to the same protected token file used by the host. The adapter reads the token from the file and never writes it to logs or Git. `CADYX_API_TOKEN` may be supplied through the process environment instead; do not put token values in command arguments or repository configuration. Restart Codex's MCP servers or open a fresh conversation after first enabling the project config so it discovers the tools.

`GET /api/v1/agent/capabilities` is a read-only route/command guide with optional parameter JSON Schema hints for commands that have confirmed schemas. Command parameter metadata is advisory; existing Command System validators and the linked model contracts remain authoritative. MCP PNG tools return image content for visual inspection. Long operations can be fenced through `cadyx_stop_agent_work`; the stop waits for an admitted atomic command and validates the committed state.

Multiple current hosts may share a workspace on the same local machine; see the [lease contract](../.codex/docs/architecture.md#edit-leases). SSE updates remain local to each host. The project workspace is `projects/`. Runtime project data is ignored by Git; keep source CAD files and scans backed up as user data.

## Check

```powershell
.\.runtime\python\python.exe -m unittest discover -s tests -v
.\.runtime\python\python.exe -m compileall -q cadyx tests
.\scripts\setup-runtime.ps1 -WithNode
$nodeVersion = (Get-Content scripts/runtime-versions.json -Raw | ConvertFrom-Json).node.version
$node = ".\.runtime\node-v$nodeVersion-win-x64\node.exe"
& $node tests/test_viewport.cjs
& $node tests/test_scan_view.cjs
& $node tests/test_ply_preview.cjs
```

## Documentation

Start with the [documentation map](README.md) for current contracts, versioned schema history, operator workflows, and verification boundaries. The project envelope is v1 and the **current document schema is v16** (`cadyx/migrations.py`); older `document-model-v*.md` contracts describe the features introduced in each version, not necessarily the current limitations. API command/route discovery is available from `GET /api/v1/agent/capabilities`.

## Architecture

See [.codex/docs/architecture.md](../.codex/docs/architecture.md), [.codex/docs/project-format-v1.md](../.codex/docs/project-format-v1.md), [.codex/docs/document-model-v7.md](../.codex/docs/document-model-v7.md), and [.codex/roadmap.md](../.codex/roadmap.md). The foundation uses a Python Core and stdlib HTTP API, a browser-native interface, atomic `project.json` saves, durable per-project SQLite edit leases, a shared command system, and an Open CASCADE geometry-kernel adapter. Editable OCCT B-reps include boxes, sketch extrusions and revolutions, path sweeps, Boolean fuses, edge fillets, imported single-solid STEP models, localized thread resizing, and rigid imported-body transforms. Reconstructed scans are validated triangle meshes rather than B-reps; direct point-cloud imports remain point-only CAD scene objects. Native kernel objects are never stored in project files. The JSON APIs support XY/XZ/YZ sketches, plus custom and face-attached work planes with stable point/line/circle/spline references, exact analytic ellipses and elliptical arcs, checked constraints, and linked solid features. Agents use the Agent API; humans edit assembly occurrences in the browser through the same Core command and lease boundary. The viewport displays supported bodies, instances and sketch elements and previews PLY files through the shared [viewport contract](../.codex/docs/viewport-api.md).

## API starting points

- `GET /api/v1/health` for minimal unauthenticated readiness
- `GET /api/v1/projects` and `GET /api/v1/agent/projects` (CADYX project records plus `folders` for immediate project directories); agent project creation uses `POST /api/v1/agent/projects`
- `GET /api/v1/agent/capabilities` for machine-readable routes, supported model command names/parameter guides, known errors and limitations
- `GET /api/v1/agent/projects/{id}/revisions` lists comparable saved model checkpoints; `POST /api/v1/agent/projects/{id}/revisions/compare` compares saved revisions or uncommitted preview proposals using read-only geometry-aware measurements. See [revision comparison](revision-comparison.md).
- `GET /api/v1/projects/{id}`
- `GET /api/v1/projects/{id}/dashboard` reads the agent workflow/task snapshot; `POST /api/v1/agent/projects/{id}/dashboard` replaces it with agent-reported progress, ETA and task states
- `GET /api/v1/projects/{id}/viewport` and its Agent API equivalent return the same read-only render scene
- `GET /api/v1/agent/projects/{id}/viewport.png` returns the browser's exact main viewport frame when available; `X-CADYX-Viewport-Source` identifies browser or server-fallback output. The browser posts revision-tagged frames to `POST /api/v1/agent/projects/{id}/viewport/snapshot`.
- `GET /api/v1/agent/workspace/selection` reports the active project or folder; `POST` selects an existing project or workspace folder and notifies the passive viewer over SSE
- `POST /api/v1/agent/projects` accepts `select: false` to create a project while preserving viewer selection; `POST /api/v1/agent/projects/{id}/files/{name}` accepts authenticated `.step`/`.stp` bytes up to 64 MiB without overwriting existing files
- `GET /api/v1/workspace/folders/{folder}/files` and `GET /api/v1/workspace/folders/{folder}/files/{name}` for safe in-folder file preview
- `POST /api/v1/projects/{id}/leases` with `owner_type` and `owner_id`
- `POST /api/v1/projects/{id}/commands` with a human `lease_id` and a command
- Agent equivalents under `/api/v1/agent/projects`
- `extrude_sketch`, `revolve_sketch`, `sweep_sketch`, `fuse_bodies`, `cut_bodies`, and `fillet_edges` commands for editable OCCT solids; see the [feature contract](../.codex/docs/document-model-v7.md#sketch-driven-solid-features)
- Document Model v12 adds persisted YZ, arbitrary and face-attached sketch work planes through `create_sketch` and `set_sketch_workplane`; see [work-plane contract](sketch-workplanes.md).
- A staged point-only project PLY can be imported with `import_project_point_cloud` as a persistent, point-only scene body, without Open3D surface reconstruction or an implied physical scale. Mesh-only scan section and alignment commands still need a triangle-mesh reference; see the [scan reverse-engineering contract](../.codex/docs/scan-reverse-engineering.md).
- Document Model v8 adds `create_model_parameters`, `bind_model_parameters`, and atomic `set_model_parameter` rebuilds; Geometry displays the named values. See the [parameter contract](../.codex/docs/document-model-v8.md).
- `fillet_edges` can round multiple compatible edges in one feature; `merge_consecutive_fillet_features` can safely consolidate a same-radius linear fillet chain while rebuilding dependents. See [Document Model v8](../.codex/docs/document-model-v8.md#rebuilding-topology-selected-edge-features).
- `insert_fillet_before_feature` inserts a boss-root fillet before a downstream pocket and atomically reorders/rebuilds the editable feature chain; see [Document Model v8](../.codex/docs/document-model-v8.md#rebuilding-topology-selected-edge-features).
- `import_step` to preserve a single-solid STEP model as an OCCT B-rep; see the [STEP import contract](../.codex/docs/document-model-v7.md#step-import-and-editable-b-reps)
- `export_brep` to validate and atomically publish a revision-bound STEP file, optional STL, and verification manifest; see the [CAD delivery contract](../.codex/docs/document-model-v7.md#verified-cad-delivery)
- `resize_imported_thread` and `transform_imported_brep` to modify and align imported OCCT B-reps; see the [imported B-rep feature contract](../.codex/docs/document-model-v7.md#step-import-and-editable-b-reps)
- `GET /api/v1/agent/projects/{id}/topology?body_id=...` to inspect current B-rep edges before selecting fillets; edge indices must be refreshed after upstream edits
- `import_scan` and `import_project_scan` for PLY-to-mesh reconstruction, and `import_project_point_cloud` to store a staged point-only cloud in the server CAD scene; see the [scan command contract](../.codex/docs/document-model-v7.md#scan-import-and-editable-triangle-meshes)
- Experimental Windows-only Revopoint tools capture UVC Y16 frames, write provisional Q-reprojected PLY files, and query/control the dual-axis turntable over BLE; see the [tool guide](../tools/revopoint/README.md) for 30° capture and calibration limits.
- `POST /api/v1/agent/projects/{id}/viewport/camera` to animate the selected browser viewport from the Agent API; see the [viewport contract](../.codex/docs/viewport-api.md)
- Opt-in `deterministic_rebuild` on `cadyx_check_project` and the project-check API/CLI compares saved, isolated cold and repeated warm parametric feature rebuilds, canonical design inputs and kernel geometry metrics; unknown feature operations or unresolved hash changes remain `untested`. See [project checks](../.codex/docs/project-checks.md).
- `GET /api/v1/projects/{id}/activity` and its Agent API equivalent return the recent visual activity snapshots; `agent.activity` SSE events update them live
- Dashboard reports emit `agent.dashboard` SSE events and are polled through the `dashboard_revision` in `/api/v1/updates`. The browser shows server-tracked elapsed duration; agent-reported ETA remains in the API. Hardware telemetry is not currently available.
- Agent `/stop` with `lease_id` and `/resume` with `owner_id` / `expected_revision`; resumed commands require `expected_revision`. See the [stop/resume contract](../.codex/docs/architecture.md#safe-agent-stop-and-resume).
- `/api/v1/updates` is polled automatically alongside SSE so project, workspace-file, activity, dashboard, and UI-asset changes appear without a manual page refresh
- `GET /api/v1/events` for the SSE update channel

The API is versioned and provider-neutral. When host authentication is configured, every endpoint above except `/api/v1/health` requires valid credentials. See the architecture guide for the exact boundary and current limits.

## CI verification

The `source` job compiles Python sources and checks discovery documentation paths,
current document-version references, command/MCP schema parity, event contracts,
and migrations without installing the native SDKs. The `windows` job runs only
after this gate succeeds and remains authoritative for the verified OCCT/Open3D
build, runtime smoke check, full Python suite and browser tests.

CI caches only runtime download archives, keyed by the reviewed version/hash
locks, setup/extraction scripts and native sources. Setup rechecks archive
SHA-256 values on every run (Node uses the official release checksum list),
extracts fresh SDK trees and rebuilds the bridges; cached DLLs are never used.
Local fast checks use `python -m compileall -q cadyx tests scripts` and
`python -m unittest discover -s tests -p test_source_contracts.py -v`, plus
the `test_events.py`, `test_migrations.py`, `test_persisted_schemas.py`,
`test_extensions.py` and `test_command_families.py` modules with the same invocation.

The source gate also installs development-only tools from `requirements-dev.txt`
(Ruff 0.17.1 and mypy 2.4.0, reviewed against PyPI on 2026-10-17).
With a normal Python 3.15 installation, run `python -m pip install -r requirements-dev.txt`,
`python -m ruff check .`, `python -m mypy`, and
`python -m unittest discover -s tests -p test_static_quality.py -v`.
These tools are not installed into the production embedded runtime.
[Ruff configuration](https://docs.astral.sh/ruff/configuration/) and
[mypy configuration](https://mypy.readthedocs.io/en/stable/config_file.html)
live in `pyproject.toml`: lint covers application Python, and type checking starts
with Agent Runtime, shared errors and the annotation checks themselves. Mypy
follows other application imports silently; it does not certify their internals.
Unreachable branches fail in the mypy-covered modules. Public Core, Command,
HTTP and Agent Runtime annotations have a separate native-free ratchet: missing
annotations or new bare `Any`/`object` annotations fail. Existing dynamic payload
containers remain supported; the small `tests/static-quality-baseline.json`
records existing bare dynamic annotations, not a blanket mypy error suppression.
Remove baseline entries as they are typed; do not grow the baseline to bypass
failures. Expand mypy's file list as modules are made type-clean. Suppressions
must name specific codes and include an inline `-- reason`; a source check
enforces this. No formatter or mass cleanup is part of this gate.

## Agent Runtime

`cadyx.AgentRuntime` accepts an implementation of the `AgentProvider` protocol.
Provider adapters translate native model requests and responses to CADYX's
provider-neutral messages, tool specifications and tool calls. The runtime
offers project inspection, viewport inspection and revision-checked command
execution, managing the agent lease for each run. When a required command is
missing, its capability tools can test and register a data-only composition of
existing commands, reuse a compatible registry entry, and resume modeling. The
same lifecycle is available through the local `cadyx-operator` MCP tools. It
does not execute generated source code; new kernel/API algorithms still require
a trusted source change and host restart. No provider adapter is included.
External integrations report workflow and queue state through the Agent API.
See the [Agent Runtime contract](../.codex/docs/architecture.md#agent-runtime)
and [Self-Evolving Capability Engine](../.codex/docs/architecture.md#self-evolving-capability-engine).

## Project status and deployment boundaries

CADYX is a **development prototype**, not a hosted CAD service or a production-ready public server. The supported reference setup is **Windows x64** with the versions pinned in `scripts/runtime-versions.json`. Native OCCT and Open3D components are built locally; other platforms are not verified. CAD agents use the Agent API and the shared Command System; the human assembly editor uses the same Core command path with a human lease. Human mutations are admitted only while the project's availability policy allows them. A model-provider adapter is **not** bundled.

The HTTP host binds to loopback by default. Non-loopback access requires authentication, but the built-in server does not provide TLS. Do not expose it directly to the public Internet. Experimental capture defaults to the tracked Revopoint MINI profile under `tools/revopoint/calibration/scanners/revopoint-mini-y16/`; physical scanner and table overrides are isolated by device under `%LOCALAPPDATA%\CADYX\scanner-calibration/` and `%LOCALAPPDATA%\CADYX\turntable-settings/`. See the [calibration guide](../tools/revopoint/calibration/README.md). Read [SECURITY.md](../SECURITY.md) before enabling network access.

## Publication readiness

CADYX is not cleared for public distribution or a GitHub release. Complete the [publication readiness gate](../PUBLISHING.md) before changing repository visibility, publishing a source archive, or creating a release. In particular, resolve CI failures, check the redistribution rights for scanner calibration files, and review tracked content and Git history for sensitive material. A pushed commit alone is not publication approval.

## Contributing and licensing

See [CONTRIBUTING.md](../CONTRIBUTING.md) for environment prerequisites, checks, and the AI-first editing contract. For responsible disclosure, see [SECURITY.md](../SECURITY.md).

CADYX is licensed under the [MIT License](../LICENSE). Third-party software downloaded by the setup scripts remains under its own license terms.


## Versioned assemblies — Document Models v9–v16

Document v9 introduced editable in-project part definitions, nested subassemblies and rigid XYZ/quaternion placement instances, rendered as placed occurrences without duplicating saved B-reps. Document v10 added explicit sibling-instance mates (`add_assembly_mate`, `set_assembly_mate`, `remove_assembly_mate`, `solve_assembly_mates`) with caller-invoked rigid kinematic solving. Document v11 added explicit material assignment (`set_assembly_part_material`) and revision-bound read-only mass/center-of-mass/inertia measurements. Document v13 adds saved per-instance grounding and visibility through `set_assembly_instance_properties`; v14 adds spherical mates and scalar motion limits; v15 adds paired persistent B-rep face references; v16 adds parallel, perpendicular, point-distance and axis-angle relations to the same mate graph. The desktop editor supports occurrence selection, face-based mate creation, placement transforms and undo/redo through leased, revision-checked commands. Legacy documents migrate in memory to the current v16 model and are persisted on a successful leased command.

Assembly geometry is millimeter-based. The [assembly graph](../.codex/docs/document-model-v9.md), [mate constraints](../.codex/docs/document-model-v10.md), [material schema](../.codex/docs/document-model-v11.md), [v13 instance state](../.codex/docs/document-model-v13.md), [current v16 mate contract](../.codex/docs/document-model-v16.md), [mass properties](assembly-mass.md), and [sampled interference/fit checks](assembly-fit.md) document the separate contracts. This solver propagates rigid joint coordinates and projects the new axis/anchor relations, then validates closed-loop consistency and redundancy. General nonlinear mechanism optimization and arbitrary vertex selection are not implemented. Assembly fit checks evaluate **explicit discrete poses**, not continuous collision-free motion. Cross-project assembly links, general unit conversion, continuous dynamics and assembly STEP export remain outside the implemented scope.

The local MCP adapter executes ordinary commands through one `POST /api/v1/agent/projects/{id}/commands/execute` request, acquiring and releasing a server-managed lease around the exact-revision command. Its default project inspection is compact; `cadyx_inspect_project_full` retrieves complete geometry and history when necessary. Use `GET /api/v1/agent/projects/{id}/inspection` for bounded, paged reads.

## Standard-part catalog (initial built-in presets)

Agent clients discover data-only, SHA-256-addressed preset manifests at
`GET /api/v1/agent/catalog`, then validate exact version, hash and parameters
through `POST /api/v1/agent/catalog/resolve`. The returned
`create_catalog_part` command is executed via the same revision-checked,
leased Agent API/MCP command route as other modeling operations. Supported
presets include a metric threaded stud, an annular reference ring, a hexagonal
bar, a solid rectangular extrusion and user-staged STEP references. Created
features retain catalog provenance. Neither an annular ring nor an externally
sourced STEP should be treated as a certified bearing or manufacturing fit.
Third-party STEP files must already be staged, explicitly rights-attested
and SHA-256-pinned; CADYX does not download packages or independently
verify third-party licenses. See [catalog contract](standard-part-catalog.md).
