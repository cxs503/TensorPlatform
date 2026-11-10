# Tensor License Inventory and Migration Plan

**Snapshot date:** 2026-11-10  
**Scope:** Root-level `LICENSE` file checks for the repositories listed below. This is a preliminary inventory, not a complete legal or dependency audit.

## 1. Observed root LICENSE status

| Repository | Observed root LICENSE | Status against Tensor policy | Action |
|---|---|---|---|
| TensorPlatform | MIT | Permissive, but not target default | Keep historical MIT grant; review rights before moving future combined work to Apache-2.0. |
| TensorPhysics | Not found | Unlicensed at root in this check | Audit authorship/contributions before adding Apache-2.0. |
| TensorLBM | GPL-3.0 | Existing copyleft exception | Keep GPL-3.0 pending full provenance and dependency review; no blanket conversion. |
| TensorFEM | Apache-2.0 | Aligned | Retain; review notices and dependencies. |
| TensorFVM | GPL-3.0 | Existing copyleft exception | Keep GPL-3.0 pending full provenance and dependency review; no blanket conversion. |
| TensorDEM | Not found | Unlicensed at root in this check | Audit authorship/contributions before adding Apache-2.0. |
| TensorBEM | Apache-2.0 | Aligned | Retain; review notices and dependencies. |
| TensorSPH | MIT | Permissive, but not target default | Keep historical MIT grant; review rights before moving future combined work to Apache-2.0. |
| TensorDG | MIT | Permissive, but not target default | Keep historical MIT grant; review rights before moving future combined work to Apache-2.0. |
| TensorCAD | Apache-2.0 | Aligned | Retain; review notices and dependencies. |
| TensorKnowledge | Not found | Unlicensed at root in this check | Audit authorship/contributions before adding Apache-2.0. |
| TensorPapers | Not found | Unlicensed at root in this check | Classify papers, prose, code, figures and third-party materials separately before licensing. |
| ship-performance-platform | Not found | Unlicensed at root in this check | Audit authorship/contributions before adding Apache-2.0. |
| TensorData | Not found | Unlicensed at root in this check | Audit code and all dataset provenance separately; code license does not license datasets. |
| TensorBench | Repository was empty at inspection | Ready for Apache-2.0 initialization | Add Apache-2.0 before first code contribution. |

**Important limitation:** this check looked for the root path named exactly `LICENSE`. It did not exhaustively inspect alternative names, nested package licenses, submodules, vendored code, source headers, dependency metadata, model weights, data files, or every PR author. “Not found” means no root `LICENSE` was retrieved in this check; it is not a conclusion that every asset is owned by the repository maintainer.

## 2. Migration rules

### Apache-2.0 repositories
Keep the current license. Verify the canonical license text, package metadata, notices, and dependency compatibility.

### MIT repositories
Do not silently replace MIT with Apache-2.0. MIT is already a permissive open-source license. For future contributions, the project may adopt Apache-2.0 only after the maintainer has established a valid licensing path for the combined work. Options include obtaining permission from all relevant rights holders or keeping separable MIT components under their existing terms with clear notices.

### GPL-3.0 repositories
Keep GPL-3.0 as the current project license until a full rights audit is complete. Any Apache-2.0 migration requires permission from every relevant copyright holder for the code being relicensed and review of all dependencies and copied code. A clean-room rewrite may be considered for components whose rights cannot be cleared. Do not combine GPL-only code into Apache-only distributions without a reviewed compatibility basis.

### Repositories without a root LICENSE
First inventory authors, merged PRs, generated/agent-authored files, imported snippets, dependencies and data. Then select Apache-2.0 for original software only where the project has authority to grant it. For mixed-content repositories, separate code from content and third-party assets.

## 3. Immediate implementation order

1. Adopt `docs/governance/LICENSE_GOVERNANCE.md` as the cross-project policy.
2. Add Apache-2.0 to the empty TensorBench repository before its first code contribution.
3. Complete per-repository authorship and dependency audits for TensorData, TensorKnowledge, TensorPhysics, TensorDEM and ship-performance-platform.
4. Review TensorPlatform, TensorSPH and TensorDG for a possible Apache-2.0 transition; preserve existing MIT releases and contributor notices.
5. Keep TensorLBM and TensorFVM on GPL-3.0 unless and until a documented relicensing audit is approved.
6. Add CI license checks, DCO sign-off, data manifests and release-time SBOM/license scans.
7. Reconcile package metadata, README badges, source headers and release archives only after the repository's licensing path is confirmed.

## 4. Approval record

No existing repository license is changed by this inventory. No third-party or contributor rights are presumed. Each migration must be approved separately and recorded in the relevant repository.
