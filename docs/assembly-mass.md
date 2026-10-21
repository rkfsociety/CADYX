# Revision-bound assembly mass and inertia

[Полный русский перевод](assembly-mass.ru.md)

[Русский обзор](#русский-обзор) · [English reference](#english-reference)

## Русский обзор

### Масса и инерция сборки

Документ v11 ввёл явные материалы, граф сборки v3 сохраняется в текущем v12. `set_assembly_part_material` принимает название, положительную плотность в кг/м³ и источник либо `null`. `cadyx_measure_assembly_mass_properties` рассчитывает массу, центр масс и тензор инерции по подходящим OCCT B-rep с привязкой к ревизии. Единицы результата — кг, мм и кг·мм². Массы экземпляров суммируются, пересечения автоматически не вычитаются. Неизвестная плотность, сеточная геометрия и устаревшая ревизия вызывают явную ошибку.

Это обзор, не полный перевод. Полные схемы, примеры и подробности контракта приведены на английском ниже.

## English reference

## Persisted schema

Document **v11** introduced assembly **v2 → v3** material fields; the
**current** document schema is v12 and continues to use assembly graph v3. Each part definition has a
`material` field which is either `null` (not assigned, the migration default)
or exactly:

```json
{"name":"Declared alloy","density_kg_m3":2700,"source":"Supplier material specification"}
```

`name` and `source` are mandatory nonblank strings (up to 256 characters).
Density is a positive, finite number at most 1e9 kg/m³. It is an explicitly
supplied nominal density, **not** a lookup, default, measured property or
manufacturing certification. Previous v10 part records upgrade to
`material:null` on isolated document reads; a read alone never rewrites data.
The v10→v11 document migration preserves part, instance, mate and body IDs.

Use the ordinary revision- and lease-protected Command System:
`{"type":"set_assembly_part_material","part_id":"part:...","material":{...}}`.
Pass `null` to clear a material. The material belongs to the source part,
so every reused instance receives the same source material. The browser stays
read-only; commands animate through the ordinary Core activity path.

## Read-only calculation contract

`POST /api/v1/agent/projects/{id}/validations/mass-properties`
uses `{"expected_revision":<saved document revision>,"persist":false}`.
Equivalent MCP tool: `cadyx_measure_assembly_mass_properties`.
No edit lease is needed. `persist:true` records the report in the existing
project-local validation sidecar without changing the document revision.

The response provides:
- `status:"pass"`, `revision`, `geometry_sha256` over saved assembly
  graph, materials and referenced body descriptors, method and explicit units;
- `parts[]`: distinct source part IDs, original body IDs, material+provenance,
  exact volume in mm³, mass in kg, local COM in mm, and symmetric 3×3
  **center-of-mass** inertia in kg·mm²;
- `instances[]`: flattened nested leaf path, source ID, global translation
  and quaternion, world COM and rotated 3×3 inertia at its own COM;
- `assembly`: sum of placed instance mass, mass-weighted world COM,
  central inertia via the parallel-axis theorem, and leaf count.

Only valid closed, single-solid **OCCT B-reps** are authoritative production
geometry; the existing native `cadyx_brep_metrics` integrates
`BRepGProp::VolumeProperties` once per distinct source part at **unit
density** and now returns centroid and full inertia matrix alongside its
prior `volume` field. The bridge must be rebuilt on Windows before use.
For a supplied density \(\rho\) in kg/m³ and a geometric volume \(V\)
in mm³, \(m=\rho V 10^{-9}\) kg; the same factor converts the native
mm⁵ inertia integral to kg·mm². All placements are in explicitly declared
assembly mm, and quaternion transforms rotate tensors rather than copy solids.

Unknown material density or provenance, unsupported graph/units, mesh-only
source, invalid native physical properties, or stale revision raises an
explicit error instead of returning fabricated mass. The current contract
supports up to 128 distinct source parts and 256 flattened leaf instances;
empty/unplaced assemblies do not yield an aggregate report. Aggregates are
**additive**: if parts overlap, overlapping volume is counted once for
each physical instance, not Boolean-unioned or automatically subtracted.
There is no physical tolerance stack, stiffness, contact stress,
interference clearance or continuous-motion certificate. Use separate
`assembly-fit` validation for collision checks.

Regression fixtures in `tests/test_assembly_mass.py` cover cuboid
moments, centroid shifts, rotation, repeated instances, unknown density,
invalid inputs, migration and stale-revision behavior using a native-free
analytic primitive fixture. They are checked into source but intentionally
not executed locally; authoritative OCCT/Windows checks remain CI work.
