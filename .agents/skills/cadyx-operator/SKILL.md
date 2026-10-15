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

## Build and inspect

1. Check `cadyx_capabilities` and the canonical contract links before using a command. The discovery parameter list is a guide; Core and Command System validation is authoritative. Use the existing editable B-rep command when one fits.
2. For each `cadyx_execute_command`, first inspect the current document revision and provide that exact `expected_revision`. The tool acquires an agent lease immediately before the command and releases it afterward. If the lock is not `FREE`, an operation is running, recovery is required, or the revision changed, do not force, silently recover, or blindly retry.
3. If a required CADYX operation is missing, identify and reproduce the gap on an isolated temporary project. Implement the smallest correct change in the existing kernel/Core/Command System/API/rendering boundary, add the relevant verification, and follow `.codex/rules.md`, `README.md`, and the canonical model contracts. Do not substitute a mesh for an editable B-rep when the requested operation requires a B-rep. After a host rebuild/restart, re-read capabilities, the target document and its actual revision before continuing.
4. For a lost command response or interruption, inspect project revision, history, activity and lock before deciding what to do. The adapter never retries a mutation automatically. Use `cadyx_stop_agent_work` only for a lease owned by this MCP session; use `cadyx_resume_agent_work` only after the API confirms a safe state and with the current revision. Use `cadyx_recover_expired_lease` only when the API explicitly reports expiration; Core validates the saved state before release.
5. Keep the viewer on the target project. Use API camera operations when a useful angle is needed. Inspect the final complete scene and image, not just the latest command: verify intended dimensions and datum, closed profiles, joins/intersections, number of solids and B-rep validity/topology where applicable. Distinguish imported triangle meshes from editable B-reps. Report only properties the API or measurements establish.
6. After final verification, mark every dashboard phase completed and progress 100%. Confirm the project lock is `FREE` and the final activity has no running operation.

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
