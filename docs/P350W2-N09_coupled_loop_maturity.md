# P350-W2-N09 — Cross-solver conservation contract and coupled-loop certificates

**Node**: P350W2-N09 (maturity of the coupled-loop / conservation mechanism; TensorPlatform, domain-neutral)
**Verdict artefact**: `artifacts/p350w2_n09/coupled_loop_maturity.json`
**Figures**: `artifacts/p350w2_n09/fig1_residual_history.png`, `fig2_refinement_order.png`, `fig3_contraction_and_divergence.png`, `fig4_sensitivity_consistency.png`
**Code**: `tools/coupled_loop.py`, `tools/coupling/sensitivity.py`, `tools/coupling/loop_contract.py`, `tools/coupling/maturity_check.py`, `tools/coupling/maturity_figures.py`, `tools/coupling/maturity_report.py`
**Contracts**: `contracts/conservation_law.schema.json`, `contracts/coupled_loop.schema.json` (+ `coupled_loop.example.v1.json`, `coupled_loop.example.aitken.v1.json`)
**Tests**: `tests/test_coupling_sensitivity.py`, `tests/test_coupled_loop_contract.py`, `tests/test_coupling_maturity.py` (57 test functions)

---

## 1. Problem statement

Two solvers that exchange a quantity across a shared surface are coupled by *two*
independent statements, and the platform could certify only the first of them:

1. *the exchange conserves and converges* — the numerically checkable part:
   interface residual `||F_A - F_B|| / ||F_A||`, accuracy against a closed-form
   flux, and the observed refinement order of the transfer operator;
2. *the coupling is designed consistently* — the part nobody checked: a partitioned
   design ships its own account of how each discipline responds to the exchanged
   variable (an interface Jacobian), and nothing forced that account to be the
   derivative of what the discipline actually does. The fixed point survives a
   wrong Jacobian; every system derivative built on it does not.

This node closes (2) with a machine gate and folds (1), the loop certificate and a
contract layer declaring the coupling graph into one acceptance artefact. No
physics enters the code: a discipline is a black box `(state, parameters) -> own
block`, and the exchanged quantity and the parameter are opaque labels carried by
the contract.

## 2. Method

**Two-discipline fixed point.** `x = f(x; p)` with blocks `f_i`, swept Jacobi
(stale) or Gauss-Seidel (fresh). Every iteration records the relative residual
(for the certificate) and the absolute-residual growth (for the contraction
estimate); `rho_hat > rho_max` for `patience` consecutive iterations declares
`DIVERGED`, which is never confused with the iteration cap. Aitken (Irons-Tuck)
relaxation is a switch on the same loop, not a different loop.

**Conservation / order gates.** A manufactured two-subdomain interface fixture with
a closed-form interface flux (conductivity jump; value and normal flux continuous)
gives each side its own discrete flux, so two-sided consistency *and* error against
the analytic flux are both measurable. The transfer operators integrate their
reconstruction exactly over the destination overlaps, so the zeroth moment is
preserved to round-off and the residual decays at the operator's design order.

**Sensitivity-consistency gate (the new mechanism).** For a scalar parameter `p`
and a scalar functional `F` of the coupled state:

* *composed*: per-discipline partials taken with the coupling variables frozen at
  the converged state, `K_ij = d f_i / d x_j`, `g_i = d f_i / d p` (measured by
  central differences, or taken from the discipline's **declared** Jacobian when
  the design ships one), chained through the coupling response
  `dF/dp = dF/dp|_x + (dF/dx) . (I - K)^-1 g`;
* *re-resolved*: ground truth from re-converging the loop at `p ± h` and
  differencing `F`.

The gate fails when the two disagree by more than 2 %, and fails closed on a
non-converged loop or on a coupling response matrix with condition number above
1e8 (where a double-precision solve can no longer support a percent-level verdict).
Because the fault injection scales only the *declared* Jacobian and never the
physics, a broken design moves the composed path while leaving the ground truth
untouched — which is what makes the negative control meaningful.

**Contracts.** `coupled-loop.v1` declares the disciplines and their state sizes,
the coupling graph (each variable with a producer and a consumer), the sweep and
relaxation, the convergence gate (`rho_deviation`, `min_iteration_reduction`,
`max_iterations_to_flag_divergence`) and the sensitivity tolerance. Validation is
fail-closed and adds two semantic checks the JSON schema cannot express: every
block must both produce and consume a coupling variable (a fixed point needs
feedback), and no variable may name an undeclared discipline.

## 3. Results

### 3.1 Criterion-by-criterion verdict

| criterion | statement | passed |
|---|---|---|
| a | interface conservation residual vs analytic flux | yes |
| b | observed convergence order of the interface operators | yes |
| c | measured contraction rate vs spectral radius (rho = 0.5) | yes |
| d | divergent system (rho = 1.2) flagged early | yes |
| e | Aitken relaxation saving on the same system | yes |
| f | sensitivity consistency: composed vs re-resolved derivative | yes |
| g | test coverage and platform neutrality | yes |

Aggregate: **7/7 criteria pass**; failed = none.

### 3.2 Numbers next to their thresholds

| quantity | measured | acceptance | within |
|---|---|---|---|
| two-sided interface residual (|F_A-F_B|/|F_A|) | 4.400e-07 | <= 1e-4 | yes |
| interface flux vs closed form (side A) | 6.740e-05 | <= 1e-4 | yes |
| interface flux vs closed form (side B) | 6.705e-05 | <= 1e-4 | yes |
| linear-reconstruction observed order | 2.118 | >= 0.95 | yes |
| quadratic-projection observed order | 3.217 | >= 1.95 | yes |
| contraction estimate vs spectral radius (rho = 0.5) | 1.998e-15 | <= 5 % | yes |
| Aitken iteration saving (rho = 0.85) | 76.4 % | >= 40 % | yes |
| composed vs re-resolved dF/dp | 8.804e-07 | <= 2 % | yes |
| composed vs analytic dF/dp | 2.049e-10 | <= 2 % | yes |

The interface fixture runs at 192x192 cells per side;
the negative control (jittered flux, amplitude 0.3) produces a residual of
1.039 and therefore fails the same 1e-4 gate.

Observed refinement orders:

| operator | observed order |
|---|---|
| donor_cell | 1.041 |
| linear_reconstruction | 2.118 |
| quadratic_projection | 3.217 |
| mesh-independent noise (control) | -0.010 |

### 3.3 Analytic anchor, divergence and relaxation

* Linear two-discipline anchor `x = A x + b0 + b1 p` with `A = [[0, rho], [rho, 0]]`:
  measured `rho_hat = 0.500000` against the analytic spectral radius
  `0.500000`, i.e. relative deviation 1.998e-15 (gate 5 %);
  the converged state matches the direct solve to 4.548e-13.
* Divergent anchor (`rho = 1.2`): status `DIVERGED` after **3 iterations**
  (cap 10); reason recorded in the artefact: *contraction estimate 1.2000 > 1.0000 for 2 consecutive iterations (iteration 3)*.
* Aitken relaxation on `rho = 0.85`: 161 -> 38 iterations, a
  **76.4 %** saving (gate 40 %), same fixed point to 3.755e-12.
* Over `rho in {0.20, 0.35, 0.50, 0.70, 0.85, 0.95}` the measured contraction estimate deviates from the analytic
  value by at most 1.299e-05; for `rho > 1` divergence is declared at
  iteration 3.

### 3.4 Sensitivity consistency

| case | composed dF/dp | relative error vs re-resolved | verdict |
|---|---|---|---|
| clean design | 3.2500 | 8.804e-07 | passes (required) |
| coupling Jacobian x1.5 | NO | 0.235 | fails (required) |
| parameter derivative x1.25 | NO | 0.128 | fails (required) |

Analytic `dF/dp = 3.2500`; composed `3.250000`; re-resolved
`3.249997`. The nonlinear two-discipline fixture (no closed form) gives composed
vs re-resolved 2.981e-07, so the mechanism is not restricted to linear
systems. Condition number of `(I - K)` for the clean design: 3.00.

### 3.5 Figures

**Figure 1 — measured relative residual of the same two-block coupling swept plain and with Aitken relaxation, against the analytic rho^k decay of the plain sweep.** — plain sweep 161 iterations, Aitken
38, measured `rho_hat = 0.8500` vs analytic
0.8500.

**Figure 2 — log-log interface-transfer residual vs destination cell width for donor-cell / linear-reconstruction / quadratic-projection operators, plus a mesh-independent noise control that cannot show an order.** — orders 1.031 /
2.087 /
3.180, noise control
8.737e-04.

**Figure 3 — left: measured contraction estimate vs the analytic spectral radius (identity line); right: iteration at which DIVERGED is declared for rho > 1, always far inside the ten-iteration acceptance cap.**

**Figure 4 — the sensitivity consistency gate: composed (frozen per-discipline partials) and re-resolved derivatives against the analytic dF/dp for the clean design and for two deliberately broken designs; dashed lines mark the ±2 % band.** — clean design error 8.804e-07, corrupted Jacobian
0.235, corrupted parameter derivative
0.128.

## 4. Error judgement (explicit)

| quantity | measured | reference | error | below 3 %? |
|---|---|---|---|---|
| two-sided interface residual | 4.400e-07 | 0 (self-consistency) | 4.400e-07 | yes |
| interface flux, side A | 6.740e-05 | closed form | 6.740e-05 | yes |
| interface flux, side B | 6.705e-05 | closed form | 6.705e-05 | yes |
| contraction rate | 0.500000 | spectral radius 0.500000 | 1.998e-15 | yes |
| system derivative, composed | 3.250000 | analytic 3.2500 | 2.049e-10 | yes |
| system derivative, composed vs re-resolved | 3.250000 | 3.249997 | 8.804e-07 | yes |
| iteration count under Aitken | 38 | plain 161 | saving 76.4 % | n/a (60 %-level saving vs a 40 % gate) |

Every accuracy quantity sits far inside the 3 % band. The tightest is the interface
flux against its closed form at 6.740e-05, limited by the
second-order reconstruction of the fixture rather than by the gate.

## 5. Caveats and scope

* The conservation fixture is **one-dimensional in the normal direction** (planar
  interface with a transverse mode); the transfer operators are 1-D segment
  operators, so the order certificate says nothing about curved or 2-D interface
  geometry. That path is the existing `tools/coupling/remap.py` /
  `multiphysics.py` stack, not this node.
* The sensitivity gate requires a **converged** loop and refuses to quote a
  derivative otherwise (fail-closed). A loop that only converges under relaxation
  must be gated on its relaxed certificate; the composed derivative is then
  evaluated at the relaxed fixed point, unchanged to round-off.
* Fault model: the gate detects wrong derivative *bookkeeping* — a declared
  Jacobian that does not match the discipline's response. It cannot detect a
  discipline whose physics is wrong in a way that is wrong consistently in both
  paths (that is a verification question for the solver repository, not a coupling
  question).
* The 1e8 condition-number ceiling on `(I - K)` is a documented design choice
  (`MAX_RESOLVENT_CONDITION` in `tools/coupling/sensitivity.py`).
* Divergence is declared at iteration 3 for the fixtures
  used, because the estimate needs `patience + 1` iterations before it can act. A
  contraction that only becomes visible later would be flagged later — still far
  below a 500-iteration cap, but the "10 iterations" acceptance is a property of
  these fixtures, not a universal bound.
* The 2 % sensitivity tolerance is a policy number supplied by the contract, not a
  mathematical property of the system; the gate's own numerical noise floor is
  ~1e-6 relative (measured on the clean fixtures).

## 6. Reproduction

```bash
python -m tools.coupling.maturity_check --out artifacts/p350w2_n09/coupled_loop_maturity.json
python -m tools.coupling.maturity_figures
python -m tools.coupling.maturity_report
python -m pytest tests/test_coupling_sensitivity.py tests/test_coupled_loop_contract.py tests/test_coupling_maturity.py -q
```

The audit is deterministic (two runs give byte-identical summaries, asserted by
`test_audit_is_deterministic`) and the CLI returns a non-zero exit code the moment
any criterion regresses (asserted by
`test_cli_exit_code_is_nonzero_when_a_criterion_regresses`).
