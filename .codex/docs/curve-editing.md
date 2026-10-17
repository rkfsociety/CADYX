# Human-in-the-loop traced-curve editing

CADYX keeps the browser observation-only for ordinary CAD authoring. Human curve correction is a narrow opt-in exception for reference tracing: a local client may prepare a draft of connected line and circular-arc segments, undo/redo that unsaved draft, and submit a confirmed version through the same human lease and Command System used by every browser-originated document mutation.

## Draft and save boundary

Unsaved edits are client-local. `web/browser-api.js` provides `TraceDraft`, an isolated snapshot history with `replace`, `update`, `undo`, and `redo`; undo/redo never writes project state. A confirmed save uses `save_traced_curve_version` through `POST /api/v1/projects/{project_id}/commands` while holding a human lease and supplying the current project revision. Agent callers may use the same command through the Agent API, but browser editing does not bypass lease ownership, revision checks, Core atomicity, or project availability policy.

Each confirmed save creates a new sketch. Earlier sketches with the same `trace_id` are immutable trace-history versions and are never modified in place. `base_version` is an optimistic trace-history fence: stale saves fail with `REVISION_CONFLICT`. This is independent of, and additional to, the project document revision fence enforced by Core.

## Source provenance and coordinate system

A save requires:

- `source_image_sha256`: the 64-character SHA-256 of the source image bytes used for tracing;
- `coordinate_system.origin`: source-image pixel coordinates used as model origin;
- `coordinate_system.units_per_pixel`: positive model units per source pixel;
- `coordinate_system.unit`: a short unit label retained as provenance;
- `coordinate_system.y_axis`: `up` or `down`;
- `segments`: one connected chain of line or circular-arc definitions in source-image coordinates.

The server does not trust preconverted model coordinates. It recomputes every endpoint, arc center, radius, and signed sweep from the source-image definitions and coordinate-system record. It then validates finiteness, positive scale/radii, non-degenerate segments, arc sweep bounds, chain connectivity, and optional closure before adding ordinary sketch point/line/circle objects. `Document.validate()` remains the final persisted-geometry authority.

The saved sketch records `trace_id`, `trace_version`, normalized source-image SHA-256, coordinate-system provenance, the original source segment records, and whether the trace was requested closed. Geometry remains standard editable CADYX sketch geometry, so downstream profile and feature commands do not need a parallel trace representation.

## Supported scope

The initial constrained editor contract supports XY and XZ sketches, exact straight segments and exact circular arcs, and at most 4096 source segments per saved version. It does not authorize a general browser sketch editor, direct DOM-driven CAD authoring, constraint solving, spline fitting, image storage in `project.json`, or mutation of an earlier trace version. Image acquisition/display and higher-level tracing UI can evolve independently while keeping this save boundary stable.
