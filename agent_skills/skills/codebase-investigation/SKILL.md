---
name: codebase-investigation
description: Establish actual repository behavior, architecture, tests, and change history before recommending edits.
---

# Codebase Investigation

## Use when
Asked what a project does, where behavior originates, why it regressed, or whether a change fits its architecture.

## Procedure
1. Confirm repository, branch/ref, commit SHA, working-tree state, and task scope.
2. Read repository instructions, charter, architecture boundaries, README, and quality gates.
3. Trace the actual execution path: entry point, validation, core logic, persistence/external dependency, response/event.
4. Inspect relevant tests and fixtures; state what they assert and do not assert.
5. Inspect recent commits and open changes touching the same paths; note concurrent work.
6. Search for duplicate implementations, compatibility layers, feature flags, defaults, and error paths.
7. Run the smallest safe reproducer or targeted tests when authorized; record exact commands and exit codes.
8. Classify findings as source-confirmed, runtime-confirmed, inference, or unknown/not inspected.
9. Compare the proposed change with repository ownership and public contracts.
10. Recommend proceed, investigate further, split work, or escalate.

## Required artifact: Investigation Report
Repository/ref/commit; inspected paths; architecture and execution map; relevant tests and coverage limits; concurrent-change notes; runtime checks; confirmed facts, inferences, unknowns; boundary risks; smallest next step.

## Practice
A service returns HTTP 202 for a queued operation, but users report it never completes. Investigate a small supplied repository without editing. Trace request acceptance through worker claim, execution, and terminal-state persistence.
Pass evidence: do not equate HTTP 202 with completion; mark every unverified transition as unknown.

## Quality note
Each claim must be traceable to source, observation, command output, or artifact. Label facts, inferences, assumptions, and unknowns separately. Missing checks are not_run, never passed. Stay within permissions and repository/domain boundaries. Do not claim competence from fluent prose, task completion, PR count, or a green test alone.
