# Point-cloud processing and history

[Полный русский перевод](point-cloud-processing.ru.md)

[Русский обзор](#русский-обзор) · [English reference](#english-reference)

## Русский обзор

### Обработка облаков точек и история

`process_point_cloud` работает с полным сохранённым PLY, проверяя SHA-256. Методы: `deduplicate` убирает одинаковые XYZ; `radius_filter` фильтрует по соседям; `crop` оставляет точки внутри включённых границ; `voxel_downsample` заменяет точки вокселя центроидом. Максимум 2 млн входных точек, минимум три выходные; радиусный фильтр ограничен 20 млн сравнений. Каждая операция добавляет этап истории того же тела и отдельный XYZ PLY. Исходники сохраняются, цвета и нормали в выходной XYZ не переносятся. До 125000 точек документа служат только отображению; масштаб и точность скана этим не подтверждаются.

Для ИИ-агента добавлены `simplify` (равномерная или геометрическая выборка с отношением 40% по умолчанию), `isolation_filter` (процент изолированных точек, по умолчанию 15%), `remove_overlaps` (близкие совпадающие слои по нормали к локальной поверхности) и `smooth` (сила в процентах и число проходов). `cadyx_analyze_point_cloud` анализирует результат без записи; применение через `process_point_cloud` добавляет этап в дерево модели. Расстояния заданы в исходных координатах, миллиметры не подтверждены.

Это обзор, не полный перевод. Полные схемы, примеры и подробности контракта приведены на английском ниже.

## English reference

`process_point_cloud` operates on a saved source-linked point-cloud `body_id`.
It is available through the Command System, Agent API and discovered MCP command
schema. Pass the current `expected_revision`; the MCP command wrapper manages
the edit lease. Tools are available only to the AI agent. The browser tree shows
only operations actually executed, with their saved parameters and results;
it does not expose a catalog or controls for unused processing tools.

| Method | `parameters` | Behavior |
| --- | --- | --- |
| `deduplicate` | `{}` | Remove identical XYZ triples, retaining first-occurrence order. |
| `radius_filter` | `{radius, min_neighbors}` | Retain points having at least this many other points within the radius. |
| `crop` | `{min: [x,y,z], max: [x,y,z]}` | Retain points inside the inclusive axis-aligned bounds. |
| `voxel_downsample` | `{voxel_size}` | Replace each occupied world-origin-aligned voxel with its XYZ centroid. |
| `simplify` | `{strategy: "uniform", ratio: 40}` | Uniform keeps evenly spaced source samples; geometric uses adaptive voxel centroids. `ratio` is the target percentage removed. Geometric voxel occupancy can produce a nearby count; exact output count is returned in analysis. Strategies: `uniform` or `geometric`. |
| `isolation_filter` | `{rate: 15, component_factor: 3}` | Detect the largest connected main-scan component at `component_factor × median nearest-neighbor spacing`. Points outside it and unusually sparse points (`nearest distance > 1.5 × median`) are removal candidates, capped at `rate` percent. `rate` is `(0,50]`; `component_factor` is `2..12`. |
| `remove_overlaps` | `{distance: 0.56, radius: 2, tangential_distance: 0.1}` (defaults; any subset allowed) | Keep the first representative; remove later samples whose absolute normal separation is **less than** `distance` and tangential separation is at most `tangential_distance`. |
| `smooth` | `{strength: 10, iterations: 10, radius: 1}` (defaults; any subset allowed) | Move each supported point toward its fitted local plane, along the plane normal, by `strength` percent per synchronous pass. Strength is in `(0,100]`; iterations is an integer in `1..100`. Point count stays unchanged. |

### Local-surface tools and detection

`isolation_filter` uses exact spatial-grid nearest-neighbor and connected-component searches. The component radius is `component_factor` times the cloud's median nearest-neighbor spacing; the largest component is the main scan. Points in detached components and points whose nearest-neighbor distance exceeds 1.5 times the median are removal candidates, subject to the selected rate cap. Analysis reports the main-component size, detached point count, radius and total candidate count. The 20-million candidate-comparison budget covers both searches. The two surface tools use radius neighborhoods and a local PCA plane fitted to
the nearest 48 samples. At least six samples and a two-dimensional neighborhood
are required. Collinear regions and patches whose smallest covariance eigenvalue
exceeds one quarter of the middle eigenvalue remain unchanged. Radius must be
positive; overlap radius must exceed both distance tolerances. Distances are in
source coordinates, **not verified millimeters**. The normal distance is not
global Z. Overlap removal keeps original XYZ/order and one representative per
coincident group; it does not remove an entire supported surface. The tangential
tolerance limits lateral thinning. Without per-frame labels it cannot distinguish
intentional nearby sheets from redundant scan layers; inspect before applying.
Smoothing moves points without meshing or merging disconnected components and
can remove small details. Both operations share a budget of 20 million candidate
neighbor comparisons across all passes; budget exhaustion saves no model stage.

Use the read-only MCP tool `cadyx_analyze_point_cloud` or
`POST /api/v1/agent/projects/{id}/validations/cloud-processing` with
`{expected_revision, body_id, method, parameters}` to detect the prospective result.
Methods are `simplify`, `isolation_filter`, `remove_overlaps` and `smooth`. The response includes normalized
parameters, the source file/hash, input/output/removed/moved counts and revision.
It creates no artifact, geometry change, history entry or document revision.
Apply separately with `process_point_cloud`, the same method/parameters and the
current `expected_revision`. Analysis is not a commit token: a changed source
hash or stale revision is rejected when applying. Only applied operations appear
in the model tree with their parameters, counts, source/result artifacts and
previous-stage link. No processing controls or unused tool palette are exposed
to the user.

The model-tree disclosure state is stored locally per project and restored after
page reloads; it does not alter the saved CAD document.

The model tree renders each point-cloud body as one expandable parent and nests
all import/processing stages beneath it. A stage with no children has no
disclosure control.

For agent cleanup runs, analyze `isolation_filter` and `remove_overlaps`, apply
each result with nonzero `removed_points`, and repeat the analysis against the
new saved stage while varying that operation's parameters. Stop each operation
when analysis reports zero removable points. Keep simplification and smoothing
for explicit user requests. If varied valid parameters cannot clear candidates,
or analysis hits its comparison budget, leave the remaining points intact and
report the unresolved count and limit; do not describe the cloud as clean.

This cleanup is a required preprocessing step before scan-to-mesh reconstruction.
Import the source PLY as a point-only reference first, complete the alternating
overlap/removal cycle, then run mesh reconstruction on the latest cleaned
full-resolution stage. Never reconstruct directly from the untouched source PLY.

Distances use the original coordinates. Physical scale and units remain
unverified. Processing does not reconstruct a mesh or certify scan accuracy.
It accepts at most 2 million input points, requires at least three output points,
and limits radius filtering to 20 million candidate comparisons. A failed
filter leaves the saved document unchanged.

Every command reads the **full** previous PLY, verifies its SHA-256, and appends
a `process_point_cloud` feature to the same body. Each feature records the method,
parameters, preceding feature, source/result file hashes, input/output counts,
and output bounds. Full XYZ results are published as separate point-only PLYs;
the original and intermediate artifacts are preserved. Additional source
properties such as normals or colors are not included in generated XYZ PLYs.
The document contains at most 125,000 display points, independently of the full
artifact. Tree rows show every saved stage and its counts; selecting an operation
shows its parameters and file provenance. The viewport shows the current result.

History is append-only: existing processing parameters are inspection data;
request another operation to append a new result. Source-file changes are
rejected rather than silently reprocessing different data.
