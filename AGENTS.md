# CADYX repository instructions

Before substantive CADYX work, read [`.codex/rules.md`](.codex/rules.md) and [`.codex/memory.md`](.codex/memory.md), then inspect the relevant code and canonical contracts. Keep this file as the repository entry point; project-specific workflow and architecture belong in the linked sources.

For requests to create, inspect, or change CAD models, use the [CADYX Operator skill](.agents/skills/cadyx-operator/SKILL.md) and its connected CADYX Agent API tools. Treat the browser as a read-only observation surface. For application development or maintenance, follow `.codex/rules.md` and the task-relevant architecture and API docs. Start from the [documentation map](docs/README.md); check `cadyx/migrations.py` and the relevant implementation before treating an older versioned document contract as the current schema.

## Worktree and commit hygiene

Before sending a final response, make sure `git status --short` is empty. Review all existing and new changes, then commit every intentional project change in one or more clearly scoped commits; do not leave completed or in-progress work uncommitted between turns. Keep separate work in separate commits when that preserves a clear scope. Never discard, reset, or overwrite changes merely to make the tree clean. If ownership is unclear or changes cannot be committed safely, stop and ask the user instead of silently finishing with a dirty tree. Do not push unless the user directly requests it.
