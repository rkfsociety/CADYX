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

The check requires a valid single-solid OCCT B-rep. It triangulates the B-rep, evaluates the oriented surface normal at triangulation nodes, and casts an inward ray from each valid surface sample. The closest inward intersection for each ray is a sampled wall chord. The response reports the smallest measured chord, witness points, sample and hit counts, mesh deflection, start offset, revision, geometry hash, and model units. Progress is published as agent activity.

The result is deliberately conservative about what it can conclude:

- `fail` means a measured chord is below `minimum_thickness` after applying the requested tolerance. It is evidence of a local thin-wall condition.
- `untested` means no violation was found in the samples. It does not prove that the global minimum wall thickness meets the requirement.

This method is a screening measurement, not an exact global-minimum calculation. Sampling density is tied to the model bounding-box diagonal (linear mesh deflection `diagonal / 500`); narrow features between samples can be missed. It reports model units and does not infer physical units or manufacturing suitability. Triangle-mesh-only bodies are not supported.

Results may be saved with `persist: true` and listed through `GET /api/v1/agent/projects/{id}/validations`. Saved records do not alter the document revision.
