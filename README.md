# CADYX

CADYX is an AI-first CAD foundation prototype. Agents create projects and modify models through the Agent API and shared Core Command System; a per-project edit lease prevents concurrent writes. The provider-neutral in-process Agent Runtime can orchestrate a provider adapter and project tools without adding a provider dependency. The browser is a read-only, full-window detail viewer with collapsed model and property docks. Agents can fetch a server-rendered PNG snapshot through the Agent API without using a browser.

## Run locally

The verified Windows x64 runtime uses Python 3.15.0 and OCCT 8.0.1. Setup downloads hash-checked runtime/SDK archives into the ignored `.runtime/` directory and builds the small native kernel bridge with installed CMake and MSVC C++ Build Tools (run setup in PowerShell 7+). Python production code needs no pip packages. Review `scripts/runtime-versions.json` against the newest stable upstream releases when choosing versions; do not downgrade dependencies to work around compatibility.

```powershell
.\scripts\setup-runtime.ps1
.\.runtime\python\python.exe -m cadyx
```

Run setup again after moving the checkout (the embedded Python import path is local to it). Stop running hosts before rebuilding the native DLL. This setup targets Windows x64; other platforms need their own OCCT SDK and bridge build. CMake and MSVC are build prerequisites, not downloaded by the setup script.

Open <http://127.0.0.1:8888>. Port `8888` is the default; to use another port or workspace, pass `--port` / `--projects-dir`, or set `CADYX_PORT`, `CADYX_HOST`, and `CADYX_PROJECTS_DIR`. The default host is loopback. Binding to a LAN interface is available with `--host`, but this prototype has no authentication.

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

Multiple current hosts may share a workspace on the same local machine; see the [lease contract](docs/architecture.md#edit-leases). SSE updates remain local to each host. The project workspace is `projects/`. Runtime project data is ignored by Git; keep source CAD files and scans backed up as user data.

## Check

```powershell
.\.runtime\python\python.exe -m unittest discover -s tests -v
.\.runtime\python\python.exe -m compileall -q cadyx tests
.\scripts\setup-runtime.ps1 -WithNode
.\.runtime\node-v26.11.1-win-x64\node.exe tests/test_viewport.cjs
```

## Architecture

See [docs/architecture.md](docs/architecture.md), [docs/project-format-v1.md](docs/project-format-v1.md), [docs/document-model-v2.md](docs/document-model-v2.md), and [.codex/roadmap.md](.codex/roadmap.md). The foundation uses a Python Core and stdlib HTTP API, a browser-native interface, atomic `project.json` saves, a durable per-project SQLite edit lease manager, a shared command system, and an Open CASCADE geometry-kernel adapter. Geometry is rebuilt from JSON-safe parameters when validated; native kernel objects are not stored in project files. The JSON APIs also support XY sketches with stable point/line/circle references and checked constraints; agents use the Agent API to create and edit them. The read-only browser displays all supported bodies and sketch elements through the shared [viewport contract](docs/viewport-api.md), and previews PLY files using the same renderer.

## API starting points

- `GET /api/v1/projects` and `GET /api/v1/agent/projects` (CADYX project records plus `folders` for immediate project directories); agent project creation uses `POST /api/v1/agent/projects`
- `GET /api/v1/projects/{id}`
- `GET /api/v1/projects/{id}/viewport` and its Agent API equivalent return the same read-only render scene
- `GET /api/v1/agent/projects/{id}/viewport.png` returns a server-rendered detail image without opening a browser
- `GET /api/v1/workspace/folders/{folder}/files` and `GET /api/v1/workspace/folders/{folder}/files/{name}` for safe in-folder file preview
- `POST /api/v1/projects/{id}/leases` with `owner_type` and `owner_id`
- `POST /api/v1/projects/{id}/commands` with a human `lease_id` and a command
- Agent equivalents under `/api/v1/agent/projects`
- Agent `/stop` with `lease_id` and `/resume` with `owner_id` / `expected_revision`; resumed commands require `expected_revision`. See the [stop/resume contract](docs/architecture.md#safe-agent-stop-and-resume).
- `GET /api/v1/events` for the SSE update channel

The API is versioned and provider-neutral. See the architecture guide for current limits and the next implementation stages.

## Agent Runtime

`cadyx.AgentRuntime` accepts an implementation of the `AgentProvider` protocol.
Provider adapters translate native model requests and responses to CADYX's
provider-neutral messages, tool specifications and tool calls. The runtime
offers project inspection, viewport inspection and revision-checked command
execution, managing the agent lease for each run. No provider adapter is
included. See the [Agent Runtime contract](docs/architecture.md#agent-runtime).
