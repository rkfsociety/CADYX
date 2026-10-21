# Mesh processing for the AI agent

[Русский обзор](#русский-обзор) · [English reference](#english-reference)

## Русский обзор

### Обработка треугольных сеток

`process_mesh` обрабатывает сохранённую сетку через GeometryKernel и обычный путь команд агента. Методы: `clean` удаляет точные дубликаты и вырожденные треугольники; `remove_small_components` удаляет малые компоненты связности по рёбрам; `orient` согласует обход, отклоняя неподходящую сетку; `repair_orientation` удаляет конфликтующие треугольники; `smooth` выполняет Taubin-сглаживание с фиксацией открытой границы. Лимит — 200000 треугольников. Каждая операция сохраняет новое тело и полноразмерный PLY, исходники остаются. Это не заполнение дыр, не проверка самопересечений и не превращение в B-rep.

Это обзор, не полный перевод. Полные схемы, примеры и подробности контракта приведены на английском ниже.

## English reference

The discovered `process_mesh` command takes `body_id`, `method` and optional
`parameters`. It works through the shared GeometryKernel, Command System,
revision-checked Agent API and MCP command wrapper. These are agent tools;
the browser tree shows only operations actually used and their saved results.

| Method | Parameters | Behavior |
| --- | --- | --- |
| `clean` | `{}` | Weld exact duplicate XYZ vertices, remove duplicate and zero-area triangles, compact unused vertices. |
| `remove_small_components` | `{min_triangles}` | Remove edge-connected components smaller than the explicit threshold (1–100000). Sharing only a vertex does not join components. |
| `orient` | `{}` | Make winding consistent within each edge-connected component. Reject non-orientable or non-manifold input. Does not claim outward orientation. |
| `repair_orientation` | `{}` | Cut conflicting triangles from non-orientable parity cycles, then orient retained triangles. Report deleted triangle count. Creates additional open boundaries rather than inventing patches. |
| `smooth` | `{iterations, strength}` | Taubin passes with factors `strength` and `-1.05 * strength`, 1–10 iterations, strength in `(0, 0.5]`. Pin all open-boundary vertices. Reject degenerate, non-manifold or inconsistently oriented input, and triangle foldovers relative to the input. |

Operations accept triangle meshes with at most 200000 triangles. A result must
retain triangles and cannot introduce additional zero-area triangles or
non-manifold edges. Smoothing also checks the persisted float32 result. It is
not a self-intersection certificate or an exact shape-preserving operation.

Each operation creates a new source-linked body, hides its source, and saves a
full-resolution hashed PLY. It records method, parameters, source geometry hash,
previous feature and before/after counts, edge-connected components, boundaries,
winding inconsistencies and degeneracies. Smoothing reports actual maximum
vertex displacement in unchanged source coordinates. Original bodies and PLYs
remain available. Incompatible source edits fail persisted validation while
descendants depend on the original geometry hash.

These operations do not fill holes, infer physical scale, convert a mesh to an
OCCT solid, or prove freedom from self-intersections. Historical operations are
inspection records; append another operation to create another result.
