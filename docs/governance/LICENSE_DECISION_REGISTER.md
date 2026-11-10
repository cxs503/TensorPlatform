# Tensor License Decision Register

**Created:** 2026-11-10  
**Status:** Proposed migration plan; no repository license is changed by this register.  
**Owner:** Tensor project maintainer  
**Authority:** `LICENSE_GOVERNANCE.md` and the maintainer's fully open-source-core strategy.

## 1. Decision principles

1. Use Apache-2.0 as the target license for original Tensor software, SDKs, infrastructure, agents and developer tools.
2. Do not treat this target as permission to relicense existing code. Existing grants, contributor rights, upstream code and dependency terms remain in force.
3. Keep software licensing separate from dataset, paper, model, benchmark, image, customer and other content rights.
4. Preserve historical releases under the license that applied when they were published.
5. Every license change or first license grant is an R3 human decision. Agents may audit and prepare evidence but may not approve or execute the legal decision.
6. Until a repository's rights review is complete, use `NOASSERTION` for unresolved assets and do not claim that the repository is Apache-2.0.

## 2. Repository decision register

| Repository | Observed root license | Recommended target / disposition | Required gate |
|---|---|---|---|
| TensorPlatform | MIT | Retain MIT for now; consider Apache-2.0 for future combined work only after contributor-rights review. | R3 rights review; preserve MIT notices and historical grants. |
| TensorPhysics | MIT (added 2026-11-10) | MIT added to match existing `pyproject.toml` metadata; no relicensing performed. | Complete dependency and third-party asset audit; consider Apache only as a separate future decision. |
| TensorLBM | GPL-3.0 | Retain GPL-3.0 pending explicit relicensing feasibility review. | Audit all copyright holders, dependencies, copied code and integration/distribution boundaries. |
| TensorFEM | Apache-2.0 | Retain Apache-2.0. | Verify notices, dependency compatibility, vendored/upstream code and package metadata. |
| TensorFVM | GPL-3.0 | Retain GPL-3.0 pending explicit relicensing feasibility review. | Same gate as TensorLBM; do not silently remove copyleft obligations. |
| TensorDEM | Apache-2.0 (added 2026-11-10) | Initial software license added; package metadata now declares Apache-2.0. | Continue dependency, imported-code and benchmark-asset audit; `LICENSE_SCOPE.md` defines exclusions. |
| TensorBEM | Apache-2.0 | Retain Apache-2.0. | Verify notices, dependencies and package metadata. |
| TensorSPH | MIT | Retain MIT for now; optional future Apache-2.0 transition only after rights review. | Preserve existing MIT grants and notices; establish rights for future combined work. |
| TensorDG | MIT | Retain MIT for now; optional future Apache-2.0 transition only after rights review. | Same gate as TensorSPH. |
| TensorCAD | Apache-2.0 | Retain Apache-2.0. | Verify notices, dependencies and package metadata. |
| TensorKnowledge | Apache-2.0 (added 2026-11-10) | Initial software license added with explicit content boundary. | Papers, web extracts, figures, datasets and third-party documents remain individually governed; see `LICENSE_SCOPE.md`. |
| TensorData | Apache-2.0 (added 2026-11-10) | Initial software license added; package metadata now declares Apache-2.0. | Dataset rights remain governed by per-asset manifests; see `LICENSE_SCOPE.md`; continue dependency audit. |
| TensorBench | Apache-2.0 | Retain Apache-2.0. | Verify any imported benchmark code, datasets and benchmark result artifacts separately. |
| TensorPapers (private) | No root LICENSE found | Keep private and unlicensed at repository level until content is classified. Do not add a blanket Apache license to papers or publisher PDFs. | Classify authored notes/code versus third-party papers, figures, supplementary data and publisher content. |
| ship-performance-platform (private) | No root LICENSE found | Apache-2.0 for original software if/when approved for open-source release; customer/commissioned data and deliverables remain separately restricted. | Confirm ownership, customer/contract restrictions, dependencies and intended publication scope. |

**Scope note:** The observed-license column is based on fetching the exact root path `LICENSE` from each default branch on 2026-11-10. A missing root file does not prove every file is unlicensed or establish ownership. Alternative license filenames, nested licenses, file headers, dependency metadata, submodules and full PR histories still require inspection.

## 3. Migration waves

### Wave A — establish rights for currently unlicensed software repositories

Priority: TensorData, TensorKnowledge, TensorDEM, TensorPhysics.

For each repository, produce a machine-readable audit packet containing:
- default branch and current commit SHA;
- merged PR contributors and material commit authors;
- agent-produced contribution provenance and accountable human;
- source/header/license scan results;
- dependency and SBOM/license scan;
- vendored, copied, generated and submodule inventory;
- data/model/paper/image/benchmark asset inventory;
- unresolved rights questions and recommended disposition.

Only after the maintainer confirms the rights basis should the repository receive an Apache-2.0 root LICENSE. For mixed-content repositories, scope the license explicitly and keep third-party/content assets under their own terms.

### Wave B — assess MIT repositories

Priority: TensorPlatform, TensorSPH, TensorDG.

MIT is already a permissive open-source license and is not a blocker to the open-source strategy. Do not spend effort changing these merely for visual uniformity. If Apache-2.0 is still desired, first establish a valid rights path for every contribution that would be included in the newly licensed combined work. Otherwise retain MIT and use Apache-2.0 for new, separable components where rights are clear.

### Wave C — assess GPL repositories

Priority: TensorLBM, TensorFVM.

Do not treat GPL-3.0 as an accidental typo until proven. It may have been selected casually, but once published it grants rights under GPL-3.0 to recipients of the covered code. A future Apache-2.0 migration requires a valid relicensing path for all relevant contributions and a complete compatibility review. If that cannot be established, retain GPL-3.0 or isolate/rewrite components with unresolved rights. Do not copy GPL-only code into Apache-only distributions without a reviewed compatibility basis.

### Wave D — private and content-heavy repositories

TensorPapers and ship-performance-platform need content/contract classification before any blanket license is added. Public release is a separate decision from choosing a software license.

## 4. Machine-checkable status vocabulary

Use these values in future inventory updates:
- `LICENSED_CONFIRMED`: license text and scope verified.
- `LICENSE_PRESENT_SCOPE_UNREVIEWED`: root license exists but file-level scope and compatibility are not fully audited.
- `NOASSERTION`: no verified license grant for the relevant asset.
- `RIGHTS_REVIEW_REQUIRED`: evidence exists but a human rights decision is pending.
- `THIRD_PARTY_RESTRICTED`: upstream terms govern; do not relicense.
- `QUARANTINED`: exclude from redistribution/release until resolved.

## 5. Completion criteria

A repository is not considered license-ready merely because a root LICENSE file exists. It must also have:
- an explicit scope statement for code versus non-code assets;
- correct SPDX metadata;
- preserved copyright and upstream notices;
- dependency and vendored-code inventory;
- dataset/model/content rights manifests where applicable;
- CI checks for missing declarations and known incompatible assets;
- a recorded human decision for any first license grant, relicensing or external release.

## 6. Approval record

Initial root licenses were added on 2026-11-10 for TensorData (Apache-2.0), TensorKnowledge (Apache-2.0), TensorDEM (Apache-2.0), and TensorPhysics (MIT, matching existing package metadata). No existing license was changed. These initial grants are scoped by each repository's `LICENSE_SCOPE.md`; full dependency, third-party asset, and historical contribution audits remain outstanding. All future license transitions remain pending explicit human approval after evidence review.
