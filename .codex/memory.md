# CADYX project memory

- Confirmed: the repository began empty. The first implementation is a Python 3.12+ standard-library application under `cadyx/`, with a browser-native interface under `web/` and tests under `tests/`.
- Confirmed: `CadyxCore` is shared by the web API and Agent API. `ProjectWorkspace` writes each project's metadata, document, revision, and command history together in `projects/<uuid>/project.json` using atomic replacement.
- Confirmed: edit leases are process-local, heartbeat-based, and required for all document mutations. Expired leases stay locked until Core reloads and validates the saved project before recovery. One host process per workspace is required by this implementation.
- Confirmed: Web API commands require a human lease; Agent API commands require an agent lease. Both invoke the same Command System.
- Confirmed: `PrimitiveKernelAdapter` is a descriptor-only demo implementation of the `GeometryKernel` protocol; it does not create B-Rep geometry or render a model.
- Verified locally with the bundled Python runtime: `python -m unittest discover -s tests -v` and `python -m compileall -q cadyx tests`; the browser script syntax check is `node --check web/app.js`.
- Canonical sources: [architecture guide](../docs/architecture.md), [project format v1](../docs/project-format-v1.md), and the implementation in `cadyx/`.
