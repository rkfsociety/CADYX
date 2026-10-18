# STL reference import

`import_stl` reads a regular non-symlink local `.stl` file identified by absolute `source_path` and exact lowercase `expected_sha256`. Optional `name` controls the reference body name. This local-host command requires the normal project lease and exact revision. The file remains unchanged; saved mesh geometry is self-contained.

Binary STL (including headers starting with `solid`) and strict ASCII STL are supported. Inputs are limited to 64 MiB and 100000 triangles. Non-finite coordinates, invalid structure and degenerate triangles are rejected before document mutation. Vertices are welded by exact coordinate equality; source coordinates and scale are preserved. STL does not specify physical units: results are unitless until confirmed separately.

The imported body is a `cadyx-mesh-v1` reference, not a B-rep. Existing `create_sketch_from_scan_section`, `create_scan_sections`, `compare_body_to_scan` and `align_scan` accept this mesh. STL alignment uses the saved source mesh so later alignment edits always start from the unchanged coordinates. Construct the final editable model from sketches and solid features; importing STL alone does not recreate a parametric model.

Tests: `tests/test_stl_reference.py`.
