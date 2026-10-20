# CADYX documentation map

**Source of truth:** current `main` implementation, particularly `cadyx/migrations.py`, `cadyx/capabilities.py`, `cadyx/web_routes.py`, `cadyx/api_contracts.py` and the tests. This map is an entry point, **not** an extra versioned specification. The project envelope is **v1**, the document schema is **v12**, and the assembly graph schema is **v3** (introduced in Document v11). Older document-model files record an introduction-era contract; later versions can supersede their stated limitations.

## Get started and operate

- [README](../README.md): Windows x64 setup, authentication, operator MCP integration, CI entry points.
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
| Catalog and standard parts | [Standard-part presets](standard-part-catalog.md) |
| Backlog, not implemented feature list | [Sketch tool backlog](sketch-tools-backlog.md) |
| Revopoint scanner tooling | [Scanner README](../tools/revopoint/README.md), [calibration notes](../tools/revopoint/calibration/README.md) |

## Version history (not current-limit declarations)

- [Document v4](../.codex/docs/document-model-v4.md), [v5](../.codex/docs/document-model-v5.md), [v6](../.codex/docs/document-model-v6.md), [v7](../.codex/docs/document-model-v7.md): successive sketch, geometry, import, and feature contracts.
- [Document v8](../.codex/docs/document-model-v8.md): named parameters and rebuilds.
- [Document v9](../.codex/docs/document-model-v9.md): assembly graph v1.
- [Document v10](../.codex/docs/document-model-v10.md): assembly mates, graph v2.
- [Document v11](../.codex/docs/document-model-v11.md): material records, graph v3.
- [Document v12](sketch-workplanes.md): persistent work-plane frames.

## Validation and scope

`GET /api/v1/agent/capabilities` discovers runtime routes and commands, and `GET /api/v1/agent/projects/{id}/inspection` supplies bounded read-only project inspection. Programmatic source/contract checks are in [CI](../.github/workflows/ci.yml). Tests that require Windows native OCCT/Open3D must run in the configured Windows environment. A source review or documentation-only commit **is not** evidence that native tests passed.

The [project roadmap](../.codex/roadmap.md), [architectural decisions](../.codex/decisions.md), and [memory](../.codex/memory.md) record history, design intent, and pending work; they must not be treated as a live capability inventory without cross-checking code.
