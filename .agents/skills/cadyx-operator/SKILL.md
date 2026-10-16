---
name: cadyx-operator
description: Create, inspect, revise, or reconstruct a CAD model in CADYX through its Agent API, including reference-driven work and PLY scans. Use when the user asks to model a part, assembly, table, handle, or model from a scan.
---

# CADYX Operator

Use the `cadyx_*` MCP tools for every CADYX document operation: workspace selection, project creation, saved model inspection, files, topology, viewport/camera, dashboard and commands. The browser is a passive viewer. Do not use browser automation, direct project file edits, shell/HTTP as a bypass for document changes, or another CAD application.

## Prepare

1. Call `cadyx_health`, `cadyx_get_selection`, `cadyx_list_workspace`, and `cadyx_capabilities`. If the host is unavailable, report that fact and point to the README host setup; do not infer current project state.
2. Select an existing project only through `cadyx_select_resource`, after API lock state is `FREE` and no activity is running. Creating a new project through `cadyx_create_project` selects it automatically. Folder selection is also API-only.
   When another project has active work, create a staging project with `cadyx_create_project(select=false)` and copy a local STEP source into it with `cadyx_upload_project_step`; leave the viewer unchanged until the active workflow is complete.
3. Call `cadyx_inspect_project` and inspect relevant direct files through the read-only file tools. Resolve the target object from the saved document and user wording. CADYX does not expose a selected body/face from the browser; if multiple plausible targets remain, ask which one rather than inventing a selection.
4. Ask only for material product requirements that cannot be inferred safely (for example dimensions, intended attachment, scale, units, or tolerance). Never invent scan scale, physical units, accuracy, or manufacturing claims. Do not ask permission for implementation changes required to fulfil the requested model; follow the repository engineering rules.
5. Before the first document mutation, publish a concrete 3–7 phase Russian plan with `cadyx_report_dashboard`, at 0% with phases pending and an honest ETA or `null`. Keep `tasks: []` unless work is actually queued. Update the same report at verified phase boundaries; do not show fabricated progress.

## Parametric modeling doctrine

Build models the way an experienced engineer would build them in a conventional history-based parametric CAD system. The target is not merely the same final surface or silhouette: the saved CADYX document must express the design intent through a logical, editable and robust feature history.

Use this conceptual order whenever it fits the part:

`design intent -> datum/reference geometry -> sketch -> constrained geometry -> solid feature -> dependent features -> patterns/mirrors -> finishing features`

Do not optimize the construction for what is easiest for an AI agent if that produces a worse CAD model.

1. **Determine design intent before creating geometry.** Decompose the requested part into its primary volume, reference planes/axes, symmetry, revolved regions, repeated features, major cuts, holes, slots, ribs, shells and finishing details. Start from the feature that best expresses the principal form instead of beginning with small details.
2. **Choose the natural base feature.** Use sketch extrusion for a substantially prismatic form, sketch revolution for an axisymmetric or turned form, sweep for a constant section following a path, and loft for a form defined by changing sections. Prefer one meaningful revolved profile over a stack of cylinders, one sweep over many short approximating extrusions, and one loft over a staircase of artificial solids. Check `cadyx_capabilities` before assuming an operation exists.
3. **Build from primary form to secondary details.** Prefer the feature order: datum/reference geometry, base sketch, base feature, major additive features, major cuts, holes/slots, ribs/bosses, mirror/pattern operations, shell/draft-like operations when available, and then fillets/chamfers/threads or other finishing details. Small edge treatments must not drive the main shape unless the design truly requires it.
4. **Treat sketches as the source of construction intent, not disposable line collections.** Pick a logical sketch plane, use origin and axes deliberately, center geometry around meaningful datums, and express symmetry and alignment through reference geometry. If a feature is centered, define it from the center or symmetry axis instead of unrelated absolute coordinates.
5. **Keep sketches minimal and exact.** Use the fewest entities needed to define the profile. Prefer analytic lines, circles, circular arcs and ellipses over sampled polylines or splines that merely imitate those shapes. Use splines only when the design genuinely requires a free-form curve. Avoid duplicate entities, near-coincident geometry, unnecessary control points and tiny segments.
6. **Create geometry that already satisfies constraints.** CADYX constraints currently validate supplied geometry; they do not solve or move an under-defined sketch. Therefore create the coordinates correctly first and use constraints as explicit design-intent invariants. For example, create a horizontal line with equal endpoint Y coordinates and then add the horizontal constraint; do not expect the constraint to repair approximate geometry.
7. **Use construction geometry for references.** Use centerlines for revolution axes and symmetry, construction lines for alignment and auxiliary references, and construction points for reference locations. Keep construction geometry out of closed profiles and sweep paths unless a command contract explicitly says otherwise.
8. **Give each sketch and feature one understandable job.** Prefer several simple, functionally meaningful sketches/features over one enormous sketch containing the whole part. When naming is available, use semantic names such as `BaseProfile`, `BaseExtrude`, `CenterBore`, `MountingHole`, `MountingHolePattern` and `OuterFillet`, not meaningless sequence names.
9. **Prefer sketch-driven features over primitive-and-Boolean assembly.** If a shape is naturally expressed as `Sketch -> Extrude/Revolve/Sweep/Loft`, use that construction. Do not replace normal parametric modeling with long chains such as `Box + Cylinder + Fuse + Cut` merely because primitives are easy to generate. Boolean operations remain valid tools, but they must represent genuine constructive intent rather than substitute for a proper feature history.
10. **Use Boolean operations deliberately.** Fuse volumes that are conceptually separate additive features, cut with a deliberate tool body/profile, and use intersection only when that is the actual design operation. After every Boolean, verify solid count, connectivity, validity and that the result changed as intended; do not continue on top of unchanged-volume, multi-solid or accidental sliver results.
11. **Keep dependencies one-directional and robust.** Later features may depend on earlier design geometry; earlier features must not be made dependent on incidental geometry created later. Prefer stable references to sketches, sketch objects, datums, axes and construction geometry over transient kernel topology when possible.
12. **Treat B-rep topology indices as transient.** When an operation genuinely requires an edge index, inspect the current topology immediately before selecting it. After any upstream edit, rebuild or Boolean change, refresh topology instead of assuming an old edge number still identifies the same physical edge.
13. **Use mirror and pattern semantics for repeated geometry.** Create one master feature and mirror or pattern it when CADYX has the required capability. Do not independently construct six identical holes or two symmetric halves if one controlled feature plus a pattern/mirror expresses the design better. Preserve a shared dimensional source for repeated elements.
14. **Apply fillets, chamfers and similar edge-based finishing features late.** Build functional geometry first, then edge treatments. Avoid making major downstream construction depend on edges created only by an early fillet when the same intent can reference the original datum or profile. This keeps the history resilient to dimensional changes.
15. **Modify the governing feature instead of patching the final body.** When revising a model, find the sketch or feature that controls the requested dimension or form and edit that source. If the user asks for a wider housing, change the controlling width; do not glue an extra slab onto the side. If the user asks for a larger hole, change its controlling diameter/profile; do not add a second approximate cut when the original feature is editable.
16. **Use symmetry in the coordinate system.** Unless the design dictates otherwise, place symmetric base geometry around the origin or a meaningful datum, use centerlines, and dimension relative to those references. Do not introduce arbitrary offsets that make future edits harder.
17. **Preserve editable B-rep intent.** If the requested result is a normal CAD part, keep the final result as an editable B-rep built from sketches and solid features whenever the geometry can be authored that way. Meshes are appropriate for scan/reference data and explicitly mesh-based workflows, not as a substitute for a missing solid-modeling operation.
18. **Implement missing CAD capabilities instead of creating geometric hacks.** If the correct professional CAD construction requires an operation CADYX lacks, confirm the gap against current capabilities and canonical contracts, reproduce it on an isolated temporary project, then implement the smallest correct Core/Command System/kernel/API/rendering extension. Do not approximate a required B-rep with a mesh or dozens of primitive workarounds simply to finish faster.
19. **Validate after meaningful feature boundaries.** After the base feature, major additions/cuts, patterns and finishing operations, inspect the committed document before continuing. Confirm revision, expected feature/body creation, profile closure, intersections, solid count and B-rep validity as applicable. Do not build a long feature chain on top of an already incorrect intermediate state.
20. **Prefer the simplest professional feature history, not the fewest commands at any cost.** Between two correct constructions, prefer the one that better expresses manufacturing/design intent, has fewer fragile references, uses standard CAD operations, preserves exact analytic geometry and remains easy to edit. Do not combine unrelated logical features solely to reduce the command count.

For example, a typical circular flange should normally be modeled as an axial profile plus `Revolve`, followed by a center-bore cut, one mounting-hole feature, a circular pattern, and late fillets/chamfers where required. Do not default to several cylinders, repeated manual Boolean cuts and duplicated hole bodies when the parametric construction is available.

Before every new feature, ask: **How would an engineer manually model this feature in a conventional parametric CAD system?** If the natural answer is a sketch-driven feature, mirror, pattern, sweep, revolve, loft or another standard CAD operation available in CADYX, use it. CADYX must create a correct CAD model, not merely correct-looking geometry.

Priority order when trade-offs are necessary:

1. geometric correctness;
2. design intent;
3. parametric editability;
4. feature-history robustness;
5. exact analytic geometry;
6. reasonable model simplicity;
7. construction speed.

## Build and inspect

1. Check `cadyx_capabilities` and the canonical contract links before using a command. The discovery parameter list is a guide; Core and Command System validation is authoritative. Use the existing editable B-rep command when one fits, and choose the command sequence according to the parametric modeling doctrine above.
2. For each `cadyx_execute_command`, first inspect the current document revision and provide that exact `expected_revision`. The tool acquires an agent lease immediately before the command and releases it afterward. If the lock is not `FREE`, an operation is running, recovery is required, or the revision changed, do not force, silently recover, or blindly retry.
3. If a required CADYX operation is missing, identify and reproduce the gap on an isolated temporary project. Implement the smallest correct change in the existing kernel/Core/Command System/API/rendering boundary, add the relevant verification, and follow `.codex/rules.md`, `README.md`, and the canonical model contracts. Do not substitute a mesh for an editable B-rep when the requested operation requires a B-rep. After a host rebuild/restart, re-read capabilities, the target document and its actual revision before continuing.
4. For a lost command response or interruption, inspect project revision, history, activity and lock before deciding what to do. The adapter never retries a mutation automatically. Use `cadyx_stop_agent_work` only for a lease owned by this MCP session; use `cadyx_resume_agent_work` only after the API confirms a safe state and with the current revision. Use `cadyx_recover_expired_lease` only when the API explicitly reports expiration; Core validates the saved state before release.
5. Keep the viewer on the target project. Use API camera operations when a useful angle is needed. Inspect the final complete scene and image, not just the latest command: verify intended dimensions and datum, closed profiles, joins/intersections, number of solids and B-rep validity/topology where applicable. Distinguish imported triangle meshes from editable B-reps. Report only properties the API or measurements establish.
6. After final verification, mark every dashboard phase completed and progress 100%. Confirm the project lock is `FREE` and the final activity has no running operation.
7. For a reference-driven model built from scratch, treat the uploaded STEP as working input. Once the new model has been verified, remove the now-unreferenced imported reference body with `remove_body` and `remove_source_file: true`. Preserve the user's original source file and any exported deliverables.

## Scan reverse engineering

1. Start with `import_scan`. Keep the PLY source unchanged and tune reconstruction parameters before committing to reconstruction geometry.
2. Do not infer physical units from PLY coordinates. Use `align_scan` to establish the datum/orientation, and set `scale` plus `units` only from a known dimension or explicit user requirement. For elongated parts, PCA with `long_axis=Y` and `ground_z=true` is the default reverse-engineering datum unless the reference requires another orientation.
3. Use `create_scan_sections` for a station series. Y-normal stations create XZ sketches whose returned `section_groups` can be passed directly to `loft_sketches`; increase station density around curvature changes, ribs, clips, bosses and contour topology changes. Use `create_sketch_from_scan_section` for one diagnostic or local station.
4. Build a clean editable design model from the scan-derived sketches using existing loft, extrusion, cut, fuse, shell, fillet and other B-rep commands. Do not reproduce scanner noise as CAD topology. X-normal/YZ sections are not faked through another plane: explicitly re-align the scan when that datum is required until native YZ sketches exist.
5. Run `compare_body_to_scan` after the principal B-rep and again after local features. Use RMS/P95/max and worst sampled points to decide where to refine the model. State that this is sampled point-to-tessellated-triangle deviation, not exhaustive exact B-rep metrology.
6. If `set_scan_parameter` is used after alignment, CADYX reapplies the stored scan alignment automatically; still inspect the resulting bounds and current revision before continuing.

## Report

Summarize what was created or changed, the actual final revision and geometry checks, and whether the result is an editable B-rep or triangle mesh. State unresolved requirements or unverified measurements plainly. Never claim browser observation, command success, cancellation, or lease release unless the API confirmed it.

## Canonical contracts

- Repository rules and host setup: `AGENTS.md`, `.codex/rules.md`, and `README.md`.
- Model commands, sketches, scans and B-rep delivery: `.codex/docs/document-model-v7.md`.
- Scan alignment, section extraction and deviation checks: `.codex/docs/scan-reverse-engineering.md`.
- Leases, cancellation, stop/resume and Core boundaries: `.codex/docs/architecture.md`.
- Viewport scene, PNG and dashboard behavior: `.codex/docs/viewport-api.md`.
- Current features and deferred engineering validation: `.codex/roadmap.md`.
