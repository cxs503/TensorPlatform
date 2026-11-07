---
name: systematic-debugging
description: Reproduce, localize, explain, and minimally fix a defect with regression coverage and traceable evidence.
---

# Systematic Debugging

## Use when
A defect, regression, intermittent failure, unexpected output, or operational incident needs diagnosis.

## Procedure
1. Triage impact and determine whether containment or rollback is needed before diagnosis.
2. Capture baseline: version/commit, environment, configuration, inputs, logs, and exact behavior.
3. Build the smallest safe reproducer. For intermittent failures, characterize frequency and conditions; never claim reproduction if absent.
4. State competing causal hypotheses.
5. Localize using evidence, controlled probes, reduction, or binary search. Change one factor at a time unless interactions are under test.
6. Explain the causal mechanism. A stack trace or failing line is a clue, not necessarily the root cause.
7. Design a regression test that fails on the unmodified baseline and targets the violated invariant.
8. Make the smallest justified fix; avoid unrelated cleanup.
9. Run focused and adjacent checks; record commands, versions, exit codes, counts, and skipped/not-run checks.
10. Obtain independent review for material risk; reviewer inspects source and raw evidence.
11. Document residual risk, rollback, and monitoring.

## Required artifact: Debug Record
Impact/containment; baseline/reproducer; hypotheses and elimination evidence; causal explanation; before/after regression result; minimal fix and paths; test results; not-run checks; reviewer; residual risk and rollback.

## Practice
Diagnose a seeded off-by-one pagination defect in a toy service. Add a test that fails before the fix, make the minimal change, and test boundary values.
Pass evidence: isolated reproducer; regression fails before and passes after; causal explanation; adjacent behavior remains covered.

## Quality note
Each claim must be traceable to source, observation, command output, or artifact. Label facts, inferences, assumptions, and unknowns separately. Missing checks are not_run, never passed. Stay within permissions and repository/domain boundaries. Do not claim competence from fluent prose, task completion, PR count, or a green test alone.
