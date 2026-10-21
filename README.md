# CADYX

[Русский](#русский) · [English](#english)

## Русский

CADYX — прототип CAD-системы, в которой ИИ-агенты создают и редактируют модели через API. Браузер показывает геометрию и ход работы; Core сохраняет модель и защищает изменения блокировкой проекта.

Среда: **Windows x64**, PowerShell 7+, CMake и MSVC C++ Build Tools. Версии компонентов закреплены в [конфигурации среды](scripts/runtime-versions.json).

```powershell
.\scripts\setup-runtime.ps1
.\.runtime\python\python.exe -m cadyx
```

Откройте [локальный интерфейс](http://127.0.0.1:8888). Подробнее: [запуск и настройка](docs/operations.ru.md), [документация](docs/README.md#русский), [разработка](CONTRIBUTING.md#русский), [безопасность](SECURITY.md#русский).

Исходный репозиторий уже публичен, но CADYX остаётся прототипом. До выпуска релиза или публичного развёртывания сервиса выполните [проверки готовности](PUBLISHING.md#русский). Встроенный HTTP-сервер нельзя напрямую открывать в Интернет. Лицензия кода — [MIT](LICENSE); сторонние материалы имеют собственные условия. См. также [двуязычную Wiki](https://github.com/rkfsociety/CADYX/wiki).

## English

CADYX is a CAD development prototype where AI agents create and edit models through an API. The browser displays geometry and progress; Core persists the model and protects edits with a project lease.

Environment: **Windows x64**, PowerShell 7+, CMake and MSVC C++ Build Tools. Component versions are pinned in the [runtime configuration](scripts/runtime-versions.json).

```powershell
.\scripts\setup-runtime.ps1
.\.runtime\python\python.exe -m cadyx
```

Open the [local viewer](http://127.0.0.1:8888). Read the [setup guide](docs/operations.md), [documentation map](docs/README.md#english), [contribution guide](CONTRIBUTING.md#english), and [security policy](SECURITY.md#english).

The source repository is public, while CADYX remains a development prototype. Complete the [release readiness gates](PUBLISHING.md#english) before publishing releases or deploying a public service. Do not expose the built-in HTTP server directly to the Internet. Source code is [MIT-licensed](LICENSE); third-party materials retain their own terms. See the [bilingual Wiki](https://github.com/rkfsociety/CADYX/wiki).
