# Contributing to CADYX

[Русский](#русский) · [English](#english)

## Русский

CADYX — прототип CAD-системы с управлением через ИИ-агентов, целевая платформа — **Windows x64**. Перед изменениями прочитайте [AGENTS.md](AGENTS.md), [правила проекта](.codex/rules.md), [карту документации](docs/README.md#русский), релевантные контракты и тесты.

### Подготовка и проверка

Установите PowerShell 7+, CMake и MSVC C++ Build Tools. Из корня репозитория:

```powershell
.\scripts\setup-runtime.ps1 -WithNode
.\.runtime\python\python.exe -m unittest discover -s tests -v
.\.runtime\python\python.exe -m compileall -q cadyx tests scripts
```

Статические проверки выполняются в обычной среде Python:

```powershell
python -m pip install -r requirements-dev.txt
python -m ruff check .
python scripts/check-static-types.py
python -m unittest discover -s tests -p test_source_contracts.py -v
```

Браузерные тесты Node и полная проверка Windows определены в [CI](.github/workflows/ci.yml). Успех source-job не означает успех нативного Windows-job. Выбирайте локальные проверки пропорционально изменению; полный набор выше нужен для полной верификации.

### Архитектура и вклад

- Агент создаёт и изменяет модели через версионированный Agent API и общий Command System. Браузер служит для наблюдения; действия человека обязаны учитывать блокировку и активность проекта.
- Геометрия, документ и история должны соответствовать одной ревизии. Изменения проверяются и сохраняются атомарно.
- Используйте закреплённые версии среды; перед обновлением проверяйте официальные стабильные выпуски.
- Изменение поведения требует релевантных проверок и обновления канонических контрактов. Не ослабляйте baseline ради зелёного результата.
- Текущие возможности описывайте в тематических руководствах; исторические версии схем не выдавайте за актуальный перечень ограничений. Сверяйте API с `cadyx/web_routes.py`, `cadyx/capabilities.py` и `cadyx/migrations.py`.
- Модели пользователей, сканы, секреты и сгенерированная среда не входят в Git.
- Поддерживайте русскую и английскую навигацию. В коротких пользовательских руководствах обновляйте обе языковые версии вместе. Для технических контрактов явно различайте русское описание и полный английский контракт; краткое описание не является полным переводом схем и примеров.

Сопровождающие работают в `main` по [правилам проекта](.codex/rules.md); внешние участники могут предложить PR из fork. Укажите цель, область, интерфейсы и результаты проверок. Не добавляйте постороннее форматирование и большие генерируемые файлы. Уязвимости сообщайте **приватно** по [политике безопасности](SECURITY.md#русский).

Код лицензирован по [MIT](LICENSE). До отправки вклада убедитесь, что имеете право предоставить его на условиях этой лицензии.

## English

CADYX is an AI-first CAD development prototype, currently verified on **Windows x64**. Before making changes, read [AGENTS.md](AGENTS.md), [`.codex/rules.md`](.codex/rules.md), the [documentation map](docs/README.md), the relevant architecture documents, and the existing tests.

## Set up and verify

Install PowerShell 7+, CMake and the MSVC C++ Build Tools. From the repository root:

```powershell
.\scripts\setup-runtime.ps1 -WithNode
.\.runtime\python\python.exe -m unittest discover -s tests -v
.\.runtime\python\python.exe -m compileall -q cadyx tests scripts
```

For the source-only static gate, use a compatible Python installation and run:

```powershell
python -m pip install -r requirements-dev.txt
python -m ruff check .
python scripts/check-static-types.py
python -m unittest discover -s tests -p test_source_contracts.py -v
```

The Node browser tests and complete Windows verification steps are defined in [`.github/workflows/ci.yml`](.github/workflows/ci.yml). Check them before calling a change verified; a green source-only gate does not imply that the native Windows workflow passed.

## Architecture invariants

- CAD agents create and modify models using the versioned Agent API and shared Command System, not by driving the browser.
- The browser is primarily a read-only observation surface. Human actions must respect the per-project lease and activity gate; do not bypass it.
- Keep geometry, saved document state, and command history revision-consistent and validate edits atomically.
- Use the repository's reviewed runtime locks and verify official stable releases before changing dependency versions.
- Add targeted regression coverage and update authoritative contracts when behavior changes; never suppress failures by weakening a baseline.
- Document current behavior in topic-specific guides and the current-schema map, without rewriting historical version contracts as if older schemas were current. Verify API/command names against `cadyx/web_routes.py`, `cadyx/capabilities.py`, and `cadyx/migrations.py`.
- Keep actual project models, scans, credentials and generated runtime files out of Git.
- Maintain Russian and English navigation. Update both language versions of short user guides together. For technical contracts, explicitly distinguish the Russian overview from the full English contract; an overview is not a complete translation of schemas and examples.

## Proposing changes

Repository maintainers currently work directly on `main` per [project rules](.codex/rules.md). External contributors can propose a pull request from a fork. Provide the scope, reason, affected interfaces and test results. Avoid unrelated formatting or large generated-file changes. Security reports belong in a **private** channel as described in [SECURITY.md](SECURITY.md).

**Licensing:** CADYX is licensed under the [MIT License](LICENSE). Before submitting a contribution, ensure you have the right to submit and license that work under the project's license.
