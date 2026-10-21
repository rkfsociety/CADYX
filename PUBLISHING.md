# Publication readiness gate

[Русский](#русский) · [English](#english)

## Русский

Это список подготовки, **не разрешение на публикацию**. Изменение видимости, публикация архива, GitHub Release и публичное развёртывание требуют отдельного решения после прохождения проверок. CADYX остаётся прототипом для Windows x64, а не готовым публичным сервисом. GitHub Wiki пока не включается; документация готовится внутри приватного репозитория.

### Исторический снимок от 2026-10-21

- `rkfsociety/CADYX`: приватный репозиторий, основная ветка `main`.
- Проверенная при первоначальной подготовке ревизия: `4e5bddf0d1d16436538d5909da17a2a0006f7674`.
- Присутствуют MIT-лицензия, README, руководства участия и безопасности, workflow CI.
- В проверенном тогда [запуске CI 39509118965](https://github.com/rkfsociety/CADYX/actions/runs/39509118965) source-job завершился ошибкой, Windows-job пропущен. Причина не установлена: журналы были недоступны через подключённый интерфейс.
- При той проверке GitHub Releases отсутствовали. Права на распространение файлов калибровки из установки Revo Scan не подтверждены.
- Локальные тесты при первоначальной подготовке не запускались. Этот снимок не подтверждает их успех и не описывает состояние будущего коммита.

Перед публикацией обновите доказательства для точной целевой ревизии.

### Обязательные проверки

- [ ] **Права на распространение.** Установить происхождение и права на калибровочные бинарные файлы, настройки устройства и сторонние материалы. MIT-лицензия CADYX не предоставляет права на них. При отсутствии разрешения отдельно заменить или удалить набор, обновить настройку сканера и оценить содержимое истории Git.
- [ ] **Содержимое и история Git.** Проверить отслеживаемые пути, особенно `.codex/`, `.agents/`, `tools/`, настройки, инженерные заметки и примеры на секреты, локальные пути и пользовательские данные. Проверить достижимые коммиты, ветки и теги, выполнить согласованное сканирование секретов. Раскрытые учётные данные отозвать; переписывание истории согласовать отдельно.
- [ ] **CI целевого коммита.** Установить причину падения source-job. Требуются зелёные source- и нативный Windows-job. Пропущенная задача, недоступные журналы или обзор документации не доказывают прохождение тестов.
- [ ] **Зависимости и лицензии.** Повторно проверить версии, хеши, требования Windows, воспроизводимость сборки и лицензии Python, OCCT, Open3D, Node и инструментов сканера. Не включать скачанные среды и SDK в архив без проверки прав.
- [ ] **Документация и безопасность.** Проверить README, актуальные контракты, схемы, платформы, ограничения и действующий приватный канал сообщений об уязвимостях. Проверить русский и английский тексты. Встроенный HTTP-сервер нельзя открывать напрямую в Интернет.
- [ ] **Состав артефактов.** Проверить архивы и релизные файлы на `projects/`, `.runtime/`, сканы, `.env*`, ключи, токены, журналы, кеши и калибровку без разрешённых прав. Проверить согласие и лицензии образцов данных.
- [ ] **Отдельное разрешение.** Согласовать версию/тег, changelog, ограничения, поддержку и контрольные суммы. Получить явное решение до изменения видимости или выпуска релиза.

### Границы подготовки

Работа с существующей приватной веткой не означает разрешения на публичное распространение. Push может запустить GitHub Actions; это не локальный запуск тестов. Репозиторий остаётся приватным; Wiki, релиз и публичное развёртывание ожидают отдельного указания.

## English

This is a **pre-publication checklist**, not a release announcement or authorization to make CADYX public. Changing repository visibility, publishing source archives, creating GitHub Releases or deploying a public host requires a separate explicit decision once the gates below pass. CADYX remains a Windows x64 development prototype, **not** a production-ready public service.

GitHub Wiki remains disabled for now; prepare documentation inside the private repository. Enabling Wiki also awaits a separate instruction.

## Snapshot at preparation (2026-10-21)

- Repository: `rkfsociety/CADYX`, **private**, default branch `main`.
- Inspected revision: `4e5bddf0d1d16436538d5909da17a2a0006f7674`.
- `LICENSE` (MIT), `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, and `.github/workflows/ci.yml` are present.
- Latest inspected Actions CI [run 39509118965](https://github.com/rkfsociety/CADYX/actions/runs/39509118965) for that revision: **source failed**, **windows skipped**. The failure cause was not established; job logs were inaccessible through the connected interface.
- No GitHub Releases were present when inspected.
- Scanner-specific assets under `tools/revopoint/calibration/` are tracked and documented as copied from a Revo Scan installation. Their public redistribution rights are **not verified**.
- **No local tests were run** during this preparation. This review does not establish test success.

This is a historical snapshot. Refresh all evidence against the exact revision proposed for publication.

## Blocking gates

- [ ] **Redistribution rights:** Establish the provenance and redistribution rights of scanner calibration binaries, device-derived settings and other vendor or third-party assets. Document each decision. The CADYX MIT license does not grant rights over third-party materials. If rights cannot be confirmed, remove or replace the tracked bundle in a separately reviewed change, adapt scanner setup/docs, and consider exposure in Git history.
- [ ] **Tracked content and history:** Review all current tracked paths, particularly `.codex/`, `.agents/`, `tools/`, configuration, engineering notes and examples, for sensitive data, machine-specific paths, customer models, credentials and material unsuitable for public distribution. Audit all reachable commits, tags and branches; perform an approved secret scan and resolve findings. `.gitignore` does not erase already committed files or history. Revoke exposed credentials; coordinate history rewriting separately.
- [ ] **Passing CI on target commit:** Investigate the failure of the `source` job and require green source checks **and** a successful native Windows `windows` job for the intended commit. A skipped job, unavailable logs, or documentation-only review is not evidence that tests pass.
- [ ] **Dependencies and licenses:** Revalidate pinned runtime versions, download hashes, supported Windows prerequisites, build reproducibility and vendor licenses/notices (Python, OCCT, Open3D, Node, scanner tools). Bundled data or binaries have additional distribution obligations. Do not add downloaded runtimes or generated SDK files to source archives without confirming rights.
- [ ] **Public-facing security/docs:** Ensure README, current implementation contracts, versioned schemas, supported platforms and limitations are accurate. Confirm a working private vulnerability-reporting path. The built-in HTTP host has no TLS and **must not** be exposed directly to the public Internet.
- [ ] **Artifact inventory:** Inspect the proposed public source archive and each release artifact for `projects/` user data, captures, `.runtime/`, `.env*`, keys, tokens, logs, caches and calibration assets without cleared redistribution rights. Review sample datasets for consent and licensing.
- [ ] **Explicit release approval:** Select and review a version/tag, changelog, known limitations, support expectations and artifact checksums as applicable. Obtain a distinct go/no-go decision before changing visibility or issuing a public release.

## Boundaries of this preparation

Changes may be committed and pushed to the **existing private `main`** branch without changing visibility or making a release. A push can automatically trigger the repository's GitHub Actions workflow; that is **not** a local test run. Leave the repository private and do not create a release or public deployment until the gates pass and publication is separately authorized.
