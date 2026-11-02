# Numerical V&V / GCI verification layer — m1646

**Scope of this mission:** close the platform's *verification debt* on the one
axis it had no tooling for at all: **discretization error**. Every solver
number the platform publishes (resistance, `C_D`, `GZ`, thrust …) implicitly
claims grid independence, but no code in the repository could estimate, bound
or falsify that claim.

## 1. Audit finding (the gap this closes)

| evidence source | query | result |
|---|---|---|
| repository source | `grep -rilE "GCI\|grid convergence index\|richardson" src/ tools/ scripts/ tests/` | **0 files** |
| capability cards | `docs/capability_registry/cards/*.json` (5 cards) | 0/5 mention `gci`, `grid_convergence`, `discretization`, `richardson` or `uncertainty` |
| platform capability audit | `audit_capabilities` (21 capabilities, 18 graded REAL) | 0 carry a discretization-uncertainty band |
| proposal pool | `p9/w1/coverage-1573.json`, domain `numerical V&V / GCI [NEW]` | **0** of 102 campaign nodes |

So the platform had *validation* evidence (comparison against reference
values) but no *verification* evidence (does the discretization converge, at
what order, and how large is the numerical error bar?). ASME V&V 20-2009 and
Celik et al. (2008) require both before a computed value may be reported as a
predictive result. This mission supplies the missing half.

## 2. What was built

| artefact | purpose |
|---|---|
| `src/verification/gci.py` | dependency-free (stdlib-only) Richardson extrapolation + GCI estimator: observed order of accuracy, extrapolated value, relative/extrapolated error, GCI band, safety factors, convergence-regime classification, refusal path outside the asymptotic range |
| `src/verification/__init__.py` | import surface |
| `tools/gci_report.py` | CLI: feed a JSON `{h, phi}` study (fractions like `"1/64"` accepted, optional `sort`), prints the band and **exits 1 when the grids are not in the asymptotic range**, so CI can gate unverified numbers |
| `examples/verification/gci_mms_study.py` | three manufactured-solution (MMS) cases with analytic QoI: 1D advection–diffusion (2nd-order central, 1st-order upwind) and 2D Poisson (5-point) |
| `examples/verification/make_gci_artifacts.py` | regenerates JSON + 2 figures + the result tables, all numbers programmatically injected |
| `tests/test_gci_verification.py` | 24 tests: synthetic-exact-model recovery, textbook-limit equivalence, oscillatory/divergent/zero-change regimes, input contract, MMS order recovery + band bracketing, CLI gate |
| `artifacts/verification/gci_mms_study.json`, `fig_gci_order.png`, `fig_gci_band.png` | evidence artefacts |
| `docs/verification/gci_results_tables.md` | auto-generated tables quoted below |

### Method (cited, nothing invented)

Three grids `h1 < h2 < h3` with solutions `phi1..phi3`:
`r21 = h2/h1`, `r32 = h3/h2`, `eps21 = phi2-phi1`, `eps32 = phi3-phi2`,
`R = eps21/eps32` (Roache 1994; Celik, Ghia, Roache et al. 2008,
J. Fluids Eng. 130:078001; ASME V&V 20-2009). Monotone convergence is
`0 < R < 1`, oscillatory `R < 0`, divergent `R > 1`.

The asymptotic model `phi_i = phi_ext + a h_i^p` gives

```
R = (r21^p - 1) / ( r21^p (r32^p - 1) )                                   (Eq. A)
```

which reduces to `p = ln(1/R)/ln(r)` only when `r21 == r32 == r`. **Platform
grids are refined anisotropically**, and the widely copied one-line formula
silently returns a wrong order in that case; Eq. A is monotone in `p` and is
solved here by bisection (200 steps, no divergence-prone fixed-point
iteration). Then

```
phi_ext = phi1 + (phi1 - phi2)/(r21^p - 1)
GCI_fine = Fs * |(phi1 - phi2)/phi1| / (r21^p - 1),   Fs = 1.25 (three grids)
```

Outside the asymptotic range no extrapolation is emitted; the tool returns the
conservative bound `Fs*(max(phi) - min(phi))` and labels the result
**UNVERIFIED** rather than inventing a number.

## 3. Results

See `docs/verification/gci_results_tables.md` (auto-generated from
`artifacts/verification/gci_mms_study.json`; quoted verbatim):

| case | p formal | p observed | rel. error of p | phi_ext rel. error vs exact | GCI band brackets exact |
|---|---|---|---|---|---|
| `advdiff-central` | 2 | 2.0014 | 0.07 % | 1.401e-07 | yes |
| `advdiff-upwind` | 1 | 0.8708 | 12.92 % | 8.332e-04 | yes |
| `poisson2d-fv5` | 2 | 2.0026 | 0.13 % | 3.868e-07 | yes |

The two second-order schemes are recovered to 0.1 %, and Richardson
extrapolation improves the QoI by 3 orders of magnitude (1.3e-04 → 1.4e-07 for
`advdiff-central`). For the 1st-order upwind case the coarse triple
20/40/80 under-predicts the order (0.871); the ladder in Table 3 shows this is
genuine pre-asymptotic behaviour, not an estimator bug: `p = 0.871 → 0.938 →
0.970` for 20/40/80 → 40/80/160 → 80/160/320, with the extrapolation error
falling 8.3e-04 → 4.4e-05. Reporting the single coarse-triple number without
the ladder would have been misleading, so both are published.

## 4. Error verdict (explicit)

* **Order recovery vs formal order** — 2nd-order cases: 0.07 % and 0.13 %
  deviation. **< 3 %: yes.** 1st-order upwind, coarse triple: 12.9 %
  deviation, **< 3 %: no** — diagnosed as pre-asymptotic (Table 3 converges
  monotonically to the formal order, 2.98 % deviation on 80/160/320).
* **Extrapolated QoI vs analytic exact value** — 1.4e-07, 3.9e-07
  (2nd-order cases) and 8.3e-04 (upwind). **< 3 %: yes for all three.**
* **Uncertainty band** — the GCI band brackets the exact value in 3/3 cases,
  which is the criterion that matters for the estimator's honesty.

No platform capability number was re-scored by this mission: the toolkit is
verified, but applying it to a real capability requires three-grid runs that
this mission did not have (see §6).

## 5. How to use it (closing debt on a real case)

```bash
cat > study.json <<'EOF'
{"h": ["1/64", "1/32", "1/16"], "phi": [1.0002, 1.0008, 1.0032],
 "quantity": "C_D_mean, DTMB5415, Fr=0.28"}
EOF
python3 tools/gci_report.py study.json --out gci.json   # exit 1 => UNVERIFIED
```

Rule adopted here: a report may quote a platform number as grid-independent
only if `gci_report.py` exits 0 and the reference/experimental value lies
inside the reported band.

## 6. Limitations / not done

* **No real capability was verified end-to-end.** The platform's stored results
  do not include three systematically refined grids for any capability (searched
  `artifacts/`, `runs/`, capability cards); the DSM/MMS cases verify the
  *estimator*, not the platform's solvers. That is the next mission's job and it
  needs actual re-runs (deferred here: `load15 ≈ 121` on a 128-core host, so this
  mission deliberately stayed compute-light).
* The estimator assumes a single dominant error term with constant sign and
  `p`; mixed error terms or non-smooth solutions can produce a meaningless `R`
  — hence the explicit refusal path.
* `Fs = 1.25` (three grids) is the ASME V&V 20 recommendation, not a
  calibrated constant; it is exposed as a parameter and tested (`Fs = 3.0`
  two-grid path).
* MMS here uses Dirichlet boundaries and a smooth `sin(pi x)` field; boundary
  condition implementation error would show up as a distorted order (that is
  the point), but the cases are 1D/2D and small — they verify the estimator and
  the discretisation, not a ship-scale solver.