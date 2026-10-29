# Document Model v13 / Assembly graph v4

[Русский](document-model-v13.ru.md) · [English](#english)

## English

Document v13 introduced saved per-instance grounding and visibility. The current document schema is v17; see the [v17 contract](document-model-v17.md) for the current mate and graph schema.

- `grounded`: keeps this occurrence at its saved pose during mate solving. A connection that would move a grounded occurrence fails atomically. Rigid transforms of a grounded occurrence or a connected group containing one are rejected; use a joint coordinate to exercise an allowed relative degree of freedom.
- `visible`: controls display per occurrence. Hiding a subassembly occurrence hides its complete nested branch without changing source body visibility or geometry.

New instances default to `grounded: false` and `visible: true`. The `set_assembly_instance_properties` command changes these fields and the instance name through the existing revision-checked Core command transaction. Kinematic inspection reports grounded instance IDs and excludes grounded components from global rigid-body DOF.

Document v12 → v13 migration upgrades assembly v3 instances in memory and assigns those defaults. A successful command persists the migrated document through the ordinary atomic save path. See [`cadyx/assemblies.py`](../../cadyx/assemblies.py), [`cadyx/assembly_mates.py`](../../cadyx/assembly_mates.py), and [`cadyx/command_assembly.py`](../../cadyx/command_assembly.py) for the authoritative schema and behavior.

This version does not add the interactive browser assembly editor or geometry-referenced constraints; those require separate contracts and implementation.
