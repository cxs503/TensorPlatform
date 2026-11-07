---
name: experimental-design
description: Design controlled, reproducible comparisons that distinguish alternatives and support a predeclared decision.
---

# Experimental Design

## Use when
Choosing between approaches, testing a hypothesis, measuring a change, or determining whether an intervention caused an effect.

## Boundary
This is a domain-neutral design method. The owning domain must supply valid domain metrics, safety constraints, and acceptance thresholds.

## Procedure
1. State the decision and competing alternatives.
2. Define hypothesis, outcome metrics, unit of analysis, and target workload/population.
3. Establish baseline and suitable control/comparison.
4. Predeclare thresholds, tolerances, exclusions, and stopping rules before inspecting results where practical.
5. Identify confounders, nuisance variables, carry-over effects, selection bias, and measurement error.
6. Specify controlled and changed variables. If interactions matter, choose a design that can estimate them.
7. Choose repetitions/sample size appropriate to variability and decision risk; explain rationale if uncertain.
8. Define randomization, ordering, blinding, and environment controls where relevant.
9. Record versions, configuration, inputs, seeds, commands, resource limits, and raw-data locations.
10. Define analysis in advance: aggregation, uncertainty, outliers, missing data, and comparison method.
11. Pilot instrumentation and safety; do not tune thresholds after seeing desired outcomes.
12. Report results, deviations, uncertainty, limitations, and the decision supported. Distinguish correlation from causation.

## Required artifact: Experiment Plan
Decision; alternatives; hypothesis; metrics; baseline/control; predeclared thresholds and stopping rule; variables/confounders; repetition rationale; environment; analysis; safety/resource limits; interpretation limits.

## Practice
Compare two caching strategies on a fixed request trace. Ensure hit rate cannot hide worse tail latency or stale-data behavior. Specify workload, warm-up, repetitions, metrics, correctness checks, and decision rule.
Pass evidence: controlled workload/environment, latency distribution, freshness checks, predeclared thresholds, and explicit limits on inference.

## Quality note
Each claim must be traceable to source, observation, command output, or artifact. Label facts, inferences, assumptions, and unknowns separately. Missing checks are not_run, never passed. Stay within permissions and repository/domain boundaries. Do not claim competence from fluent prose, task completion, PR count, or a green test alone.
