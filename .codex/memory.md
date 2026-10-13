# CADYX project memory

- Confirmed: CADYX is a Python 3.12+ application under `cadyx/`, with a browser-native interface under `web/`; its pinned Open CASCADE bindings are declared in `requirements.txt`.
- Confirmed: `CadyxCore` is shared by the web API and Agent API. `ProjectWorkspace` writes each project's metadata, document, revision, and command history together in `projects/<uuid>/project.json` using atomic replacement.
- Confirmed: edit leases are process-local, heartbeat-based, and required for all document mutations. Expired leases stay locked until Core reloads and validates the saved project before recovery. One host process per workspace is required by this implementation.
- Confirmed: Web API commands require a human lease; Agent API commands require an agent lease. Both invoke the same Command System.
- Confirmed: `PrimitiveKernelAdapter` is a descriptor-only demo implementation of the `GeometryKernel` protocol; it does not create B-Rep geometry or render a model.
- Confirmed: `OCPKernelAdapter` is the default kernel. It creates and validates Open CASCADE B-reps from JSON-safe box descriptors and still validates saved `primitive-v1` boxes for compatibility.
- Canonical sources: [run instructions](../README.md), [architecture guide](../docs/architecture.md), [project roadmap](roadmap.md), [project format v1](../docs/project-format-v1.md), [runtime dependency](../requirements.txt), and the implementation in `cadyx/`.
