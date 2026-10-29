# Document Model v14 / Assembly graph v5

[Русский](document-model-v14.ru.md) · [English](#english)

## English

The current document schema is **v14** and the assembly graph is **v5**. Document v14 adds a `limits` object to each mate and adds the `spherical` mate kind. The v13 → v14 migration gives legacy mates unbounded defaults and does not change their poses or coordinates.

```json
{"angle_min_deg":null,"angle_max_deg":null,"offset_min_mm":null,"offset_max_mm":null}
```

Limits must be finite numbers or `null`; a supplied minimum cannot exceed its maximum. `revolute` accepts angle limits, `slider` accepts axial offset limits, and `coaxial`/`cylindrical` accept both. `fixed`, `coincident`, and `spherical` do not accept scalar motion limits. Coordinates outside configured limits fail validation before the command commits.

The `spherical` mate has three relative rotational degrees of freedom. Its explicit pose uses the existing unit quaternion `orientation`; local frame Z axes define the axes for axial mate types. A mate's persisted `angle_deg` and `offset_mm` remain the explicit coordinates for revolute, slider, coaxial, and cylindrical motion.

The solver remains deterministic and rigid. It diagnoses inconsistent loop closures and consistent redundant graph edges, but does not perform nonlinear geometric constraint solving or rank analysis for arbitrary mechanisms. Mates still refer to instance-local frames rather than persistent B-rep face/edge references. See [`cadyx/assembly_mates.py`](../../cadyx/assembly_mates.py) and the [v10 mate contract](document-model-v10.md) for the implementation.
