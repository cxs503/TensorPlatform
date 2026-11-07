---
name: hypothesis-generation
description: Generate competing, falsifiable explanations and identify evidence that distinguishes them.
---

# Hypothesis Generation

## Use when
An observation has multiple plausible causes, debugging is inconclusive, or a team is converging prematurely.

## Procedure
1. State the observation precisely: conditions, frequency, impact, and what has not been observed.
2. Separate established facts, assumptions, and missing measurements.
3. Generate 3–5 materially different causal hypotheses, not synonyms.
4. For each, describe mechanism, supporting evidence, disconfirming evidence, predicted observations, and scope.
5. Rank by plausibility, impact, and testability; explain the basis without false numerical precision.
6. Identify the cheapest discriminating observation or experiment for leading hypotheses.
7. Check shared upstream causes, interactions, and confounders.
8. Preserve serious alternatives until evidence discriminates.
9. Update confidence explicitly when evidence arrives; preserve the original hypothesis history.
10. If no test can distinguish candidates, specify needed instrumentation or access.

## Required artifact: Hypothesis Register
Observation; facts/assumptions/unknowns; competing hypotheses; mechanisms and predictions; supporting/disconfirming evidence; discriminating tests and costs; provisional ranking and rationale; next observation; update history.

## Practice
A background worker's completion time doubled while request volume appears unchanged. Produce competing explanations across at least two system layers. Do not recommend a fix before specifying a discriminating test.
Pass evidence: predictions differ observably; workload mix and resource contention are considered; no root cause is declared without evidence.

## Quality note
Each claim must be traceable to source, observation, command output, or artifact. Label facts, inferences, assumptions, and unknowns separately. Missing checks are not_run, never passed. Stay within permissions and repository/domain boundaries. Do not claim competence from fluent prose, task completion, PR count, or a green test alone.
