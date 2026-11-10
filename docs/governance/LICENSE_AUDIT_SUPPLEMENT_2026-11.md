# License Audit Supplement — 2026-11-10

**Purpose:** Extend the initial root-license snapshot with a second verification pass and concrete follow-up controls.

## 1. Rechecked root LICENSE inventory

The following exact root paths were fetched from the current default branches:

| Repository | Current root LICENSE | Verified interpretation |
|---|---|---|
| TensorPlatform | MIT | Historical MIT grant; no automatic conversion |
| TensorPhysics | Not found | Root license still unresolved |
| TensorLBM | GPL-3.0 | Retain pending rights and dependency audit |
| TensorFEM | Apache-2.0 | Existing license |
| TensorFVM | GPL-3.0 | Retain pending rights and dependency audit |
| TensorDEM | Not found | Root license still unresolved |
| TensorBEM | Apache-2.0 | Existing license |
| TensorSPH | MIT | Historical MIT grant; no automatic conversion |
| TensorDG | MIT | Historical MIT grant; no automatic conversion |
| TensorCAD | Apache-2.0 | Existing license |
| TensorKnowledge | Not found | Root license still unresolved |
| TensorPapers | Not found | Private repository; content-level rights review required |
| ship-performance-platform | Not found | Private repository; content-level rights review required |
| TensorData | Not found | Dataset rights must be governed independently of code |
| TensorBench | Apache-2.0 | Added before first code contribution |

“Not found” refers only to the exact root path `LICENSE`; it does not establish that every file in that repository is unlicensed or that the maintainer owns every contribution. Alternative license filenames, nested licenses, file headers, dependency metadata, submodules and complete PR histories remain to be inspected.

## 2. Third-party material signal

A repository search in TensorPlatform surfaced an existing FEM open-source inventory artifact at `docs/oss_inventory_fem/raw/repo_readme.json`. Its captured README material references OOFEM under LGPL-2.1 and ryujin under Apache-2.0 WITH LLVM-exception, and explicitly notes that portions of contributed source code and dependencies may use different licenses.

This is a **review signal, not a finding of infringement**. Before any corresponding code, benchmark, documentation, or source bundle is redistributed, inspect the actual imported files, upstream license texts, notices, versions, and local modifications. Do not assume a project-level license overrides those terms.

## 3. Priority remediation queue

1. **TensorData:** define per-asset rights manifests and block promotion of unknown/unreviewed rights. A structural validator is now present in TensorData under `rights/` and `scripts/check_rights_manifests.py`.
2. **TensorKnowledge / TensorPhysics / TensorDEM:** inspect authorship, merged PR contributors, agent-generated files, imports and dependencies before adding any root license.
3. **TensorPlatform / TensorSPH / TensorDG:** evaluate whether a prospective Apache-2.0 path is possible; preserve existing MIT grants and notices unless every necessary right is cleared.
4. **TensorLBM / TensorFVM:** retain GPL-3.0; identify dependency and distribution obligations before any integration with Apache-only components.
5. **All repositories:** scan alternate license files, package metadata, source headers, vendored code, submodules, datasets, model weights and benchmark assets. Generate SBOM/license reports from actual build inputs rather than hand-maintained guesses.

## 4. Current status and limits

- No existing repository license was changed in this supplement.
- No third-party asset was relicensed.
- No conclusion is made about the legal ownership of code merely because it appears in a repository owned by the maintainer.
- Automated scanners can identify candidate licenses and dependencies, but ambiguous findings require human review.
- Any relicensing, dual licensing, or release of private/customer/restricted material requires explicit rights confirmation and, where appropriate, qualified legal advice.
