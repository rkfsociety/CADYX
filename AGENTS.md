# CADYX repository instructions

Before substantive CADYX work, read [`.codex/rules.md`](.codex/rules.md) and [`.codex/memory.md`](.codex/memory.md), then inspect the relevant code and canonical contracts. Keep this file as the repository entry point; project-specific workflow and architecture belong in the linked sources.

For requests to create, inspect, or change CAD models, use the [CADYX Operator skill](.agents/skills/cadyx-operator/SKILL.md) and its connected CADYX Agent API tools. Treat the browser as a read-only observation surface. For application development or maintenance, follow `.codex/rules.md` and the task-relevant architecture and API docs. Start from the [documentation map](docs/README.md); check `cadyx/migrations.py` and the relevant implementation before treating an older versioned document contract as the current schema.
