# Advanced solid modeling

CADYX extends the v7 document/command contract with OCCT-backed operations intended for complex molded housings and similar single-part models. The browser remains observation-only; agents author these features through `cadyx_execute_command` / the Agent API.

## Direct section loft workflow

Use `create_section_sketch` to create editable XZ sections at explicit Y offsets, draw closed profiles with the normal sketch commands, then call `loft_profiles` with the ordered sketch ids and matching strictly increasing offsets. Unlike `loft_sketches`, this workflow does not require the sketches to originate from an imported STEP section.

`create_sketch_from_body_section` can extract closed sections from any editable B-rep body on X, Y or Z planes for downstream reconstruction or feature authoring.

## Wall and shape operations

- `shell_body`: remove selected faces and create a signed-thickness hollow solid.
- `offset_body`: offset the complete solid skin.
- `draft_faces`: apply molding draft relative to a pull direction and neutral plane.
- `chamfer_edges`: constant edge chamfer.
- `variable_fillet_edges`: linear start/end-radius fillet.
- Existing `fillet_edges` remains available for constant-radius fillets.

Topology inspection now includes faces as well as edges. Each reported face/edge carries a `semantic_key` generated from current geometric properties. Advanced commands accept `face_keys` / `edge_keys` as alternatives to raw indices. These keys are guards against blind index selection, not permanent topological names: after an upstream geometry change, inspect topology again before applying a downstream topology-selected feature.

## Constructive features

- `hole` / `boss`: arbitrary-axis cylindrical subtractive/additive features.
- `pad_sketch_to_body`: extrude a closed sketch and fuse it into an existing body.
- `rib_from_sketch`: additive thin-profile rib workflow.
- `pocket_sketch`: extrude a closed sketch and subtract it from a body.
- `mirror_body`: mirror a complete body across an arbitrary plane.
- `linear_pattern_body` / `circular_pattern_body`: transform and fuse repeated instances; the result must remain one valid solid.

All advanced solid results are persisted as validated OCCT `imported_brep` geometry. Existing rendering, topology inspection, booleans and verified STEP/STL delivery therefore continue through the same native-kernel path.

## Native runtime

The advanced bridge is compiled into `cadyx_kernel` from `native/advanced.cpp` and `native/advanced_topology.cpp`. After pulling a commit that adds these sources, rebuild the local Windows runtime:

```powershell
.\scripts\setup-runtime.ps1
```

A host still running an older DLL intentionally rejects advanced commands with a stale-runtime error instead of silently degrading the operation.

## Modeling scope

These features target exact single-solid B-rep workflows such as molded lamp housings: section lofts for the primary envelope, shell/offset for wall thickness, draft/fillets/chamfers for molded transitions, and pads/ribs/pockets/bosses/holes for internal and mounting details. Surface-only bodies and a separate trim/sew surface document model are not introduced; the workflow stays within validated closed solids so CADYX's existing delivery and revision guarantees remain intact.
