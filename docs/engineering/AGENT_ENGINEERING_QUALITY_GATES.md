# Agent Engineering Quality Gates

## Purpose

This document defines a reusable, evidence-driven quality mechanism for Agent-authored changes across the Tensor ecosystem. A green CI check or an Agent summary is not, by itself, proof. The mechanism is tool-neutral: Ruff, pytest, Perch, solver-native checks, HPC schedulers, and future validators are adapters to the same policy; no single tool is the policy itself.

## Core rule

**Accept a change only against explicit, proportionate evidence.** Separate software correctness, numerical reproducibility, and scientific validity. Passing one layer must never be reported as passing the others.

## Quality-gate model

### Gate 0 — Change contract and risk classification

Before editing, record:
- user-visible intent and expected behavior;
- files/modules in scope and interfaces that must remain compatible;
- change class: documentation-only, refactor, bug fix, numerical-method change, solver/physics change, or infrastructure/security change;
- risk: low, medium, or high, with a short reason;
- smallest reproducer or regression test for the reported failure;
- baseline commit SHA and relevant existing CI status.

High-risk changes include collision/forcing schemes, conservation laws, constitutive/contact/fracture laws, coupling algorithms, unit conversions, parallel reductions, persistent data formats, and security boundaries.

### Gate 1 — Static and contract checks

Run applicable formatters, linters, type checks, API/schema checks, dependency/security scans, and semantic rules.

1. Report the tool and exact version/configuration.
2. Distinguish new findings from baseline findings; do not silently reclassify pre-existing debt as caused by the patch.
3. A new high-confidence correctness rule may begin in advisory mode. Promote it to blocking only after precision, false-positive rate, and remediation guidance have been reviewed.
4. Never add a rule merely because an unsolicited email recommends it. Verify the project, source, package identity, permissions, and execution path first.
5. Third-party scanners run with least privilege, no production secrets, and no write access unless specifically required and approved.

### Gate 2 — Focused regression tests

For every reported defect, add or identify a test that fails before the fix and passes after it. Include negative/invalid configurations when the contract prohibits them.

Record exact commands and exit codes. A test that was not run is not_run, not passed. A conditionally skipped test is skipped, not passed. Tests must target the intended invariant rather than merely execute the affected function.

### Gate 3 — Numerical and scientific validation

For numerical solvers, passing unit tests is necessary but insufficient. Run the smallest relevant reproducible case and then the appropriate benchmark tier:

- **Tier A — Kernel/unit:** local algebraic invariants, invalid-option rejection, conservation checks where applicable.
- **Tier B — Solver regression:** fixed seeds/inputs, tolerances, convergence and stability checks, and comparison with a trusted baseline.
- **Tier C — Published or community benchmark:** reference curves/values, mesh/time-step sensitivity where applicable, and documented discrepancy.
- **Tier D — Application case:** realistic coupled case with run manifest, parameters, resource details, outputs, and acceptance criteria.

Declare benchmark tolerances before inspecting the new result. Do not tune acceptance thresholds after a failure without documenting and reviewing the scientific rationale. Save curves, fields, logs, and machine-readable metrics as artifacts; record hashes and provenance.

### Gate 4 — Reproducibility and evidence bundle

Each run should emit a machine-readable record containing, at minimum:
- repository, branch, commit SHA, and dirty-worktree status;
- case/benchmark ID and version;
- software/runtime/dependency versions and hardware/backend where relevant;
- parameters, seed, command, and environment settings that affect results;
- start/end time, exit code, and timeout/resource status;
- test/validator identifiers, thresholds, measured values, and status;
- artifact URI/path, byte size, and SHA-256;
- separate execution_status and validation_status.

Recommended status values are not_run, running, passed, failed, skipped, and inconclusive where meaningful. execution_status=passed does not imply validation_status=passed. Missing evidence remains explicitly missing.

Never store secrets in manifests or logs. Redact credentials and sensitive environment values.

### Gate 5 — Review and promotion

The Agent submits a PR containing the implementation, regression coverage, evidence summary, and known limitations. A reviewer should see what changed and why; which checks ran and did not run; new failures versus baseline failures; numerical differences and acceptance thresholds; artifact links/hashes and exact reproduction commands; remaining risks and rollback path.

Merge policy is risk-based. Documentation-only changes may use lightweight checks. High-risk solver/physics changes require focused regression plus relevant benchmark evidence and human review until automated acceptance is independently auditable.

## Failure attribution: never mix unrelated failures

For every failing check, classify it as:
- introduced: demonstrated to be caused by the patch;
- pre_existing: reproducible on the baseline commit;
- environment: dependency, hardware, service, or infrastructure issue;
- flaky: intermittently failing under a documented rerun protocol;
- unknown: insufficient evidence.

Do not dismiss failures as unrelated without a baseline comparison or clear causal explanation. Do not claim a patch is validated because most tests pass. Report the denominator, failures, skips, and relevant environment.

## Semantic rule / scanner adoption protocol

When evaluating a tool such as Perch or a custom semantic checker:

1. Verify the upstream repository, maintainer, package registry identity, license, release activity, and installation instructions independently of any unsolicited email.
2. Inspect rule source and test fixtures; understand scope and data/network/credential behavior.
3. Run it on a dedicated branch or non-blocking CI job, with a pinned version and no privileged secrets.
4. Compare findings against existing linters, unit tests, and known regressions. Label each finding true positive, false positive, duplicate, or uncertain.
5. Measure precision and incremental value on representative changes; include a known-positive fixture and a known-negative fixture.
6. Document suppressions with narrow scope, rationale, owner, and review date.
7. Promote to a required check only after review; retain a documented disable/rollback path.

A rule is valuable when it catches a meaningful class of defect not already reliably caught by current checks—not simply when it produces more findings.

## Agent operating protocol

For every engineering task, the Agent follows this loop:

1. **Inspect:** read actual repository state, branch, relevant source/tests, and latest CI evidence.
2. **Plan:** state the invariant, risk, planned edit, and verification commands.
3. **Implement:** make a bounded, reviewable change; avoid unrelated cleanup.
4. **Verify:** run focused checks first, then broader checks appropriate to risk.
5. **Compare:** separate patch-caused regressions from baseline failures.
6. **Package evidence:** preserve logs, metrics, manifests, and artifact hashes.
7. **Report honestly:** summarize commit/PR, commands and results, unrun checks, residual risks, and reproduction steps.
8. **Learn:** turn confirmed defects into regression fixtures or reusable rules without hard-coding one-off assumptions into general policy.

The Agent must not claim a command ran, a test passed, a file changed, or a deployment was verified unless a tool result or retained artifact proves it.

## Minimum acceptance checklist

- [ ] Scope, invariant, and risk class are recorded.
- [ ] Baseline commit and relevant baseline CI are known.
- [ ] Focused regression test covers the defect or prohibited configuration.
- [ ] Applicable static/contract checks are reported with versions.
- [ ] Relevant numerical benchmark and tolerances are declared before evaluation.
- [ ] Execution and scientific validation statuses are separate.
- [ ] Run manifest and artifacts include provenance and SHA-256 hashes where supported.
- [ ] New failures are distinguished from baseline/environment/flaky failures.
- [ ] PR includes exact reproduction commands and honest limitations.
- [ ] High-risk changes receive appropriate independent review.

## Tensor ecosystem implementation path

Adopt this policy incrementally rather than adding a large mandatory CI matrix all at once:

1. Publish the contract and checklist in TensorPlatform.
2. Reuse the existing RunManifest/artifact model for execution provenance and validation status.
3. Add adapters in TensorPhysics for solver-agnostic gate orchestration; solver repositories retain ownership of domain tests and benchmarks.
4. Pilot new semantic scanners in advisory mode on a dedicated branch.
5. Establish per-repository baseline reports and promote only high-confidence, useful checks to required status.
6. Demonstrate the full loop on a real solver benchmark before claiming end-to-end scientific validation.

## Example evidence summary

~~~text
Change: reject incompatible forcing/equilibrium configuration
Risk: high (collision/momentum semantics)
Baseline: <commit SHA>
Focused test: <command> — passed | failed | not_run
Static rules: <tool/version> — N new, M baseline, K uncertain
Solver regression: <case + command> — status and metrics
Scientific benchmark: <benchmark/version> — status, tolerance, measured error
Execution status: passed | failed | not_run
Validation status: passed | failed | inconclusive | not_run
Artifacts: <URI/path>, SHA-256 <digest>
Unrun checks / residual risks: <explicit list>
~~~

This example describes the evidence format, not a claim that any particular change or benchmark has been executed.
