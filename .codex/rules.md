# CADYX Codex rules

- When choosing or updating any dependency/runtime/tool version, use the newest stable release and verify it from its official source. Do not choose an older compatible release as a workaround: the user explicitly requires adapting or building the kernel when necessary. Never bypass dependency checks to claim compatibility. Reviewed versions and reproducible local setup live in `scripts/runtime-versions.json` and `scripts/setup-runtime.ps1`.

- Keep CADYX AI-first: the Core owns authoritative project state, and both agents and the browser use its Command System for document mutations.
- Keep the browser a clean working interface: no promotional slogans or banners, and show live visual model changes instead of an agent command feed. Use the event stream to update the model and status.
- Manage the Windows host through `scripts/manage-host-task.ps1` from PowerShell (for example, `-Action Restart`); use the browser only to observe the host, never to control its lifecycle.
- Every document mutation must hold the project's current edit lease. AI and human must never edit the same project concurrently.
- Treat `projects/` as user workspace data. Do not overwrite or delete imported CAD files, scans, meshes, images, measurements, or references during routine work.
- Keep geometry-kernel-specific APIs behind the `GeometryKernel` boundary; do not represent the demo primitive adapter as a production CAD kernel.
- Prefer existing mechanisms and avoid dependencies with only marginal benefit. Standing user authorization: add a mature third-party dependency when it provides a concrete improvement to performance, correctness, maintainability, or UX; no separate approval is needed for each such dependency. Keep the choice scoped, record why it helps, and update dependency manifests/lockfiles as required.
- Before every commit, review `.codex/memory.md` against the confirmed current project state: add useful durable facts, update changed facts, and remove stale ones. Keep canonical details in project documentation or code instead of duplicating them in memory.
