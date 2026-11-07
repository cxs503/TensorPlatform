---
name: knowledge-distillation
description: Convert verified task or incident experience into reusable knowledge with provenance, scope, and regression protection.
---

# Knowledge Distillation

## Use when
A task or incident is complete, a repeated failure is understood, or a reusable method, constraint, or tool procedure has emerged.

## Procedure
1. Gather task contract, decisions, source changes, raw evidence, review findings, and outcome.
2. Separate durable facts from context-specific observations, hypotheses, and opinions.
3. Define the exact applicability boundary and where the lesson may not apply.
4. Express the reusable rule or procedure in the smallest actionable form.
5. Preserve provenance: task/run, repository and commit, artifact references, author, reviewer, and date.
6. Record failed approaches only when conditions and evidence are clear; explain why they failed.
7. Convert recurring lessons into a checklist, test, tool, template, or documentation change where appropriate.
8. Add a regression guard when violation can be detected mechanically.
9. Remove secrets, credentials, personal data, and unnecessary sensitive context.
10. Submit changes for review; do not silently rewrite shared policy or evaluation criteria.
11. Revisit contradicted lessons and mark superseded guidance rather than erasing history.

## Required artifact: Reusable Lesson
Title/rule; context and applicability; evidence/provenance; mechanism; recommended procedure; counterexamples/limitations; regression guard; review status; last-verified date.

## Practice
Turn an incident where retries caused duplicate processing into a runbook using the supplied timeline, logs, code change, and regression test.
Pass evidence: distinguish retry from idempotency; link source evidence; state scope; propose a testable guard without overgeneralizing.

## Quality note
Each claim must be traceable to source, observation, command output, or artifact. Label facts, inferences, assumptions, and unknowns separately. Missing checks are not_run, never passed. Stay within permissions and repository/domain boundaries. Do not claim competence from fluent prose, task completion, PR count, or a green test alone.
