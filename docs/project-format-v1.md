# CADYX Project Format v1

Every project lives at `projects/<project-name>/project.json`. The directory name is a filesystem-safe version of the display name, with a numeric suffix when a folder already exists. A local `.cadyx-project-id` marker keeps the stable UUID addressable even if `project.json` is damaged; the UUID remains the API and lease identity. Legacy `projects/<project-id>/` directories remain readable. The JSON document is UTF-8 and uses `format: "cadyx-project"` with `version: 1`.

```json
{
  "format": "cadyx-project",
  "version": 1,
  "id": "<uuid>",
  "name": "Example",
  "created_at": "<UTC ISO-8601>",
  "modified_at": "<UTC ISO-8601>",
  "document": {
    "id": "document:<uuid>",
    "version": 2,
    "revision": 0,
    "objects": []
  },
  "history": []
}
```

The document and its successful command history are replaced together using a temporary file and an atomic filesystem replace. Edit leases are stored separately in a per-project `.cadyx-leases.sqlite3` runtime sidecar; live host sessions and leases are not serialized into `project.json`. See the [local multi-process lease contract](architecture.md#edit-leases) for lifecycle and backup restrictions. Unknown project or document versions are rejected rather than silently interpreted.

Agent stop fences and resumed-lease revision protection are stored separately
in `.cadyx-agent.sqlite3`; they do not change the portable schema. See the
[stop/resume contract](architecture.md#safe-agent-stop-and-resume).

The project envelope remains version 1; the nested document now uses version 3.
Legacy document v1 and v2 snapshots are explicitly upgraded in memory on read
and persisted only with a successful command. See [Document Model v3](document-model-v3.md)
for the sketch schema, solid feature commands, constraint semantics and
compatibility policy.

Box geometry is stored as a JSON descriptor with its kernel id, dimensions, and measured volume. New descriptors use `occt-v1`; Open CASCADE reconstructs the B-rep from dimensions and checks its validity when creating or validating a project. Native kernel objects are not serialized. Existing `primitive-v1` box descriptors remain readable and are checked by rebuilding the corresponding Open CASCADE solid.

## Migration contract

`cadyx/migrations.py` owns current schema versions and explicit one-version
steps. Project-envelope and document versions are independent: migrate the
envelope first, then the nested document. The envelope is still v1 (no envelope
step is needed); the supported document steps are v1 to v2 and v2 to v3. New schema changes
must add each required consecutive step instead of relabelling an unknown
snapshot. Missing steps, unsupported versions (including future versions), and
non-integer version tags are errors.

Workspace load and validation migrate deep copies and then validate the current
document, references, project identity and revision/history consistency. Saved
documents must include id, revision and objects; incomplete snapshots are
rejected. Core additionally validates geometry on inspection and commands.
Migration preserves metadata, ids, geometry, references and existing history;
it never adds a command or increments a revision. Reapplying migration to a
current snapshot leaves its value unchanged.

Reads, listings, lease operations and rejected commands do not rewrite legacy
files. The next successful Core command under the current edit lease commits
the migrated snapshot and its command/history change through the existing
atomic save. A failed atomic replacement leaves the original file intact.
There is no standalone migration write endpoint. Source CAD and scan files are
outside migration scope. Older hosts are not guaranteed to read newer schemas.
