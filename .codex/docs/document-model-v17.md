# Document Model v17 / Assembly graph v8

[Русский](document-model-v17.ru.md) · [English](#english)

## English

The current document schema is **v17** and assembly graph **v8**. Document v17 generalizes mate references from face-only references to typed B-rep entities. A non-null `reference_a` or `reference_b` has exactly `body_id`, `entity_kind`, `entity_key`, `entity_index`, `surface`, `origin`, and unit `direction`. `entity_kind` is `face` or `vertex`; a vertex uses `surface: "point"` and its local B-rep position. The v16 → v17 migration converts old `face_key`/`face_index` fields to typed entity fields without changing frames, geometry or placements.

The desktop editor can pick analytic faces or OCCT vertices in the 3D preview. Vertex selection creates point-to-point coincidence, point distance or fixed point-frame relations through the same assembly mate graph. Face selection continues to create axis and joint relations. The Core resolves exact vertex semantic keys. If a vertex key changes during a geometry edit, it may rebind only to a unique vertex within 1e-6 mm of its saved local position; otherwise the whole edit is rejected atomically and the dependency remains saved.

References: [`native/agent_features.cpp`](../../native/agent_features.cpp), [`cadyx/advanced_topology.py`](../../cadyx/advanced_topology.py), [`cadyx/assembly_geometry.py`](../../cadyx/assembly_geometry.py), [`cadyx/migrations.py`](../../cadyx/migrations.py).
