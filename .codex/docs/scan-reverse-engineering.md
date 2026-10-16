# Scan reverse-engineering contract

This contract extends the Document Model v7 scan workflow from reconstructed triangle meshes to editable CAD reconstruction. It does not change the persisted document version: the new operations use existing body, feature, sketch, point, and line objects and the existing OCCT loft/Boolean/fillet pipeline.

## Scope and evidence

`import_scan` remains the ingestion step. A PLY point cloud is cleaned and reconstructed by the Open3D CPU bridge; a PLY triangle mesh is cleaned/simplified. The result remains `cadyx-mesh-v1`, not a B-rep. Reverse-engineering commands use that validated mesh as reference data and create ordinary editable sketches that can feed existing OCCT features.

CADYX never infers physical units from PLY coordinates. `align_scan` records explicit units and a numeric scale supplied by the agent/user. A model built from those scan-derived sketches inherits the resulting coordinate scale by construction; the document schema itself remains unitless.

`compare_body_to_scan` is a sampled geometric check against tessellated triangles. It is useful for reconstruction iteration, but is not an exhaustive exact B-rep distance proof, metrology certificate, manufacturing tolerance proof, or fit guarantee.

## `align_scan`

Required: `body_id` of the original `reconstruct_scan` body.

Optional parameters:

- `mode`: `pca` (default) or `explicit`.
- `long_axis`: `X`, `Y`, or `Z` in PCA mode; default `Y` because Y-normal XZ stations feed `loft_sketches` directly.
- `basis`: three right-handed orthonormal XYZ axis vectors in explicit mode. Each row is a target axis expressed in source coordinates.
- `origin`: source-space datum. PCA mode defaults to the sampled centroid when `center=true`; explicit mode defaults to `[0,0,0]`.
- `center`: PCA-only boolean, default `true`.
- `axis_signs`: three `+1`/`-1` values whose product is positive, allowing 180-degree datum flips without mirroring.
- `scale`: positive coordinate multiplier, default `1`.
- `units`: `unitless`, `mm`, `cm`, `m`, or `in`; default `unitless`.
- `translation`: final XYZ translation, default `[0,0,0]`.
- `ground_z`: boolean, default `true`; after alignment, shifts the scan so its minimum Z is zero.
- `name`: optional replacement body name.

PCA uses a bounded deterministic sample of mesh vertices, a symmetric 3x3 covariance eigensolve, deterministic axis signs, and a right-handed basis. The command mutates the scan body's current mesh in place so the passive viewport does not show duplicate raw/aligned bodies. The `reconstruct_scan` feature retains an `alignment` record and the raw artifact reference. If `set_scan_parameter` later rebuilds the mesh, the Command System automatically reapplies the stored alignment before the revision is committed.

Re-running `align_scan` on an already aligned scan reconstructs from the unchanged source SHA-256 first, then applies the new transform. Alignment therefore does not compound numerical transforms.

## Scan sections

### `create_sketch_from_scan_section`

Required: `body_id`, finite `offset`.

Optional:

- `normal_axis`: `Y` (default) or `Z`.
- `join_tolerance`: endpoint welding tolerance; default is derived from scan bounds.
- `simplify_tolerance`: closed-contour RDP tolerance, default `0`.
- `max_points`: at least 16 points per contour, default `512`. Longer loops are arc-length resampled when this value is lower than the source contour's point count.
- `max_contours`: optional positive count for explicitly limiting generated sketches; omitted means keep every eligible contour. When a limit is supplied, largest enclosed areas are kept first.
- `min_area`, `min_perimeter`: non-negative noise filters.
- `name`: sketch name prefix.

The mesh is intersected triangle-by-triangle with the requested plane. Segment endpoints are tolerance-welded into a graph. Only closed, non-branched components become sketches; open/branched/noise component counts are returned so the agent can adjust the plane or tolerances instead of silently accepting broken profiles.

A Y-normal station becomes an `XZ` sketch with `section_normal_axis="Y"` and its exact `section_offset`. It is immediately compatible with `loft_sketches`. A Z-normal station becomes an `XY` sketch. X-normal/YZ authoring is deliberately not faked through the wrong sketch plane; use an explicit re-alignment when a YZ-style station is required until native YZ sketches are added.

Each returned contour includes area, signed area, perimeter, centroid, 2D bounds, point count, and the sketch id. Generated sketches retain their source scan body and section tolerances as provenance metadata.

### `create_scan_sections`

Batch form of the same operation. `offsets` contains any number of strictly increasing stations. The command returns:

- `section_groups`: one sketch-id list per station, including multi-contour stations;
- flattened `sketch_ids`;
- per-station contour measurements and rejected-component counts.

For Y-normal stations the returned `section_groups` can be passed directly to `loft_sketches`. The existing loft implementation already supports one or multiple contours per station, normalizes profile correspondence, and requires strictly increasing `section_offset` values.

## `compare_body_to_scan`

Required: editable non-mesh `body_id` and `scan_body_id`.

Optional:

- `max_samples`: 64–20000, default `4096`.
- `tolerance`: positive threshold in the aligned scan/model coordinate units.
- `bidirectional`: default `false`; when true, also samples body-tessellation vertices against the scan mesh.

The command builds a bounding-volume hierarchy over target triangles and measures sampled point-to-triangle distances. `scan_to_body` reports sample count, mean, RMS, P95, maximum deviation, and the ten worst sampled points. With `tolerance`, it also reports the count and percentage within tolerance. Bidirectional mode adds `body_to_scan` and a sampled two-sided maximum.

Because the B-rep side is evaluated through its current viewport tessellation, the method id is `sampled-point-to-tessellated-triangle-bvh-v1`. Always report that limitation with the numeric result.

## Recommended agent workflow

1. Import the source with `import_scan` and tune reconstruction parameters while preserving the original PLY.
2. Run `align_scan`; keep units `unitless` until scale is known. For elongated parts, map the dominant axis to global Y and keep `ground_z=true` unless the product datum says otherwise.
3. Inspect aligned bounds. If a known physical dimension is available, re-run `align_scan` with `scale = target_dimension / measured_scan_dimension` and explicit `units`.
4. Create strategically spaced Y-normal stations with `create_scan_sections`. Use more stations where curvature, ribs, clips, bosses, or contour topology changes.
5. Build the principal editable B-rep with `loft_sketches`; add local solids/cuts/fillets with existing CADYX commands rather than trying to preserve scan noise as CAD topology.
6. Run `compare_body_to_scan`, inspect P95/max and worst sampled locations, then refine stations/features and repeat.
7. Inspect the complete final B-rep and viewport, then use `export_brep` for verified STEP/STL delivery when requested.

The reverse-engineering goal is a clean editable design model that matches measured reference geometry within the stated sampled tolerance, not a triangle-for-triangle conversion of scanner noise.
