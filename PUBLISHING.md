# Publication readiness gate

This is a **pre-publication checklist**, not a release announcement or authorization to make CADYX public. Changing repository visibility, publishing source archives, creating GitHub Releases or deploying a public host requires a separate explicit decision once the gates below pass. CADYX remains a Windows x64 development prototype, **not** a production-ready public service.

## Snapshot at preparation (2026-10-21)

- Repository: `rkfsociety/CADYX`, **private**, default branch `main`.
- Inspected revision: `4e5bddf0d1d16436538d5909da17a2a0006f7674`.
- `LICENSE` (MIT), `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, and `.github/workflows/ci.yml` are present.
- Latest inspected Actions CI [run 39509118965](https://github.com/rkfsociety/CADYX/actions/runs/39509118965) for that revision: **source failed**, **windows skipped**. The failure cause was not established; job logs were inaccessible through the connected interface.
- No GitHub Releases were present when inspected.
- Scanner-specific assets under `tools/revopoint/calibration/` are tracked and documented as copied from a Revo Scan installation. Their public redistribution rights are **not verified**.
- **No local tests were run** during this preparation. This review does not establish test success.

This is a historical snapshot. Refresh all evidence against the exact revision proposed for publication.

## Blocking gates

- [ ] **Redistribution rights:** Establish the provenance and redistribution rights of scanner calibration binaries, device-derived settings and other vendor or third-party assets. Document each decision. The CADYX MIT license does not grant rights over third-party materials. If rights cannot be confirmed, remove or replace the tracked bundle in a separately reviewed change, adapt scanner setup/docs, and consider exposure in Git history.
- [ ] **Tracked content and history:** Review all current tracked paths, particularly `.codex/`, `.agents/`, `tools/`, configuration, engineering notes and examples, for sensitive data, machine-specific paths, customer models, credentials and material unsuitable for public distribution. Audit all reachable commits, tags and branches; perform an approved secret scan and resolve findings. `.gitignore` does not erase already committed files or history. Revoke exposed credentials; coordinate history rewriting separately.
- [ ] **Passing CI on target commit:** Investigate the failure of the `source` job and require green source checks **and** a successful native Windows `windows` job for the intended commit. A skipped job, unavailable logs, or documentation-only review is not evidence that tests pass.
- [ ] **Dependencies and licenses:** Revalidate pinned runtime versions, download hashes, supported Windows prerequisites, build reproducibility and vendor licenses/notices (Python, OCCT, Open3D, Node, scanner tools). Bundled data or binaries have additional distribution obligations. Do not add downloaded runtimes or generated SDK files to source archives without confirming rights.
- [ ] **Public-facing security/docs:** Ensure README, current implementation contracts, versioned schemas, supported platforms and limitations are accurate. Confirm a working private vulnerability-reporting path. The built-in HTTP host has no TLS and **must not** be exposed directly to the public Internet.
- [ ] **Artifact inventory:** Inspect the proposed public source archive and each release artifact for `projects/` user data, captures, `.runtime/`, `.env*`, keys, tokens, logs, caches and calibration assets without cleared redistribution rights. Review sample datasets for consent and licensing.
- [ ] **Explicit release approval:** Select and review a version/tag, changelog, known limitations, support expectations and artifact checksums as applicable. Obtain a distinct go/no-go decision before changing visibility or issuing a public release.

## Boundaries of this preparation

Changes may be committed and pushed to the **existing private `main`** branch without changing visibility or making a release. A push can automatically trigger the repository's GitHub Actions workflow; that is **not** a local test run. Leave the repository private and do not create a release or public deployment until the gates pass and publication is separately authorized.
