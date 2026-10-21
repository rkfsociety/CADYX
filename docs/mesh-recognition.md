# Mesh references for solid reconstruction

[Полный русский перевод](mesh-recognition.ru.md)

[Русский обзор](#русский-обзор) · [English reference](#english-reference)

## Русский обзор

### Опоры для реконструкции по сетке

`recognize_mesh` измеряет сохранённую треугольную сетку и создаёт вложенные редактируемые CUSTOM-эскизы: опорные плоскости, сечения, внешнюю проекцию и консервативные кандидаты окружностей и цилиндров. Проекция не является сечением, кандидат не является подтверждённым отверстием. Предел — 200000 треугольников; исходная сетка не изменяется, параметры и ошибки измерений сохраняются с её хешем. `validate_profiles=true` проверяет временное выдавливание без сохранения тела. Повторная обработка не заменяет опоры с зависимостями. Номинальные размеры, физический масштаб и итоговое твёрдое тело требуют отдельной работы.

Это обзор, не полный перевод. Полные схемы, примеры и подробности контракта приведены на английском ниже.

## English reference

`recognize_mesh` takes a saved triangle-mesh `body_id`, optional `parameters`
and optional `validate_profiles`. The GeometryKernel computes measurements;
the shared command creates a source-hash-bound `recognize_mesh` feature and
nested editable CUSTOM sketches. The source mesh is unchanged. All measured
references, parameters, residuals and unresolved issues persist in the project.
The browser tree exposes these results for inspection; tools remain agent-only.

Parameters use unverified source coordinates: `distance_tolerance` (default
0.35), `normal_angle_degrees` (15, range 1–30), `min_plane_area` (40),
`max_planes` (12, range 1–24) and `min_contour_length` (12). Positive distances
and areas are required. Recognition accepts at most 200000 triangles.

The deterministic search produces:

- Local best-fit plane datums from area-weighted sampled face hypotheses and
  PCA, checked against full supporting triangle vertices. Each records support
  face IDs, area, frame, RMS and maximum distance. Its construction rectangle
  shows the datum extent, not a certified rectangular physical face.
- Open and closed section traces at three interior stations along each principal
  axis. Branched section components are rejected and counted. Quantized joining
  and contour simplification tolerances are recorded; open ends stay open.
- A primary external footprint projected onto the principal plane. This is
  explicitly **a projection, not a cross-section**. Raster cell spacing and
  simplification tolerance are recorded. Thin features, inner holes and detached
  regions require review; the primary outer contour does not assert their absence.
  Touching raster walks split into simple cycles before selecting the outer loop.
- Circular support curves only when arc coverage, continuity, radial residuals
  and alternating-sample fit repeatability pass conservative checks. They are
  construction circles supporting measured arcs, not confirmed complete holes.
- Candidate cylinder axes when circular section supports repeat coaxially with
  similar radii. Projection curves cannot supply cylinder evidence. Candidates
  require confirmation against the actual surface.

The footprint must pass CAD closed-profile validation. `validate_profiles=true`
also performs a temporary unit-depth extrusion through the configured kernel,
validates it and saves the verdict under the footprint's `profile_validation`.
No solid is persisted and the diagnostic depth is not a design dimension.

Rerunning recognition atomically refreshes the existing feature and its unused
reference sketches. References from other retained objects or model-parameter
bindings prevent replacement. Source mesh hash checks reject stale reports.
Original fit errors describe the initial recognition; later sketch edits do not
remeasure the mesh. Parameter/model constraints must be chosen during design.

This prepares references for reconstruction. It does not automatically certify
the source mesh, determine nominal dimensions/units, recover manufacturing
tolerances, repair all section gaps, or create the final solid. Search is bounded
and non-exhaustive; absence of a circle result is not evidence that no hole exists.
