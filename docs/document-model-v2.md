# Document Model v2

The project envelope remains `cadyx-project` version 1. New documents use version
2. Reading a document v1 upgrades its version in memory without changing ids,
objects, revision, history or files. The next successful command atomically saves
the upgraded document with its new history entry. Failed commands leave the file
unchanged. Unknown versions are rejected. A v1 document supports only bodies and
features. Older CADYX hosts cannot read document v2; this is a forward schema
change, not a claim of backwards host compatibility. Sequential migration steps are defined in `cadyx/migrations.py`; see the
[project migration contract](project-format-v1.md#migration-contract).

## Supported sketch objects

All objects live in `document.objects`. Each id is a canonical lowercase typed
UUID, unique across the document. Object types are body, feature, sketch, point,
line, circle and constraint. Existing body/feature relationships remain intact.

| Type | Persisted fields in addition to id/type | Meaning |
| --- | --- | --- |
| sketch | name, plane | Name of 1–120 characters; plane is `XY` |
| point | sketch_id, x, y | Finite coordinates in the XY plane |
| line | sketch_id, start_id, end_id | References two points; length must exceed 1e-7 |
| circle | sketch_id, center_id, radius | Point center and finite positive radius |
| constraint | sketch_id, kind, references, value | Explicit invariant checked on every load and command |

Membership is defined by `sketch_id`, with no duplicated membership lists.
Point/line/circle references must resolve to the appropriate object type in the
same sketch. Editing coordinates preserves ids and references. There are no
references to transient kernel topology. Sketches are independent of solid
features in this stage; extrusion and sketch-to-body operations are future work.

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

Example: create a sketch; add points at (0, 0) and (3, 0); add a line referencing
their returned ids; add a horizontal constraint referencing the line id. Changing
only the second point's y to 1 is rejected without changing revision, history or
saved bytes. Changing both y coordinates to 1 in one batch succeeds with all ids
and references preserved.

## Browser inspection and Agent API editing

The browser is a read-only detail viewer. It shows the shared viewport full
window by default; the model tree and properties are available in collapsed
docks. The tree lists bodies and their features, plus sketches and their points,
lines, circles and constraints. The viewport renders supported bodies and
sketch geometry; view controls do not edit document state.

Agents create and edit models through the Agent API under an agent edit lease.
Projects are also created through the Agent API. Agents can get a server-rendered
PNG image without a browser from
`GET /api/v1/agent/projects/{id}/viewport.png`; the regular viewport API remains
available as JSON scene data. The command objects above create boxes and XY
sketches, add sketch geometry and checked constraints, change box parameters or
sketch elements, and remove unreferenced sketch objects. Every command uses the
shared Command System, and the complete document is validated before the atomic
save. Constraints check
the supplied result but do not solve or move geometry. The browser exposes no
project or model creation, editing, or deletion controls.
