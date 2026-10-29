# Advanced solid modeling

[Полный русский перевод](advanced-solid-modeling.ru.md)

[Русский обзор](#русский-обзор) · [English reference](#english-reference)

## Русский обзор

### Сложное твердотельное моделирование

Контракт описывает редактируемые операции OCCT: loft по сечениям, оболочку, смещение, уклоны, фаски и переменные скругления. Геометрия создаётся через Agent API и общий Command System. Перед выбором граней и рёбер инспектируйте текущую топологию: semantic keys проверяют выбор относительно ревизии, но не являются постоянными идентификаторами. Ограничения рабочих плоскостей зависят от конкретной операции; общий переход на документ v12 не снимает их автоматически.

Это обзор, не полный перевод. Полные схемы, примеры и подробности контракта приведены на английском ниже.

## English reference

These OCCT-backed operations extend the earlier Document v8 feature model; they run in the **current v15 document** and support complex molded housings, machined parts, and similar single-part models. The [work-plane contract](sketch-workplanes.md) identifies which operations accept YZ/custom/face-attached sketches and which still require legacy planes. The browser remains observation-only for these single-part operations; agents author these features through `cadyx_execute_command` / the Agent API.

## Direct section loft workflow

Use `create_section_sketch` to create editable XZ sections at explicit Y offsets, draw closed profiles with the normal sketch commands, then call `loft_profiles` with the ordered sketch ids and matching strictly increasing offsets.

`loft_sketches` also accepts directly authored XY/Z-normal or XZ/Y-normal sketches. An ordinary named sketch without section metadata is the zero-offset station on its canonical axis; later stations specify matching `section_normal_axis` and `section_offset`. Creation, section replacement, persistence and parameter rebuild use the same defaults. Imported STEP sections are optional.

`create_sketch_from_body_section` can extract closed sections from any editable B-rep body on X, Y or Z planes for downstream reconstruction or feature authoring.

## Wall and shape operations

- `shell_body`: remove selected faces and create a signed-thickness hollow solid.
- `offset_body`: offset the complete solid skin.
- `draft_faces`: apply molding draft relative to a pull direction and neutral plane.
- `chamfer_edges`: constant edge chamfer.
- `variable_fillet_edges`: linear start/end-radius fillet.
- Existing `fillet_edges` remains available for constant-radius fillets.

Topology inspection includes faces as well as edges. Each reported face/edge carries a `semantic_key` generated from current geometric properties. Advanced commands accept `face_keys` / `edge_keys` as alternatives to raw indices. These keys are guards against blind index selection, not permanent topological names. Saved fillet/chamfer features also retain geometric edge references so dependent rebuilds can resolve the changed edge and refresh the stored indices. Inspect topology again when authoring a new topology-selected feature; unresolved or ambiguous references fail the atomic rebuild. See [Document Model v8](../.codex/docs/document-model-v8.md#rebuilding-topology-selected-edge-features).

The topology response is also the agent's measurement surface. In addition to body volume, surface area, bounds, and topology counts, current native runtimes report analytic data where OCCT exposes it:

- line edges: endpoints, line origin and direction;
- circular edges: radius and axis;
- elliptical edges: major/minor radii and axis;
- planar faces: plane origin and normal;
- cylindrical faces: radius and axis;
- conical faces: reference radius, semi-angle and axis;
- spherical/toric faces: center/axis and their radii.

This makes dimensions such as hole diameter, coaxiality cues, face orientation, body extents, and edge radii directly inspectable through the existing `cadyx_inspect_topology` MCP tool. Semantic fingerprints include these analytic properties when available.

## Constructive features

`add_sketch_geometry_batch` adds 1–128 rectangles, circles, circumscribed regular polygons, or closed polylines to one editable sketch in one atomic command. A regular-polygon `radius` is its inradius (apothem); `rotation_degrees` defaults to zero. The command is useful for keeping a design's construction in one sketch without issuing a command for each contour.

- `hole` / `boss`: arbitrary-axis cylindrical subtractive/additive features.
- `through_hole`: arbitrary-axis cylindrical hole with a cutter automatically extended across the current body bounds.
- `counterbore_hole`: a through or blind main bore plus a larger coaxial cylindrical recess.
- `countersink_hole`: a through or blind main bore plus an analytic conical countersink; the default included angle is 90 degrees.
- `threaded_hole`: an arbitrary-axis internal single-start ISO metric thread, created by subtracting CADYX's exact external metric-thread B-rep.
- `pad_sketch_to_body`: extrude a closed sketch and fuse it into an existing body.
- `rib_from_sketch`: additive thin-profile rib workflow.
- `pocket_sketch`: extrude a closed sketch and subtract it from a body.
- `mirror_body`: mirror a complete body across an arbitrary plane.
- `linear_pattern_body` / `circular_pattern_body`: transform and fuse repeated instances; the result must remain one valid solid.

The specialized hole commands keep the existing simple `hole` command backward compatible. Through cutters are resolved from the inspected body bounds at execution time and the resolved cutter origin/depth are retained in feature parameters for auditability. Counterbore/countersink commands reject no-op cuts and require the result to remain one closed solid.

`pad_sketch_to_body` and `pocket_sketch` also accept optional `profile_groups` for sketches with several disjoint regions. Each group has the form `{"outer_index": 0, "inner_indices": [1, 2]}`; indices refer to the sketch's closed-profile order. Every contour must occur exactly once. The outer contour defines one additive or cutting region, and its inner contours remain holes in that region. Separate groups are applied by the same feature, and the rebuilt result must remain one connected solid. Omitting `profile_groups` preserves the legacy single-outer-profile behavior.

## Arbitrary work-plane authoring

`extrude_profile_on_plane` creates a standalone exact B-rep from a local 2D closed profile on an arbitrary 3D plane. The command takes:

- `profile`: ordered local `{x, y}` profile points;
- `origin`: the world-space work-plane origin;
- `normal`: work-plane normal / positive extrusion direction;
- `x_axis`: local profile X direction, which must not be parallel to `normal`;
- `depth`, with optional `centered`.

CADYX orthonormalizes the supplied work-plane frame, builds the profile as a local XY extrusion, then applies an exact rigid OCCT transform. The normalized plane definition is stored with the feature. This intentionally adds arbitrary-plane solid authoring without changing the persisted v7 sketch schema: ordinary editable sketches remain XY/XZ, while the new feature is a direct local-profile operation intended for agent-driven reconstruction and reference-image workflows.

## Feature history and viewport behavior

Advanced commands preserve their source body and append a new result body/feature so the operation remains auditable. The live viewport suppresses consumed source bodies for shell, offset, chamfer, draft, fillet variants, body mirror/pattern, pads/ribs/pockets, and the high-level hole commands. This prevents the historical source B-rep from visually filling a subtractive result such as a hole.

All advanced solid results are persisted as validated OCCT geometry (normally an `imported_brep` at the extension boundary). Existing rendering, topology inspection, booleans and verified STEP/STL delivery therefore continue through the same native-kernel path.

## Native runtime

The advanced bridge is compiled into `cadyx_kernel` from `native/advanced.cpp`, `native/advanced_topology.cpp`, and `native/agent_features.cpp`. After pulling a commit that changes these sources, rebuild the local Windows runtime:

```powershell
.\scripts\setup-runtime.ps1
```

A host still running an older DLL intentionally rejects commands that need the new authoring bridge with a stale-runtime error instead of silently degrading the operation. Basic existing advanced commands remain available when their older symbols are present.

## Modeling scope

These features target exact single-solid B-rep workflows such as machined mounting plates and molded housings: section lofts for the primary envelope, shell/offset for wall thickness, draft/fillets/chamfers for transitions, and pads/ribs/pockets/bosses/holes for internal and mounting details. Surface-only bodies and a separate trim/sew surface document model are not introduced; the workflow stays within validated closed solids so CADYX's existing delivery and revision guarantees remain intact.

Absolute dimensions still come from the agent/user input or another calibrated source. A reference image can guide feature recognition and proportions, but CADYX does not infer a physical scale from pixels by itself.
