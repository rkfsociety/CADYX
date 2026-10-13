# CADYX project roadmap

This is the current development sequence. The milestones are goals, not claims that the corresponding capabilities are entirely absent; inspect the implementation before starting work or changing a milestone's status. Stage 1 is complete; the remaining stages are proposed.

## Stages

1. **Complete — Real geometry kernel:** integrate Open CASCADE behind `GeometryKernel`, validate reconstructed box B-reps, retain compatibility with saved `primitive-v1` descriptors, and cover the adapter with conformance tests. Evidence: [`cadyx/kernel.py`](../cadyx/kernel.py), [`tests/test_kernel.py`](../tests/test_kernel.py), and [`requirements.txt`](../requirements.txt).
2. **Richer Document Model:** add sketches, constraints, and stable relationships between objects.
3. **Project Format migrations:** add explicit migrations between persisted format versions as the model evolves.
4. **Multi-process leases:** make lease coordination reliable when multiple processes access the same project workspace.
5. **Safe agent stop and resume:** let agent work stop safely after the current atomic command, validate saved state, and resume from a known state.
6. **Shared rendering and viewport API:** expose model rendering and viewport capabilities for the browser and agents.
7. **Model inspection and manual editing:** expand the browser UI to inspect model state and support guarded manual edits under a human lease.
8. **Provider-neutral Agent Runtime:** add an agent runtime without coupling Core to a specific AI provider.
9. **Authentication before LAN access:** implement and verify authentication before supporting access from other machines.
10. **Scan-to-CAD:** build scan preprocessing and editable reconstruction after the geometry foundation is established.

## Sequencing gates

- Plan format migrations alongside any change that evolves persisted project or document data.
- Do not allow multiple host processes to share a workspace until stage 4 is complete.
- Do not expose the host on a LAN until stage 9 is complete.
- Start Scan-to-CAD only when the geometry kernel and Document Model foundations from stages 1 and 2 are adequate for editable reconstruction.

Before marking a stage complete, verify its result against the current code and relevant checks, then update this roadmap. Architecture details and current implementation constraints remain in [the architecture guide](../docs/architecture.md); durable technical decisions belong in [decisions](decisions.md).
