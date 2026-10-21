# Human-in-the-loop traced-curve editing

[Русский обзор](#русский-обзор) · [English reference](#english-reference)

## Русский обзор

### Трассировка и исправление кривой человеком

Узкое исключение из режима наблюдения — отдельный диалог трассировки по изображению. Черновик и undo/redo остаются локальными, изображение не загружается; сохраняются его SHA-256 и система координат. Подтверждённое сохранение использует человеческую блокировку, ревизию и `save_traced_curve_version`. Каждая версия создаёт новый эскиз; предыдущие версии не меняются. Сервер пересчитывает геометрию из координат изображения. Поддерживаются XY/XZ и связная цепочка прямых и круговых дуг до 4096 сегментов, не общий браузерный CAD-редактор.

Это обзор, не полный перевод. Полные схемы, примеры и подробности контракта приведены на английском ниже.

## English reference

CADYX keeps the browser observation-only for ordinary CAD authoring. Human curve correction is a narrow opt-in exception for reference tracing: a local client may prepare a draft of connected line and circular-arc segments, undo/redo that unsaved draft, and submit a confirmed version through the same human lease and Command System used by every browser-originated document mutation.

## Browser entry point

The scanner window exposes a separate `ТРАССИРОВКА КРИВОЙ` dialog. It is intentionally outside the ordinary CAD viewport so the main project surface stays observation-only. The dialog lets the user choose a local source image and an existing project, trace a single connected chain with line/circular-arc tools, undo/redo the local draft, close the contour, and enter the source-to-model coordinate system.

The selected image is never uploaded or persisted by the editor. The browser computes its SHA-256 locally and stores only that digest plus coordinate-system provenance with the saved trace. Before save, the dialog checks the current project availability, reads the current document revision, acquires a human lease, submits the versioned trace command, and releases the lease in `finally`. Agent-active, locked, recovery-required, stale-revision and stale-trace-version cases remain server-authoritative failures.

## Draft and save boundary

Unsaved edits are client-local. `web/browser-api.js` provides `TraceDraft`, an isolated snapshot history with `replace`, `update`, `undo`, and `redo`; undo/redo never writes project state. The scanner trace UI follows the same snapshot-history rule. A confirmed save uses `save_traced_curve_version` through `POST /api/v1/projects/{project_id}/commands` while holding a human lease and supplying the current project revision. Agent callers may use the same command through the Agent API, but browser editing does not bypass lease ownership, revision checks, Core atomicity, or project availability policy.

Each confirmed save creates a new sketch. Earlier sketches with the same `trace_id` are immutable trace-history versions and are never modified in place. `base_version` is an optimistic trace-history fence: stale saves fail with `REVISION_CONFLICT`. This is independent of, and additional to, the project document revision fence enforced by Core.

## Source provenance and coordinate system

A save requires:

- `source_image_sha256`: the 64-character SHA-256 of the source image bytes used for tracing;
- `coordinate_system.origin`: source-image pixel coordinates used as model origin;
- `coordinate_system.units_per_pixel`: positive model units per source pixel;
- `coordinate_system.unit`: a short unit label retained as provenance;
- `coordinate_system.y_axis`: `up` or `down`;
- `segments`: one connected chain of line or circular-arc definitions in source-image coordinates.

The server does not trust preconverted model coordinates. It recomputes every endpoint, arc center, radius, and signed sweep from the source-image definitions and coordinate-system record. It then validates finiteness, positive scale/radii, non-degenerate segments, arc sweep bounds, chain connectivity, and optional closure before adding ordinary sketch point/line/circle objects. `Document.validate()` remains the final persisted-geometry authority.

The saved sketch records `trace_id`, `trace_version`, normalized source-image SHA-256, coordinate-system provenance, the original source segment records, and whether the trace was requested closed. Geometry remains standard editable CADYX sketch geometry, so downstream profile and feature commands do not need a parallel trace representation.

## Supported scope

The constrained editor supports XY and XZ sketches, exact straight segments and exact circular arcs, and at most 4096 source segments per saved version. Arc input uses the current chain endpoint plus a chosen center and end direction; the browser normalizes that to one exact radius and a signed sweep before sending source coordinates. It does not authorize a general browser sketch editor, direct DOM-driven CAD authoring, constraint solving, spline fitting, image storage in `project.json`, or mutation of an earlier trace version.
