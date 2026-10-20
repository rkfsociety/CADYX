# Versioned standard-part catalog — built-in foundation

The catalog is caller-invoked, deterministic and read-only until the ordinary
leased Command System creates a part. Its built-in CADYX-authored presets are
MIT-licensed data declarations, **not** manufacturing standards, calibrated
tolerances, or certified supplier models. The browser remains passive.

## Discovery and resolution

- `GET /api/v1/agent/catalog` / `cadyx_list_standard_parts` lists complete
  immutable preset manifests with `id`, `version`, `sha256`, `recipe_version`,
  category, required parameter names, source, license and fit limitations.
- `POST /api/v1/agent/catalog/resolve` / `cadyx_resolve_standard_part`
  accepts exactly `catalog_id`, `catalog_version`, `catalog_sha256`,
  `parameters` and optional `name`; it checks both version/hash and every
  required parameter, rejects extras and returns a ready-to-execute
  `create_catalog_part` command plus a content-addressed instance digest.
- Submit the returned command with **exact `expected_revision`** through the
  leased `POST /api/v1/agent/projects/{id}/commands` route when you already
  own a valid agent lease. Alternatively use
  `POST /api/v1/agent/projects/{id}/commands/execute` or MCP
  `cadyx_execute_command`: the server acquires/releases the short-lived lease
  around that one command and returns a compact receipt. Do **not** hold a
  separate lease merely to invoke that MCP tool. Core stages and atomically
  persists the result, source sketch (if any), history and provenance.
  There is no autonomous catalog operator or second mutation path.

## Available preset geometries

| Catalog ID | Category | Parameters | Actual geometry |
| --- | --- | --- | --- |
| `cadyx.threaded-stud` | fastener | `major_diameter, pitch, length` | Editable OCCT right-hand external metric thread; no head |
| `cadyx.ring-spacer` | bearing_profile | `outer_diameter, inner_diameter, length` | Editable concentric-circle sketch and annular extrusion, **not** a working rolling bearing |
| `cadyx.hex-bar` | profile | `across_flats, length` | Editable regular-hexagon sketch and extrusion |
| `cadyx.rectangular-extrusion` | extrusion | `width, height, length` | Editable native box |
| `cadyx.staged-step-reference` | purchased_reference | `file, file_sha256, supplier, part_number, license_id, usage_rights_confirmed` | Original staged STEP imported as a single B-rep |

Linear values are in mm. Native dimensions must be positive, finite and no
greater than 100,000 mm; ring bore must be smaller than its outside diameter.
Thread major diameter must exceed 1.25 times pitch. These parameter constraints
are **not** proof of ISO thread conformity or engineering suitability. The
`catalog_origin` field on the created feature freezes creation parameters,
manifest version, SHA-256, source, license, and instance hash. Later edits
remain allowed, but the origin describes creation, **not** present compliance.

## Third-party STEP safeguards

The user stages their own direct `.stp`/`.step` file in the project using the
existing authenticated Agent API before invoking the preset. No network
download, script execution, plugin installation or silent file overwrite occurs.
The ordinary STEP importer checks file bounds, size and geometry, and the
catalog additionally compares its computed source SHA-256 with the pinned
lowercase hex `file_sha256` **before the command can commit**.

`supplier` and `part_number` must be explicit nonblank text. `license_id`
must be one of `MIT`, `BSD-3-Clause`, `Apache-2.0`, `CC0-1.0`,
`CC-BY-4.0`, or `Proprietary`. The caller must pass
`usage_rights_confirmed: true`. This is **only a user attestation**, not
external license verification: proprietary licensing, redistribution and
manufacturing specifications remain the caller's responsibility. Nothing is
licensed, downloaded or shared by CADYX on the caller's behalf. The imported
STEP retains its existing source-file SHA provenance.

## Scope and verification

Tests in `tests/test_standard_parts.py` cover stable manifests, ownership,
version/parameter failures, operation dispatch, source hashing and provenance.
No local tests were run for the initial commit at the user's request; native
OCCT validation and Windows CI remain outstanding. External signed/package
ingestion, standards-grade rolling bearings, supplier catalog verification and
independently authenticated license checks are explicitly not implemented.
