# CADYX Document Model v10 — Assembly mates and rigid kinematics

Document v10 upgrades the independent assembly graph from v1 to **v2**,
adding a `mates` array. Earlier documents migrate on read; v9 instances,
sources, transforms and envelope v1 remain unchanged. Edits use the existing
leased Command System and atomic revision checks, including MCP
`cadyx_execute_command` and Agent API
`POST /api/v1/agent/projects/{id}/commands`.

## Mate record

```json
{
  "id": "mate:<uuid>",
  "name": "Shaft pivot",
  "kind": "revolute",
  "a_instance_id": "instance:<uuid>",
  "b_instance_id": "instance:<uuid>",
  "frame_a": {"translation": [0, 0, 0], "rotation": [1, 0, 0, 0]},
  "frame_b": {"translation": [0, 0, 0], "rotation": [1, 0, 0, 0]},
  "angle_deg": 30,
  "offset_mm": 0,
  "orientation": [1, 0, 0, 0]
}
```

Two distinct **sibling** instances (same `parent_id`) can share a mate.
Frames are local to each instance; the mate Z axis is the joint axis.
Coordinates are mm and degrees; quaternions are normalized `[w,x,y,z]`.
Defaults are identity frames, zero angle/offset and identity orientation.
All saved fields are required after normalization. No source B-rep is copied
or Boolean-fused.

Kinematic semantics and free relative degrees of freedom (DOF):

| Type | Relative relation between joint frames | DOF |
| --- | --- | --- |
| `fixed` | Frames coincide in position and orientation | 0 |
| `coincident` | Origins coincide, free orientation set by `orientation` | 3 |
| `coaxial` | Joint Z axes coincide, axial offset and twist can vary | 2 |
| `revolute` | Coincident joint frames except Z twist `angle_deg` | 1 |
| `slider` | Coincident joint frames except axial `offset_mm` | 1 |
| `cylindrical` | Axial offset and Z twist explicitly controllable | 2 |

Coaxial and cylindrical have equivalent instantaneous geometric DOF;
`cylindrical` identifies an explicitly controlled two-coordinate joint,
while `coaxial` indicates an axis-alignment mate. No dynamics are inferred.
For `coincident`, only `orientation` selects the freely rotating pose.
Unused motion fields must be zero; non-coincident orientation is identity.

For each mate the exact rigid relation is
`world(A) * frame_a * motion = world(B) * frame_b`.
The motion is a Z twist and/or translation for the selected mate type.
These `world` transforms are local to the common parent subassembly;
nested subassemblies continue to compose rigid transforms during rendering.

## Commands and diagnostics

- `add_assembly_mate`: required `kind`, `a_instance_id`, `b_instance_id`;
  optional `name`, `frame_a`, `frame_b`, `angle_deg`, `offset_mm`,
  `orientation`. Solves connected instance placements after insertion.
- `set_assembly_mate`: required `mate_id` and at least one mutable field
  from the optional parameters above. Solves with instance A as the anchor.
- `remove_assembly_mate`: required `mate_id`; retains last instance poses.
- `solve_assembly_mates`: optional `anchor_instance_id`; recalculates
  all connected poses, returns joint and component diagnostics.
- `transform_assembly_instance`: when mated, its edited instance becomes
  the anchor and the connected components move consistently.
- `remove_assembly_instance`: fails while mates still reference it.

The read-only standard project inspection includes
`assembly_kinematics`, calculated against the same saved document revision:
`instance_poses`, `anchor_instance_ids`, `component_count`,
`global_dof`, `internal_dof`, `redundant_mate_ids`, `status`, and
translation/rotation tolerances. Each disconnected component has six **global**
ungrounded freedoms. `internal_dof` is the sum of the spanning-tree joint
freedoms; `fully_mated` therefore means zero relative freedoms, **not**
global grounding. `underconstrained` means relative freedoms remain and
`overconstrained` flags consistent redundant cycle edges. This DOF report is
a tree count, not a Jacobian rank proof for arbitrary closed mechanisms.

The solver traverses mate edges deterministically by ID, anchors the
requested instance or one stable root per disconnected component, and checks
closure of every remaining edge. A cyclic mate graph whose stored transforms
do not agree within **1e-6 mm** and **1e-6 rad** is rejected explicitly
with the conflicting mate/instance IDs. Failed commands are rejected by the
existing isolated Core transaction and do not persist intermediate placements.
An inspection of inconsistent saved mates also fails rather than reporting a
plausible pose. Rebuilds of shared source parts do not move joint frames or
recompute expensive source geometry.

**Scope limits:** No simultaneous nonlinear closed-loop mechanism solving;
overconstraint denotes redundancy in the chosen rigid relation model, not a
full rank-based kinematic analysis. No automatic B-rep contact detection,
feature-face-based mate references, cross-parent mates, motion sweeps,
dynamic simulation, globally fixed world anchor, unit conversion,
collision/clearance proof or human browser editing. Source-only regression
coverage is present; local execution was deliberately skipped and platform
CI/native compatibility remains unverified.
