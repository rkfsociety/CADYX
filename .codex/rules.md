# CADYX Codex rules

- Keep CADYX AI-first: the Core owns authoritative project state, and both agents and the browser use its Command System for document mutations.
- Keep the browser a clean working interface: no promotional slogans or banners, and show live visual model changes instead of an agent command feed. Use the event stream to update the model and status.
- Every document mutation must hold the project's current edit lease. AI and human must never edit the same project concurrently.
- Treat `projects/` as user workspace data. Do not overwrite or delete imported CAD files, scans, meshes, images, measurements, or references during routine work.
- Keep geometry-kernel-specific APIs behind the `GeometryKernel` boundary; do not represent the demo primitive adapter as a production CAD kernel.
- Prefer the standard library and existing mechanisms. A new production dependency requires explicit user approval.
- Before every commit, review `.codex/memory.md` against the confirmed current project state: add useful durable facts, update changed facts, and remove stale ones. Keep canonical details in project documentation or code instead of duplicating them in memory.
