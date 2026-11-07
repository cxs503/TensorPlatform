# First Cohort Practice Track

| ID | Skill | Exercise | Expected evidence |
|---|---|---|---|
| P01 | problem-framing | Convert “make the service better” into a testable change contract | Goal, baseline, success criteria, non-goals |
| P02 | problem-decomposition | Plan an API feature across code, tests, and docs | Dependency graph, bounded packages, owners |
| P03 | codebase-investigation | Investigate a queued operation that never completes | Execution map, tests, facts vs unknowns |
| P04 | hypothesis-generation | Explain doubled worker latency from logs and system sketch | Competing hypotheses and discriminating observations |
| P05 | experimental-design | Compare two caching strategies on a fixed workload | Controls, metrics, repetitions, decision rule |
| P06 | systematic-debugging | Diagnose a seeded pagination boundary defect | Reproducer, root cause, regression test |
| P07 | adversarial-review | Review upload endpoint with missing negative tests | Severity-ranked evidence-backed findings |
| P08 | knowledge-distillation | Convert retry-induced duplicate processing incident into a runbook | Scoped lesson, provenance, regression guard |

## Cohort protocol
- Keep a baseline run without the skill.
- Compare with a skill-assisted run on a comparable task.
- Test transfer on a held-out task.
- Where feasible, use an evaluator blind to condition.
- Preserve raw outputs and scoring rationale.
- Record cost, wall time, retries, and tool calls.
- Do not reveal seeded answers before evaluation.

## Promotion
Keep skills experimental until multiple agents complete multiple distinct cases and evidence shows repeatable benefit without unacceptable cost, safety, or latency regressions. Publish sample sizes, failures, and limitations. Revise or retire skills that do not help.
