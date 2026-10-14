# CADYX

CADYX is an AI-first CAD foundation prototype. Agents create projects and modify models through the Agent API and shared Core Command System; a per-project edit lease prevents concurrent writes. The provider-neutral in-process Agent Runtime can orchestrate a provider adapter and project tools without adding a provider dependency. The browser is a read-only, full-window detail viewer with collapsed model and property docks and a single-line agent action/result status. Agents can fetch a server-rendered PNG snapshot through the Agent API without using a browser.

## Run locally

The verified Windows x64 runtime uses Python 3.15.0, OCCT 8.0.1, and the CPU build of Open3D 0.20.0. Setup downloads hash-checked runtime/SDK archives into the ignored `.runtime/` directory and builds native geometry and scan bridges with installed CMake and MSVC C++ Build Tools (run setup in PowerShell 7+). Python production code needs no pip packages. Open3D handles PLY scan cleanup, point-cloud surface reconstruction, and mesh simplification on CPU; the defaults cap processing at four threads and 100,000 downsampled points. Review `scripts/runtime-versions.json` against the newest stable upstream releases when choosing versions; do not downgrade dependencies to work around compatibility.

```powershell
.\scripts\setup-runtime.ps1
.\.runtime\python\python.exe -m cadyx
```

Run setup again after moving the checkout (the embedded Python import path is local to it). Stop running hosts before rebuilding the native DLL. This setup targets Windows x64; other platforms need their own OCCT SDK and bridge build. CMake and MSVC are build prerequisites, not downloaded by the setup script.

Open <http://127.0.0.1:8888>. Port `8888` is the default; to use another port or workspace, pass `--port` / `--projects-dir`, or set `CADYX_PORT`, `CADYX_HOST`, and `CADYX_PROJECTS_DIR`. The default host is loopback and remains usable without HTTP authentication. A non-loopback bind is refused unless an authentication token is configured.

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

Multiple current hosts may share a workspace on the same local machine; see the [lease contract](.codex/docs/architecture.md#edit-leases). SSE updates remain local to each host. The project workspace is `projects/`. Runtime project data is ignored by Git; keep source CAD files and scans backed up as user data.

## Check

```powershell
.\.runtime\python\python.exe -m unittest discover -s tests -v
.\.runtime\python\python.exe -m compileall -q cadyx tests
.\scripts\setup-runtime.ps1 -WithNode
.\.runtime\node-v26.11.1-win-x64\node.exe tests/test_viewport.cjs
.\.runtime\node-v26.11.1-win-x64\node.exe tests/test_ply_preview.cjs
```

## Architecture

See [.codex/docs/architecture.md](.codex/docs/architecture.md), [.codex/docs/project-format-v1.md](.codex/docs/project-format-v1.md), [.codex/docs/document-model-v3.md](.codex/docs/document-model-v3.md), and [.codex/roadmap.md](.codex/roadmap.md). The foundation uses a Python Core and stdlib HTTP API, a browser-native interface, atomic `project.json` saves, durable per-project SQLite edit leases, a shared command system, and an Open CASCADE geometry-kernel adapter. Editable OCCT B-reps include boxes, sketch extrusions and revolutions, path sweeps, Boolean fuses, and edge fillets. Imported scan results remain validated triangle meshes rather than B-reps; native kernel objects are never stored in project files. The JSON APIs support XY/XZ sketches with stable point/line/circle references, checked constraints, and linked solid features. Agents use the Agent API to create and edit models. The read-only browser displays supported bodies and sketch elements through the shared [viewport contract](.codex/docs/viewport-api.md), and previews PLY files using the same renderer.

## API starting points

- `GET /api/v1/health` for minimal unauthenticated readiness
- `GET /api/v1/projects` and `GET /api/v1/agent/projects` (CADYX project records plus `folders` for immediate project directories); agent project creation uses `POST /api/v1/agent/projects`
- `GET /api/v1/projects/{id}`
- `GET /api/v1/projects/{id}/viewport` and its Agent API equivalent return the same read-only render scene
- `GET /api/v1/agent/projects/{id}/viewport.png` returns a server-rendered detail image without opening a browser
- `GET /api/v1/workspace/folders/{folder}/files` and `GET /api/v1/workspace/folders/{folder}/files/{name}` for safe in-folder file preview
- `POST /api/v1/projects/{id}/leases` with `owner_type` and `owner_id`
- `POST /api/v1/projects/{id}/commands` with a human `lease_id` and a command
- Agent equivalents under `/api/v1/agent/projects`
- `extrude_sketch`, `revolve_sketch`, `sweep_sketch`, `fuse_bodies`, and `fillet_edges` commands for editable OCCT solids; see the [feature contract](.codex/docs/document-model-v3.md#sketch-driven-solid-features)
- `GET /api/v1/agent/projects/{id}/topology?body_id=...` to inspect current B-rep edges before selecting fillets; edge indices must be refreshed after upstream edits
- `import_scan` and `set_scan_parameter` commands for PLY-to-mesh reconstruction; see the [scan command contract](.codex/docs/document-model-v3.md#scan-import-and-editable-triangle-meshes)
- `POST /api/v1/agent/projects/{id}/viewport/camera` to animate the selected browser viewport from the Agent API; see the [viewport contract](.codex/docs/viewport-api.md)
- `GET /api/v1/projects/{id}/activity` and its Agent API equivalent return the recent visual activity snapshots; `agent.activity` SSE events update them live
- Agent `/stop` with `lease_id` and `/resume` with `owner_id` / `expected_revision`; resumed commands require `expected_revision`. See the [stop/resume contract](.codex/docs/architecture.md#safe-agent-stop-and-resume).
- `/api/v1/updates` is polled automatically alongside SSE so project, workspace-file, activity, and UI-asset changes appear without a manual page refresh
- `GET /api/v1/events` for the SSE update channel

The API is versioned and provider-neutral. When host authentication is configured, every endpoint above except `/api/v1/health` requires valid credentials. See the architecture guide for the exact boundary and current limits.

## Agent Runtime

`cadyx.AgentRuntime` accepts an implementation of the `AgentProvider` protocol.
Provider adapters translate native model requests and responses to CADYX's
provider-neutral messages, tool specifications and tool calls. The runtime
offers project inspection, viewport inspection and revision-checked command
execution, managing the agent lease for each run. No provider adapter is
included. See the [Agent Runtime contract](.codex/docs/architecture.md#agent-runtime).

