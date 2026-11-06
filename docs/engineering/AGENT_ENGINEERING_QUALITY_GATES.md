# Agent Engineering Quality Gates

**Standard:** Agent Production Quality & Governance Standard  
**Version:** 1.1  
**Status:** Normative policy for adoption across the Tensor ecosystem  
**Owner:** TensorPlatform  
**Scope:** Agent-authored and agent-assisted changes, from task intake through post-merge monitoring

## 1. Purpose and governing principles

This standard defines a reusable, evidence-driven quality and governance mechanism for Agent-authored changes across the Tensor ecosystem. A green CI check, a completed task status, or an Agent summary is not, by itself, proof of quality.

The mechanism is tool-neutral. Ruff, pytest, solver-native checks, HPC schedulers, semantic scanners, and future validators are adapters to the policy; no single tool is the policy itself.

### 1.1 Core rule

**Accept a change only against explicit, proportionate, independently reviewable evidence.**

Keep three assurance dimensions separate:

1. **Engineering quality:** software behavior, interfaces, maintainability, security, and compatibility.
2. **Scientific quality:** numerical correctness, reproducibility, benchmark agreement, and validity within a declared physical/application scope.
3. **Production quality:** task ownership, Agent coordination, integration safety, evidence integrity, delivery, and post-merge operation.

Passing one dimension never implies that the others passed.

### 1.2 Normative language

- **MUST / MUST NOT:** mandatory for policy compliance.
- **SHOULD / SHOULD NOT:** expected unless a documented rationale justifies deviation.
- **MAY:** optional practice.

A missing required record or required check is **unknown/not_run**, never an implicit pass.

## 2. Quality-gate model

### Gate 0 — Change contract and risk classification

Before editing, the task record MUST capture:

- user-visible intent, expected behavior, and acceptance criteria;
- files/modules in scope and interfaces that must remain compatible;
- change class: documentation-only, refactor, bug fix, feature, numerical-method change, solver/physics change, data migration, or infrastructure/security change;
- risk level and a short rationale;
- smallest reproducer or regression test for the reported failure, where applicable;
- baseline commit SHA and relevant existing CI status;
- task ID, parent task ID where applicable, task owner, and intended verifier/reviewer;
- explicit out-of-scope items and resource/permission boundaries.

High-risk changes include collision/forcing schemes, conservation laws, constitutive/contact/fracture laws, coupling algorithms, unit conversions, parallel reductions, persistent data formats, authentication/authorization, secrets handling, and production deployment controls.

If the task scope or acceptance criteria materially change, the Agent MUST update the contract before proceeding.

### Gate 1 — Static and contract checks

Run applicable formatters, linters, type checks, API/schema checks, dependency/security scans, compatibility checks, and semantic rules.

1. Report tool name, exact version, configuration, command, and result.
2. Distinguish new findings from baseline findings.
3. A new high-confidence correctness rule MAY begin in advisory mode. Promote it to blocking only after precision, false-positive rate, and remediation guidance have been reviewed.
4. Never add a rule merely because an unsolicited email recommends it. Verify the project, source, package identity, permissions, and execution path first.
5. Third-party scanners MUST run with least privilege, no production secrets, and no write access unless specifically required and approved.
6. Policy checks MUST be applied consistently across equivalent execution routes; a bypass path must not silently avoid a required gate.

### Gate 2 — Focused regression tests

For every reported defect, add or identify a test that fails before the fix and passes after it. Include negative/invalid configurations when the contract prohibits them.

Record exact commands, environment, exit codes, and test counts. A test that was not run is `not_run`, not passed. A conditionally skipped test is `skipped`, not passed. Tests MUST target the intended invariant rather than merely execute the affected function.

When a test cannot be run, record the reason, impact, owner, and next action. Do not convert an environmental limitation into a pass.

### Gate 3 — Numerical and scientific validation

For numerical solvers, passing unit tests is necessary but insufficient. Run the smallest relevant reproducible case and then the appropriate benchmark tier.

- **Tier A — Kernel/unit:** local algebraic invariants, invalid-option rejection, conservation checks where applicable.
- **Tier B — Solver regression:** fixed seeds/inputs, tolerances, convergence and stability checks, and comparison with a trusted baseline.
- **Tier C — Published/community benchmark:** reference curves/values, mesh/time-step sensitivity where applicable, and documented discrepancy.
- **Tier D — Application case:** realistic coupled case with run manifest, parameters, resource details, outputs, and acceptance criteria.

The benchmark contract MUST identify the benchmark source, version, reference data or method, applicable physical regime, metric definitions, and acceptance thresholds. Declare tolerances before inspecting the candidate result. Do not tune thresholds after a failure without documented scientific rationale and independent review.

For relevant solver changes, validation SHOULD address:

- conservation and physical invariants;
- stability and convergence behavior;
- mesh, particle, and time-step sensitivity;
- comparison with analytical solutions, experiments, or trusted reference solvers;
- parameter sensitivity and uncertainty;
- coupled-interface consistency;
- precision, hardware, and parallel-scaling effects where relevant.

A benchmark validates only the tested configuration and scope. It MUST NOT be generalized into a claim that the entire solver or all applications are scientifically validated.

### Gate 4 — Reproducibility and evidence bundle

Each execution SHOULD emit a machine-readable record containing, at minimum:

- task/run ID and parent/related run IDs;
- repository, branch, commit SHA, and dirty-worktree status;
- case/benchmark ID, version, source, and reference-data identity;
- software/runtime/dependency versions and hardware/backend where relevant;
- parameters, seed, command, and environment settings that affect results;
- start/end time, executor identity, exit code, timeout/resource status;
- test/validator identifiers, versions, thresholds, measured values, and status;
- artifact URI/path, byte size, media/type information, and SHA-256;
- separate `execution_status`, `engineering_validation_status`, and `scientific_validation_status`;
- provenance linking the evidence to the exact commit and run.

Recommended status values are `not_run`, `queued`, `running`, `passed`, `failed`, `skipped`, `inconclusive`, and `blocked`, where meaningful. Status vocabularies MUST be defined by the relevant schema; systems MUST NOT silently map unknown states to passed.

Execution success does not imply engineering validation or scientific validation success. Missing evidence remains explicitly missing.

Never store secrets in manifests or logs. Redact credentials and sensitive environment values.

#### 4.1 Evidence integrity

The platform SHOULD collect execution facts directly from the runner, scheduler, CI provider, or artifact store rather than relying only on Agent-authored summaries.

Evidence MUST be bound to the subject it claims to verify: repository, commit SHA, run ID, benchmark version, and artifact hashes. Where practical, retain immutable or append-only records and signed execution attestations. A hash proves byte identity, not scientific correctness or the truth of the producer's claim.

A verifier MUST be able to inspect raw logs, metrics, manifests, and artifacts, subject to access controls. Summaries are indexes into evidence, not substitutes for it.

### Gate 5 — Independent review and promotion

The Agent submits a PR containing the implementation, regression coverage, evidence summary, known limitations, and reproduction commands.

Reviewers MUST assess:

- why the change is needed and whether it matches the task contract;
- code scope, interfaces, compatibility, and security implications;
- checks that ran, did not run, failed, or were skipped;
- new failures versus baseline failures;
- numerical differences, reference sources, and predeclared thresholds;
- artifact links, hashes, and reproducibility;
- remaining risks, rollback path, and required follow-up.

The authoring Agent MUST NOT be the sole authority that certifies its own high-risk change. Independent verification means the verifier inspects the relevant source and raw evidence and, for high-risk work, independently reruns or reproduces key checks where feasible. A second Agent that merely repeats the first Agent's summary is not independent verification.

High-risk solver/physics changes require focused regression, relevant benchmark evidence, independent review, and human approval until an automated acceptance path is independently auditable and explicitly authorized.

## 3. Risk levels and merge authority

Risk classification MUST control required checks and merge permissions, not merely appear in a report. The following levels are the default minimum policy; repository owners MAY impose stricter rules.

| Level | Typical changes | Minimum evidence | Merge authority |
|---|---|---|---|
| **L0 — Trivial** | Documentation, comments, non-executable metadata | Formatting/link checks as applicable | Automation may merge after required checks |
| **L1 — Routine** | Low-risk features, isolated refactors, ordinary bug fixes | Focused tests, applicable static/contract checks | Automation may merge if repository policy permits |
| **L2 — Core engineering** | Shared APIs, core data structures, persistence behavior, performance-sensitive paths | Regression suite, compatibility checks, independent review | Designated reviewer or authorized policy gate |
| **L3 — Scientific/high impact** | Numerical methods, solver kernels, physics models, coupling, material/contact/fracture laws | L2 evidence plus relevant benchmark tiers, scientific evidence, independent verification | Human approval required by default |
| **L4 — Critical control plane** | Authentication, authorization, secrets, destructive operations, release/deployment controls, critical migrations | Security review, negative tests, recovery/rollback evidence, least-privilege audit | Explicit human approval; no self-approval by the implementing Agent |

The task owner MUST NOT lower a risk level merely to obtain a faster merge. Any downgrade requires a recorded rationale and approval by an authorized reviewer. Changes spanning multiple levels inherit the highest applicable level.

### 3.1 Merge conditions

A PR is eligible for merge only when:

1. required checks for its risk level have completed successfully;
2. no unresolved blocking finding remains;
3. required independent review/approval is recorded;
4. the head commit being merged is the commit that was verified, or all required checks have been rerun against the updated head;
5. evidence and task/PR relationships are recorded;
6. any exception is explicitly approved and unexpired.

A green status from an obsolete head SHA is not a valid merge gate. A successful merge is not, by itself, proof of scientific validity.

## 4. Task ownership and accountability

Every non-trivial task MUST have one accountable task owner. Work may be delegated, parallelized, or split into subtasks, but accountability MUST remain explicit across handoffs.

The task record SHOULD include:

- `task_id`, `parent_task_id`, and linked PR/run IDs;
- `owner_agent_id` and human/organizational owner where applicable;
- contributor Agent IDs and their bounded responsibilities;
- verifier/reviewer identity and independence declaration;
- acceptance owner and required approval level;
- task contract, risk level, state transitions, and handoff records.

The task owner is responsible for keeping the task contract current, coordinating dependencies, ensuring evidence is assembled, and obtaining the required acceptance. This does not make the owner the sole technical reviewer.

Handoffs MUST transfer scope, current state, changed files/commits, known failures, evidence locations, unresolved risks, and the next expected action. A handoff is not complete merely because a message was sent.

The platform SHOULD preserve an auditable event history for assignment, delegation, handoff, verification, approval, merge, rejection, and rollback. Agent identity and authority MUST be checked by the platform; self-declared identity in task text is not sufficient.

## 5. Independent verification and separation of duties

The degree of separation MUST be proportionate to risk.

- L0/L1 MAY use automated checks authored by the same contributor, subject to repository policy.
- L2 SHOULD use a reviewer who did not author the relevant change and should use independent checks for shared contracts.
- L3 MUST have an independent verifier for the key regression/benchmark evidence and human approval by default.
- L4 MUST have explicit human approval and independent security/control review.

The verifier MUST have access to the actual change and raw evidence. The verifier SHOULD use a clean or controlled environment and MUST NOT silently modify acceptance criteria. Any verifier-authored fix creates a new change that requires its own verification.

Where full independence is infeasible, record the limitation and downgrade the assurance claim; do not label the result independently verified.

## 6. Parallel Agent production and integration gates

A PR can pass in isolation and still break the integrated system. Multi-Agent production therefore requires integration controls.

1. **Change registration:** tasks SHOULD declare expected files, modules, APIs, and dependencies before parallel work begins.
2. **Conflict awareness:** the platform SHOULD detect overlapping file/API ownership and notify task owners before conflicting changes accumulate.
3. **Branch freshness:** required checks MUST run against the current PR head and, where merge-base changes affect behavior, against the latest target branch.
4. **Integration ordering:** shared interfaces, schemas, solver kernels, and critical control-plane files SHOULD use a serialized or explicitly coordinated merge queue.
5. **Cross-PR contracts:** related PRs MUST identify dependencies and expected merge order. A dependent PR MUST NOT be treated as independently releasable if its contract depends on an unmerged change.
6. **Post-merge integration:** high-impact merges MUST trigger relevant integration regression on the resulting target-branch commit.
7. **Conflict ownership:** the task owner MUST coordinate resolution of semantic conflicts; resolving Git text conflicts alone does not establish behavioral compatibility.

The platform SHOULD track merge queue age, stale-head failures, integration regressions, and rework caused by conflicting changes. Parallelism is successful only when accepted integrated outcomes improve, not merely when PR volume increases.

## 7. Failure attribution, exceptions, and recovery

### 7.1 Failure attribution

For every failing check, classify it as:

- `introduced`: demonstrated to be caused by the patch;
- `pre_existing`: reproducible on the baseline commit;
- `environment`: dependency, hardware, service, or infrastructure issue;
- `flaky`: intermittently failing under a documented rerun protocol;
- `unknown`: insufficient evidence.

Do not dismiss failures as unrelated without a baseline comparison or clear causal explanation. Report the denominator, failures, skips, and relevant environment.

### 7.2 Exception states

Use explicit dispositions:

- **Blocked:** a required condition is unmet; merge is prohibited.
- **Quarantined:** behavior or evidence is unstable; isolate from normal promotion.
- **Waived:** an authorized, narrowly scoped exception has been granted.
- **Rejected:** acceptance criteria were not met; return for remediation.
- **Rolled back:** a merged change was reverted or disabled due to unacceptable impact.

A waiver MUST record the exact requirement waived, rationale, risk, scope, approver, compensating controls, creation time, expiry/review date, and linked task/PR. Waivers MUST expire or be explicitly renewed. A waiver MUST NOT be used to relabel a failed check as passed.

### 7.3 Recovery

For a serious regression, the task owner MUST coordinate containment, rollback or disablement, impact assessment, and a corrective task. Preserve the failing commit, evidence, and reproduction case. Confirm the recovery on the target branch and add a regression fixture for the confirmed defect where feasible.

Repeated failures should trigger process review: test quality, task decomposition, permissions, Agent assignment, and verification design. Do not respond only by increasing retries or weakening thresholds.

## 8. Scientific assurance levels

Scientific claims MUST state their assurance scope. The following labels are cumulative only when the required evidence for each level is present:

- **software_verified:** software behavior satisfies the declared software contract and relevant tests.
- **numerically_verified:** applicable numerical invariants, stability/convergence, and sensitivity checks passed for the declared configuration.
- **benchmark_validated:** specified benchmark(s) passed against identified reference data/method and predeclared criteria.
- **experimentally_validated:** results were compared with identified experimental data, including uncertainty and measurement limitations.
- **application_qualified:** evidence supports use within a clearly stated application envelope, assumptions, and limitations.

A higher label MUST NOT be inferred from a lower one. For example, a benchmark result does not automatically establish experimental validation or application qualification. Every claim MUST identify the solver version, configuration, tested regime, benchmark/reference, and known limitations.

For coupled and multiphysics applications, each component's evidence and the coupling/interface evidence MUST be reported separately. A successful end-to-end run alone does not prove each physical model is valid.

## 9. Semantic rule / scanner adoption protocol

When evaluating a tool such as Perch or a custom semantic checker:

1. Verify the upstream repository, maintainer, package registry identity, license, release activity, and installation instructions independently.
2. Inspect rule source and test fixtures; understand scope and data/network/credential behavior.
3. Run it on a dedicated branch or non-blocking CI job, with a pinned version and no privileged secrets.
4. Compare findings against existing linters, unit tests, and known regressions. Label each finding true positive, false positive, duplicate, or uncertain.
5. Measure precision and incremental value on representative changes; include known-positive and known-negative fixtures.
6. Document suppressions with narrow scope, rationale, owner, and review date.
7. Promote to a required check only after review; retain a documented disable/rollback path.

A rule is valuable when it catches a meaningful class of defect not already reliably caught by current checks—not simply when it produces more findings.

## 10. Agent operating protocol

For every engineering task, the Agent follows this loop:

1. **Inspect:** read actual repository state, branch, relevant source/tests, task contract, and latest CI evidence.
2. **Plan:** state the invariant, risk, ownership, planned edit, dependencies, and verification commands.
3. **Implement:** make a bounded, reviewable change; avoid unrelated cleanup.
4. **Verify:** run focused checks first, then broader checks appropriate to risk.
5. **Compare:** separate patch-caused regressions from baseline failures.
6. **Package evidence:** preserve logs, metrics, manifests, and artifact hashes.
7. **Handoff/review:** provide complete state and evidence to the independent reviewer or next owner.
8. **Report honestly:** summarize commit/PR, commands and results, unrun checks, residual risks, and reproduction steps.
9. **Learn:** turn confirmed defects into regression fixtures or reusable rules without hard-coding one-off assumptions into general policy.

The Agent MUST NOT claim a command ran, a test passed, a file changed, or a deployment was verified unless a tool result or retained artifact proves it.

Agents MUST operate within task-scoped permissions. A task assignment does not automatically authorize unrelated repository writes, secret access, destructive actions, production deployment, or policy changes. The platform SHOULD enforce least privilege outside the model prompt.

## 11. Post-merge monitoring and continuous quality

Quality control continues after merge.

For changes selected by risk and repository policy:

- run post-merge smoke and integration checks against the resulting target-branch SHA;
- track benchmark drift, numerical regression, performance/resource regression, and API/schema compatibility;
- preserve links from deployed/released versions to source commits, tasks, verification runs, and artifacts;
- create a tracked incident or corrective task when a regression is confirmed;
- pause or restrict automatic merge privileges for a workflow/Agent when repeated serious violations indicate the gate is ineffective;
- verify rollback or disablement procedures periodically for critical paths.

The platform SHOULD report quality outcomes, not just activity volume. Recommended measures include:

- escaped defects and severity;
- rollback and post-merge regression rate;
- first-pass acceptance rate and rework rate;
- proportion of changes with complete evidence;
- independent verification coverage by risk level;
- flaky/blocked check rate and waiver count/age;
- integration conflicts and merge-queue revalidation failures;
- benchmark drift and performance regression;
- median time to detect, contain, and correct a defect.

PR count, lines of code, Agent task count, and raw merge speed MUST NOT be used alone as productivity or quality measures.

## 12. Minimum acceptance checklist

- [ ] Scope, invariant, acceptance criteria, owner, and risk class are recorded.
- [ ] Baseline commit and relevant baseline CI are known.
- [ ] Task, PR, run, and parent/child relationships are traceable.
- [ ] Focused regression test covers the defect or prohibited configuration.
- [ ] Applicable static/contract checks are reported with versions and results.
- [ ] Relevant numerical benchmark and tolerances were declared before evaluation.
- [ ] Execution, engineering validation, and scientific validation statuses are separate.
- [ ] Evidence is bound to the exact commit/run and artifacts include hashes where supported.
- [ ] New failures are distinguished from baseline/environment/flaky failures.
- [ ] Required independent review and approvals are recorded.
- [ ] PR includes exact reproduction commands and honest limitations.
- [ ] Integration checks are complete for the change risk.
- [ ] Waivers, if any, are approved, scoped, and unexpired.
- [ ] Post-merge checks and rollback path are defined where required.

## 13. Tensor ecosystem implementation path

Adopt this policy incrementally. This document defines the policy; it does not claim that every mechanism is already implemented in TensorPlatform or every solver repository.

1. **Platform contract:** publish this standard and map its fields to the existing Task, RunManifest, artifact, PR, and audit models.
2. **Enforcement map:** maintain a per-repository matrix showing each requirement as `implemented`, `partial`, `documented_only`, or `not_implemented`, with code/test evidence.
3. **Task ownership:** connect task IDs, owner/contributor/verifier roles, handoffs, PRs, runs, and approvals.
4. **Policy gates:** implement risk-aware checks and merge authorization in TensorPlatform; do not rely on prompts alone for permissions or enforcement.
5. **Evidence plane:** collect runner-originated execution facts and immutable artifact references; separate execution from engineering/scientific verdicts.
6. **Independent verification:** pilot on high-risk solver changes with clean reruns and reference comparisons.
7. **Integration safety:** add branch freshness, dependency-aware merge ordering, and post-merge regression.
8. **Solver ownership:** TensorPhysics coordinates solver-agnostic scientific gates; solver repositories retain domain-specific tests, benchmarks, and acceptance criteria.
9. **Pilot and audit:** demonstrate the full workflow on at least one real solver benchmark, audit the evidence independently, then expand repository by repository.
10. **Promotion:** only describe a gate as enforced after implementation and tests demonstrate that bypass routes are closed.

### 13.1 Required implementation status vocabulary

Every requirement in the enforcement map MUST use one of:

- `implemented`: code and tests demonstrate the mechanism is enforced;
- `partial`: some paths or cases are enforced, with gaps listed;
- `documented_only`: written guidance exists but platform enforcement is absent;
- `not_implemented`: no verified implementation exists;
- `not_applicable`: justified for the repository or change class.

A feature flag, UI label, prompt instruction, or roadmap item alone is not proof of enforcement.

## 14. Example evidence summary

```text
Task: <task ID>   Owner: <Agent/human ID>   Risk: L3
Change: reject incompatible forcing/equilibrium configuration
Baseline: <commit SHA>   Candidate: <commit SHA>
Focused test: <command> — passed | failed | not_run
Static rules: <tool/version> — N new, M baseline, K uncertain
Solver regression: <case + command> — status and metrics
Scientific benchmark: <benchmark/version/source> — status, tolerance, measured error
Execution status: passed | failed | not_run
Engineering validation: passed | failed | inconclusive | not_run
Scientific validation: passed | failed | inconclusive | not_run
Independent verifier: <identity>   Approval: <identity/status>
Artifacts: <URI/path>, SHA-256 <digest>
Waivers: <none or scoped waiver reference>
Unrun checks / residual risks: <explicit list>
Post-merge check: <required check and status>
```

This example describes the evidence format, not a claim that any particular change or benchmark has been executed.

## 15. Change history

### Version 1.1

Expanded the original engineering quality gates into an Agent production quality and governance standard. Added task ownership and handoff accountability, independent verification and separation of duties, risk-to-merge authority mapping, parallel Agent integration controls, exception/waiver/recovery procedures, explicit scientific assurance levels, evidence integrity requirements, post-merge monitoring, and an implementation-status vocabulary. Clarified that policy publication does not prove platform enforcement.
