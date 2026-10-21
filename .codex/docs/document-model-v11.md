# CADYX Document Model v11 — Assembly material assignment

[Полный русский перевод](document-model-v11.ru.md)

[Русский обзор](#русский-обзор) · [English reference](#english-reference)

## Русский обзор

### Документ v11: материалы сборки

Исторический v11 ввёл граф сборки v3 и nullable-материал определения детали с именем, плотностью в кг/м³ и источником. Материал задаётся явно, не подбирается автоматически. Миграция сохраняет идентификаторы и ставит `null` для неизвестного материала. Сейчас документ v12 продолжает использовать граф v3. Расчёт массы, центра масс и инерции описан в [отдельном контракте](../../docs/assembly-mass.md).

Это обзор, не полный перевод. Полные схемы, примеры и подробности контракта приведены на английском ниже.

## English reference

Document v11 upgrades the assembly graph from version 2 to version 3. Each
assembly part now stores an explicit nullable `material` record. Earlier
documents migrate on read; v10 parts receive `material: null`. Project envelope
version remains 1, and the v10 mate graph and prior document contracts remain
valid.

```json
{
  "assembly": {
    "version": 3,
    "units": "mm",
    "parts": [{
      "id": "part:<uuid>",
      "name": "Bracket",
      "body_id": "body:<uuid>",
      "material": {
        "name": "Aluminum",
        "density_kg_m3": 2700,
        "source": "supplier datasheet"
      }
    }],
    "subassemblies": [],
    "instances": [],
    "mates": []
  }
}
```

`material` is either `null` or an explicit record with a name, positive
`density_kg_m3`, and provenance `source`. CADYX does not infer material or
density. Assembly mass properties require assigned material data and report
mass in kilograms from mm-based geometry. See [assembly mass properties and
materials](../../docs/assembly-mass.md).

The v10 mate and rigid-kinematics contract is in
[Document Model v10](document-model-v10.md). Base features and scan import
remain specified in [Document Model v7](document-model-v7.md); scan reverse
engineering is specified in [scan reverse engineering](scan-reverse-engineering.md).
