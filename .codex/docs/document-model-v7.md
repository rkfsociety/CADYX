# Document Model v7

The project envelope remains `cadyx-project` version 1. New documents use version
7. Reading documents v1 through v6 upgrades their version in memory without changing ids,
objects, revision, history or files. The next successful command atomically saves
the upgraded document with its new history entry. Failed commands leave the file
unchanged. Unknown versions are rejected. A v1 document supports only bodies and
features. Older CADYX hosts cannot read document v7; this is a forward schema
change, not a claim of backwards host compatibility. Sequential migration steps are defined in `cadyx/migrations.py`; see the
[project migration contract](project-format-v1.md#migration-contract).

## Supported sketch objects

All objects live in `document.objects`. Each id is a canonical lowercase typed
UUID, unique across the document. Object types are body, feature, sketch, point,
line, circle, ellipse, spline and constraint. Existing body/feature relationships remain intact.

| Type | Persisted fields in addition to id/type | Meaning |
| --- | --- | --- |
| sketch | name, plane | Name of 1–120 characters; plane is `XY` or `XZ` |
| point | sketch_id, x, y, optional construction | Finite coordinates; construction points are reference geometry |
| line | sketch_id, start_id, end_id, optional construction/extent | References two points; length must exceed 1e-7; extent is `segment` (default), `infinite`, or `centerline` |
| circle | sketch_id, center_id, radius, optional start_id/end_id/sweep_radians | Exact full circle or signed partial circular arc; arc endpoints lie on the radius and match the sweep |
| ellipse | sketch_id, center_x, center_y, major_radius, minor_radius, rotation_radians, optional start_id/end_id/start_angle_radians/sweep_radians | Exact analytic ellipse or signed partial arc; for an arc, endpoint references lie on the curve and match the signed parameter sweep |
| spline | sketch_id, point_ids, optional construction | Open centripetal Catmull–Rom curve interpolating 3–128 ordered same-sketch points |
| constraint | sketch_id, kind, references, value | Explicit invariant checked on every load and command |

Membership is defined by `sketch_id`, with no duplicated membership lists.
Point/line/circle references must resolve to the appropriate object type in the
same sketch. Editing coordinates preserves ids and references. There are no
references to transient kernel topology. An `XZ` sketch maps stored `x,y`
coordinates to global `X,Z`; `XY` retains its existing mapping.

Construction points and lines remain visible in the sketch scene, but are not
included in closed profiles or sweep paths. Infinite lines render across the
viewport through their two defining points; centerlines and construction lines
render dashed. Optional fields preserve compatibility with documents that
pre-date construction geometry.

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
- `add_sketch_point` / `add_sketch_construction_point`: `sketch_id`, finite
  `x`, `y`. Construction points are excluded from profiles and sweep paths.
- `add_sketch_segment`, `add_sketch_line`, `add_sketch_infinite_line`,
  `add_sketch_centerline`, and `add_sketch_construction_line`: `sketch_id`,
  distinct finite `start` and `end` coordinate objects (`{x, y}`). Infinite
  lines and centerlines are excluded from profiles and sweep paths; centerlines
  and construction lines are dashed in the viewport.
- `add_sketch_element`: `sketch_id`, `element_type` (point, line or circle),
  and that element's fields from the table. Returns `object_id`.
- `add_sketch_polyline`: `sketch_id`, `points` (`{x, y}` coordinates), and
  optional `closed` (default `false`). Adds one open chain of 2–512 points, or
  one closed polygon of 3–4096 points, in a single command. Adjacent points,
  including the closing pair for a polygon, must be more than 1e-7 apart.
  Returns the created `point_ids`, `line_ids`, and `closed` state. These are
  ordinary point and line objects in the document.
- `add_sketch_line_chain`: `sketch_id`, `points` (`{x, y}` coordinates).
  Adds one open chain of 2–512 points; use `add_sketch_polyline` when a closed
  profile is required.
- `add_sketch_rectangle`: `sketch_id`, lower-left `x` and `y`, positive `width`
  and `height`. Adds a closed four-sided profile as ordinary points and lines
  and returns their ids in one command.
- `add_sketch_rectangle_two_corners`: `sketch_id`, opposite-corner `x1`, `y1`,
  `x2`, `y2`; both sides must exceed 1e-7. `add_sketch_rectangle_center` uses
  `center_x`, `center_y`, positive `width` and `height`.
  `add_sketch_rectangle_three_points` takes `points` as three consecutive
  `{x, y}` corners and checks perpendicular adjacent sides. `add_sketch_rotated_rectangle`
  uses `center_x`, `center_y`, positive `width`/`height`, and `angle_degrees`.
  `add_sketch_parallelogram` takes three consecutive non-collinear `{x, y}`
  corners. `add_sketch_regular_polygon` uses `center_x`, `center_y`, integer
  `side_count` (3–512), positive `radius`, optional `rotation_degrees` (default
  0) and `radius_mode` (`inscribed` default or `circumscribed`). Each command
  creates a closed point/line profile and returns its point and line ids.
  `add_sketch_inscribed_polygon` fixes the circumradius; `add_sketch_circumscribed_polygon`
  fixes the inradius. Both expose the same center, side count and optional rotation.
  `add_sketch_polygon_from_center` takes the center and first vertex coordinates
  directly; the vertex sets both the circumradius and initial rotation.
- `add_sketch_circle`: `sketch_id`, center `x` and `y`, and positive `radius`.
  Adds the center point and circle together and returns `point_id` and `circle_id`.
- `add_sketch_circle_diameter`: `sketch_id`, center `x`/`y`, and positive
  `diameter`. `add_sketch_circle_two_points` takes `first` and `second` `{x, y}`
  coordinates as opposite ends of a diameter. `add_sketch_circle_three_points`
  takes three distinct non-collinear coordinate objects. All create an exact
  circle object and its center point, returning `point_id` and `circle_id`.
- `add_sketch_ellipse`: `sketch_id`, `major_axis_start` and `major_axis_end`
  coordinate objects defining the endpoints of the major axis, and positive
  `minor_radius`. Half the major-axis length must be at least the minor radius.
  Stores one exact analytic ellipse and returns `ellipse_id`, center and radii.
- `add_sketch_ellipse_center`: `sketch_id`, `center` coordinate object, positive
  `major_radius` and `minor_radius`, and optional `rotation_degrees` (default 0).
  The major radius must be at least the minor radius. Stores one exact analytic
  ellipse and returns `ellipse_id`, center and radii.
- `add_sketch_ellipse_three_points`: `sketch_id`, `center`, `major_axis_endpoint`
  and `minor_axis_endpoint` coordinate objects. The two endpoint vectors from
  center must be perpendicular within 1e-7 relative tolerance; their lengths
  are the radii and the major one must be at least the minor one. Stores one
  exact analytic ellipse and returns `ellipse_id`, center and radii.
- `add_sketch_elliptical_arc`: `sketch_id`, `center`, positive `major_radius` and
  `minor_radius`, required `start_angle_degrees` and signed `sweep_degrees`, and
  optional `rotation_degrees` (default 0). The major radius must be at least the
  minor radius; sweep must be non-zero and less than 360 degrees in magnitude.
  Stores the exact analytic arc and its two endpoint points, returning
  `ellipse_id`, `arc_id`, `ellipse_ids`, `point_ids`, and endpoint coordinates.
- `add_sketch_spline`: `sketch_id`, 3–128 ordered `{x, y}` points, and optional
  boolean `construction` (default false). Adjacent points must be distinct. The
  open centripetal Catmull–Rom curve passes through every supplied point; its
  first and last points are the curve endpoints. The sketch stores stable point
  references, and the scene samples the curve at 24 segments per span. Returns
  `spline_id`, `point_ids`, `point_count`, and `closed: false`. A spline can form
  part of a closed profile and can be used as an open sweep path; control points
  are interpolated points, not a B-spline control polygon.
- `add_sketch_circle_tangent_to_line`: `sketch_id`, a same-sketch `line_id`,
  `tangent_point` (`{x, y}`), positive `radius`, and optional `side` (`1`
  default or `-1` relative to the line direction). The contact point must lie
  on a finite line segment, or on the support of an infinite line. Creates an
  exact circle whose center is offset by the requested radius along the chosen
  line normal.
- `add_sketch_circle_tangent_to_two_lines`: `sketch_id`, distinct same-sketch
  `first_line_id` and `second_line_id`, positive `radius`, and optional
  `first_side`/`second_side` (each `1` by default or `-1` relative to the
  corresponding line direction). The side pair selects one of four
  angle-bisector solutions. Parallel lines are rejected, and contact points
  must lie within finite line segments; infinite lines use their full support.
  Returns the circle and both exact contact coordinates.
- `add_sketch_circle_tangent_to_three_lines`: `sketch_id` and three distinct
  same-sketch line ids, with optional `first_side`, `second_side`, and
  `third_side` (each `1` by default or `-1` relative to the corresponding line
  direction). The three line offsets jointly solve the center and positive
  radius. Degenerate side combinations are rejected; contact points must lie
  within finite segments. Returns the solved radius and all contact coordinates.
- `add_sketch_arc_center`: `sketch_id`, `center`, `start`, and `end` coordinate
  objects, with optional boolean `clockwise` (default false). Start and end
  must lie on the same circle and be distinct from each other and the center.
  Creates an exact signed circular arc; the default sweep is counterclockwise.
- `add_sketch_arc_three_points`: `sketch_id`, `start`, `through`, and `end`
  coordinate objects. All three points must be distinct and non-collinear. The
  command solves their unique circumcircle and chooses the signed sweep that
  passes through `through`, returning the exact arc, center and radius.
- `add_sketch_arc_start_center_end`: the same exact geometry as
  `add_sketch_arc_center`, with parameter emphasis/order `start`, `center`,
  `end`; optional `clockwise` selects sweep direction.
- `add_sketch_arc_radius`: `sketch_id`, `start`, `end`, and positive `radius`;
  endpoint distance cannot exceed the diameter. `large_arc` (default false)
  selects the minor or major solution, and `clockwise` (default false) selects
  its signed direction.
- `add_sketch_arc_tangent`: `sketch_id`, same-sketch `line_id`, `tangent_point`,
  positive `radius`, and signed `sweep_degrees` strictly between -360 and 360
  (excluding zero). `side` (default 1 or -1) selects the circle center side of
  the directed line. The tangent point must lie on the finite segment or, for
  an infinite line, its support. The arc starts at that point and its initial
  tangent is parallel to the selected line.
- `add_sketch_rounded_rectangle`: `sketch_id`, lower-left `x`,`y`, positive
  `width`,`height`, and positive `corner_radius` less than half both side lengths.
  Creates exact quarter-circle arcs and tangent line segments as sketch geometry;
  profile extraction samples arcs at no more than 2.8125 degree intervals.
- `mirror_sketch_elements`: `sketch_id`, non-empty `object_ids`, and `axis_start`
  / `axis_end` coordinate objects (`{x, y}`). Copies selected points, lines,
  circles, ellipses and splines reflected across the arbitrary in-plane axis; its
  endpoints must be more than 1e-7 apart. Selecting a line or circle also copies
  its endpoint or center point dependencies; each copied component gets new
  point references. Selecting a spline copies its ordered points. Ellipses are reflected analytically. Constraints are not
  copied. Returns the source closure ids and created point, line, circle,
  ellipse and object ids.
- `pattern_sketch_elements`: `sketch_id`, non-empty `object_ids`, `mode`
  (`linear` or `circular`), and `count` from 2 through 64 including the source.
  Linear mode uses `offset_x` / `offset_y` per instance. Circular mode uses
  `center_x`, `center_y` and `angle_step_degrees` per instance. It copies the
  the same point/line/circle/spline dependency closure as mirroring; ellipses are
  transformed analytically. Constraints are not copied. A linear offset must
  exceed 1e-7; repeated circular positions and
  results over 4096 created objects are rejected. The response groups created
  ids by object type and reports the source closure and total instance count.
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
axis and touch it so OCCT can form a solid. A sketch with one circle or full
ellipse can be used as an extrusion profile; each is sampled into a 128-edge
polygon. Elliptical arcs can join line and circular-arc edges to make a closed
profile; they are sampled at no more than 2.8125-degree intervals. Full ellipses
and elliptical arcs remain exact in the sketch and are sampled only for viewport
and profile representations. Spline curves remain defined by their ordered
interpolated points and are sampled at 24 segments per span for display and
profile use; sweep paths use four samples per span to respect the kernel's
512-point bound. Profile line loops support 3–4096 edges. Each
operation accepts one contour; holes and multi-contour profiles are not supported.

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

`cut_bodies` accepts `base_body_id`, `tool_body_id`, and optional `name`. The
native OCCT Boolean cut subtracts the tool from the base and requires one valid
remaining solid with positive volume. Both editable B-rep source descriptors
are retained in the `cut_solids` feature graph, and the result rebuilds when
either source changes. A tool that removes no material or splits the base into
multiple solids is rejected atomically. Triangle meshes are not B-rep inputs.

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

## Verified CAD delivery

`export_brep` runs through the existing leased command endpoint and accepts one
existing valid, closed, single-solid OCCT B-rep body. Triangle meshes and
invalid imported B-reps are rejected. `expected_revision` is required in the
request envelope so every artifact is bound to the exact saved model state:

```json
{
  "lease_id": "...",
  "expected_revision": 12,
  "command": {
    "type": "export_brep",
    "body_id": "body:...",
    "include_stl": true,
    "linear_deflection": 0.1,
    "angular_deflection": 0.5
  }
}
```

STEP is always written as an analytic OCCT STEP model and is re-imported before
publication. The round-trip must contain one valid solid and preserve source
volume within relative/absolute tolerances of 1e-8/1e-7 model units and all six
bounds within 1e-6. STEP files larger than 64 MiB are rejected because they
cannot pass the existing STEP import limit.

When `include_stl` is true, CADYX writes a binary STL with OCCT linear and
angular deflections. Defaults are 0.1 model units and 0.5 radians; supported
ranges are at least 0.001 model units and 0.01–1.0 radians. STL output is also
limited to 64 MiB. Before publication, CADYX checks that the serialized STL is
watertight and consistently oriented, computes its signed mesh volume and
relative error against the exact B-rep, and samples up to 64 evenly spaced
triangle centroids against the exact B-rep faces. The manifest labels this
one-sided surface deviation as sampled; it is not an exhaustive geometric
proof. STEP remains the exact authoritative output, and neither output proves
physical fit, strength, or printability.

STEP, optional STL, and `*.cad-delivery.json` manifest are written directly into
the current project folder. Names include a sanitized body name, source
revision, geometry identity, and output content hash. Each file is atomically
created without replacing existing files; an identical existing artifact is
reused and a name collision with different content fails the command. All
exports and checks complete before publication, and the manifest is published
last. The manifest records the source body/revision and canonical geometry
SHA-256, OCCT version, file names/hashes/sizes, source and re-imported STEP
metrics, tolerances, and optional STL checks and measurements.

The export command is recorded in project history and advances the command
revision, while its manifest's `source.document_revision` remains the revision
whose body was exported. Outputs can be listed and downloaded through the
existing project file APIs.

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
being edited and its points, lines, circles, ellipses and constraints. Inactive sketches
and sketches consumed by solid features are hidden from the tree and viewport.
The viewport renders supported bodies and smoothly animates active sketch,
profile-feature, sweep, fuse, and fillet changes; view controls do not edit
document state.

Agents create and edit models through the Agent API under an agent edit lease.
Projects are also created through the Agent API. Agents can get the browser's
exact main viewport PNG from
`GET /api/v1/agent/projects/{id}/viewport.png` when the browser has published a
current frame; without one, the API uses the standalone server-rendered fallback.
The regular viewport API remains available as JSON scene data. The command objects above create boxes and XY/XZ
sketches, add sketch geometry and checked constraints, extrude or revolve closed
profiles, sweep circular sections along open sketches, fuse solid B-reps, fillet
selected B-rep edges, change feature parameters or sketch elements, and remove
unreferenced sketch objects. Every command uses the shared Command System, and the complete
document is validated before the atomic save. Constraints check the supplied
result but do not solve or move geometry. The browser exposes no
project or model creation, editing, or deletion controls.
