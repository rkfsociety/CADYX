# CADYX documentation map

[Русский](#русский) · [English](#english)

## Русский

Источник истины — текущий код `main`, прежде всего `cadyx/migrations.py`, `cadyx/capabilities.py`, `cadyx/web_routes.py`, `cadyx/api_contracts.py` и тесты. Карта служит навигацией, а не дополнительной спецификацией. Оболочка проекта — **v1**, документ — **v12**, граф сборки — **v3**. Старые версии контрактов описывают возможности на момент их введения; поздние версии могут менять ограничения.

### Начало работы

- [Запуск и эксплуатация на русском](operations.ru.md) / [English](operations.md): установка Windows x64, сеть, MCP и проверки.
- [Подготовка к публикации](../PUBLISHING.md#русский), [участие в разработке](../CONTRIBUTING.md#русский), [безопасность](../SECURITY.md#русский).
- [Правила агента](../AGENTS.md), [CADYX Operator](../.agents/skills/cadyx-operator/SKILL.md).
- [Архитектура EN](../.codex/docs/architecture.md) · [RU](../.codex/docs/architecture.ru.md), [viewport API EN](../.codex/docs/viewport-api.md) · [RU](../.codex/docs/viewport-api.ru.md), [хранение и миграции EN](../.codex/docs/project-format-v1.md) · [RU](../.codex/docs/project-format-v1.ru.md).
- [Отмена Agent Runtime](../.codex/docs/agent-runtime-execution.md), [компактная инспекция](../.codex/docs/compact-inspection.md), [проверка проекта](../.codex/docs/project-checks.md).

### Контракты по темам

| Область | Документация |
| --- | --- |
| Плоскости эскизов, документ v12 | [XY/XZ/YZ, CUSTOM и FACE](sketch-workplanes.md) |
| Профили и зависимые тела | [Базовый контракт v7](../.codex/docs/document-model-v7.md), [сложные тела EN](advanced-solid-modeling.md) · [RU](advanced-solid-modeling.ru.md), [трассировка кривых EN](../.codex/docs/curve-editing.md) · [RU](../.codex/docs/curve-editing.ru.md) |
| Параметры и перестроение | [Документ v8 EN](../.codex/docs/document-model-v8.md) · [RU](../.codex/docs/document-model-v8.ru.md) |
| Сборки, сопряжения и материалы | [v9 EN](../.codex/docs/document-model-v9.md) · [RU](../.codex/docs/document-model-v9.ru.md), [v10 EN](../.codex/docs/document-model-v10.md) · [RU](../.codex/docs/document-model-v10.ru.md), [v11 EN](../.codex/docs/document-model-v11.md) · [RU](../.codex/docs/document-model-v11.ru.md) |
| Масса и зазоры сборки | [Масса EN](assembly-mass.md) · [RU](assembly-mass.ru.md), [положения EN](assembly-fit.md) · [RU](assembly-fit.ru.md) |
| Проверка геометрии | [Инженерные проверки EN](engineering-validation.md) · [RU](engineering-validation.ru.md), [спецификации модели EN](../.codex/docs/model-specifications.md) · [RU](../.codex/docs/model-specifications.ru.md), [проверка проекта EN](../.codex/docs/project-checks.md) · [RU](../.codex/docs/project-checks.ru.md) |
| История ревизий | [Сравнение моделей EN](revision-comparison.md) · [RU](revision-comparison.ru.md) |
| Реконструкция по сканам | [Сетка → CAD EN](../.codex/docs/scan-reverse-engineering.md) · [RU](../.codex/docs/scan-reverse-engineering.ru.md), [STL-референсы EN](../.codex/docs/stl-reference.md) · [RU](../.codex/docs/stl-reference.ru.md) |
| Облака точек | [Обработка EN](point-cloud-processing.md) · [RU](point-cloud-processing.ru.md) |
| Треугольные сетки | [Обработка EN](mesh-processing.md) · [RU](mesh-processing.ru.md) |
| Опоры для реконструкции | [Распознавание EN](mesh-recognition.md) · [RU](mesh-recognition.ru.md) |
| Стандартные детали | [Каталог EN](standard-part-catalog.md) · [RU](standard-part-catalog.ru.md) |
| Отложенные инструменты | [Backlog эскизов EN](sketch-tools-backlog.md) · [RU](sketch-tools-backlog.ru.md) |
| Revopoint | [Инструменты сканера — EN, RU в процессе](../tools/revopoint/README.md), [калибровка RU/EN](../tools/revopoint/calibration/README.md) |

### Версии и достоверность

Исторические [v4 EN](../.codex/docs/document-model-v4.md) · [RU](../.codex/docs/document-model-v4.ru.md), [v5 EN](../.codex/docs/document-model-v5.md) · [RU](../.codex/docs/document-model-v5.ru.md), [v6 EN](../.codex/docs/document-model-v6.md) · [RU](../.codex/docs/document-model-v6.ru.md) и [v7 EN](../.codex/docs/document-model-v7.md) · [RU](../.codex/docs/document-model-v7.ru.md) описывают последовательные изменения эскизов, геометрии и импорта. [v8](../.codex/docs/document-model-v8.ru.md) добавила параметры; [v9](../.codex/docs/document-model-v9.ru.md) — граф сборки v1; [v10](../.codex/docs/document-model-v10.ru.md) — сопряжения и граф v2; [v11](../.codex/docs/document-model-v11.md) · [RU](../.codex/docs/document-model-v11.ru.md) — материалы и граф v3; [v12 EN](../.codex/docs/document-model-v12.md) · [RU](../.codex/docs/document-model-v12.ru.md) — сохраняемые плоскости эскизов.

Текущие команды доступны через `GET /api/v1/agent/capabilities`, ограниченная инспекция — через `GET /api/v1/agent/projects/{id}/inspection`. [CI](../.github/workflows/ci.yml) содержит source-проверки и нативные Windows-проверки. Документационная правка не подтверждает прохождение нативных тестов.

[План развития](../.codex/roadmap.md), [решения](../.codex/decisions.md) и [память](../.codex/memory.md) содержат контекст и намерения; не используйте их как перечень текущих возможностей без проверки кода.

### Языковое покрытие

README, эта карта, руководства участия, безопасности и выпуска содержат русскую и английскую версии. Запуск описан в отдельных RU/EN-руководствах. GitHub Wiki включена и содержит RU/EN-навигацию, а также доступные полные пары спецификаций; незавершённые переводы явно отмечены. Завершите все русские версии и обновите Wiki по мере готовности. Имена API, поля JSON, команды и пути сохраняются без перевода. Агентные правила и рабочая память `.codex/` не являются пользовательской локализацией.

## English

**Source of truth:** current `main` implementation, particularly `cadyx/migrations.py`, `cadyx/capabilities.py`, `cadyx/web_routes.py`, `cadyx/api_contracts.py` and the tests. This map is an entry point, **not** an extra versioned specification. The project envelope is **v1**, the document schema is **v12**, and the assembly graph schema is **v3** (introduced in Document v11). Older document-model files record an introduction-era contract; later versions can supersede their stated limitations.

## Get started and operate

- [Setup and operations](operations.md): Windows x64 setup, authentication, operator MCP integration, CI entry points.
- [Publication readiness gate](../PUBLISHING.md): security, third-party rights, Git history and CI evidence required before opening the repository or releasing software.
- [Contributing](../CONTRIBUTING.md), [security](../SECURITY.md) and [agent instructions](../AGENTS.md).
- [CADYX Operator skill](../.agents/skills/cadyx-operator/SKILL.md): modeling operations via Agent API/MCP, without browser automation.
- [Project architecture](../.codex/docs/architecture.md) · [RU](../.codex/docs/architecture.ru.md), [viewport contract](../.codex/docs/viewport-api.md) · [RU](../.codex/docs/viewport-api.ru.md), [project persistence/migrations](../.codex/docs/project-format-v1.md) · [RU](../.codex/docs/project-format-v1.ru.md).
- [Agent Runtime cancellation](../.codex/docs/agent-runtime-execution.md) · [RU](../.codex/docs/agent-runtime-execution.ru.md), [compact inspection](../.codex/docs/compact-inspection.md) · [RU](../.codex/docs/compact-inspection.ru.md), [project checks](../.codex/docs/project-checks.md) · [RU](../.codex/docs/project-checks.ru.md).
- Russian scan reverse-engineering contract: [RU](../.codex/docs/scan-reverse-engineering.ru.md).

## Current capabilities by topic

| Area | Contract |
| --- | --- |
| Sketch work planes, Document v12 | [Named YZ, CUSTOM and FACE EN](sketch-workplanes.md) · [RU](sketch-workplanes.ru.md) |
| Sketch profiles and dependent solids | [Document v7 base contract](../.codex/docs/document-model-v7.md) · [RU](../.codex/docs/document-model-v7.ru.md), [advanced solids](advanced-solid-modeling.md) · [RU](advanced-solid-modeling.ru.md), [curve tracing](../.codex/docs/curve-editing.md) · [RU](../.codex/docs/curve-editing.ru.md) |
| Parameters and atomic rebuilds | [Document v8](../.codex/docs/document-model-v8.md) · [RU](../.codex/docs/document-model-v8.ru.md) |
| Assembly graph, mates, materials | [v9 EN](../.codex/docs/document-model-v9.md) · [RU](../.codex/docs/document-model-v9.ru.md), [v10 EN](../.codex/docs/document-model-v10.md) · [RU](../.codex/docs/document-model-v10.ru.md), [v11 EN](../.codex/docs/document-model-v11.md) · [RU](../.codex/docs/document-model-v11.ru.md) |
| Assembly properties and fit | [Mass/inertia](assembly-mass.md), [discrete-pose fit](assembly-fit.md) |
| Geometry verification | [Engineering validation](engineering-validation.md), [model specifications](../.codex/docs/model-specifications.md), [project checks](../.codex/docs/project-checks.md) |
| Revision history and comparison | [Revision diff](revision-comparison.md) |
| PLY/scan reverse engineering | [Mesh-based scan-to-CAD](../.codex/docs/scan-reverse-engineering.md), [STL references](../.codex/docs/stl-reference.md) · [RU](../.codex/docs/stl-reference.ru.md) |
| Point-only cloud cleanup and history | [Processing tools and persisted stages](point-cloud-processing.md) |
| Triangle-mesh cleanup and smoothing | [Agent mesh processing](mesh-processing.md) |
| Reconstruction datums, sections and support curves | [Mesh recognition](mesh-recognition.md) |
| Catalog and standard parts | [Standard-part presets](standard-part-catalog.md) |
| Backlog, not implemented feature list | [Sketch tool backlog](sketch-tools-backlog.md) |
| Revopoint scanner tooling | [Scanner README — EN, RU in progress](../tools/revopoint/README.md), [calibration notes RU/EN](../tools/revopoint/calibration/README.md) |

## Version history (not current-limit declarations)

- [Document v4](../.codex/docs/document-model-v4.md) · [RU](../.codex/docs/document-model-v4.ru.md), [v5](../.codex/docs/document-model-v5.md) · [RU](../.codex/docs/document-model-v5.ru.md), [v6](../.codex/docs/document-model-v6.md) · [RU](../.codex/docs/document-model-v6.ru.md), [v7 EN](../.codex/docs/document-model-v7.md) · [RU](../.codex/docs/document-model-v7.ru.md): successive sketch, geometry, import, and feature contracts.
- [Document v8](../.codex/docs/document-model-v8.md): named parameters and rebuilds.
- [Document v9](../.codex/docs/document-model-v9.md): assembly graph v1.
- [Document v10](../.codex/docs/document-model-v10.md): assembly mates, graph v2.
- [Document v11](../.codex/docs/document-model-v11.md) · [RU](../.codex/docs/document-model-v11.ru.md): material records, graph v3.
- [Document v12 EN](../.codex/docs/document-model-v12.md) · [RU](../.codex/docs/document-model-v12.ru.md): current-schema entry point and [persistent work-plane frames](sketch-workplanes.md).

## Validation and scope

`GET /api/v1/agent/capabilities` discovers runtime routes and commands, and `GET /api/v1/agent/projects/{id}/inspection` supplies bounded read-only project inspection. Programmatic source/contract checks are in [CI](../.github/workflows/ci.yml). Tests that require Windows native OCCT/Open3D must run in the configured Windows environment. A source review or documentation-only commit **is not** evidence that native tests passed.

The [project roadmap](../.codex/roadmap.md), [architectural decisions](../.codex/decisions.md), and [memory](../.codex/memory.md) record history, design intent, and pending work; they must not be treated as a live capability inventory without cross-checking code.

## Language coverage

The README, this map, contribution guide, security policy and release readiness guide have Russian and English sections. Setup has separate RU/EN guides. GitHub Wiki is enabled and has RU/EN navigation with completed specification pairs; unfinished translations are explicitly marked. Complete remaining Russian counterparts and update the Wiki as they are ready. API identifiers, JSON keys, commands and paths stay unchanged. Agent rules and working memory are not user-facing localization.
