# Wetted-surface verification: Holtrop-Mennen regression vs the legacy axisymmetric fit

**Mission**: m1686 (selfstart tp-impl-8, platform seat)
**Date**: 2026-11-01
**Code under test**: `backend/api/calculations.py` (`/calculations/resistance`),
`backend/services/holtrop_mennen.py` (`wetted_surface`),
`backend/services/ship_hydrostatics.py` (girth integration)
**Harness**: `tools/verif/verify_wetted_surface.py` → `outputs/wetted_surface/`
**Tests**: `tests/test_wetted_surface_holtrop_verification.py` (14 passed)

---

## 1. Problem statement (why this verification was owed)

Mission m1414 (previous selfstart) documented — but did not repair — the fact that
`POST /calculations/resistance` estimates the wetted surface with

```
S_legacy = pi * B * L * (0.5 + 0.25 * Cb)
```

a body-of-revolution (axisymmetric) fit. On a ship hull this is wrong by tens of
percent, and S is the single multiplicative factor of the whole resistance sum.
Meanwhile the platform **already ships** the published Holtrop & Mennen (1982)
regression in `services/holtrop_mennen.wetted_surface`, which the resistance
endpoint did not use. This report closes both halves of that account:

1. verify the merged Holtrop regression against an external published value
   (it had never been checked against a published wetted surface);
2. verify the platform's *geometric* wetted surface (offsets integration) against
   a quasi-analytic reference, so "geometry first" claims have a number;
3. make the endpoint able to use the verified model (`hull_form="ship"`), keep
   the legacy path bit-compatible for existing benchmark rows, and stop it from
   being silent about its own bias.

## 2. Case definition

| Case | Geometry | Source of truth | Role |
|---|---|---|---|
| KVLCC2 tanker | L=320 m, B=58 m, T=20.8 m, Cb=0.8098, Cm=0.998, Cwp=0.8748, ∇=312 622 m³ | S = 27 194 m² (SIMMAN 2008 hull table) | in-domain accuracy of Holtrop |
| Prolate spheroid | L=4.0 m, D=0.5 m (L/D=8, Cb_eq=0.0655) | closed-form spheroid area | shape family the legacy fit came from |
| Wigley parabolic hull | L=10 m, B=1 m, T=0.625 m (Cb=4/9, Cm=Cp=Cwp=2/3) | high-resolution Simpson integral of the exact surface parametrisation (n=640, converged to 7×10⁻⁹ %) | platform geometry integration + out-of-domain Holtrop probe |
| KVLCC2 resistance sweep | 7…17 kn | platform's own full Holtrop-Mennen implementation | cross-method consistency of the simplified endpoint |

Water: ρ = 998 kg/m³, ν = 1.004×10⁻⁶ m²/s (model-experiment conventions used by
the rest of the platform's benchmark chain).

## 3. Method

* **R1 — external anchor.** Evaluate `holtrop_mennen.wetted_surface` with the
  moulded coefficients of KVLCC2 and compare with the published 27 194 m².
  Repeat with the endpoint's coefficient *estimates* (Cm = 0.9 + 0.1·Cb,
  Cwp = (1 + 2·Cp)/3) to bound the cost of not knowing Cm/Cwp.
* **R2 — legacy bias.** Evaluate the legacy fit on (a) the prolate spheroid it
  was fitted to (closed-form reference) and (b) the KVLCC2 hull.
* **R3 — geometry integration.** The Wigley hull has an exact parametrisation
  y(x,z) = (B/2)(1−(2x/L)²)(1−(z/T)²); its wetted surface is
  S = 2·∫∫√(1+y_x²+y_z²) dz dx, integrated with composite Simpson on n×n grids
  up to n=640 (convergence study in the JSON). Compare with
  `compute_hydrostatics(wigley_offsets(...)).wetted_surface_m2`.
  Then evaluate the Holtrop *regression* on the integrated Wigley coefficients —
  an intentionally out-of-domain probe (a mathematical 2-D hull, not a ship).
* **R4 — consequence.** Run `/calculations/resistance` for KVLCC2 at six speeds
  with the Holtrop S and with the legacy S, same friction line, same residual
  fit. Report R_T and C_T.
* **R5 — cross-method.** Compare the simplified endpoint with the platform's
  full `holtrop_mennen.holtrop_resistance` on the same hull and the same S. This
  isolates what the endpoint's empirical residual fit + missing form factor cost,
  without needing an external R_T reference.

All numbers are read from `outputs/wetted_surface/wetted_surface_verification.json`
(generated programmatically; nothing hand-copied).

## 4. Results

### 4.1 Wetted surface vs the published KVLCC2 value

| Model | S [m²] | Error vs 27 194 m² | Verdict (<3 %) |
|---|---|---|---|
| Published (SIMMAN 2008) | 27 194.0 | — | reference |
| Holtrop 1982, caller Cm/Cwp | 26 725.55 | **−1.72 %** | PASS |
| Holtrop 1982, estimated Cm/Cwp | 26 753.91 | **−1.62 %** | PASS |
| Legacy π·B·L·(0.5+0.25·Cb) | 40 958.43 | **+50.62 %** | FAIL |

The coefficient estimates cost only 0.1 % of S, i.e. the endpoint can now be used
by a caller who knows nothing but L, B, T, Cb.

### 4.2 Legacy fit on its own shape family

| Shape | Exact S | Legacy S | Error |
|---|---|---|---|
| Prolate spheroid, L/D = 8 | 4.9697 m² | 3.2444 m² | −34.72 % |

The legacy fit is not "slightly optimistic": it is biased **low** on bodies of
revolution and **+50 % high** on ships, so neither family is safe.

### 4.3 Platform geometry integration (Wigley)

| n_stations × n_waterlines | Cb (integrated) | S [m²] | Error vs quasi-analytic 14.87906 m² |
|---|---|---|---|
| 21 × 10 | 0.443073 | 14.8311 | −0.322 % |
| 41 × 20 | 0.444137 | 14.8351 | −0.295 % |
| 81 × 40 | 0.444371 | 14.8360 | −0.289 % |
| 161 × 80 | 0.444427 | 14.8362 | −0.288 % |

Exact Wigley coefficients recovered: Cb = 0.4444 (=4/9), Cm = Cp = Cwh = 2/3.
The residual −0.29 % is the systematic cost of ignoring the longitudinal slope
dy/dx in the transverse girth length — a *bounded, converged* bias, not a grid
artefact (it stops changing after n_stations = 81).

Holtrop regression applied to the Wigley coefficients (out of domain):
14.879 → **12.8544 m² (−13.6 %)**. Ship-hull regressions do not transfer to
mathematical 2-D hulls; do not use Holtrop for Wigley/analytic test cases.

### 4.4 Consequence for resistance (KVLCC2, `/calculations/resistance`)

| Speed [kn] | Fn | S_Holtrop [m²] | S_legacy [m²] | R_T Holtrop [kN] | R_T legacy [kN] | C_T (both) |
|---|---|---|---|---|---|---|
| 7.0 | 0.0643 | 26 725.6 | 40 958.4 | 261.2 | 400.3 | 0.00151026 |
| 10.0 | 0.0918 | 26 725.6 | 40 958.4 | 513.3 | 786.6 | 0.00145423 |
| 13.0 | 0.1194 | 26 725.6 | 40 958.4 | 848.3 | 1 300.1 | 0.00142218 |
| 15.0 | 0.1377 | 26 725.6 | 40 958.4 | 1 119.6 | 1 715.9 | 0.00140989 |
| 15.5 | 0.1423 | 26 725.6 | 40 958.4 | 1 193.7 | 1 829.4 | 0.00140776 |
| 17.0 | 0.1561 | 26 725.6 | 40 958.4 | 1 431.5 | 2 193.9 | 0.00140346 |

Two findings:

* **R_T is 53.26 % too high with the legacy S** (ratio constant in speed, because
  the bias is a pure scale factor). Any EHP/power/fuel number derived from R_T
  inherits that error.
* **C_T is bit-identical** (0.00 % difference at every speed). Every term of the
  endpoint (R_f, R_form, R_r) is proportional to S, so a wrong S cancels out of
  the coefficient. **C_T must therefore never be used as a check on S** — a trap
  that self-consistency audits fall into.

### 4.5 Cross-method consistency with the full Holtrop implementation

| Speed [kn] | Simplified endpoint R_T | Full Holtrop R_T | Difference | Full C_T | 1+k |
|---|---|---|---|---|---|
| 13.0 | 848.3 kN | 1 257.4 kN | −32.53 % | 0.0021080 | 1.3385 |
| 15.5 | 1 193.7 kN | 1 782.2 kN | −33.02 % | 0.0021018 | 1.3385 |

With the same (verified) S, the simplified endpoint is a **−33 % lower bound**:
its residual term is a curve fit with no published provenance and it defaults to
(1+k) = 1.0. The gap is a *model* gap, not an S gap — the S account is now
closed, this one is not.

## 5. What changed in the code

`backend/api/calculations.py`:

* new request fields `hull_form` (`auto` | `ship` | `axisymmetric`),
  `midship_coefficient`, `waterplane_coefficient`, `bulb_section_area_m2`;
* new response fields `midship_coefficient`, `waterplane_coefficient`,
  `wetted_surface_warnings`;
* `hull_form="ship"` (or supplying Cm/Cwp) routes S through
  `services.holtrop_mennen.wetted_surface` with documented estimates for missing
  coefficients, reported back as warnings;
* the default path is **unchanged numerically** (backward compatibility for
  shipped benchmark rows) but now returns a
  `legacy_axisymmetric_wetted_surface` warning for non-axisymmetric hulls;
* `wetted_surface_m2` (geometry-validated input) still wins over both models.

## 6. Error verdict (explicit)

Criterion: |error| < 3 % on the primary quantity (wetted surface).

| Item | Error | < 3 %? |
|---|---|---|
| Holtrop S vs published KVLCC2 (caller coefficients) | −1.72 % | **YES** |
| Holtrop S vs published KVLCC2 (estimated coefficients) | −1.62 % | **YES** |
| Platform girth-integrated S vs quasi-analytic Wigley | −0.288 % | **YES** |
| Legacy S vs published KVLCC2 | +50.62 % | NO (documented defect, kept only for backward compatibility) |
| Holtrop S on Wigley hull (out of domain) | −13.6 % | NO — declared out of domain, not a supported use |
| Simplified endpoint R_T vs full Holtrop R_T | −33.0 % | NO — withheld: the endpoint's residual fit has no published reference; must not be quoted as a validated resistance model |

## 7. Limitations / traps

* The KVLCC2 published S is a single literature value; no uncertainty band is
  attached (the SIMMAN table quotes 4 significant figures).
* The residual −0.29 % of the girth integration grows with hull slenderness
  (dy/dx term). It is bounded here because Wigley is the slenderest hull in the
  platform's test set; for extreme forms (bulbous bow, transom) the omission of
  the bulb section (Abt) matters and must be passed explicitly.
* `Cm = 0.9 + 0.1·Cb` is an estimate, not geometry. It is always flagged; the
  sensitivity guard (`test_holtrop_insensitive_to_midship_coefficient_estimate`)
  bounds its effect on S below 1 % for Cm ∈ [0.95, 1.0].
* The pre-existing red `backend/tests/test_holtrop_mennen.py::test_audit_rejects_every_shipped_benchmark_case`
  and `::test_artifact_written_by_audit_is_parseable` fail on a pristine checkout
  (verified by reverting this change) — unrelated to this mission, still open.
* The live service on port 8001 exposes a different `/calculations/resistance`
  schema (requires `displacement`/`speed`), so verification here is module-level
  (importlib load of `backend/api/calculations.py`) rather than HTTP-level.

## 8. Artefacts

| Artefact | Path |
|---|---|
| Harness | `tools/verif/verify_wetted_surface.py` |
| Machine-readable results | `outputs/wetted_surface/wetted_surface_verification.json` |
| Fig 1 — S models vs published KVLCC2 | `outputs/wetted_surface/fig1_kvlcc2_surface_comparison.png` |
| Fig 2 — S error over a Cb sweep | `outputs/wetted_surface/fig2_surface_error_vs_cb.png` |
| Fig 3 — C_T cancels, R_T does not | `outputs/wetted_surface/fig3_resistance_consequence.png` |
| Fig 4 — Wigley integration convergence + out-of-domain probe | `outputs/wetted_surface/fig4_wigley_convergence.png` |
| Regression tests | `tests/test_wetted_surface_holtrop_verification.py` (14 passed) |