# Resident workspace reliability: upstream comparison and extraction

Baseline: TensorPlatform `origin/main` at `fce5cac4270c` (fetched 2026-10-25). Source: locally deployed development line, including d0d44ae, aca5a45, 2675afd, 518a978 and related workspace repairs. Previous extraction 2f7d2fe is already upstream through PR #55; it is not submitted again.

## Findings

The upstream now includes the resident community and Tensor Soul phase-one runtime, stable identity migration and CJK recall work. Those paths, native agents, operations tooling, APIs, migrations and frontend are retained unchanged. Local work adds further workspace, observation, recovery, coordination, execution and research capabilities; they are not all present upstream.

## This focused submission

- Atomic, SHA-guarded source writes and unique literal replacement, preserving file mode and rejecting stale cooperative writers.
- Staged-only changes included when committing a checked candidate.
- Default sync divergence and conflict diagnostics with retained WIP backup.
- Explicit candidate-protected sync, recovery references, staged/worktree separation, preflight in disposable worktrees, and rollback on conflicts/timeouts.
- Saved check-log pagination and missing support import without overwriting existing edits; named optional research-check dependency imports committed blob bytes.
- Updated model-visible tool contracts and a generic industry-construction research agenda.

The original upstream source boundaries, snapshot quotas, executor paths, exact-source review/integration gates and optional deployment are preserved. No client credentials, private state, customer records, business runtime artifacts, host sudo access or service restarts are included.

## Validation

Existing factory development environment, Python 3.11. Targeted workspace/source regressions: **60 passed**, six dependency deprecation warnings, 4.25 seconds. Full upstream `tests/factory`: **152 passed**, six warnings, 14.51 seconds. Tests create isolated temporary PostgreSQL and real Git histories. No Docker execution, production migration, browser acceptance or autonomous model trial was performed for this extraction. No frontend changes require a frontend build in this patch.

Reproduce with `python -m pip install -r requirements-factory-dev.txt` then `python -m pytest tests/factory -q`. The environment was reused, not freshly provisioned for this run. Existing frozen-check test expectations and upstream Soul tests passed; no pre-existing assertion was weakened.

## Remaining extraction work

Host execution, waiting/recovery lifecycle, oversized-observation archives, coordination/PDCA, longitudinal sampling and the reference-evidence auditor remain local pending portable integration. These require separate API/worker/migration and deployment review; this patch must not be presented as their upstream delivery. Precise historical-commit review also remains outside this submission; current upstream review still binds to the author's current clean candidate.

The new research method is a protocol proposal, not evidence of cross-industry autonomous capability or publication readiness. Test success proves selected software contracts rather than organizational effectiveness.
