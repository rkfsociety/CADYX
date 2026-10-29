# Document Model v15 / Assembly graph v6

[Русский](document-model-v15.ru.md) · [English](#english)

## English

Document v15 introduced the assembly graph v6 and nullable `reference_a` and `reference_b` values on each mate. When present, both are required and each has `body_id`, `face_key`, `face_index`, `surface`, local `origin`, and unit `direction`. Existing v14 mates migrate with both values set to `null`; their frames and placements are preserved. The current schema is [v16](document-model-v16.md).

The references bind the saved local mate frames to analytic B-rep faces. The Core resolves exact semantic face keys while loading and validating. When a command changes referenced geometry, Core may rebind a missing key only to one unambiguous face with the same analytic surface, compatible direction, and nearest frame origin. Missing or ambiguous candidates reject the whole command without saving partial geometry, references, or poses. The cache is bounded and keyed by both kernel adapter and geometry hash.

The desktop assembly editor can select occurrence faces in the viewport and create supported mates from two selected faces. The command still uses the ordinary human lease, expected document revision, validation, and atomic save path. Geometry references do not change the current rigid graph solver's supported mate kinds or turn it into a general nonlinear constraint solver.

The authoritative schema and migration are in [`cadyx/migrations.py`](../../cadyx/migrations.py), graph validation in [`cadyx/assembly_mates.py`](../../cadyx/assembly_mates.py), and face resolution in [`cadyx/assembly_geometry.py`](../../cadyx/assembly_geometry.py).
