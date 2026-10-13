# CADYX

CADYX is an AI-first CAD foundation prototype. An AI agent and a human use the same Core and Command System; a per-project edit lease ensures only one of them can mutate the document at a time. The browser is an observer and API client, while the Core owns project state.

## Run locally

Requires Python 3.12–3.14 and the pinned Open CASCADE Python bindings:

```powershell
python -m pip install -r requirements.txt
```

```powershell
python -m cadyx
```

Open <http://127.0.0.1:8888>. To use another port or workspace, pass `--port` / `--projects-dir`, or set `CADYX_PORT`, `CADYX_HOST`, and `CADYX_PROJECTS_DIR`. The default host is loopback. Binding to a LAN interface is available with `--host`, but this prototype has no authentication.

Multiple current hosts may share a workspace on the same local machine; see the [lease contract](docs/architecture.md#edit-leases). SSE updates remain local to each host. The project workspace is `projects/`. Runtime project data is ignored by Git; keep source CAD files and scans backed up as user data.

## Check

```powershell
python -m unittest discover -s tests -v
python -m compileall -q cadyx tests
```

## Architecture

See [docs/architecture.md](docs/architecture.md), [docs/project-format-v1.md](docs/project-format-v1.md), [docs/document-model-v2.md](docs/document-model-v2.md), and [.codex/roadmap.md](.codex/roadmap.md). The foundation uses a Python Core and stdlib HTTP API, a browser-native interface, atomic `project.json` saves, a durable per-project SQLite edit lease manager, a shared command system, and an Open CASCADE geometry-kernel adapter. Geometry is rebuilt from JSON-safe parameters when validated; native kernel objects are not stored in project files. The JSON APIs also support XY sketches with stable point/line/circle references and checked constraints; the browser currently previews boxes and PLY files.

## API starting points

- `GET /api/v1/projects` and `GET /api/v1/agent/projects` (CADYX project records plus `folders` for immediate project directories); `POST /api/v1/projects` creates a CADYX project record
- `GET /api/v1/projects/{id}`
- `GET /api/v1/workspace/folders/{folder}/files` and `GET /api/v1/workspace/folders/{folder}/files/{name}` for safe in-folder file preview
- `POST /api/v1/projects/{id}/leases` with `owner_type` and `owner_id`
- `POST /api/v1/projects/{id}/commands` with a human `lease_id` and a command
- Agent equivalents under `/api/v1/agent/projects`
- `GET /api/v1/events` for the SSE update channel

The API is versioned and provider-neutral. See the architecture guide for current limits and the next implementation stages.
