# Engineering validation

CADYX exposes read-only engineering checks through the Agent API. Checks use the saved document revision and geometry hash, and may optionally store immutable results in the project's validation sidecar. Validation does not change the model revision.

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

The check requires a valid single-solid OCCT B-rep. It triangulates the B-rep, takes each sampled triangle's centroid and oriented normal, and casts an inward ray from that surface sample. Both normal directions are classified so reversed face orientations remain measurable. The 10,000-ray budget is allocated across triangulated faces: when there are at most 10,000 such faces, at least one triangle is selected from each, with the remaining selections allocated in proportion to each face's remaining triangles. If there are more faces than the budget, sampled faces are spaced evenly through the face list. Within each face, selected triangles are spaced evenly through its triangulation. Degenerate triangles and samples that cannot be classified inside the solid do not produce rays, so the returned `sample_count` may be lower than the allocation. The closest inward intersection for each ray is a sampled wall chord. The response reports the smallest measured chord, witness points, sample and hit counts, mesh deflection, start offset, revision, geometry hash, and model units. Progress is published as agent activity.

The result is deliberately conservative about what it can conclude:

- `fail` means a measured chord is below `minimum_thickness` after applying the requested tolerance. It is evidence of a local thin-wall condition.
- `untested` means no violation was found in the samples. It does not prove that the global minimum wall thickness meets the requirement.

This method is a screening measurement, not an exact global-minimum calculation. Mesh deflection is tied to the model bounding-box diagonal (`diagonal / 500`) and ray sampling is capped at 10,000; narrow features between samples can be missed. It reports model units and does not infer physical units or manufacturing suitability. Triangle-mesh-only bodies are not supported.

Results may be saved with `persist: true` and listed through `GET /api/v1/agent/projects/{id}/validations`. Saved records do not alter the document revision.
