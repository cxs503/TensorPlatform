# Agent Breakthrough Skills — v1

**Status:** experimental, opt-in practice library  
**Owner:** TensorPlatform  
**Scope:** domain-neutral reasoning and delivery practices for agents across the Tensor ecosystem.

## Purpose
These are repeatable work methods, not a promise that installing a prompt creates competence. Each skill has triggers, a procedure, required artifacts, stop conditions, and a practice exercise. Evaluate on held-out tasks before claiming effectiveness.

Specialist theories and acceptance rules stay in domain-owned packages. This library excludes physics, solver algorithms, video grammar, game mechanics, humanities methodology, and other specialist rules.

## First cohort
1. [problem-framing](skills/problem-framing/SKILL.md) — Turn an ambiguous request into a bounded, testable problem contract.
2. [problem-decomposition](skills/problem-decomposition/SKILL.md) — Break complex goals into bounded work packages with dependencies, interfaces, owners, and integration checks.
3. [codebase-investigation](skills/codebase-investigation/SKILL.md) — Establish actual repository behavior, architecture, tests, and change history before recommending edits.
4. [hypothesis-generation](skills/hypothesis-generation/SKILL.md) — Generate competing, falsifiable explanations and identify evidence that distinguishes them.
5. [experimental-design](skills/experimental-design/SKILL.md) — Design controlled, reproducible comparisons that distinguish alternatives and support a predeclared decision.
6. [systematic-debugging](skills/systematic-debugging/SKILL.md) — Reproduce, localize, explain, and minimally fix a defect with regression coverage and traceable evidence.
7. [adversarial-review](skills/adversarial-review/SKILL.md) — Independently challenge a proposal, implementation, or evidence bundle to find material defects and unsupported claims.
8. [knowledge-distillation](skills/knowledge-distillation/SKILL.md) — Convert verified task or incident experience into reusable knowledge with provenance, scope, and regression protection.

## Practice
1. Run a baseline task without the skill.
2. Run a comparable skill-assisted task.
3. Run a held-out transfer task after practice.
4. Score observable artifacts with [EVALUATION_RUBRIC.md](EVALUATION_RUBRIC.md).
5. Preserve raw outputs, model, skill version, task, tool access, budget, evaluator, time, retries, and cost.
6. Repeat across multiple tasks; one exercise does not establish mastery.

See [PRACTICE_TRACK.md](PRACTICE_TRACK.md) for the first eight exercises.

## Package contract
Every skill has stable YAML frontmatter, triggers/non-goals, procedure, output artifact, escalation conditions, and practice acceptance criteria. Skills are advisory procedures, not security controls. Permissions, budgets, and approvals must be enforced in platform code.

## Adoption status and limitations
This PR adds a versioned content library only. It does **not** change worker prompt assembly, skill discovery, assignment, telemetry, or enforcement. The agent directory currently records free-form skill descriptions, but that is not proof of runtime skill loading. Runtime integration must be a separate tested increment against the actual worker and mission lifecycle.

Do not assign all skills to every agent. Assign a small relevant set by role and task; measure context cost and outcomes. Promote a skill from experimental only after repeated held-out evaluation shows repeatable benefit without unacceptable cost, latency, or safety regressions.
