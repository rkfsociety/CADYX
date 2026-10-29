# Document Model v12 / Документ модели v12

[Русский](#русский) · [English](#english)

[Полный русский перевод](document-model-v12.ru.md)

## Русский

Документ v12 добавляет сохраняемые рабочие плоскости эскизов YZ, CUSTOM и FACE. Текущая версия документа — **v17**; её краткое описание находится в [контракте v17](document-model-v17.ru.md).

v12 добавляет сохраняемые рабочие плоскости эскизов YZ, CUSTOM и FACE, сохраняя прежние XY/XZ, локальные координаты и смещения. Каноническое описание полей, правил привязки к грани, миграции v11→v12 и ограничений операций находится в [контракте рабочих плоскостей](../../docs/sketch-workplanes.md). Эта страница — входная точка текущей версии, не копия схемы.

Остальные действующие контракты:

- [Формат проекта и сохранение](project-format-v1.md).
- [Геометрия, импорт и экспорт](document-model-v7.md), [параметры и перестроение](document-model-v8.md).
- [Граф сборки](document-model-v9.md), [сопряжения](document-model-v10.md), [материалы](document-model-v11.md).
- [Карта текущих возможностей](../../docs/README.md#русский).

Старые документы описывают контракт на момент введения; поздние версии и текущая реализация могут заменять их ограничения. Изменения модели проходят общий Command System с проверкой ревизии, блокировкой проекта и атомарным сохранением. Чтение и миграция в памяти сами по себе не переписывают пользовательские файлы.

## English

Document v12 introduced persisted work-plane frames. The current document version is **v17**, the project envelope remains **v1**, and the assembly graph is **v8**. A schema version is distinct from a project revision: `expected_revision` must match that project's saved revision.

v12 adds persisted YZ, CUSTOM and FACE sketch work planes while preserving legacy XY/XZ sketches, local coordinates and offsets. The authoritative field definitions, face-attachment rules, v11→v12 migration and operation limitations live in the [work-plane contract](../../docs/sketch-workplanes.md). The current document version is **v17**; see the [v17 entry point](document-model-v17.md).

Other applicable contracts:

- [Project format and persistence](project-format-v1.md).
- [Geometry, import and export](document-model-v7.md), [parameters and rebuilds](document-model-v8.md).
- [Assembly graph](document-model-v9.md), [mates](document-model-v10.md), [materials](document-model-v11.md).
- [Current capability map](../../docs/README.md#english).

Older documents describe introduction-era contracts; later versions and the current implementation may supersede their limitations. Model mutations use the shared Command System with revision checks, a project edit lease and atomic persistence. Reading and in-memory migration alone do not rewrite user files.
