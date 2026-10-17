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
`GET /api/v1/agent/projects/{id}/viewport.png` endpoints return the exact
`image/png` frame drawn in the browser's main model canvas when a current
browser frame is available. This includes the same camera, wireframe mode,
rotation axis, global XYZ axes through the origin with a `0, 0, 0` marker,
coordinate gizmo, and grid. The browser publishes frames to
`POST /api/v1/agent/projects/{id}/viewport/snapshot`, tagged with the document
revision; this is a read-only observation update, needs no edit lease, and is
held in memory only. Frames are refreshed as the viewport renders, with uploads
coalesced to at most 2.5 per second. The image's pixel dimensions match the
browser canvas backing size. Responses identify their origin through
`X-CADYX-Viewport-Source` (`browser` or `server-fallback`) and the source
revision through `X-CADYX-Document-Revision`. If no browser frame has arrived,
the API retains the standalone 960×720 isometric server render as a fallback.
If a stored browser frame belongs to an older document revision, it is discarded
and the endpoint returns the current server-rendered image while the browser
catches up. Both read routes return the same bytes for a given request state.

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
exports. OCCT B-rep edges are sampled at a 0.16 document-unit deflection and
returned as body `lines`; periodic seams and edges between adjacent faces whose
local normals differ by less than 30 degrees are omitted. The browser draws
these feature curves with the shaded depth buffer, so rear edges do not draw
through front faces. It derives only smooth-surface silhouettes from triangles;
triangle tessellation seams are never treated as feature edges. Triangle-mesh
bodies have no B-rep feature edges and therefore show only their view-dependent
silhouette. Active sketch lines and points remain overlays and are not
hidden-line tested.
Editable OCCT solids include boxes, closed-profile extrusions and
revolutions, swept pipes, Boolean fuses, fillets, and external metric threads.
Generated involute spur gears render as ordinary XY sketch linework until
extruded through the profile-feature command. Imported scans remain validated
`cadyx-mesh-v1` triangle meshes rather than B-reps. XY sketch points/lines lie at Z=0; XZ
sketches map their points/lines onto Y=0 with stored y becoming global Z.
Circles use 64 closed segments, a display approximation rather than replacement
CAD geometry. Coordinates retain document units without inventing a physical
unit convention.

The browser checks inspection and scene revisions before presenting a snapshot;
if a command commits between requests, it refreshes again. SSE refreshes the
scene immediately after local agent/human commands. A lightweight update
snapshot also checks workspace, project, file, activity, and UI asset versions
every five seconds, so changes made by another cooperating host appear
automatically. Changed HTML, CSS, JavaScript assets, or browser/API compatibility
version trigger a page reload;
the selected project is restored. Returning to a hidden tab or reconnecting also
triggers a sync. The browser keeps the read-only model viewport dominant, with
section navigation on the left and an inspection column on the right. The
column shows a single progress block with current status, progress and elapsed duration, the
current or last actual agent action, and its current or most recently finished
task. The browser does not show planned phases or future queue entries; agents
continue to receive the full plan and queue through the Agent API. The column
also shows front/right/top projections of the same validated viewport
geometry. Orthographic wireframes include view-dependent silhouette edges so
smooth revolved walls remain outlined in
front and side views. Scene geometry metrics remain in the
viewport footer. Object selection remains available in the Geometry view;
selecting a feature shows its dimensions without overlaying tessellation edges.
When the document revision changes, tree selection follows the latest visible
feature so its result dimensions stay attached to the current modeling action;
manual selection remains in effect until the next revision.
Clicking empty space in the feature tree clears the tree selection, viewport
highlight, and selected-item properties.
Object properties are not shown in a separate panel. Files is navigation item 06 and
lists direct user files in the active project through `GET /api/v1/projects/{id}/files`;
selected files are served from the corresponding `/files/{name}` route. The tab
is enabled only when `GET /api/v1/updates?project_id={id}` reports
`project_lock.state: FREE` and the activity snapshot has no running operation.
Unknown or busy lock/activity state disables the tab. Lock changes arrive over
SSE and are also polled for cooperating hosts. The file view never changes the
active project. Body geometry renders with only
the currently edited sketch. Inactive sketches, their points/lines, and sketches
already referenced by a solid feature are omitted from both the viewport and the
user-facing object tree. A previously used sketch may reappear only while its profile is actively
being edited, then is hidden when that edit finishes. Agent operation events
animate each new sketch stroke and apply eased visual transitions to profile
extrusions/revolutions, metric-thread construction, path sweeps, Boolean fuses,
and edge fillets; consuming a sketch removes its overlay as the solid feature
appears. Sweep animation precomputes each mesh vertex's progress along its
nearest path segment using a segment AABB hierarchy, so it avoids scanning the
entire path for every vertex. Boolean fuse hides its consumed source bodies and
eases the newly connected B-rep into the scene. Sketch lines and points overlay
solid shading only while that sketch is active (no hidden-line removal or
viewport picking). The progress card shows the current action/result, a distinct
current or most recently finished task, and a compact history of completed or
failed agent actions in newest-first order. Action rows include local completion
time derived from the API snapshot timestamp; the full API journal remains
available to agents and is not rendered as a transcript.

## Agent dashboard reports

`GET /api/v1/projects/{id}/dashboard` (also available under
`/api/v1/agent/projects/{id}/dashboard`) returns the latest persisted
dashboard snapshot:

```json
{
  "workflow": {
    "title": "Проектирование детали",
    "progress_percent": 68,
    "eta_seconds": 252,
    "phases": [
      {"id": "concept", "title": "Подготовка модели", "state": "completed"},
      {"id": "design", "title": "Уточнение конструкции", "state": "in_progress"}
    ]
  },
  "tasks": [
    {"id": "042", "title": "Уточнение крепёжных элементов", "state": "running"}
  ],
  "revision": 1,
  "updated_at": "2026-10-14T12:00:00+00:00",
  "started_at": "2026-10-14T11:55:48+00:00",
  "elapsed_seconds": null
}
```

Agents replace the current report with
`POST /api/v1/agent/projects/{id}/dashboard`, using `workflow: null` when no
plan is available and `tasks: []` when the queue is empty. A workflow contains
1–24 ordered phases (`pending`, `in_progress`, `completed`, or `failed`), an
agent-reported progress estimate from 0 to 100, and an optional ETA in seconds.
100% is accepted only when every phase and queued task is completed; an active
report cannot be cleared with `workflow: null`. Mark verification phases
completed only after the model and its deliverables have been inspected.
The queue accepts at most 100 tasks with `running`, `pending`, `completed`, or
`failed` state. `revision` and `updated_at` are assigned by Core. Reports do not
edit the CAD document or require an edit lease. External agent integrations
publish workflow and queue updates through this Agent API endpoint. Core
publishes each snapshot as `agent.dashboard` over SSE; `/api/v1/updates` includes
`dashboard_revision` so the browser can recover missed events. Dashboard
reports and their monotonic global work revision are saved atomically in the
workspace runtime state, so an unfinished workflow remains active after a host
restart. `/api/v1/updates` exposes `agent_work_active` and
`agent_work_revision`, and the host publishes `workspace.agent-work` over
SSE. The browser disables every project control while any workflow is below
100%, any phase or task is not completed, or the global work state has not yet
been confirmed. It persists an active browser lock across reloads and clears it
only after a newer API revision confirms that all workflows and queues have
completed. Hardware telemetry is not currently reported, so the UI does not
display invented CPU/GPU/memory values.

Core adds `started_at` and `elapsed_seconds` to persisted dashboard snapshots;
agents must not send these fields. `started_at` is set at the first phase
`in_progress` or task `running` report. The browser counts up from this UTC
timestamp and displays elapsed time in the progress strip. On verified 100%
completion or a failed phase/task, Core freezes the whole-second duration in
`elapsed_seconds`, which remains available after host or page restarts. Legacy
dashboard snapshots without timer fields remain readable. The API retains the
agent-reported `eta_seconds` for integrations, while the browser shows elapsed
time in this location.
Workflow titles, phase names, task titles, and `agent.activity.action` values
are shown as received; send them in Russian to keep the localized browser
interface consistent. Project names, object names, and file names remain as
entered by the user or supplied by their source files.

For a modeling request, the agent should inspect the project and references,
then publish a concrete ordered plan before its first model command. Use
3–7 request-specific phases with observable outcomes, initially all `pending`,
and report 0% progress. Set ETA only when it can be estimated honestly; use
`null` otherwise, and leave the task queue empty unless work is actually queued.
Update phase states, progress, and ETA as work advances; publish a revised plan
before continuing if findings change the steps. Mark phases complete only after
verification, and mark failed work explicitly. Publish the plan before model
changes so the agent has a stable execution sequence; the browser displays the
latest actual action, current or most recently finished task, and the timestamped
history of completed/failed actions without exposing future steps.
When the latest action has completed and no action or agent lease is active,
the progress strip appends `· завершено` to its action text so present-tense
action names cannot be mistaken for work that is still running.

`window.CadyxViewport.createViewer(canvas)` is shared with PLY preview and returns:

- `load({positions, triangles, bounds, lines?, points?, bodyTriangleRanges?, featureTriangleRanges?}, {reset = true, transition?})`;
  omit lines/points for a PLY point cloud. Use `reset: false` on document refresh.
  Agent-created sketch and profile transitions are eased in the shared viewer;
  regular loads and PLY previews remain static unless a transition is supplied.
- `setSelectedBody(bodyId)` highlights the visible faces of that body in the
  shared preview. `bodyTriangleRanges` maps body IDs to triangle-index ranges;
  highlight tint is depth-tested, so hidden faces are not drawn through the model.
  Pass `null` to clear the highlight.
- `setSelectedFeature(featureId)` highlights only OCCT-generated faces associated
  with the selected fillet or chamfer feature. `featureTriangleRanges` maps feature
  IDs to triangle-index ranges. Consumed edge-feature faces can be supplied through
  `overlayTriangleRanges`; the renderer depth-tests them against the visible mesh
  and keeps them out of the normal shaded pass.
- `render()` redraws after resize/visibility changes.
- `fit()` resets orientation and zoom to fit all scene bounds.
- `getCamera()` returns `{orientation, zoom, projection: "orthographic"}`;
  `orientation` is a row-major 3×3 camera rotation matrix.
- `setCamera(camera)` accepts that orientation matrix or the legacy `{yaw, pitch}`
  form, along with zoom within 0.25–5 and the orthographic projection. Pitch is
  not clamped, so legacy callers can select orientations beyond either pole.
- `orbitBy(yawDelta, pitchDelta, zoomFactor = 1)` applies relative camera
  rotations and zoom without changing the document.

Camera drag and wheel events update camera state immediately and coalesce
canvas rendering to one animation-frame callback. CAD body shading averages
adjacent triangle normals at shared vertices so the lighter viewport mesh
keeps a smooth surface appearance.

Camera orientation maps world XYZ into camera `[right, depth, up]` coordinates.
Dragging uses a virtual trackball, allowing unrestricted rotation around all
three axes, including complete turns and roll. The camera triad and view cube
follow the complete orientation. Wheel zooms; double-click or **Вписать**
restores the fitted isometric view. API camera events apply incremental yaw and
pitch rotations without clamping the accumulated pitch.
Unless a modeling request specifies another datum, new geometry starts at
global Z=0. When a scene has a shared revolution axis, the camera orbits around
that axis at the body's Z midpoint; an asymmetric handle therefore cannot shift
the camera pivot away from the mug centerline. Fit size accounts for the full
scene bounds relative to that pivot. A compact XYZ triad tracks camera
orientation; revolved bodies show a dashed Z-axis line through their rotation
axis. Camera stays local to the viewer, requires no document mutation and is
retained across revisions of the same project. New projects/files reset it.

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
`tests/test_viewport.cjs` checks Z-up projection, trackball turns, camera
orientation, top view, coordinate axes, zoom, validation, fit and camera
preservation. See
[run/check commands](../../README.md).

## Source comparison

GET /api/v1/agent/projects/{id}/comparison (also available under the read-only projects API) returns an aligned source/result mesh overlay and original, modified and delta volumes. Optional original_body_id and modified_body_id select an ancestor and descendant; defaults use the first and last saved body. Rigid transforms along the source chain are applied to the reference mesh. The panel automatically displays available comparisons: cyan reference wireframe and green result. This is a tessellated overlay, not a Boolean added/removed-material analysis. Independent assemblies return available=false by default. No project document is modified.
