# Document Model v16 / Assembly graph v7

[Русский](document-model-v16.ru.md) · [English](#english)

## English

Document v16 introduced assembly graph v7. The v15 → v16 migration validates existing v6 mate records and advances the graph version without changing their geometry or placements. The current schema is [v17](document-model-v17.md); the project envelope remains v1.

The existing mate graph accepts four additional geometric relations: `parallel` constrains selected face-frame Z axes to parallel while retaining their saved separation; `perpendicular` constrains the axes to 90 degrees; `distance` constrains the Euclidean distance between face-frame origins in millimeters using `offset_mm`; and `angle` constrains the directed angle between face-frame axes in degrees using `angle_deg` (0–180). The desktop editor creates these relations from two selected planar or axial faces. Limits apply to the scalar coordinate where supported.

These relations use the existing deterministic rigid-pose propagation. The solver projects unconstrained relative pose onto each relation, reports the relation's remaining degrees of freedom, rejects inconsistent closed loops and grounded violations atomically, and identifies duplicate relations and constraints that exceed the component's structural degree-of-freedom budget. It does not perform a Jacobian-rank analysis or simultaneous nonlinear optimization for general mechanisms. Point anchors currently come from analytic face-frame origins; arbitrary topological vertex selection is not part of this contract.

Solver inputs are bounded to 16,384 instances and 65,536 mates. The viewport separately caps flattened visible part occurrences at 4,096. Topology metadata caching is bounded to 64 entries and 16 MiB.

Source: [`cadyx/assembly_mates.py`](../../cadyx/assembly_mates.py), [`cadyx/command_assembly.py`](../../cadyx/command_assembly.py), and [`cadyx/migrations.py`](../../cadyx/migrations.py).
