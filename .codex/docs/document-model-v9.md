# CADYX Document Model v9 — Assembly graph

Document v9 adds `assembly` as a separately versioned graph alongside the
existing sketch/feature model. Earlier snapshots through v8 migrate on **read**
to an empty graph; no read overwrites project.json. A successful leased Core
command persists the migrated snapshot and history atomically. Project envelope
version remains 1. The prior contracts remain in [v8](document-model-v8.md)
and [v7](document-model-v7.md).

```json
{
  "assembly": {
    "version": 1, "units": "mm",
    "parts": [{"id": "part:<uuid>", "name": "Bracket", "body_id": "body:<uuid>"}],
    "subassemblies": [{"id": "subassembly:<uuid>", "name": "Frame"}],
    "instances": [{
      "id": "instance:<uuid>", "name": "Left bracket",
      "parent_id": null, "target_id": "part:<uuid>",
      "translation": [0, 0, 0], "rotation": [1, 0, 0, 0]
    }]
  }
}
```

Each part references one existing editable **body in the same project**. An
imported STEP part retains its original feature history and source provenance.
Multiple instances of a part reuse its authoritative body/feature history:
the saved document never stores per-instance copies of B-reps. Editing that
source body using current parametric commands updates its dependent features
under the existing Core transaction; instances display the new geometry at the
next saved revision. Assembly transforms do not cause source B-rep rebuilds.

Instances target a part or subassembly. `parent_id=null` is the root; a
non-null parent must reference a subassembly. Subassembly instance transforms
are composed along paths from root to leaf, using right-handed rigid quaternions
in `[w, x, y, z]` order and `[x, y, z]` translations. Values must be finite,
and quaternions unit-length within 1e-6. The only supported length units are
**mm**; CADYX rejects other units rather than guessing a scale. Duplicate IDs,
missing bodies/parents/targets, circular subassembly references and attempts to
remove referenced bodies/components are rejected. Rootless uninstantiated
subassemblies do not render, but retain their definitions.

Create/edit via `POST /api/v1/agent/projects/{id}/commands` under the standard
edit lease and `expected_revision`, or the same `cadyx_execute_command`
MCP tool. Versioned transaction preview and commit work through the existing
Core path. Commands and fields:

- `create_assembly_part` — `body_id`, optional `name`
- `create_subassembly` — `name`
- `add_assembly_instance` — `target_id`, optional `parent_id`,
  `name`, `translation`, `rotation`
- `transform_assembly_instance` — `instance_id` and at least one
  `translation` or `rotation`
- `remove_assembly_instance` — `instance_id`
- `remove_assembly_component` — `component_id`, unreferenced only

Read-only project inspection includes this graph and the document revision.
The shared viewport renders placed part occurrences with stable instance paths,
source body identity and instance name; placed bodies are not Boolean-fused.
Distinct source bodies are tessellated once per scene; instances expand only
transport-side transformed vertices. Display traversal is bounded to 32 nested
levels and 4096 part occurrences. Oversized graphs fail explicitly.

**Limits in v9:** no cross-project linked parts, independent units or general unit
conversion, assembly mate/kinematic constraints, assembly mass/inertia, motion
collision validation, exact assembly STEP export, or browser editing. In current
Document v10, mates are defined in [the v10 contract](document-model-v10.md).
The rendering path is an expanded mesh scene rather than GPU instancing;
persistent source B-reps remain shared. Windows native CI and browser
regressions are required before asserting production compatibility.
