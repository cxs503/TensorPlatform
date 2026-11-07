---
name: adversarial-review
description: Independently challenge a proposal, implementation, or evidence bundle to find material defects and unsupported claims.
---

# Adversarial Review

## Use when
Reviewing a change, design, plan, incident conclusion, or completion claim, especially when the author is invested in the result.

## Independence
Do not rely solely on the author's summary. Inspect relevant source, tests, raw logs, artifacts, and contracts. Disclose shared context, same-model dependence, prior authorship, or other limits. Never claim independent reproduction unless performed.

## Procedure
1. Read problem contract, intended behavior, diff, and acceptance criteria.
2. Reconstruct the execution path independently.
3. Probe high-impact failure modes: invalid inputs, boundaries, concurrency, retries, partial failure, persistence, permissions, compatibility, resource exhaustion, rollback.
4. Check whether tests assert invariants or merely execute code.
5. Bind claims to raw evidence and exact commit/run identities.
6. Seek counterexamples and attempt authorized reproduction.
7. Classify findings by severity, likelihood, evidence, and affected scope.
8. Separate blocking correctness/security issues from non-blocking suggestions.
9. Mark missing checks unknown/not_run, not passed.
10. Give a disposition: approve, approve with notes, request changes, or escalate.

## Required artifact: Review Report
Reviewed commit/artifacts; independence limitations; scope/checks performed; severity-ranked findings with evidence and reproduction; checks not run; overstated claims; disposition and remediation.

## Practice
Review a proposed file-upload endpoint with happy-path tests but no size limit, authorization-negative test, or interrupted-upload handling. Support each finding with code/contract evidence.
Pass evidence: identify material gaps; distinguish confirmed defects from untested risks; do not approve merely because tests pass.

## Quality note
Each claim must be traceable to source, observation, command output, or artifact. Label facts, inferences, assumptions, and unknowns separately. Missing checks are not_run, never passed. Stay within permissions and repository/domain boundaries. Do not claim competence from fluent prose, task completion, PR count, or a green test alone.
