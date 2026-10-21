# CADYX documentation map

[Русский](#русский) · [English](#english)

## Русский

Источник истины — текущий код `main`, прежде всего `cadyx/migrations.py`, `cadyx/capabilities.py`, `cadyx/web_routes.py`, `cadyx/api_contracts.py` и тесты. Карта служит навигацией, а не дополнительной спецификацией. Оболочка проекта — **v1**, документ — **v12**, граф сборки — **v3**. Старые версии контрактов описывают возможности на момент их введения; поздние версии могут менять ограничения.

### Начало работы

- [Запуск и эксплуатация на русском](operations.ru.md) / [English](operations.md): установка Windows x64, сеть, MCP и проверки.
- [Подготовка к публикации](../PUBLISHING.md#русский), [участие в разработке](../CONTRIBUTING.md#русский), [безопасность](../SECURITY.md#русский).
- [Правила агента](../AGENTS.md), [CADYX Operator](../.agents/skills/cadyx-operator/SKILL.md).
- [Архитектура](../.codex/docs/architecture.md), [viewport API](../.codex/docs/viewport-api.md), [хранение и миграции](../.codex/docs/project-format-v1.md).
- [Отмена Agent Runtime](../.codex/docs/agent-runtime-execution.md), [компактная инспекция](../.codex/docs/compact-inspection.md), [проверка проекта](../.codex/docs/project-checks.md).

### Контракты по темам

| Область | Документация |
| --- | --- |
| Плоскости эскизов, документ v12 | [XY/XZ/YZ, CUSTOM и FACE](sketch-workplanes.md) |
| Профили и зависимые тела | [Базовый контракт v7](../.codex/docs/document-model-v7.md), [сложные тела](advanced-solid-modeling.md), [трассировка кривых](../.codex/docs/curve-editing.md) |
| Параметры и перестроение | [Документ v8](../.codex/docs/document-model-v8.md) |
| Сборки, сопряжения и материалы | [v9](../.codex/docs/document-model-v9.md), [v10](../.codex/docs/document-model-v10.md), [v11](../.codex/docs/document-model-v11.md) |
| Масса и зазоры сборки | [Масса и инерция](assembly-mass.md), [дискретные положения](assembly-fit.md) |
| Проверка геометрии | [Инженерные проверки](engineering-validation.md), [спецификации модели](../.codex/docs/model-specifications.md), [проверка проекта](../.codex/docs/project-checks.md) |
| История ревизий | [Сравнение моделей](revision-comparison.md) |
| Реконструкция по сканам | [Сетка → CAD](../.codex/docs/scan-reverse-engineering.md), [STL-референсы](../.codex/docs/stl-reference.md) |
| Облака точек | [Обработка и история](point-cloud-processing.md) |
| Треугольные сетки | [Очистка и сглаживание](mesh-processing.md) |
| Опоры для реконструкции | [Плоскости, сечения, кривые](mesh-recognition.md) |
| Стандартные детали | [Каталог пресетов](standard-part-catalog.md) |
| Отложенные инструменты | [Backlog эскизов — не список реализованного](sketch-tools-backlog.md) |
| Revopoint | [Инструменты сканера](../tools/revopoint/README.md), [калибровка](../tools/revopoint/calibration/README.md) |

### Версии и достоверность

Исторические [v4](../.codex/docs/document-model-v4.md), [v5](../.codex/docs/document-model-v5.md), [v6](../.codex/docs/document-model-v6.md) и [v7](../.codex/docs/document-model-v7.md) описывают последовательные изменения эскизов, геометрии и импорта. v8 добавила параметры; v9 — граф сборки v1; v10 — сопряжения и граф v2; v11 — материалы и граф v3; [v12](../.codex/docs/document-model-v12.md) — сохраняемые системы координат эскизов.

Текущие команды доступны через `GET /api/v1/agent/capabilities`, ограниченная инспекция — через `GET /api/v1/agent/projects/{id}/inspection`. [CI](../.github/workflows/ci.yml) содержит source-проверки и нативные Windows-проверки. Документационная правка не подтверждает прохождение нативных тестов.

[План развития](../.codex/roadmap.md), [решения](../.codex/decisions.md) и [память](../.codex/memory.md) содержат контекст и намерения; не используйте их как перечень текущих возможностей без проверки кода.

### Языковое покрытие

README, эта карта, руководства участия, безопасности и публикации содержат русскую и английскую версии. Запуск описан в отдельных RU/EN-руководствах. В тематических технических документах есть русские обзоры и полный английский контракт; схемы, все примеры и исторические подробности **пока не переведены полностью**. Имена API, поля JSON, команды и пути сохраняются без перевода. Агентные правила и рабочая память `.codex/` не являются пользовательской локализацией. Wiki не включена, публикация не выполнялась.

## English

**Source of truth:** current `main` implementation, particularly `cadyx/migrations.py`, `cadyx/capabilities.py`, `cadyx/web_routes.py`, `cadyx/api_contracts.py` and the tests. This map is an entry point, **not** an extra versioned specification. The project envelope is **v1**, the document schema is **v12**, and the assembly graph schema is **v3** (introduced in Document v11). Older document-model files record an introduction-era contract; later versions can supersede their stated limitations.

## Get started and operate

- [Setup and operations](operations.md): Windows x64 setup, authentication, operator MCP integration, CI entry points.
- [Publication readiness gate](../PUBLISHING.md): security, third-party rights, Git history and CI evidence required before opening the repository or releasing software.
- [Contributing](../CONTRIBUTING.md), [security](../SECURITY.md) and [agent instructions](../AGENTS.md).
- [CADYX Operator skill](../.agents/skills/cadyx-operator/SKILL.md): modeling operations via Agent API/MCP, without browser automation.
- [Project architecture](../.codex/docs/architecture.md), [viewport contract](../.codex/docs/viewport-api.md), [project persistence/migrations](../.codex/docs/project-format-v1.md).
- [Agent Runtime cancellation](../.codex/docs/agent-runtime-execution.md), [compact inspection](../.codex/docs/compact-inspection.md), [project checks](../.codex/docs/project-checks.md).

## Current capabilities by topic

| Area | Contract |
| --- | --- |
| Sketch work planes, Document v12 | [Named YZ, CUSTOM and FACE](sketch-workplanes.md) |
| Sketch profiles and dependent solids | [Document v7 base contract](../.codex/docs/document-model-v7.md), [advanced solids](advanced-solid-modeling.md), [curve tracing](../.codex/docs/curve-editing.md) |
| Parameters and atomic rebuilds | [Document v8](../.codex/docs/document-model-v8.md) |
| Assembly graph, mates, materials | [v9](../.codex/docs/document-model-v9.md), [v10](../.codex/docs/document-model-v10.md), [v11](../.codex/docs/document-model-v11.md) |
| Assembly properties and fit | [Mass/inertia](assembly-mass.md), [discrete-pose fit](assembly-fit.md) |
| Geometry verification | [Engineering validation](engineering-validation.md), [model specifications](../.codex/docs/model-specifications.md), [project checks](../.codex/docs/project-checks.md) |
| Revision history and comparison | [Revision diff](revision-comparison.md) |
| PLY/scan reverse engineering | [Mesh-based scan-to-CAD](../.codex/docs/scan-reverse-engineering.md), [STL references](../.codex/docs/stl-reference.md) |
| Point-only cloud cleanup and history | [Processing tools and persisted stages](point-cloud-processing.md) |
| Triangle-mesh cleanup and smoothing | [Agent mesh processing](mesh-processing.md) |
| Reconstruction datums, sections and support curves | [Mesh recognition](mesh-recognition.md) |
| Catalog and standard parts | [Standard-part presets](standard-part-catalog.md) |
| Backlog, not implemented feature list | [Sketch tool backlog](sketch-tools-backlog.md) |
| Revopoint scanner tooling | [Scanner README](../tools/revopoint/README.md), [calibration notes](../tools/revopoint/calibration/README.md) |

## Version history (not current-limit declarations)

- [Document v4](../.codex/docs/document-model-v4.md), [v5](../.codex/docs/document-model-v5.md), [v6](../.codex/docs/document-model-v6.md), [v7](../.codex/docs/document-model-v7.md): successive sketch, geometry, import, and feature contracts.
- [Document v8](../.codex/docs/document-model-v8.md): named parameters and rebuilds.
- [Document v9](../.codex/docs/document-model-v9.md): assembly graph v1.
- [Document v10](../.codex/docs/document-model-v10.md): assembly mates, graph v2.
- [Document v11](../.codex/docs/document-model-v11.md): material records, graph v3.
- [Document v12](../.codex/docs/document-model-v12.md): current-schema entry point and [persistent work-plane frames](sketch-workplanes.md).

## Validation and scope

`GET /api/v1/agent/capabilities` discovers runtime routes and commands, and `GET /api/v1/agent/projects/{id}/inspection` supplies bounded read-only project inspection. Programmatic source/contract checks are in [CI](../.github/workflows/ci.yml). Tests that require Windows native OCCT/Open3D must run in the configured Windows environment. A source review or documentation-only commit **is not** evidence that native tests passed.

The [project roadmap](../.codex/roadmap.md), [architectural decisions](../.codex/decisions.md), and [memory](../.codex/memory.md) record history, design intent, and pending work; they must not be treated as a live capability inventory without cross-checking code.

## Language coverage

The README, this map, contribution guide, security policy and publication gates have Russian and English sections. Setup has separate RU/EN guides. Topic contracts provide Russian overviews and the full English reference; schemas, all examples and historical detail are **not fully translated yet**. API identifiers, JSON keys, commands and paths stay unchanged. Agent rules and working memory are not user-facing localization. GitHub Wiki remains disabled; documentation preparation does not authorize publication.
