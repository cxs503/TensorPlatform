---
name: problem-framing
description: Turn an ambiguous request into a bounded, testable problem contract.
---

# Problem Framing

## Use when
The request is vague, broad, outcome-oriented, or lacks acceptance criteria.

## Procedure
1. Restate the desired outcome without assuming the proposed solution is correct.
2. Identify beneficiary, users, decision owner, and acceptance authority.
3. Separate observed facts, supplied claims, inferences, assumptions, and unknowns.
4. Describe the current state and baseline evidence. If none exists, state what must be observed.
5. Define observable target behavior. Replace adjectives such as better, robust, and optimized with measurable outcomes.
6. List scope, non-goals, dependencies, compatibility constraints, permissions, budgets, and resource limits.
7. Define acceptance checks, metrics, thresholds, conditions, and who can judge them. Predeclare thresholds before seeing candidate outcomes where practical.
8. Classify uncertainty and risk. Escalate security, persistence, destructive, public-interface, costly, or specialist-domain decisions to the owner.
9. Ask only questions that block safe progress; otherwise proceed with reversible, labeled assumptions.
10. Update the contract before continuing if scope or acceptance changes materially.

## Required artifact: Problem Contract
Include goal; beneficiary; current state and evidence; target behavior; acceptance criteria; scope and non-goals; constraints and dependencies; assumptions and unknowns; risk and rationale; accountable owner; acceptance authority; next decision.

## Stop conditions
Stop or escalate when intent conflicts with policy, an irreversible decision lacks approval, specialist acceptance criteria are missing, or safe progress requires unavailable permissions/data.

## Practice
Turn “make background jobs reliable” into a contract for a service that sometimes loses jobs after restart. Do not propose code changes.
Pass evidence: distinguish lost jobs from delayed jobs; do not assume restart is the cause; define measurable recovery behavior; identify duplicate-execution and persistence risks; list baseline observations needed.

## Quality note
Each claim must be traceable to source, observation, command output, or artifact. Label facts, inferences, assumptions, and unknowns separately. Missing checks are not_run, never passed. Stay within permissions and repository/domain boundaries. Do not claim competence from fluent prose, task completion, PR count, or a green test alone.
