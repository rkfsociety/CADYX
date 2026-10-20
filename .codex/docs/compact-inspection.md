# Compact model inspection (P0-6)

CADYX preserves its original full document and history endpoints. An external agent
can avoid returning B-rep strings, meshes, and large command arguments by using
read-only **GET** `/api/v1/agent/projects/{project_id}/inspection`. MCP exposes
the same endpoint through `cadyx_inspect_compact`. No edit lease, extra state
store, or browser automation is involved. The ordinary MCP
`cadyx_inspect_project` and in-process AgentRuntime project summary default
to this compact summary; `cadyx_inspect_project_full` is an explicit full
model/history read for tasks that require it. Page large inspections by
`expected_revision` rather than repeatedly requesting complete B-reps.

## Query contract

| Parameter | Value | Default |
| --- | --- | --- |
| `section` | `summary`, `entities`, `parameters`, `topology`, `changes` | `summary` |
| `expected_revision` | Current document revision (nonnegative integer) | Optional; pin on later pages |
| `offset` / `limit` | Nonnegative index / 1–100 entries | 0 / 25 |
| `kind` | Object type for `entities`; `face` or `edge` for `topology` | No filter / required for topology |
| `body_id` | Saved body ID, required by `topology` | None |
| `shape` | Optional `surface` or `curve` descriptor for `topology` | All |
| `include_metrics` | `1` requests native geometry metrics for selected body entities | 0 |
| `since_revision` | Required by `changes`; exclusive start revision | None |

Every answer includes `schema_version`, `project_id`, `revision` and `section`.
Paged answers include `items`, `total`, `returned`, `offset`, `limit`,
`omitted` and `has_more`. Query parameters are not silently ignored;
unexpected or repeated arguments are errors. A changed `expected_revision`
returns HTTP 409 `REVISION_CONFLICT`; request the first page again instead of
merging two revisions.

- **Summary** reports sorted entity-type counts, constraints, body and parameter
  counts, binding count, parameter units, material availability and explicit
  geometry-metric availability. CADYX does not assert an inferred document unit
  or non-existent materials catalog.
- **Entities** are sorted lexicographically by persistent typed object ID, not
  by document insertion order. They include short names, type, select scalar
  attributes, bounded dependency IDs and parameter names. Full shape/mesh/B-rep
  and large parameters are intentionally omitted. With `kind=body&include_metrics=1`
  the kernel supplies requested body volume, area, bounds and topology metrics;
  this may cost more CPU than the default summary.
- **Parameters** returns named parameter values and their explicit units in
  stable name order.
- **Topology** reuses the existing OCCT topology reader, restricts output to
  one current body, sorts by per-revision face/edge index and includes semantic
  keys when present. `kind=face&shape=plane`, for example, lists current planar
  faces. Indices **do not** persist across rebuilds. Complex typed predicate
  searches remain in `POST /topology/query`.
- **Changes** returns small revision-ordered command receipts from existing
  persisted history after `since_revision`. Each receipt lists feature IDs
  *explicitly referenced* in its command/result, when available. The response
  sets `feature_change_coverage=explicit_history_references_only` and
  `complete_geometry_diff=false`. A rebuilt indirect dependency may not be
  recorded in that list; old B-rep snapshots are not stored in command history.
  Do not interpret an empty feature list as proof that no feature changed.

The API does not include a `next_cursor`: request `offset + limit` while
`has_more`, always providing the same `expected_revision`. The output
reports omitted fields and elements; full-fidelity inspection remains at
`GET /api/v1/agent/projects/{project_id}`, existing topology routes and
viewport routes.

## Performance and regression evidence

`tests/test_compact_inspection.py` checks page ordering, filters, stable
revisions, explicit omissions, safe feature references, HTTP and MCP delegation
and that large geometry/history fixtures yield materially smaller JSON payloads.

For a **representative saved model**, run the optional benchmark (on an installed
CADYX runtime):

```shell
python scripts/benchmark-compact-inspection.py --projects-dir projects --project-id <UUID> --samples 30
```

It prints UTF-8 response bytes, approximate tokens (bytes / 4), p50/p95 wall
time and p50/p95 peak traced allocations for full versus compact inspections.
Metrics depend on model, runtime, kernel and warmed caches; a favorable benchmark
is not claimed until these measurements actually run. Unlike output bytes,
the base project JSON still has to be loaded and validated; this API does not
promise independent storage-layer memory savings. Benchmark execution and all
tests are deferred to the CI/verification environment as requested.
