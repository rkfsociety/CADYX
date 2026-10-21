# Revision-bound assembly interference and fit checks

[Полный русский перевод](assembly-fit.ru.md)

[Русский обзор](#русский-обзор) · [English reference](#english-reference)

## Русский обзор

### Зазоры и пересечения в сборке

`cadyx_validate_assembly_fit` и `/validations/assembly-fit` измеряют минимальное расстояние и общий объём размещённых конечных экземпляров, включая вложенные сборки. Результат привязан к ревизии и хешу геометрии. Проверяются сохранённое положение и до 16 заданных положений сопряжений, не более 32 пар и 128 измерений. Единицы — мм и мм³. `pass` относится только к проверенным положениям, не ко всему движению. Сетки и неопределённые численные случаи получают `untested`. Сохранение отчёта не меняет ревизию модели.

Это обзор, не полный перевод. Полные схемы, примеры и подробности контракта приведены на английском ниже.

## English reference

The read-only `POST /api/v1/agent/projects/{id}/validations/assembly-fit` endpoint and `cadyx_validate_assembly_fit` MCP tool measure OCCT solid-to-solid minimum distance and Boolean common volume for **placed leaf instances**, including nested subassemblies. Results bind to the exact saved document revision and a SHA-256 of the source geometry, assembly graph, selected instance pairs, and pose inputs. They may be persisted as immutable validation records without a model revision change. This endpoint is distinct from the generic `check_project` composite `assembly` stage, which is still explicitly `untested`; see [project checks](../.codex/docs/project-checks.md).

## Request

```json
{
  "expected_revision": 7,
  "minimum_clearance": 0.5,
  "tolerance": 0.01,
  "instance_pairs": [
    ["instance:first", "instance:second"]
  ],
  "pose_samples": [
    {
      "joint_values": [
        {"mate_id": "mate:slider", "offset_mm": 4.0}
      ]
    },
    {
      "joint_values": [
        {"mate_id": "mate:slider", "offset_mm": 8.0}
      ]
    }
  ],
  "persist": false
}
```

- `instance_pairs` is optional. Each ID is the flattened leaf `id` from the assembly scene, e.g. `instance:root/instance:child`, not merely a part or body ID. When omitted, all pairs are checked for assemblies with at most eight leaf instances. At most 32 distinct pairs are allowed; the pair must contain different leaf paths, although repeated instances may reference the same source part. Source B-reps are not fused.
- `pose_samples` is optional (0–16). The **saved pose is always sample 0**. Each additional sample overrides 1–16 existing mate joint coordinates and re-solves deterministic rigid placements on a copy of the assembly graph. A joint can be overridden once per sample; other mates retain their saved values. The existing mate validator rejects inadmissible joint settings, while incompatible closed-loop poses return `untested` with an explicit reason. This does not support simultaneous nonlinear loop solving.
- The operation admits at most 128 pair/pose measurements total. The assembly may have up to 256 flattened leaves for selection. Required `minimum_clearance` and `tolerance` are finite non-negative numbers in mm. The document's assembly placement convention is mm; reported intersection volumes are mm³.
- `persist` defaults to `false`. Persisted results use the existing validation SQLite sidecar.

## Result interpretation

Each sample returns its index, requested joint values, solved leaf-instance placements, status and selected pair measurements. Each measurable pair includes both instance paths and underlying body IDs, the measured distance and the deflection-based interval, intersection volume, OCCT inner-solution flag, intersection classification (`interfering`, `touching_or_indeterminate`, `separate`), and two closest-point witnesses in global XYZ.

An exact OCCT Boolean common volume above numerical resolution, or a confirmed inner solution, yields `fail`. The fit check also fails when the upper distance bound is smaller than `max(0, minimum_clearance - tolerance)`. It passes when the solids are separate and the lower bound satisfies that threshold. Contacts near the deflection limit and indeterminate numerical cases remain `untested`. Invalid/missing B-reps fail explicitly; triangle-mesh-only bodies and unsolvable sampled kinematic poses return `untested`.

The overall status is `fail` if any evaluated pose fails, otherwise `untested` if any measurement is inconclusive, otherwise `pass`. `coverage: "discrete_poses_only"` and `continuous_motion_certified: false` are always returned. A pass **does not certify clearance throughout the continuous path between samples**, strength, tolerances of manufactured parts or fit at an unsampled pose. Callers must choose and justify adequate samples.

Implementation: `cadyx/assembly_fit.py`, `cadyx/kernel.py`, `cadyx/core_services.py`; the HTTP/MCP contract is derived from `cadyx/api_contracts.py`.
