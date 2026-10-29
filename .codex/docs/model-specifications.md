# Model specifications

[Полный русский перевод](model-specifications.ru.md)

[Русский обзор](#русский-обзор) · [English reference](#english-reference)

## Русский обзор

### Спецификации требований к модели

JSON-спецификация версии 1 объединяет именованные требования и существующие инженерные проверки одной сохранённой ревизии. Вызов `/validations/specification` требует точного `expected_revision`; это ревизия проекта, не номер схемы документа. Проверки размеров, зазоров, сохранности и толщины сохраняют собственную доказательность и статусы `pass`/`fail`/`untested`. Опциональное сохранение пишет отдельный отчёт, не геометрию. Композиция не делает выборочные измерения полными сертификатами.

Это обзор, не полный перевод. Полные схемы, примеры и подробности контракта приведены на английском ниже.

## English reference

CADYX can evaluate a small, versioned JSON specification against one saved
document revision. A specification groups existing engineering validations
under named requirements and returns their evidence together. It does not
change model geometry.

## Agent API

Call `POST /api/v1/agent/projects/{id}/validations/specification` with an exact
`expected_revision` and a version 1 `specification`. The sample value `12`
for `expected_revision` is a **project revision example**, not the document
schema version (which currently happens to be v14):

```json
{
  "expected_revision": 12,
  "persist": true,
  "specification": {
    "schema_version": 1,
    "title": "Mounting plate requirements",
    "checks": [
      {
        "id": "overall dimensions",
        "operation": "dimensions",
        "arguments": {
          "body_id": "body-id-from-inspection",
          "expected_dimensions": {"x": 80, "y": 50, "z": 5},
          "tolerance": 0.2
        }
      },
      {
        "id": "minimum wall thickness",
        "operation": "thickness",
        "arguments": {
          "body_id": "body-id-from-inspection",
          "minimum_thickness": 2,
          "tolerance": 0.1,
          "method": "sampled"
        }
      }
    ]
  }
}
```

`persist` defaults to `false`. When true, CADYX stores one combined validation
record containing the specification, its SHA-256, document revision, every
check result, and measurement evidence. The definition can also be kept as a
regular project JSON file or in source control and supplied to later calls.

Each check has a unique `id`, an `operation`, and that operation's arguments.
The supported operations are:

- `dimensions`: compare global axis-aligned body dimensions to the expected
  X/Y/Z values within a tolerance.
- `clearance`: verify minimum separation between two bodies and report
  interference evidence.
- `preservation`: compare reference and candidate bodies by common volume and
  check the allowed lost volume.
- `thickness`: check a minimum body thickness using the existing sampled or
  certified `global_minimum` method.

Specifications contain 1–16 checks. Every check runs against the same
`expected_revision`; if the document changes during evaluation, the request
fails with a revision conflict instead of returning mixed-revision evidence.
Invalid definitions or arguments fail the request. A geometrically ambiguous
check can return `untested`.

The overall status is `fail` if any check fails, otherwise `untested` if any
check is untested, otherwise `pass`. Each check includes its original
engineering-validation evidence. The MCP tool is
`cadyx_validate_model_specification`; the in-process Agent Runtime exposes
`validate_specification` and binds it to the revision last observed by that
agent run.

This contract composes the existing engineering validators. It does not infer
design intent, create checks automatically, or claim a tolerance/units system
beyond the existing validation contracts. For an orchestrated, optional
strict headless check that includes this specification alongside document,
geometry and export-eligibility stages, see [project checks](project-checks.md).
A model specification does not implicitly run all project-check stages.
