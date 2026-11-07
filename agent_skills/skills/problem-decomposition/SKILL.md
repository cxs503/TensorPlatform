---
name: problem-decomposition
description: Break complex goals into bounded work packages with dependencies, interfaces, owners, and integration checks.
---

# Problem Decomposition

## Use when
A goal spans multiple modules, agents, repositories, disciplines, or sequential decisions.

## Procedure
1. Start from an agreed problem contract; create one if missing.
2. Identify the smallest end-to-end outcome that demonstrates useful progress.
3. Map component ownership and system boundaries before splitting work.
4. Decompose by independently verifiable outcomes, not merely by files or layers.
5. For each package define objective, inputs, outputs, owner, acceptance test, dependencies, risk, permissions, and effort estimate.
6. Draw a dependency graph; mark critical path, parallel work, shared-resource contention, and integration points.
7. Agree interface contracts before parallel implementation.
8. Keep one accountable integrator. Avoid ambiguous write ownership over shared files or mutable resources.
9. Test high-uncertainty assumptions early and cheaply.
10. Reserve budget for integration, regression, and independent review.
11. Re-plan when evidence invalidates dependencies or estimates; record why.

## Required artifact: Work Breakdown
Include parent goal; work packages and owners; deliverables and acceptance tests; dependency graph; interfaces and version assumptions; parallelization rationale; conflict risks; integrator; risk register; completion definition.

## Quality checks
Every package has an inspectable artifact. “Research” has a concrete question. No overlapping write ownership. The plan includes end-to-end integration. Total work fits available time, compute, API, and review budgets.

## Practice
Plan an authenticated export endpoint across API, storage, docs, and tests using at most four agents. Identify dependencies, security review, ownership, and end-to-end acceptance.
Pass evidence: explicit interfaces, negative authorization tests, dependency-aware assignments, and one integration owner.

## Quality note
Each claim must be traceable to source, observation, command output, or artifact. Label facts, inferences, assumptions, and unknowns separately. Missing checks are not_run, never passed. Stay within permissions and repository/domain boundaries. Do not claim competence from fluent prose, task completion, PR count, or a green test alone.
