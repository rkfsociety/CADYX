# Shared rendering and viewport API v1

`GET /api/v1/projects/{id}/viewport` and
`GET /api/v1/agent/projects/{id}/viewport` return identical JSON scenes.
Core holds the existing project guard through load, validation and rendering,
so each response reflects one committed revision. Observation requires no edit
lease and does not alter `project.json`, command history or lease ownership.
Missing projects use the existing 404 contract; invalid geometry uses 400 and
busy projects use 409 (`PROJECT_BUSY`). The scene is derived, never persisted;
no project/document migration is required.

The response has `version: 1`, document `revision`, `coordinate_system: "XYZ"`,
`projection: "orthographic"`, `bounds` (`min`/`max` XYZ triples, or null when
empty), and `objects`. Each render object has its stable `object_id`, document
`type`, flat XYZ `positions`, and zero-based vertex index arrays: `triangles`
(triples), `lines` (pairs), `points` (single indices). Empty arrays are explicit.
Features, sketches and constraints have no independent visible geometry.
Body triangles come from native OCCT tessellation, including face location
transforms and outward winding. Supported saved solids are still boxes; the
scene/viewport are not box-specific, but this stage adds no new solid commands.
XY sketch points/lines lie at Z=0. Circles use 64 closed segments, a display
approximation rather than replacement CAD geometry. Coordinates retain document
units without inventing a physical unit convention.

The browser checks inspection and scene revisions before presenting a snapshot;
if a command commits between requests, it refreshes again. SSE refreshes the
scene after agent/human commands. All visible bodies and sketch elements render
together; selecting a body still controls its properties panel. Sketch lines and
points overlay solid shading (no hidden-line removal or picking).

`window.CadyxViewport.createViewer(canvas)` is shared with PLY preview and returns:

- `load({positions, triangles, bounds, lines?, points?}, {reset = true})`;
  omit lines/points for a PLY point cloud. Use `reset: false` on document refresh.
- `render()` redraws after resize/visibility changes.
- `fit()` resets orientation and zoom to fit all scene bounds.
- `getCamera()` returns `{yaw, pitch, zoom, projection: "orthographic"}`.
- `setCamera(camera)` validates finite values, pitch within +/-1.45 radians,
  zoom within 0.25–5 and the orthographic projection before updating.

Camera vertical is Z; yaw rotates around Z, pitch changes elevation.
Drag right/left orbits horizontally; drag down increases elevation and drag up
decreases it. Wheel zooms; double-click or **Вписать** restores the fitted view.
Camera stays local to the viewer, requires no document mutation and is retained
across revisions of the same project. New projects/files reset it.

Validation: `tests/test_rendering.py` checks kernel mesh winding/volume/bounds,
sketch rendering, parameter refresh and read-only persistence;
`tests/test_web_api.py` checks both endpoints and unchanged inspection state;
`tests/test_viewport.cjs` checks Z-up projection, vertical drag, zoom, camera
validation, fit and camera preservation. See [run/check commands](../README.md).
