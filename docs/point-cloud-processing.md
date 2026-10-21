# Point-cloud processing and history

[Русский обзор](#русский-обзор) · [English reference](#english-reference)

## Русский обзор

### Обработка облаков точек и история

`process_point_cloud` работает с полным сохранённым PLY, проверяя SHA-256. Методы: `deduplicate` убирает одинаковые XYZ; `radius_filter` фильтрует по соседям; `crop` оставляет точки внутри включённых границ; `voxel_downsample` заменяет точки вокселя центроидом. Максимум 2 млн входных точек, минимум три выходные; радиусный фильтр ограничен 20 млн сравнений. Каждая операция добавляет этап истории того же тела и отдельный XYZ PLY. Исходники сохраняются, цвета и нормали в выходной XYZ не переносятся. До 125000 точек документа служат только отображению; масштаб и точность скана этим не подтверждаются.

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
