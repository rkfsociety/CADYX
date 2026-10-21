# Revision-bound model comparison

[Русский обзор](#русский-обзор) · [English reference](#english-reference)

## Русский обзор

### Сравнение ревизий модели

`cadyx_list_revision_checkpoints` перечисляет сохранённые контрольные точки, `cadyx_compare_revisions` сравнивает их или существующие preview-предложения внутри одного проекта. Проверка read-only и привязана к текущему `expected_revision`. Старые ревизии без снимков не восстанавливаются из истории команд: возвращается `REVISION_UNAVAILABLE`. Отчёт включает изменения объектов, параметров, сборки и доступные измерения B-rep. Бюджет — 32 геометрических измерения; пробелы отражаются через `partial` и `untested`. Разность объёмов отдельных тел не доказывает глобальную разность объединённых моделей. Архив `.cadyx-revisions.sqlite3` нужно учитывать в резервных копиях.

Это обзор, не полный перевод. Полные схемы, примеры и подробности контракта приведены на английском ниже.

## English reference

CADYX exposes read-only comparisons of two **committed model checkpoints** or
two in-process **transaction preview proposals** in one project. A comparison
does not acquire an edit lease, modify a document, refresh its revision, publish
files, or imply that native OCCT measurements ran unless they are reported.

## Discover checkpoints

`GET /api/v1/agent/projects/{id}/revisions?offset=0&limit=50`

MCP: `cadyx_list_revision_checkpoints`.

The response lists available checkpoint revisions and SHA-256 fingerprints, most
recent first, with explicit `total` and `has_more`. The live authoritative
`project.json` revision is always readable. Beginning with this feature, each
successful `ProjectWorkspace.save` that advances the document revision archives
the previous and next documents into the project's private
`.cadyx-revisions.sqlite3` file using compressed canonical JSON. Snapshots
are bound to the exact committed history prefix and project document identity.
An archive write happens **before** atomic replacement of `project.json`;
the history check prevents an interrupted save's uncommitted checkpoint from
appearing. The authoritative project remains `project.json`.

**Historical compatibility:** Commands committed before checkpoint recording
cannot be losslessly reconstructed from history. Older revisions are not
silently manufactured; referencing one returns `REVISION_UNAVAILABLE`.
An atomic multi-command proposal commits only its final staged document:
intermediate revision numbers inside that batch are history entries, not
independently saved model checkpoints. Callers may compare the full proposal
before commit, or compare the final checkpoint after commit. Archive data is
project-local, excluded from direct project-file listings, and follows the
project folder across an ordinary rename. Losing the private archive does not
corrupt the authoritative project, but removes old comparison checkpoints.
Back up this file together with project files if historical comparison matters.

## Compare revisions and previews

`POST /api/v1/agent/projects/{id}/revisions/compare`

MCP: `cadyx_compare_revisions`.

Example:

```json
{
  "expected_revision": 17,
  "before": {"revision": 15},
  "after": {"proposal_id": "existing-preview-uuid"},
  "absolute_tolerance": 0.00000001,
  "relative_tolerance": 0.000000001
}
```

Selectors are exactly one of `revision` (committed checkpoint) or
`proposal_id` (already created through `/commands/preview`). Both are
associated with the same document; a missing, evicted or restarted-process
proposal is unavailable. `expected_revision` fences the current committed
project revision even when both comparison selectors name historical snapshots.
The preview's prospective revision is a **proposal**, not a committed revision,
and proposal comparison does not consume or authorize its commit.

The response contains:

- Document version, source selector identities, both snapshot SHA-256 values,
  current committed revision, explicit model-unit/assembly-millimeter units.
- Added/removed/changed object IDs, feature IDs, parameter values and bindings.
- Added/removed/changed parts, subassemblies, mates and instances, including
  old/new translation and quaternion for moved instances.
- Per-matching-`body_id` geometry descriptor hashes and exact OCCT B-rep
  preservation measurements where supported: reference/candidate volume,
  **common (intersecting)** volume, lost volume, added volume, native estimated
  volume resolution, volume-balance discrepancy and topology summary changes.
- For newly added or removed bodies, a bounded `unmatched_body_reports`
  array with the exact OCCT standalone body volume and topology summary when
  supported. Standalone volumes are **not** the net model-union volume.
- `visual_highlights` ID lists for added, removed and changed bodies. These
  are **selection hints** for external viewers/agents; CADYX's passive browser
  does not automatically overlay a second model or display deleted geometry.
- Explicit `untested` / `partial` coverage, including missing exact B-reps,
  uncertain volume balance, unpaired added/removed solids, body-pair budget,
  and topology face/edge identity correspondence.

The default numeric tolerances are **absolute 1e-8 model volume units** and
**relative 1e-9** of the larger compared body volume. Native OCCT
`estimated_volume_resolution` is included in the threshold. These measure
volumetric changes; they **do not** establish topological identity, feature
equivalence, manufacturing fit, or cross-body union volume. Surface changes
with zero volume difference may still change topology. Changed-geometry
calculations share a budget of **32**: first sorted matching-ID body
pairs, then new/removed individual bodies with any remaining slots. Additional
measurements are explicitly omitted; unlike model feature creation, this is
an inspection resource budget. Each pair and unmatched body is computed
independently. Triangle meshes and unsupported kernel operations remain
`untested`. The overall result is `partial` whenever unmatched body volumes
cannot establish the actual added/removed union across bodies. The comparison
does not claim exact global added/removed union volume or infer body identity
after deletion/recreation.

The SQLite sidecar is derived metadata and is not an interface for writing
model geometry. Model changes still require the usual Command System edit
lease and revision checks. New checkpoints consume storage proportional to the
models saved; CADYX currently provides no automatic history compaction or
retention policy, so workspace owners should include that storage in backups
and capacity planning.

## Verification

Regression scenarios in `tests/test_revision_diff.py` cover checkpoint
provenance, interrupted/changed history, legacy unavailable revisions,
parameter and assembly changes, OCCT measurement contracts, mesh limitations,
Core preview selection and the public route/tool registrations. Source changes
were authored without local test execution; Windows CI/native results must be
verified separately before treating them as runtime-proven.
