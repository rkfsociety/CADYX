# CADYX Project Format v1

[Полный русский перевод](project-format-v1.ru.md)

[Русский обзор](#русский-обзор) · [English reference](#english-reference)

## Русский обзор

### Формат хранения проекта v1

Оболочка проекта v1 отделена от версии документа модели, сейчас v12. Авторитетное состояние сохраняет ProjectWorkspace, атомарно заменяя `project.json`; служебная история, B-rep и блокировки имеют собственные файлы. Пользовательские исходники в `projects/` нужно сохранять и резервировать независимо от Git. Изменения выполняются только через Core/Command System с защитой ревизии и блокировки. Старые версии мигрируют по правилам `cadyx/migrations.py`; чтение не даёт разрешения переписывать исходные файлы.

Это обзор, не полный перевод. Полные схемы, примеры и подробности контракта приведены на английском ниже.

## English reference

Every project lives at `projects/<project-name>/project.json`. Newly created projects use a filesystem-safe directory name, with a numeric suffix when a folder already exists. Existing workspace directories are adopted in place and keep their names. A local `.cadyx-project-id` marker keeps the stable UUID addressable even if `project.json` is damaged; the UUID remains the API and lease identity. Legacy `projects/<project-id>/` directories remain readable. The JSON document is UTF-8 and uses `format: "cadyx-project"` with `version: 1`.

```json
{
  "format": "cadyx-project",
  "version": 1,
  "id": "<uuid>",
  "name": "Example",
  "url_slug": "example",
  "created_at": "<UTC ISO-8601>",
  "modified_at": "<UTC ISO-8601>",
  "document": {
    "id": "document:<uuid>",
    "version": 12,
    "revision": 0,
    "objects": [],
    "model_parameters": {},
    "parameter_bindings": [],
    "assembly": {
      "version": 3,
      "units": "mm",
      "parts": [],
      "subassemblies": [],
      "instances": [],
      "mates": []
    }
  },
  "history": []
}
```

`name` is the user-facing project title and may use any language. Optional
`url_slug` is the stable lowercase English route key used by `/p/<slug>`; it
contains ASCII letters, digits, and single hyphens. Older records without this
field receive a deterministic Latin fallback from their title when loaded.

The document and its successful command history are committed together. `project.json` is replaced atomically; history entries are appended to `.cadyx-history.jsonl` and fsynced before the project snapshot advances its committed history count and byte offset. A crash before the snapshot replacement leaves an ignored journal tail; a crash after it leaves the durable history and document revision together. Edit leases are stored separately in a per-project `.cadyx-leases.sqlite3` runtime sidecar; live host sessions and leases are not serialized into `project.json`. See the [local multi-process lease contract](architecture.md#edit-leases) for lifecycle and backup restrictions. Unknown project or document versions are rejected rather than silently interpreted.

The private `$cadyx_storage` envelope stores the history journal pointer.
Imported B-rep descriptors are stored once as hidden content-addressed
`.cadyx-brep-<sha256>.json` files in the project directory; document and history
geometry fields hold references. Workspace loading verifies referenced blobs
and expands them, then loads the committed history prefix, before validation or
API responses. Callers continue to see the ordinary project document and full
command results. Legacy records with inline history and no storage envelope
remain readable; their history moves to the journal on the next successful save.
Any uncommitted journal tail is excluded by the saved byte offset and is
truncated before a later append. Backups and project copies must include the
complete project directory, including its hidden `.cadyx-*` files.
Command execution, preview, and project-list summaries load the committed
document plus the history pointer without parsing old journal entries. Full
project inspection and compact history-change pages read and validate the
committed history.
The JSON example above describes the expanded project view. After the first
successful command, the on-disk envelope stores `history: {file, count, bytes}`
under `$cadyx_storage`; the history array is reconstructed from the journal when
the workspace loads the project. A newly created empty project may retain the
inline empty `history` array until its first command is committed.

## Existing workspace directories

Project enumeration adopts each immediate, non-symlink directory that has no
`project.json` and no conflicting `.cadyx-project-id` metadata. CADYX writes a
new UUID marker and an empty project record in that same directory, using
create-if-absent publication so existing files are preserved. A valid existing
marker is reused if its project record is missing. Existing `project.json`
files and invalid reserved metadata are never overwritten. Nested directories
remain part of their parent project and are not enumerated as projects.
Adopted directories appear in the project list, and their direct user files are
available through the project file API.

Agent stop fences and resumed-lease revision protection are stored separately
in `.cadyx-agent.sqlite3`; they do not change the portable schema. See the
[stop/resume contract](architecture.md#safe-agent-stop-and-resume).

The project envelope remains version 1; the current nested document uses version **12** (`cadyx/migrations.py`).
Legacy document v1 through v11 snapshots are upgraded **in memory** on read and
persisted only by a successful command. See [Document Model v7](document-model-v7.md)
for the base object graph, [v8](document-model-v8.md) for named parameters,
[v9](document-model-v9.md) for assemblies, [v10](document-model-v10.md) for mates,
[v11](document-model-v11.md) for materials, and [v12](../../docs/sketch-workplanes.md)
for editable YZ, custom and face-attached sketch work planes.

Box geometry is stored as a JSON descriptor with its kernel id, dimensions, and measured volume. New descriptors use `occt-v1`; Open CASCADE reconstructs the B-rep from dimensions and checks its validity when creating or validating a project. Native kernel objects are not serialized. Existing `primitive-v1` box descriptors remain readable and are checked by rebuilding the corresponding Open CASCADE solid.

## Migration contract

`cadyx/migrations.py` owns current schema versions and explicit one-version
steps. Project-envelope and document versions are independent: migrate the
envelope first, then the nested document. The envelope is still v1 (no envelope
step is needed); the supported document steps are v1 to v2, v2 to v3, v3 to v4,
v4 to v5, v5 to v6, v6 to v7, v7 to v8, v8 to v9, v9 to v10,
v10 to v11, and v11 to v12. New schema changes
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

Reads, listings, lease operations and rejected commands do not rewrite existing
project records for schema migration. Project listing can initialize an
unregistered workspace directory as described above. The next successful Core
command under the current edit lease commits the migrated snapshot and its
command/history change through the existing atomic save. A failed atomic
replacement leaves the original file intact. There is no standalone migration
write endpoint. Source CAD and scan files are outside migration scope. Older
hosts are not guaranteed to read newer schemas.
