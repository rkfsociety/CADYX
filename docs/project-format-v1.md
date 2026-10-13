# CADYX Project Format v1

Every project lives at `projects/<project-id>/project.json`, where `project-id` is a UUID. The JSON document is UTF-8 and uses `format: "cadyx-project"` with `version: 1`.

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
    "version": 1,
    "revision": 0,
    "objects": []
  },
  "history": []
}
```

The document and its successful command history are replaced together using a temporary file and an atomic filesystem replace. Edit leases and live host sessions are runtime state and are not serialized into `project.json`. Unknown project or document versions are rejected rather than silently interpreted.

Box geometry is stored as a JSON descriptor with its kernel id, dimensions, and measured volume. New descriptors use `occt-v1`; Open CASCADE reconstructs the B-rep from dimensions and checks its validity when creating or validating a project. Native kernel objects are not serialized. Existing `primitive-v1` box descriptors remain readable and are checked by rebuilding the corresponding Open CASCADE solid.
