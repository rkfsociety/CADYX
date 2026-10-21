# Engineering validation

[Русский обзор](#русский-обзор) · [English reference](#english-reference)

## Русский обзор

### Инженерные проверки

Проверки используют сохранённую ревизию и хеш геометрии; отчёты можно сохранить отдельно без изменения модели. Метод `sampled` проверяет толщину ограниченной выборкой лучей: найденное нарушение даёт `fail`, отсутствие нарушения — `untested`, а не доказательство глобальной толщины. `global_minimum` сертифицирует минимальную ширину только подходящего выпуклого многогранника с плоскими гранями и прямыми рёбрами. Криволинейные, вогнутые и неподдерживаемые тела остаются `untested`. Спецификации и комплексные проверки проекта используют отдельные контракты.

Это обзор, не полный перевод. Полные схемы, примеры и подробности контракта приведены на английском ниже.

## English reference

CADYX exposes read-only engineering checks through the Agent API. Checks use the saved document revision and geometry hash, and may optionally store immutable results in the project's validation sidecar. Validation does not change the model revision. These are separate measurement
operations; the [model specification](../.codex/docs/model-specifications.md)
composes selected requirements, while [project checks](../.codex/docs/project-checks.md)
orchestrate document/geometry/specification/export-preflight stages.
[Assembly fit](assembly-fit.md) evaluates sampled assembly poses independently;
none of these calls certifies a manufacturing process by default.

## Global minimum thickness

The thickness endpoint accepts an optional `method` field. `method: "sampled"` is the default and preserves the screening behavior below. `method: "global_minimum"` requests a convex-polyhedron certificate instead:

```json
{
  "body_id": "...",
  "expected_revision": 12,
  "minimum_thickness": 1.5,
  "tolerance": 0.05,
  "method": "global_minimum",
  "persist": false
}
```

The certified method applies to a valid single solid with one shell, planar faces, and linear edges, and verifies convexity from the oriented face half-spaces. It enumerates the support directions required for the minimum caliper width of a convex polyhedron (face normals and cross-products of edge directions) and evaluates every solid vertex in those directions. It returns `minimum_global_thickness`, `minimum_bound`, `maximum_bound`, the support direction and points, topology counts, `metric_definition: "minimum_caliper_width"`, and `certified: true`. The requested threshold is `pass` only when the full enclosure meets it after the requested tolerance, `fail` only when the full enclosure is below it, and `untested` when the enclosure overlaps the threshold.

Curved, non-convex, hollow, or otherwise unsupported B-reps return `untested` with `certified: false` and a reason. The certificate is for global minimum caliper width, a whole-solid thickness measure for the supported convex polyhedra; it is not a local-thickness certificate for arbitrary curved or concave solids. The sampled method remains available for those shapes as a non-certifying screen.

## Sampled wall thickness

`POST /api/v1/agent/projects/{id}/validations/thickness` accepts:

```json
{
  "body_id": "...",
  "expected_revision": 12,
  "minimum_thickness": 1.5,
  "tolerance": 0.05,
  "persist": false
}
```

The check requires a valid single-solid OCCT B-rep. It triangulates the B-rep, takes each sampled triangle's centroid and oriented normal, and casts an inward ray from that surface sample. Both normal directions are classified so reversed face orientations remain measurable. The 10,000-ray budget is allocated across triangulated faces: when there are at most 10,000 such faces, at least one triangle is selected from each, with the remaining selections allocated in proportion to each face's remaining triangles. If there are more faces than the budget, sampled faces are spaced evenly through the face list. Within each face, selected triangles are spaced evenly through its triangulation. Degenerate triangles and samples that cannot be classified inside the solid do not produce rays, so the returned `sample_count` may be lower than the allocation. The response reports `face_count` and `sampled_face_count`; the latter counts faces with at least one valid inward-ray origin, even if that ray finds no opposite hit. The closest inward intersection for each ray is a sampled wall chord. It also reports the minimum, median, and nearest-rank 10th percentile among rays that hit, the number of hit rays that violate `minimum_thickness` after tolerance, witness points for the minimum, sample and hit counts, mesh deflection, start offset, revision, geometry hash, and model units. These statistics describe only the bounded sample and do not imply complete surface coverage. Progress is published as agent activity.

The result is deliberately conservative about what it can conclude:

- `fail` means a measured chord is below `minimum_thickness` after applying the requested tolerance. It is evidence of a local thin-wall condition.
- `untested` means no violation was found in the samples. It does not prove that the global minimum wall thickness meets the requirement.

This method is a screening measurement, not an exact global-minimum calculation. Mesh deflection is tied to the model bounding-box diagonal (`diagonal / 500`) and ray sampling is capped at 10,000; narrow features between samples can be missed. It reports model units and does not infer physical units or manufacturing suitability. Triangle-mesh-only bodies are not supported.

Results may be saved with `persist: true` and listed through `GET /api/v1/agent/projects/{id}/validations`. Saved records do not alter the document revision.
