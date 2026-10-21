# Publication readiness gate

[Русский](#русский) · [English](#english)

## Русский

Это список готовности к выпуску, а не подтверждение, что продукт безопасен или готов к производственному использованию. Репозиторий уже публичный, GitHub Wiki включена по решению владельца. Публикация архивов/релизов и публичное развёртывание сервиса — отдельные действия, не выполненные этим изменением. CADYX остаётся прототипом для Windows x64, а не готовым производственным сервисом.

### Исторический снимок от 2026-10-21

- `rkfsociety/CADYX`: приватный репозиторий, основная ветка `main`.
- Проверенная при первоначальной подготовке ревизия: `4e5bddf0d1d16436538d5909da17a2a0006f7674`.
- Присутствуют MIT-лицензия, README, руководства участия и безопасности, workflow CI.
- В проверенном тогда [запуске CI 39509118965](https://github.com/rkfsociety/CADYX/actions/runs/39509118965) source-job завершился ошибкой, Windows-job пропущен. Причина не установлена: журналы были недоступны через подключённый интерфейс.
- При той проверке GitHub Releases отсутствовали. Права на распространение файлов калибровки из установки Revo Scan не подтверждены.
- Локальные тесты при первоначальной подготовке не запускались. Этот снимок не подтверждает их успех и не описывает состояние будущего коммита.

Перед публикацией обновите доказательства для точной целевой ревизии.

### Текущее состояние (2026-10-21)

- `rkfsociety/CADYX` имеет видимость **public**, Wiki включена. Документационные RU/EN-переводы публикуются в `main`; двуязычная Wiki доступна в `master` репозитория Wiki. Не все спецификации уже переведены, незавершённые страницы помечены.
- Проверенный после этих изменений [запуск CI 39560320922](https://github.com/rkfsociety/CADYX/actions/runs/39560320922) завершился ошибкой в source-job на статической проверке mypy legacy-error baseline; Windows-job был пропущен. Это не является успешной проверкой нативной сборки или тестов.
- На момент последней проверки GitHub Releases отсутствовали. Права на распространение файлов калибровки из установки Revo Scan по-прежнему не подтверждены.
- Проверка истории показала, что бинарный набор попал в публичную историю через `c764cdf`, а `depth_scale.json` — через `4b7a287`. Источник — `%LOCALAPPDATA%\RevoScan5\cameraparam` на ПК с подключённым сканером; разрешение на распространение не установлено. Эта рабочая копия исключает файлы данных из текущего дерева и игнорирует локальные копии, но изменение ещё не отправлено. Старые blobs остаются в публичной истории.
- Согласованное сканирование Gitleaks v8.30.1 на локальном `HEAD` `23e9873` проверило все refs: 718 достижимых коммитов, 689 просканированных изменений. Единственное совпадение `generic-api-key` — тестовая константа в `tests/test_auth.py:22` из `3bc1d2d`, используемая только тестами авторизации. Подтверждённых активных учётных данных не обнаружено; на `origin/main` найдено то же совпадение.
- В текущих `.codex/`, `.agents/`, `tools/`, настройках, документации и тестах нет путей `C:\Users\...`, `C:\github\...`, `/home/...` или `/Users/...`. Тесты содержат фиктивные `D:\` пути и частный IP как значения для проверок; старые абсолютные `D:\...` в `.codex/config.toml` удалены в `0982a87`, но остались в истории.
- История `origin/main` раскрывает личный адрес автора в метаданных 203 коммитов; на локальном `HEAD` `23e9873` такой адрес стоял в 9 коммитах сверх `origin/main`, а локальная настройка Git выбирает тот же персональный почтовый домен. Настройку не менял. Исходные тексты адресов не содержат. Пользовательские файлы `framesmall.mkf`, `framesmall_points.csv` и `framesmall_points.ply` добавлены в `c764cdf`, удалены из дерева в `3b3d4b9`, но остались в публичной истории. Локальный `HEAD` отслеживает только README калибровки, тогда как `origin/main` всё ещё отслеживает набор Revo Scan и настройку устройства. Полный аудит лицензий зависимостей и прочих сторонних материалов не выполнялся.

### Обязательные проверки

- [x] **Текущие калибровочные файлы.** Происхождение прежнего набора задокументировано, разрешение на распространение не подтверждено. Бинарные файлы и настройка масштаба исключены из текущего Git-дерева, локальные пути игнорируются, инструкции захвата требуют калибровку пользователя. MIT CADYX не распространяется на эти данные.
- [ ] **Историческое раскрытие калибровки.** Данные остаются в уже публичной истории (`c764cdf`, `4b7a287`). Эта рабочая копия историю не переписывает; оценить и отдельно согласовать её переписывание. Коммит удаления не удаляет старые blobs.
- [ ] **Остальные сторонние права.** Проверить зависимости, лицензии/уведомления и другие материалы поставщиков перед распространением. Целевая проверка сканера не является полным аудитом лицензий.
- [x] **Текущие пути и секреты.** Проверены отслеживаемые `.codex/`, `.agents/`, `tools/`, настройки, заметки и примеры; Gitleaks v8.30.1 просканировал все refs и нашёл только тестовый placeholder в `tests/test_auth.py:22`. Реальные учётные данные не подтверждены.
- [ ] **Исторические пользовательские данные.** В публичной истории остаются адрес автора в метаданных, старые абсолютные `D:\...` из `.codex/config.toml` и удалённые пользовательские файлы `framesmall.*`. Удаление калибровок пока не отправлено, поэтому `origin/main` всё ещё содержит их. Историю не переписывал; такое изменение требует отдельного согласования.
- [ ] **CI целевого коммита.** Установить причину падения source-job. Требуются зелёные source- и нативный Windows-job. Пропущенная задача, недоступные журналы или обзор документации не доказывают прохождение тестов.
- [ ] **Зависимости и лицензии.** Повторно проверить версии, хеши, требования Windows, воспроизводимость сборки и лицензии Python, OCCT, Open3D, Node и инструментов сканера. Не включать скачанные среды и SDK в архив без проверки прав.
- [ ] **Документация и безопасность.** Проверить README, актуальные контракты, схемы, платформы, ограничения и действующий приватный канал сообщений об уязвимостях. Завершить русские переводы: Wiki явно отмечает страницы, где RU-версия ещё готовится. Встроенный HTTP-сервер нельзя открывать напрямую в Интернет.
- [ ] **Состав артефактов.** Проверить архивы и релизные файлы на `projects/`, `.runtime/`, сканы, `.env*`, ключи, токены, журналы, кеши и калибровку без разрешённых прав. Проверить согласие и лицензии образцов данных.
- [ ] **Отдельное разрешение на выпуск/развёртывание.** Согласовать версию/тег, changelog, ограничения, поддержку и контрольные суммы. Получить отдельное решение до создания релиза или публичного развёртывания.

### Границы подготовки

Публичная видимость репозитория и Wiki разрешены владельцем. Это не подтверждает прохождение проверок безопасности и лицензий и не означает, что CADYX готов к производственному развёртыванию. Push может запустить GitHub Actions; это не заменяет проверку нативного Windows-job.

## English

This checklist tracks release and deployment readiness; it does not certify production suitability. The repository is now **public** and GitHub Wiki is enabled at the owner's direction. Publishing release archives/GitHub Releases or deploying a public service are separate actions and have not been authorized or performed here. CADYX remains a Windows x64 development prototype, **not** a production-ready service.

## Snapshot at preparation (2026-10-21)

- Repository: `rkfsociety/CADYX`, **private**, default branch `main`.
- Inspected revision: `4e5bddf0d1d16436538d5909da17a2a0006f7674`.
- `LICENSE` (MIT), `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, and `.github/workflows/ci.yml` are present.
- Latest inspected Actions CI [run 39509118965](https://github.com/rkfsociety/CADYX/actions/runs/39509118965) for that revision: **source failed**, **windows skipped**. The failure cause was not established; job logs were inaccessible through the connected interface.
- No GitHub Releases were present when inspected.
- Scanner-specific assets under `tools/revopoint/calibration/` are documented as copied from a Revo Scan installation; redistribution rights are **not verified**. This checkout removes local calibration data from the tracked tree and ignores local copies. See the current-state review below for historical exposure.
- **No local tests were run** during this preparation. This review does not establish test success.

This is a historical snapshot. Refresh all evidence against the exact revision proposed for publication.

## Current state (2026-10-21)

- `rkfsociety/CADYX` is **public** and GitHub Wiki is enabled. RU/EN documentation translations are maintained in `main`; the bilingual Wiki is published from its `master` branch. Not all specifications are translated yet; incomplete pages are marked.
- The latest inspected Actions [run 39560320922](https://github.com/rkfsociety/CADYX/actions/runs/39560320922), after those changes, failed in the source job during the mypy legacy-error baseline check. The Windows job was skipped. This does not establish successful native build or tests.
- No GitHub Releases were present at the latest check. Redistribution rights for calibration files copied from Revo Scan remain unverified.
- The current review found the calibration bundle in public `origin/main` history at `c764cdf` and the device-derived scale setting at `4b7a287`. Local removal is not pushed, and old blobs remain reachable.
- Gitleaks v8.30.1 scanned all reachable refs at local `HEAD` `23e9873`: 718 reachable commits and 689 scanned changes. Its only `generic-api-key` alert is a test-only constant in `tests/test_auth.py:22` from `3bc1d2d`; the auth tests use it as a fixture. No active credential was confirmed. The `origin/main` scan found the same alert.
- Current `.codex/`, `.agents/`, `tools/`, configuration, documentation and tests contain no `C:\Users\...`, `C:\github\...`, `/home/...` or `/Users/...` workstation paths. Tests contain synthetic `D:\` paths and a private IP as test values. Older absolute `D:\...` values in `.codex/config.toml` were removed by `0982a87` but remain in history.
- Public `origin/main` history contains the author's personal mail address in metadata for 203 commits; the 9 local-only commits at `HEAD` `23e9873` use the same personal mail domain, and local Git configuration still selects it. That setting was not changed. Inspected source text contains no email addresses. User-provided `framesmall.mkf`, `framesmall_points.csv` and `framesmall_points.ply` were added by `c764cdf` and removed from the tree by `3b3d4b9`, but both commits remain public. Local `HEAD` tracks only the calibration README, while `origin/main` still tracks the Revo Scan bundle and device setting. Dependency licenses and other third-party materials have not been fully audited.

## Blocking gates

- [x] **Current calibration files:** Provenance of the former scanner calibration bundle is documented; permission to redistribute remains unconfirmed. The data files are removed from this checkout's tracked tree, local paths are ignored, and capture instructions require user-supplied local calibration. The CADYX MIT license does not grant rights over third-party material.
- [ ] **Historical calibration exposure:** The files remain reachable in already-public Git history (`c764cdf`, `4b7a287`). This checkout did not rewrite history; coordinate that separately. A normal deletion commit does not remove old blobs.
- [ ] **Other third-party rights:** Review dependencies, notices and vendor/third-party materials before distribution. The targeted scanner-file review is not a complete license audit.
- [x] **Current paths and secrets:** Reviewed tracked `.codex/`, `.agents/`, `tools/`, configuration, notes and examples. Gitleaks v8.30.1 scanned all refs; its only alert was a test placeholder in `tests/test_auth.py:22`. No real credentials were confirmed.
- [ ] **Historical personal data:** Public history contains the author's personal mail address in commit metadata, old absolute `D:\...` values from `.codex/config.toml`, and removed user-provided `framesmall.*` files. The calibration removal has not been pushed, so `origin/main` still contains the bundle. No history rewrite was performed; coordinate any rewrite separately.
- [ ] **Passing CI on target commit:** Investigate the failure of the `source` job and require green source checks **and** a successful native Windows `windows` job for the intended commit. A skipped job, unavailable logs, or documentation-only review is not evidence that tests pass.
- [ ] **Dependencies and licenses:** Revalidate pinned runtime versions, download hashes, supported Windows prerequisites, build reproducibility and vendor licenses/notices (Python, OCCT, Open3D, Node, scanner tools). Bundled data or binaries have additional distribution obligations. Do not add downloaded runtimes or generated SDK files to source archives without confirming rights.
- [ ] **Public-facing security/docs:** Ensure README, current implementation contracts, versioned schemas, supported platforms and limitations are accurate, including complete RU translations (the Wiki marks translations still in progress). Confirm a working private vulnerability-reporting path. The built-in HTTP host has no TLS and **must not** be exposed directly to the public Internet.
- [ ] **Artifact inventory:** Inspect the proposed public source archive and each release artifact for `projects/` user data, captures, `.runtime/`, `.env*`, keys, tokens, logs, caches and calibration assets without cleared redistribution rights. Review sample datasets for consent and licensing.
- [ ] **Explicit release/deployment approval:** Select and review a version/tag, changelog, known limitations, support expectations and artifact checksums as applicable. Obtain a distinct go/no-go decision before issuing a public release or deploying a public service.

## Boundaries of this preparation

The repository is public by explicit owner decision. This preparation does not satisfy release/deployment gates. A push may trigger GitHub Actions; that is **not** a local test run or proof that native Windows checks passed.
