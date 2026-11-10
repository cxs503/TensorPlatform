# Hull-form three-objective Pareto study (`domains/hull_form_pareto`)

Domain package for warship-ship design exploration on the TensorPlatform tree:
**resistance `R_T` (min) — initial metacentric height `GM` (max) — cargo hold
volume `V_cargo` (max)** searched with NSGA-II over a 13-parameter parametric
hull family.

The *generic* NSGA-II / metrics / constraint machinery is **not** reimplemented
here: it is imported from `optimizer/multiobjective` (domain-free shared code).
Everything ship-specific lives in this directory, so `backend/`, `tools/` and
`tests/` stay free of domain semantics.

## Layout

| file | content |
|---|---|
| `config/hull_design_space.json` | 13 design variables + bounds + constants + acceptance thresholds |
| `hull.py` | parametric geometry, hydrostatics (KB/BM/GM/roll period), cargo-volume integral, Holtrop–Mennen resistance call |
| `objectives.py` | 3-objective vector, constraint set (`g ≤ 0` feasible), evaluation record |
| `pareto.py` | NSGA-II driver, external Pareto archive, quality metrics, ZDT1 verification, negative controls, artefacts |
| `figures.py` | matplotlib figures (Pareto front, HV convergence, resistance breakdown, section profiles, ZDT1, negative control) |
| `run_pareto.py` | CLI: full run → `artifacts/hull_form_pareto/*.json`, `docs/figures/hull_form_pareto/*.png`, `docs/hull_form_pareto/PARETO_REPORT.md` |
| `tests/test_hull_form_pareto.py` | physics unit tests + the six machine acceptance criteria |

## Run

```bash
python -m domains.hull_form_pareto.run_pareto           # full study (~3 min)
python -m domains.hull_form_pareto.run_pareto --fast    # smoke run
python -m pytest domains/hull_form_pareto/tests -q
```

## Physical model (short)

* **Geometry**: `B = L/(L/B)`, `T = B/(B/T)`, `D = (D/T)T`, `∇ = Cb L B T`, `Cp = Cb/Cm`.
  Longitudinal sectional-area distribution is a trapezoid whose integral equals
  `Cp` exactly, so the section model reproduces `∇` (verified in tests).
* **Resistance**: platform implementation of Holtrop–Mennen 1982/1984
  (`backend/api/domain/holtrop_mennen.py`), i.e. `R_T = R_F(1+k1) + R_W + R_B +
  R_TR + R_A`, with the hull's own `Cm`, `Cwp`, `lcb`, bulb and transom areas.
* **Stability**: `KB` from a volume-consistent waterplane-growth model
  (`KB → T/2` for a box hull), `BM = I_T/∇`, `KG = kg_ratio·D`, `GM = KB + BM − KG`,
  natural roll period `T_roll = 2π k_x/√(g GM)`.
* **Capacity**: longitudinal integral of the hold section area over the hold
  window, `V_cargo = η Cm (B−2t)(D−DB) L ∫ s(x) dx`.
* **Constraints**: `GM ≥ 0.15 m` (IS Code 2008), `T_roll ≥ 10 s`, plus the
  Holtrop–Mennen regression domain (`Cb ≤ 0.85`, `0.55 ≤ Cp ≤ 0.87`, `Fn ≤ 0.45`,
  `L/B`, `B/T`).

## Evidence grades and known limitations

* `R_T` uses a **REAL** formula, but no external model-test baseline is available
  in the repository (the shipped benchmark dataset is rejected as a baseline by
  `tools/verify/resistance_benchmark_audit.py`), so the **absolute** accuracy is
  `WITHHELD`; only relative trade-offs are used, and internal identities
  (component sum, ITTC-57 `Cf`, wetted surface vs Denny–Mumford) are tested.
* `KB`/`BM` are preliminary-design approximations and `KG` is a design variable,
  so `GM` is a *ranking* quantity, not a statutory stability calculation.
* The cargo hold is one longitudinal window; bulkheads, hatch openings and
  side-ballast voids are lumped into `η_stow = 0.92`.
* Mutation probability is annealed `0.10 → 0.02` (declared deviation): with a
  constant `p_m = 0.1` the archive hypervolume was still rising by 1.5 % over the
  last 20 generations of the 200-generation budget.

Numbers, figures and the acceptance table are regenerated from the artefacts by
the CLI; see `docs/hull_form_pareto/PARETO_REPORT.md`.