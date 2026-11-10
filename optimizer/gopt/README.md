# G-OPT — optimization library (TensorPlatform)

Domain-free optimization kernels plus the solver → gradient → optimizer protocol
used by the numerical-method repos (LBM / FVM / FEM / BEM / SPH / DEM).

```
optimizer/gopt/
├── cmaes.py              # CMA-ES: ask/tell, IPOP/BIPOP restarts, noise resampling,
│                         # eigen-decomposition sampling, Hansen termination set
├── slsqp.py              # own SLSQP: dense convex QP (penalty-Newton + exact
│                         # active-set polish with KKT multipliers), L1 merit line
│                         # search, Powell-modified BFGS
├── interfaces.py         # Objective / ConstraintSet / AdjointSolver protocols,
│                         # finite-difference gradients, adjoint_consistency_check
├── adjoint_reference.py  # executable reference adjoint: A(x)u=b, J=0.5u'Qu+c'x
├── benchmarks.py         # reference problems with closed-form optima
├── validation/           # benchmark sweep driver + report generator
└── cases/                # worked cases (domain content, injected through the protocols)
    └── slender_body_lb/  # fixed-displacement L/D optimisation + literature check
```

## Why two optimizers

| situation | use |
|---|---|
| non-convex, noisy, non-smooth, no gradient available | `CMAESOptimizer` (`minimize_cmaes`) |
| smooth objective **with** gradient (analytic / finite difference / adjoint) | `SLSQPOptimizer` (`minimize_slsqp`) |

`SLSQPOptimizer` raises `ValueError` when `objective.gradient(x)` returns `None`
instead of silently degrading to a derivative-free heuristic.

## Plugging in a solver (adjoint protocol)

```python
from optimizer.gopt import InjectedAdjointObjective, SLSQPOptimizer

class MySolver:                      # implements optimizer.gopt.interfaces.AdjointSolver
    dim = 12
    def primal(self, x):             # one forward solve
        ...                          # -> PrimalResult(value=J, state=...)
    def adjoint(self, x, state):     # one adjoint (transposed) solve
        ...                          # -> dJ/dx  (cost independent of dim)

res = SLSQPOptimizer(bounds=b).optimize(InjectedAdjointObjective(MySolver()), x0)
```

Before trusting the gradient, run the audit (Taylor first-order test + 5-point
finite-difference comparison + negative control):

```python
from optimizer.gopt import adjoint_consistency_check
report = adjoint_consistency_check(MySolver(), x0)   # report["passes"], report["evidence_grade"]
```

## Verification

```bash
pytest tests/test_gopt_cmaes.py tests/test_gopt_slsqp.py \
       tests/test_gopt_adjoint.py tests/test_gopt_case_slender_body.py -q
python -m optimizer.gopt.validation.run_benchmarks
python -m optimizer.gopt.cases.slender_body_lb.run_case
python -m optimizer.gopt.validation.build_report
```

Report: `docs/gopt/GOPT_MATURATION_REPORT.md` (numbers injected from
`artifacts/gopt/*.json`, never hand-copied).

## Boundary rule

The kernels must stay domain-free: no field names, criteria or case data in
`cmaes.py` / `slsqp.py` / `interfaces.py` / `benchmarks.py` / the core tests.
Physics enters only through `cases/` (injected objective) or an external solver
implementing the protocol.