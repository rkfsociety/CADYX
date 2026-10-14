# Document Model v3

The project envelope remains `cadyx-project` version 1. New documents use version
3. Reading documents v1 or v2 upgrades their version in memory without changing ids,
objects, revision, history or files. The next successful command atomically saves
the upgraded document with its new history entry. Failed commands leave the file
unchanged. Unknown versions are rejected. A v1 document supports only bodies and
features. Older CADYX hosts cannot read document v3; this is a forward schema
change, not a claim of backwards host compatibility. Sequential migration steps are defined in `cadyx/migrations.py`; see the
[project migration contract](project-format-v1.md#migration-contract).

## Supported sketch objects

All objects live in `document.objects`. Each id is a canonical lowercase typed
UUID, unique across the document. Object types are body, feature, sketch, point,
line, circle and constraint. Existing body/feature relationships remain intact.

| Type | Persisted fields in addition to id/type | Meaning |
| --- | --- | --- |
| sketch | name, plane | Name of 1–120 characters; plane is `XY` or `XZ` |
| point | sketch_id, x, y | Finite coordinates in the owning sketch plane |
| line | sketch_id, start_id, end_id | References two points; length must exceed 1e-7 |
| circle | sketch_id, center_id, radius | Point center and finite positive radius |
| constraint | sketch_id, kind, references, value | Explicit invariant checked on every load and command |

Membership is defined by `sketch_id`, with no duplicated membership lists.
Point/line/circle references must resolve to the appropriate object type in the
same sketch. Editing coordinates preserves ids and references. There are no
references to transient kernel topology. An `XZ` sketch maps stored `x,y`
coordinates to global `X,Z`; `XY` retains its existing mapping.

## Constraint semantics

Constraints check geometry; they do not solve or move geometry. The absolute
tolerance is 1e-7 in the stored coordinate units, with no relative tolerance.
CADYX does not yet assign physical units. Unsupported or unsatisfied constraints
are rejected rather than retained as unresolved state.

| kind | Ordered references | Invariant |
| --- | --- | --- |
| horizontal | one line | Endpoints have equal y |
| vertical | one line | Endpoints have equal x |
| coincident | two distinct point ids | Euclidean distance is zero within tolerance |
| distance | two distinct point ids | Euclidean distance equals finite non-negative value |
| radius | one circle | Radius equals finite positive value |

## Command API

Both existing Web and Agent command endpoints accept these command objects under
their respective human/agent edit leases. Results below appear inside
`history_entry.result`; all commands return the committed document as usual.

- `create_sketch`: optional `name` (default Sketch), `plane` (default XY).
  Returns `sketch_id`.
- `add_sketch_element`: `sketch_id`, `element_type` (point, line or circle),
  and that element's fields from the table. Returns `object_id`.
- `add_sketch_polyline`: `sketch_id`, `points` (`{x, y}` coordinates), and
  optional `closed` (default `false`). Adds one open chain of 2–512 points, or
  one closed polygon of 3–4096 points, in a single command. Adjacent points,
  including the closing pair for a polygon, must be more than 1e-7 apart.
  Returns the created `point_ids`, `line_ids`, and `closed` state. These are
  ordinary point and line objects in the document.
- `add_sketch_rectangle`: `sketch_id`, lower-left `x` and `y`, positive `width`
  and `height`. Adds a closed four-sided profile as ordinary points and lines
  and returns their ids in one command.
- `add_sketch_circle`: `sketch_id`, center `x` and `y`, and positive `radius`.
  Adds the center point and circle together and returns `point_id` and `circle_id`.
- `add_sketch_constraint`: `sketch_id`, `kind`, ordered `references`, and `value`
  for distance/radius. Returns `constraint_id`.
- `update_sketch_elements`: non-empty `updates` list of
  `{object_id, changes}`. Points accept x/y, circles accept radius. One update per
  id; changes cannot modify ownership or references. The complete batch is checked
  together so constrained points can move in one atomic command.
  Returns `object_ids`.
- `remove_sketch_object`: `object_id`. No cascading deletion: a referenced object
  cannot be removed. Remove constraints, lines/circles, points, then the sketch
  as appropriate. Returns `object_id`.
- `import_step`: `file` names a direct `.step` or `.stp` file in the current
  CADYX project folder; optional `name` overrides the body name. It imports one
  solid through OCCT, stores its editable B-rep in the document, and returns the
  body/feature ids, source SHA-256 and byte length, and source geometry metrics.
  The source file is preserved. Paths cannot leave the current project folder.

Example: create a sketch; add points at (0, 0) and (3, 0); add a line referencing
their returned ids; add a horizontal constraint referencing the line id. Changing
only the second point's y to 1 is rejected without changing revision, history or
saved bytes. Changing both y coordinates to 1 in one batch succeeds with all ids
and references preserved.

## Engineering profiles

`add_sketch_involute_gear` accepts an empty `XY` `sketch_id`, positive `module`,
integer `tooth_count` from 3 to 128, `pressure_angle_degrees` from 5 through 45
(default 20), optional `center_x`/`center_y`, `rotation_degrees`, and non-negative
`backlash` less than half the circular pitch. The minimum tooth count is checked
against the rack-undercut limit for the selected pressure angle. It adds one
closed polygon as ordinary sketch points and lines and returns the calculated
pitch, base, tip and root radii in `gear_parameters`. It models an unshifted
external spur gear: `d=mz`, `db=d cos(alpha)`, `da=m(z+2)`, `df=m(z-2.5)`.
Mating gears use center distance `m(z1+z2)/2` and a half-tooth-pitch phase offset.
The flanks are sampled from the involute curve at 12 roll intervals; the saved
sketch is therefore a line-segment approximation, not an analytic curve or a
linked parametric gear feature. Profile shift, internal gears, helical gears,
and cutter-generated root fillets are not represented.

`create_metric_thread` accepts positive `major_diameter`, `pitch`, and `length`
(at least one pitch and at most 64 pitches), optional `handedness` (`right` by
default, or `left`) and optional `name`. It creates a single-start external ISO
metric 60-degree thread as one editable OCCT solid, trims it to Z=0 through the
requested length, and saves a `metric_thread` geometry descriptor with a linked
feature. For the basic external profile, the minor diameter is
`d3 = d - 1.226869P`; the rounded root approximates `R = sqrt(3)P/12` with
eight segments per section corner. `set_parameter`
can change `major_diameter`, `pitch`, `length`, or `handedness`; each edit rebuilds
the B-rep atomically. Internal threads, multiple starts, tolerance classes and
thread run-outs are not modeled. CADYX documents do not assign physical units;
module, diameter and pitch use the document's coordinate units.

These additions use the existing document v3 object and feature contracts and
do not require a schema migration. Gear geometry remains ordinary editable
sketch geometry; thread parameters remain on its feature. Reference formulas:
[ISO 724:2023](https://cdn.standards.iteh.ai/samples/85104/9815392ad8904c9aa23cf7727e7b1dde/ISO-724-2023.pdf),
[spur gear geometry](https://technische-antriebselemente.de/en/tools/gear-geometry-calculator/).

## Sketch-driven solid features

`extrude_sketch` accepts `sketch_id`, positive `depth`, optional `name`, and
optional boolean `centered`. A centered extrusion spans equally on both sides
of its sketch plane. Otherwise it starts at the plane and extends along the
positive plane normal. It extrudes one closed line loop on `XY` along global Z
or on `XZ` along global Y. `revolve_sketch` accepts `sketch_id`, optional
`angle_degrees` (default 360,
range greater than 0 through 360), and optional `name`. It revolves one closed
profile on `XZ` about global Z. Revolution profiles must stay on one side of the
axis and touch it so OCCT can form a solid. A sketch with one circle can be used
as an extrusion profile; it is sampled into a 128-edge polygon. Profile line
loops support 3–4096 edges. Each operation accepts one contour; holes and
multi-contour profiles are not supported.

The command stores an OCCT B-rep descriptor plus a feature that references its
source sketch and operation parameter. Editing source points rebuilds the linked
body in the same atomic command. `set_parameter` can change extrusion depth,
revolution angle, sweep radius, or fillet radius. Removing a sketch used by a solid feature is rejected. Profile
constraints still check the supplied geometry and do not solve it. The OCCT
adapter rebuilds and validates each saved solid and derives viewport triangles
from the B-rep. These are solids, not triangle-mesh imports.

`replace_sketch_profile` replaces the point/line loop of a selected sketch and
atomically rebuilds its dependent profile features. It accepts `sketch_id` and
3–4096 finite `{x, y}` points; sketches with circles or constraints are
rejected. Editing a sketch also rebuilds downstream derived solid features.

`replace_sketch_path` replaces a point-and-line sketch with one open connected
chain. It accepts `sketch_id` and 3–512 finite `{x, y}` points, creates one line
between each adjacent pair, and animates the path while it is drawn. A path sketch
cannot contain branches, loops, circles, or constraints.

`sweep_sketch` accepts `sketch_id`, a positive circular section `radius`, and
optional `name`. It sweeps a circle along the sketch's open line chain, fitting a
C2 B-spline spine within the OCCT approximation tolerance. XY and XZ paths are
supported; an XZ path is vertical in global Z. The resulting `swept_pipe` is a
closed OCCT solid, remains linked to the source sketch, and rebuilds when path
points or the radius change. The command transition progressively reveals the
pipe along the path.

`fuse_bodies` accepts `base_body_id`, `tool_body_id`, and optional `name` for two
intersecting OCCT B-rep solids. The native Boolean operation must produce one
connected valid solid. It supports profile solids, swept pipes, and prior
fuse/fillet results; imported triangle meshes are not B-rep inputs. The resulting
`fused_solids` descriptor retains both source geometry descriptors and rebuilds
when either source changes. Legacy `fused_profiles` descriptors remain supported.

`GET /api/v1/agent/projects/{project_id}/topology?body_id=...` inspects a body's
current unique B-rep edges for downstream edge selection. Each edge has a
traversal `index`, length, center of mass, and curve type. Indices are valid for
that exact current topology and are not persistent names; inspect again after
any upstream edit before selecting edges.

`fillet_edges` accepts `body_id`, 1–128 distinct `edge_indices`, a positive
`radius`, and optional `name`. It creates a new body with an OCCT rolling-edge
fillet and can round the mug rim, base, or a fused handle junction.
`set_parameter` can edit its radius. The `filleted_solid` descriptor retains the
source B-rep, selected current edge indices, and radius. If an upstream edit
changes topology or makes the requested radius impossible, the dependent rebuild
fails atomically and leaves the saved model unchanged.

Sweep sketches used by a feature and all inactive sketches are hidden from the
user-facing tree and viewport. Fillet and fuse source bodies remain in document
history for editing but are hidden from the viewport and object tree; only
unconsumed results are displayed. Solid source-sketch edits rebuild downstream
sweep, fillet, and fuse features in document order.

## STEP import and editable B-reps

`import_step` reads a direct `.step` or `.stp` source file from the current
project folder through OCCT's `STEPControl_Reader`. It accepts a single solid and
stores an OCCT B-rep serialization in the body's `imported_brep` descriptor;
viewport triangles are only a display tessellation. The project feature records
the source filename, SHA-256, byte length, and source geometry metrics. The saved
B-rep is self-contained and remains usable if the source file later moves. Source
and serialized B-rep payloads are limited to 64 MiB each.

The source metrics include volume, surface area, bounding coordinates, unique
solid/face/edge/vertex counts, B-rep validity, and whether OCCT repaired the shape
within a precision of 1e-7 and a maximum tolerance of 1e-5 model units. CADYX
checks the B-rep serialization round trip against those measures. If OCCT cannot
repair an imported solid, CADYX preserves the exact transferred shape and marks
`brep_valid` false rather than changing it silently. Such a shape can still be
displayed when OCCT can tessellate it, but Boolean and fillet operations require
a valid B-rep and may reject it.

`resize_imported_thread` accepts an imported `body_id`, the `x_start` and `x_end`
of a threaded interval, its `major_diameter`, a positive `diameter_delta`, and
optional `axis_origin_y` / `axis_origin_z` coordinates for a thread whose axis
is parallel to global X. The selected interval must contain only the thread.
When needed, OCCT repairs self-intersecting wires on invalid planar faces, then
cuts and radially scales the interval about the specified axis before fusing it
back into the source. The result must be one valid closed solid; the source body
is retained in feature history and hidden from the viewport.

`transform_imported_brep` accepts an imported B-rep `body_id`, a three-component
`rotation_axis`, an `angle_degrees` value, and a three-component `translation`.
It rotates about the global origin, applies the translation, and saves the result
as another editable imported B-rep feature. Rigid transforms preserve the
source volume and topology; consumed source bodies remain in history.

## Scan import and editable triangle meshes

`import_scan` reads one direct PLY file from a workspace folder, preserves the
source, and creates a body with a `cadyx-mesh-v1` triangle mesh plus a
`reconstruct_scan` feature. The command is submitted through the existing Web or
Agent command endpoint under that project's edit lease:

```json
{
  "type": "import_scan",
  "folder": "kobras1backcover",
  "file": "Merge_02_pc.ply",
  "name": "Scanned cover",
  "parameters": {
    "voxel_size": 0.75,
    "target_triangles": 30000,
    "outlier_neighbors": 20,
    "outlier_std_ratio": 2.0,
    "poisson_depth": 8
  }
}
```

`folder` and `file` must resolve to a direct regular `.ply` file in the project
workspace; traversal and symlinks are rejected. The command stores the source
relative path and SHA-256 in the feature. Changing a processing parameter
re-reads the source and rejects a changed file. The PLY remains read-only; the
reconstructed mesh is self-contained in `project.json`, so it still opens if the
source is later unavailable. CADYX also writes the processed mesh as a binary
little-endian PLY beside the source. Its name is content-addressed
(`{source-stem}.cadyx-{mesh-sha256-prefix}.ply`), and the feature stores that
relative output location, hash, and size. Publication is atomic and never
replaces an existing file: a byte-identical output is reused, while a conflict
fails the command. Temporary files are hidden from workspace listings.

Both PLY triangle meshes and point clouds are supported. Meshes are cleaned and
simplified; point clouds are voxel-downsampled, filtered for statistical
outliers, given consistently oriented normals, and reconstructed with screened
Poisson. Output is an editable triangle mesh, not an OCCT B-rep. The packed
float32 vertices and uint32 triangle indices are compressed, bounded, and
checksum-validated by `GeometryKernel`; command history stores only ids,
parameters, and counts, not a second mesh copy.

The defaults are `voxel_size=0.75` in the source's coordinate units,
`target_triangles=30000`, `outlier_neighbors=20`, `outlier_std_ratio=2.0`, and
`poisson_depth=8`. `voxel_size` and `target_triangles` apply to both input kinds;
the outlier and Poisson settings apply to point clouds. `set_scan_parameter`
accepts `feature_id`, `name`, and `value` to rebuild from the unchanged source.
Editable parameters are bounded: voxel size 0.00001–10000, target triangles
100–100000, outlier neighbors 4–128, outlier standard ratio 0.1–5, and Poisson
depth 5–9. The CPU processor caps itself at four threads and downsamples point
clouds to at most 100,000 points; input PLYs are limited to 512 MiB, 2 million
vertices, and 5 million faces. The processor is the hash-pinned CPU Open3D
0.20.0 SDK behind a native C ABI bridge, so it does not depend on CADYX's Python
ABI. See [runtime setup](../../README.md#run-locally).

The Core emits `agent.activity` SSE snapshots as the scan moves through source
validation, mesh or point-cloud cleanup, reconstruction, validation, sidecar
publication, and project save. Agent Runtime/API model inspections and
viewport requests are recorded in the same activity stream. The read-only
browser animates model changes and displays one compact action/result status;
it does not show operation stages or a raw command transcript.
`GET /api/v1/projects/{id}/activity` and the Agent API equivalent return the
latest 128 in-process operation snapshots so an open or reloaded browser can
resume displaying them. Activity state is process-local, like the SSE stream.

## Browser inspection and Agent API editing

The browser is a read-only detail viewer. It shows the shared viewport full
window by default; the model tree and properties are available in collapsed
docks. The tree lists bodies and their features, plus only the sketch currently
being edited and its points, lines, circles and constraints. Inactive sketches
and sketches consumed by solid features are hidden from the tree and viewport.
The viewport renders supported bodies and smoothly animates active sketch,
profile-feature, sweep, fuse, and fillet changes; view controls do not edit
document state.

Agents create and edit models through the Agent API under an agent edit lease.
Projects are also created through the Agent API. Agents can get a server-rendered
PNG image without a browser from
`GET /api/v1/agent/projects/{id}/viewport.png`; the regular viewport API remains
available as JSON scene data. The command objects above create boxes and XY/XZ
sketches, add sketch geometry and checked constraints, extrude or revolve closed
profiles, sweep circular sections along open sketches, fuse solid B-reps, fillet
selected B-rep edges, change feature parameters or sketch elements, and remove
unreferenced sketch objects. Every command uses the shared Command System, and the complete
document is validated before the atomic save. Constraints check the supplied
result but do not solve or move geometry. The browser exposes no
project or model creation, editing, or deletion controls.
