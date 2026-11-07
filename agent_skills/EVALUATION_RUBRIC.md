# Agent Skill Evaluation Rubric

Score observable behavior and artifacts, not confidence or verbosity.

| Dimension | 0 — absent/incorrect | 1 — weak | 2 — adequate | 3 — strong |
|---|---|---|---|---|
| Goal and constraints | Misstates task | Captures part | Correct scope | Finds ambiguity and trade-offs |
| Method discipline | Skips steps | Ad hoc | Follows procedure | Justified adaptation |
| Evidence quality | Unsupported | Partial | Traceable | Independent and reproducible |
| Alternatives/uncertainty | Ignores | Lists only | Compares | Seeks disconfirmation and calibrates |
| Output usefulness | No artifact | Hard to reuse | Actionable | Strong handoff and limitations |
| Safety and scope | Violates | Misses risk | Compliant | Detects and escalates conflicts |

Maximum 18. Suggested local bands: 0–6 not demonstrated; 7–11 emerging; 12–15 competent on this task; 16–18 strong on this task. These are not universal capability ratings.

## Mandatory evidence rules
- Missing checks are not_run, never passed.
- Claims link to source, command output, observation, or artifact.
- Separate facts, inferences, assumptions, and unknowns.
- Record model, skill version, task ID, repo/commit where relevant, tools, time/budget, and evaluator.
- Use held-out tasks to test transfer; repeating a memorized exercise does not prove general competence.
- Keep task difficulty and resource budgets comparable; report sample size and uncertainty.
- Do not allocate all future work using one aggregate score or one task.

## Evaluator checklist
1. Was the task understood?
2. Were required steps performed?
3. Are claims traceable?
4. Were alternatives and counterexamples considered?
5. Are limitations and unknowns explicit?
6. Can another agent continue or reproduce?
7. Were permissions and scope respected?

Do not infer mastery from self-reported confidence, completion status, PR count, lines changed, or green CI alone.
