# STL reference import

`import_stl` accepts either a staged `.stl` filename in the current project or a regular non-symlink local `.stl` file identified by absolute `source_path` and exact lowercase `expected_sha256`. Optional `name` controls the reference body name. Both forms require the normal project lease and exact revision. Project imports compute and record the source hash; the file remains unchanged and saved mesh geometry is self-contained.

Project-file imports resolve `file` through the current project's workspace, store the relative source filename and computed SHA-256 in feature provenance, and are available to the Files tab alongside STEP and PLY import actions.

Binary STL (including headers starting with `solid`) and strict ASCII STL are supported. Inputs are limited to 64 MiB; triangle count has no separate ceiling and is bounded by source size. Non-finite coordinates, invalid structure and degenerate triangles are rejected before document mutation. Vertices are welded by exact coordinate equality; source coordinates and scale are preserved. STL does not specify physical units: results are unitless until confirmed separately.

The imported body is a triangle-bearing `cadyx-mesh-v1` reference, not a B-rep.
This differs from staged point-only PLY import (`import_project_point_cloud`),
which stores vertices for scene inspection but has no faces and cannot be used
for mesh-section or mesh-deviation operations. See the [scan contract](scan-reverse-engineering.md). Existing `create_sketch_from_scan_section`, `create_scan_sections`, `compare_body_to_scan` and `align_scan` accept this mesh. STL alignment uses the saved source mesh so later alignment edits always start from the unchanged coordinates. Construct the final editable model from sketches and solid features; importing STL alone does not recreate a parametric model.

Tests: `tests/test_stl_reference.py`.
