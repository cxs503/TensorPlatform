# Scientific Regression Rule Contract

## Purpose

This document defines a repository-neutral contract for recording and evaluating regression rules across TensorLBM, TensorBEM, TensorDEM, TensorFEM, TensorFVM, and other scientific projects in the Tensor ecosystem.

The contract is a metadata and evidence interface, not a replacement for solver-owned tests. A rule is only enforceable when its implementation and fixtures exist and the applicable CI job actually runs.

## Design principles

1. **Domain ownership stays local.** Each solver repository owns the scientific meaning, implementation, tests, and acceptance tolerances of its rules.
2. **Common metadata is portable.** TensorPlatform/TensorPhysics can catalog rules and evidence without reimplementing domain physics.
3. **No evidence, no pass.** Missing or unexecuted checks are `not_run`; skipped checks are `skipped`; a successful process is not automatically a scientifically validated result.
4. **Rules need a counterexample.** Each blocking rule should have a known-positive fixture (the prohibited or faulty behavior is detected) and a known-negative fixture (valid behavior is accepted).
5. **Adoption is staged.** New scanners begin in advisory mode until precision, false positives, overlap with existing checks, and remediation guidance are reviewed.
6. **Reproducibility matters.** Every reported decision points to a commit, command, validator version, threshold, and available artifacts.

## Rule lifecycle

- `proposed`: intent and scientific rationale recorded; not enforced.
- `advisory`: runs in CI but does not block changes; findings are reviewed.
- `blocking`: approved rule with fixtures, owner, documented scope, and stable CI integration.
- `deprecated`: no longer recommended; reason and replacement (if any) recorded.

Allowed check statuses: `not_run`, `running`, `passed`, `failed`, `skipped`, `inconclusive`.

## Minimum rule record

A rule registry entry should include:

| Field | Meaning |
|---|---|
| `schema_version` | Version of this metadata contract |
| `rule_id` | Stable, namespaced identifier |
| `title` | Short human-readable summary |
| `project` | Owning repository, e.g. `cxs503/TensorLBM` |
| `owner` | Team or maintainer responsible for scientific interpretation |
| `category` | `static`, `api_contract`, `numerical_invariant`, `solver_regression`, or `benchmark` |
| `severity` | `info`, `warning`, `error`, or `critical` |
| `lifecycle` | One of the lifecycle states above |
| `statement` | Precise behavior that must hold or must be rejected |
| `rationale` | Scientific/software reason, with references where applicable |
| `scope` | Files, symbols, APIs, or benchmark IDs covered |
| `validator` | Tool/module and pinned version or revision |
| `fixtures` | Positive and negative fixture/test identifiers |
| `acceptance` | Expected outcome and numeric tolerances, if applicable |
| `remediation` | Actionable fix guidance |
| `reviewed_at` | Date of last scientific/engineering review |
| `evidence` | CI run, commit, logs, reports, and artifact references |

## Example rule record

The following is an illustrative record only. It is not evidence that the rule is currently implemented or enabled in CI.

```json
{
  "schema_version": "1.0",
  "rule_id": "tensorlbm.multiphase3d_d3q27.forcing_velocity_contract",
  "title": "Reject an incompatible forcing and velocity configuration",
  "project": "cxs503/TensorLBM",
  "owner": "TensorLBM maintainers",
  "category": "api_contract",
  "severity": "error",
  "lifecycle": "proposed",
  "statement": "Reject the specific forcing/velocity combination only where the solver's documented physical formulation requires rejection.",
  "rationale": "Prevent a future edit from silently changing the intended momentum/forcing semantics.",
  "scope": [
    "src/tensorlbm/multiphase3d_d3q27.py",
    "focused regression tests"
  ],
  "validator": {
    "kind": "pytest",
    "command": "python -m pytest -q <focused-test-path>",
    "version": "pinned by project environment"
  },
  "fixtures": {
    "positive": "invalid combination is rejected",
    "negative": "documented valid combinations remain accepted"
  },
  "acceptance": {
    "invalid_configuration": "raises the documented exception",
    "valid_configuration": "preserves existing documented behavior"
  },
  "remediation": "Use the supported velocity/forcing formulation and add a regression test for any newly supported combination.",
  "reviewed_at": null,
  "evidence": []
}
```

The rule's exact statement, validator, fixtures, and lifecycle must be confirmed against the implementation and the numerical formulation before promotion to `blocking`. In particular, do not infer scientific correctness solely from an email, scanner finding, or rule title.

## Run evidence contract

Every rule evaluation should be associated with a run record containing:

- `repository`, `branch`, `commit_sha`, and clean/dirty source status;
- `rule_id`, `rule_revision`, validator name/version, and exact command;
- `execution_status` and `validation_status` as separate fields;
- start/end timestamps, exit code, environment and dependency versions;
- measured values, declared thresholds, and comparison/reference provenance where applicable;
- artifact URI/path, byte size, and SHA-256 when artifacts exist;
- failure attribution: `introduced`, `pre_existing`, `environment`, `flaky`, or `unknown`.

Never place credentials or sensitive environment values in a rule record or artifact.

## Validation layers

- **Static/API rules:** check code shape or prohibited configuration. Pair semantic rules with known-positive and known-negative fixtures.
- **Numerical invariants:** check properties such as finite values, conservation, symmetry, or valid ranges with explicit tolerances.
- **Solver regressions:** run a fixed reproducible case and compare metrics against declared tolerances or a trusted baseline.
- **Scientific benchmarks:** document the reference, discretization sensitivity, error norm, observed trend, and limitations.
- **Application validation:** record the actual run parameters, resource context, outputs, and independent acceptance evidence.

Passing one layer must not be reported as passing the next layer.

## CI and integration path

1. Keep the first implementation local to the solver repository and run it in advisory mode.
2. Store rule metadata next to the tests or in a small repository-local registry; avoid a central registry becoming a new source of truth for solver physics.
3. Export a normalized evaluation record as a CI artifact.
4. Let TensorPhysics orchestrate applicable checks and aggregate outcomes; let TensorPlatform catalog run manifests, evidence, provenance, and access policy.
5. Promote only reviewed high-confidence rules to blocking checks.
6. Retain a documented suppression/rollback path, with narrow scope, rationale, owner, and review date.

## Acceptance checklist for a new blocking rule

- [ ] The invariant or prohibited behavior is precise and scientifically reviewed.
- [ ] The rule has an owner and a stable identifier.
- [ ] A known-positive fixture demonstrates detection.
- [ ] A known-negative fixture guards against overblocking.
- [ ] The validator and its version/configuration are recorded.
- [ ] Baseline findings and false positives have been reviewed.
- [ ] The exact command runs in CI and its exit status is retained.
- [ ] Numeric thresholds are justified and declared before evaluation.
- [ ] Evidence distinguishes execution from validation.
- [ ] Remediation and rollback/suppression guidance are documented.
