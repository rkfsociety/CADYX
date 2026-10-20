# Headless project checks

`check_project` composes existing CADYX validators for an exact saved revision,
without editing the document or taking an edit lease. It is an external-agent
tool, not an autonomous CAD controller.

Call `core.check_project(project_id, expected_revision, ...)`,
`POST /api/v1/agent/projects/{id}/validations/project-check`, or MCP
`cadyx_check_project`. Required input: `expected_revision`. Optional:
`specification` (existing model specification v1), `strict`,
`export_body_id`, `require_assembly`, `require_drawings`, `max_bodies`
(1–128), `time_budget_seconds` (>0 and <=120),
`deterministic_rebuild` (default false) and `persist` (default false).
Core supports an additional `cancelled` callable. Cancellation and deadlines
are **cooperative between stages and specification checks**: an already
running native kernel call cannot be preempted by this operation.

The version 1 JSON response contains `project_id`, `revision`, `status`,
`passed`, `strict`, `counts`, `limits` and ordered `stages`. Every
stage has `id`, `status` (`pass`, `fail` or `untested`),
`required`, `method` and `evidence`. Stages are:

- `document_graph`: existing Document schema/object/reference validation.
- `geometry`: existing Core/kernel validation of saved live geometry.
- `specification`: existing model specifications with revision-checked
  dimension, clearance, preservation and thickness evidence (only required
  if supplied).
- `export_eligibility`: source B-rep existence, one solid, positive volume,
  kernel metrics and exporter support. This is **preflight only**, not verified
  STEP/STL delivery or a round-trip test. Multiple bodies require an explicit
  `export_body_id`.
- `deterministic_rebuild` (opt-in): runs an isolated rebuild of saved,
  supported parametric root features and derived features, then repeats on the
  rebuilt copy. It compares the canonical design parameter/binding and entity
  graph SHA-256, geometry descriptor SHA-256, OCCT B-rep validity, volume,
  bounding boxes and topology across saved/cold/warm stages. The tolerance
  for numeric metrics is absolute 1e-8 model units and relative 1e-9.
  Modified graph or measured metric changes are `fail`; unknown feature
  operations, budget/cancellation, or changed descriptor hashes with otherwise
  matching coarse metrics are explicitly `untested` (equal volume and bounds
  do not establish shape identity). The stage is required only when requested.
  A "cold" check means a freshly loaded, isolated document copy **within
  the current host process**, not a new OS process or a fresh OCCT DLL.
  It checks a saved revision only, not arbitrary changed inputs across revisions.
  Arbitrary-plane profile extrusions are rebuilt through the same Command
  System feature rebuild used by parameter edits, preserving their saved profile,
  plane origin, normal, local X axis and depth. Native work is cooperatively
  budgeted at stage boundaries, not preemptible.
- `assembly`, `drawings`: unsupported **inside the composite project-check
  pipeline** and `untested`, required only if explicitly requested. Separate
  revision-bound [assembly mass](../../docs/assembly-mass.md) and
  [discrete-pose assembly fit](../../docs/assembly-fit.md) endpoints exist;
  they are **not silently substituted** for the `assembly` project-check
  stage. No fabricated evidence for unavailable composite backends.

Only **required** stages determine overall `status`: fail takes precedence,
then untested, otherwise pass. `strict` additionally makes `passed=false`
for required untested stages while retaining their actual `untested` status.
Saving an optional sidecar record with `persist=true` does not change the
CAD document revision. A project-check record stores the aggregate SHA-256 of
its saved body geometries in stable body-ID order. A concurrent save triggers
`REVISION_CONFLICT`.

## CI invocation

```shell
python -m cadyx.check_project --projects-dir projects --project-id PROJECT_UUID --expected-revision 12 --strict --specification requirements.json --export-body-id body:UUID --deterministic-rebuild > report.json
```

Exit 0: accepted. Exit 1: measured failure or required untested in strict
mode. Exit 2: invalid input, missing project or revision mismatch.
CI can run this directly and archive JSON. Kernel checks require the
configured native Windows OCCT runtime. The CLI never modifies user files.
