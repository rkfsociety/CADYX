# Editable sketch work planes — Document Model v12

[Полный русский перевод](sketch-workplanes.ru.md)

[Русский обзор](#русский-обзор) · [English reference](#english-reference)

## Русский обзор

### Рабочие плоскости эскизов — документ v12

`create_sketch` и `set_sketch_workplane` поддерживают XY, XZ, YZ, CUSTOM и FACE. Элементы остаются в локальных 2D-координатах. CUSTOM задаёт начало, нормаль и ось X; FACE использует текущий `semantic_key` плоской грани. Некорректные оси и неоднозначная привязка отклоняются. Перестроение сохраняет ревизионную и атомарную защиту. `revolve_sketch` по-прежнему требует XZ, `loft_sketches` сохраняет ограничения XY/XZ, а сечения сканов — нормали Y/Z. Миграция v11→v12 сохраняет прежние координаты и смещения.

Это обзор, не полный перевод. Полные схемы, примеры и подробности контракта приведены на английском ниже.

## English reference

The leased Agent API/MCP command `create_sketch` accepts `plane` = `XY` (default), `XZ`, `YZ`, `CUSTOM`, or `FACE`. `set_sketch_workplane` additionally requires `sketch_id` and accepts the same placement fields. Every sketch element remains editable in its original local two-dimensional coordinates; the existing constraint system is unchanged. Dependent solid rebuilds occur within the existing revision-checked atomic command path. The browser remains observation-only.

- **Named planes:** XY and XZ retain their legacy orientation and section offsets. YZ has local X=global Y, local Y=global Z, normal=global X. A named offset requires a matching `section_normal_axis` (Z, Y, X respectively) and finite `section_offset` in mm.
- **CUSTOM:** `{"plane":"CUSTOM","frame":{"origin":[x,y,z],"normal":[nx,ny,nz],"x_axis":[ux,uy,uz]}}`. Nonzero nonparallel frame axes are normalized on input and persisted as unit orthogonal vectors. The local Y axis is the cross product normal × local X. Zero/parallel vectors, invalid numbers and non-orthogonal saved frames are rejected.
- **FACE:** `{"plane":"FACE","attachment":{"body_id":"body:…","face_key":"…","x_axis":[ux,uy,uz]}}`. Obtain a current planar face's `semantic_key` from topology inspection first. A nonunique or missing key fails rather than picking a face arbitrarily. The frame origin is the face center, normal follows that face, and supplied local X is projected into its tangent plane. On rebuilding the source body, an unchanged unique key is preferred; otherwise reattachment is allowed only where **exactly one** planar face has the previous normal. If the normal flips or multiple compatible faces exist, the rebuild fails instead of silently changing the sketch. Semantic topology keys are revision-relative, not durable topology IDs.

Work-plane positioning affects viewport sketch geometry and OCCT sketch extrusion and circular-pipe sweep results, including subsequent local-point edits and parameter rebuilds. Manufacturing pads/ribs/pockets construct their Boolean tool in the same frame. These are exact OCCT B-rep rigid placements, not mesh approximations. The existing `revolve_sketch` operation still requires XZ; `loft_sketches` retains the axis-aligned XY/XZ section contract. Independently, **scan-derived** `create_scan_sections` and `create_sketch_from_scan_section` accept only Y/Z normal axes even though generic YZ sketches exist; the mesh slicing contract has not automatically expanded to X-normal stations. These unsupported combinations remain explicit validation errors.

Document v11 → v12 migration preserves the previous XY/XZ sketch records, section offsets and geometry without rewriting coordinates. Core leases, expected-revision checks, stop fencing and atomic save remain authoritative. Face references to missing source bodies and malformed frames fail validation. New regression tests are included in source but were not run locally.
