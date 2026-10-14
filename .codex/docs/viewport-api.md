# Shared rendering and viewport API v1

`GET /api/v1/projects/{id}/viewport` and
`GET /api/v1/agent/projects/{id}/viewport` return identical JSON scenes.
Core reads the atomically saved project snapshot, so each response reflects one
committed revision. Observation requires no edit
lease and does not alter `project.json`, command history or lease ownership.
Read-only routes load the last atomically saved snapshot without waiting on an
agent's edit lease, so the viewer can show the latest committed operation while
the next command runs. Core checks a stat-only project-file token before loading
and validating the full document; unchanged reads reuse the cached scene. On a
cache miss, geometry-validation results are keyed by the document revision,
which changes atomically with accepted edits. This avoids serializing and hashing
large scan meshes just to check the cache. A document edit invalidates the scene,
and callers receive copies so they cannot mutate the cache. Missing projects use
the existing 404 contract; invalid geometry uses 400. The scene is derived,
never persisted; no project/document migration is required.

The matching `GET /api/v1/projects/{id}/viewport.png` and
`GET /api/v1/agent/projects/{id}/viewport.png` endpoints return the same scene
as a server-rendered `image/png` isometric snapshot. The server derives it
from the same validated scene data, so an agent can fetch a view of its model
directly through HTTP without opening the browser. The response is read-only,
is 960×720 pixels, and needs no edit lease or extra runtime dependency.

The response has `version: 1`, document `revision`, `coordinate_system: "XYZ"`,
`projection: "orthographic"`, `bounds` (`min`/`max` XYZ triples, or null when
empty), and `objects`. Each render object has its stable `object_id`, document
`type`, flat XYZ `positions`, and zero-based vertex index arrays: `triangles`
(triples), `lines` (pairs), `points` (single indices). Empty arrays are explicit.
Features, sketches and constraints have no independent visible geometry.
OCCT B-rep triangles come from native tessellation, including face location
transforms and outward winding. Viewport-only tessellation uses an absolute
linear deflection of 0.16 document units to keep camera orbit responsive; this
is a display approximation and does not alter the exact editable B-rep or
exports. Editable OCCT solids include boxes, closed-profile extrusions and
revolutions, swept pipes, Boolean fuses, and fillets. Imported scans remain
validated `cadyx-mesh-v1` triangle meshes rather than B-reps. XY sketch points/lines lie at Z=0; XZ
sketches map their points/lines onto Y=0 with stored y becoming global Z.
Circles use 64 closed segments, a display approximation rather than replacement
CAD geometry. Coordinates retain document units without inventing a physical
unit convention.

The browser checks inspection and scene revisions before presenting a snapshot;
if a command commits between requests, it refreshes again. SSE refreshes the
scene immediately after local agent/human commands. A lightweight update
snapshot also checks workspace, project, file, activity, and UI asset versions
every five seconds, so changes made by another cooperating host appear
automatically. Changed HTML, CSS, or JavaScript assets trigger a page reload;
the selected project is restored. Returning to a hidden tab or reconnecting also
triggers a sync. The browser shows the detail full-window by default; model
navigation and properties remain in collapsed read-only docks. Body geometry
renders with only the currently edited sketch. Inactive sketches, their
points/lines, and sketches already referenced by a solid feature are omitted
from both the viewport and the user-facing object tree. A previously used
sketch may reappear only while its profile is actively being edited, then is
hidden when that edit finishes. Agent operation events animate each new sketch
stroke and apply eased visual transitions to profile extrusions/revolutions,
path sweeps, Boolean fuses, and edge fillets; consuming a sketch removes its
overlay as the solid feature appears. Sweep animation precomputes each mesh
vertex's progress along its nearest path segment using a segment AABB hierarchy,
so it avoids scanning the entire path for every vertex. Boolean fuse hides its consumed source
bodies and eases the newly connected B-rep into the scene. Sketch lines and points
overlay solid shading only while that sketch is active (no hidden-line removal
or viewport picking). The activity card contains the fixed `РАБОТА АГЕНТА`
label and one status line; it shows the current action, a short completion or
failure result, or `Думает` when idle. Detailed operation history remains in the
Agent API snapshot and is not rendered in the user interface.

`window.CadyxViewport.createViewer(canvas)` is shared with PLY preview and returns:

- `load({positions, triangles, bounds, lines?, points?}, {reset = true, transition?})`;
  omit lines/points for a PLY point cloud. Use `reset: false` on document refresh.
  Agent-created sketch and profile transitions are eased in the shared viewer;
  regular loads and PLY previews remain static unless a transition is supplied.
- `render()` redraws after resize/visibility changes.
- `fit()` resets orientation and zoom to fit all scene bounds.
- `getCamera()` returns `{yaw, pitch, zoom, projection: "orthographic"}`.
- `setCamera(camera)` validates finite values, pitch within +/-π/2 radians,
  zoom within 0.25–5 and the orthographic projection before updating.

Camera drag and wheel events update camera state immediately and coalesce
canvas rendering to one animation-frame callback. CAD body shading averages
adjacent triangle normals at shared vertices so the lighter viewport mesh
keeps a smooth surface appearance.

Camera vertical is Z; yaw rotates around Z, pitch changes elevation.
Unless a modeling request specifies another datum, new geometry starts at
global Z=0. When a scene has a shared revolution axis, the camera orbits around
that axis at the body's Z midpoint; an asymmetric handle therefore cannot shift
the camera pivot away from the mug centerline. Fit size accounts for the full
scene bounds relative to that pivot. Drag right/left orbits horizontally; drag
down increases elevation and drag up decreases it. Wheel zooms; double-click or **Вписать** restores the
fitted view. The orbit can reach the exact top view. A compact XYZ triad tracks
camera orientation; revolved bodies show a dashed Z-axis line through their
rotation axis. Camera stays local to the viewer, requires no document mutation
and is retained across revisions of the same project. New projects/files reset it.

Agents can animate a camera change without synthesizing browser input by posting
to `POST /api/v1/agent/projects/{id}/viewport/camera`:

```json
{
  "yaw_delta": 1.2,
  "pitch_delta": 0.1,
  "zoom_factor": 1.0,
  "duration_ms": 1200
}
```

`yaw_delta` is limited to ±2π, `pitch_delta` to ±0.5 radians,
`zoom_factor` to 0.5–2, and `duration_ms` to 0–5000. The Core records a visual
camera operation and publishes `viewport.camera` over SSE. The browser animates
the change only when that project is selected. Camera state remains local to
each viewer; this endpoint needs no edit lease and never changes the saved
document. Events are not replayed after an SSE reconnect.

Validation: `tests/test_rendering.py` checks kernel mesh winding/volume/bounds,
sketch rendering, parameter refresh and read-only persistence;
`tests/test_web_api.py` checks both endpoints and unchanged inspection state;
`tests/test_viewport.cjs` checks Z-up projection, top view, coordinate axes,
vertical drag, zoom, camera validation, fit and camera preservation. See
[run/check commands](../../README.md).
