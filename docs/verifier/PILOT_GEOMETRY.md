# Geometry pilot — hydrostatics adjudicated against closed-form baselines

**Mission** m1574 (self-start, platform seat) · **Branch** `agent/m1574` · **Artifact**
`artifacts/verifier/geometry_pilot_report.json`

## 1. Why this pilot exists

The platform's first discipline rule is *"geometry must be verified before anything
else: Cb, L/B, coefficients"*. Yet:

* report **1148** (capability ledger) records every `geometry.*` capability as
  **UNVERIFIED** — `geometry.hydro_curves`, `geometry.generate`, `geometry.list_models`,
  `geometry.verify_official`, `shiphull.generate` all claim REAL with no evidence;
* at the time of writing the TensorPlatform tree contained **no hydrostatics
  implementation at all** (`grep -ri "hydrostat|waterplane|sectional" backend/` → 0 hits),
  and the serving platform `:8001` was unreachable, so there was nothing to adjudicate;
* consequently no verifier existed either: unlike resistance (`tp_ver_resistance.py`)
  there was no independent reference to compare a candidate against.

A verifier cannot be built out of nothing: it needs values that are *true independently
of the code under test*. These are supplied here as **closed-form hydrostatics** of three
hull families, so that a candidate is judged against mathematics, not against itself.

## 2. Closed-form baselines (derivations)

Surface convention: `y(x,z)` = half-breadth, `x ∈ [-L/2, L/2]` from midship, `z ∈ [0, D]`
from base line, hull symmetric about `y = 0`, draft `T ≤ D`.

Identities used throughout (ITTC 7.5-02-03-01 notation; Rawson & Tupper, *Basic Ship
Theory*; PNA Vol. I §2):

```
S(x) = ∫₀ᵀ 2y dz      ∇ = ∫ S dx        A_W = ∫ 2y(x,T) dx      I_T = ∫ (2/3) y(x,T)³ dx
Cb = ∇/(LBT)          Cm = S(0)/(BT)    Cp = ∇/(S(0)L) = Cb/Cm  Cw = A_W/(LB)
KB = (1/∇)∫∫ z·2y      BM = I_T/∇       LCB = (1/∇)∫ x S dx      LCF = (1/A_W)∫ x·2y(x,T) dx
```

### 2.1 Box barge — `y = B/2`

`∇ = LBT`, `S(0) = BT`, `A_W = LB`, `I_T = LB³/12`, so `Cb = Cm = Cp = Cw = 1`,
`KB = T/2`, `BM = B²/(12T)`, `LCB = LCF = 0`.

### 2.2 Wigley — `y = (B/2)(1 − (2x/L)²)(1 − (z/D)²)`

With `zfac = 1 − T²/(3D²)` and `wl = 1 − T²/D²`:

| quantity | closed form |
|---|---|
| `S(x)` | `B (1 − 4x²/L²) zfac` |
| `∇` | `(2/3) L B T · zfac` |
| `Cb` | `(2/3) zfac` |
| `Cm` | `zfac` |
| `Cp` | `2/3` (independent of `T`, `D`) |
| `A_W`, `Cw` | `(2/3) L B · wl`, `Cw = (2/3) wl` |
| `I_T` | `32 L k³/105`, `k = (B/2) wl` |
| `KB` | `(T/2)(1 − T²/(2D²)) / zfac` |
| `LCB`, `LCF` | `0` |

*Limits used as self-checks:* `D → T` gives the textbook Wigley `Cb = 4/9`, `Cm = Cp = 2/3`,
`KB = 0.375 T`, `Cw = 0` (knife-edge waterplane); `D → ∞` gives wall-sided sections,
`Cm → 1`, `Cb → 2/3`, `KB → T/2`.

### 2.3 Sine-form — `y = (B/2) cos(πx/L)(1 − (z/D)²)`

`∇ = (2/π) L B T zfac`, `Cb = (2/π) zfac`, `Cm = zfac`, `Cp = 2/π`,
`A_W = (2/π) L B wl`, `Cw = (2/π) wl`, `I_T = 8 L k³/(9π)`, `KB` as for Wigley
(same `z` factor). This case exists because its `x`-dependence is **not polynomial**: it is
where Simpson's fourth-order error is actually exercised (on the polynomial cases the
integrals are exact, which would make a convergence-order criterion vacuous).

**A pitfall worth recording**: the first hand derivation of `KB` in this pilot was missing
a factor `1/2` (`KB = T(1−T²/2D²)/(1−T²/3D²)` instead of `(T/2)(…)`). The integrator
disagreed and was right — the `D → ∞` limit of the wrong formula gives `KB = T`, impossible
for a box barge. **Always pin an analytic formula by a degenerate limit before trusting it.**

## 3. Reference integrator (`tools/verifier/geometry/ref_hydrostatics.py`)

Composite **Simpson's first rule** over an `(n_x+1) × (n_z+1)` sampling of the surface;
panel counts rounded up to even values (Simpson needs an even panel count); a
`trapezoid` branch exists only so the mutation gate can demonstrate the order criterion.
All required integrals are listed in §2; `S(0)` is read at the mid grid point `x = 0`
(`n_x` even guarantees it is a grid point).

## 4. Criteria (what makes a candidate pass)

| id | criterion | threshold | why this threshold |
|---|---|---|---|
| C1 | agreement with closed form at `n_x = 160` | `< 1e-6` | a correct Simpson implementation is at `1.4e-8`; `1e-6` still leaves 2 decades of margin against round-off |
| C2 | observed convergence order (log–log slope, grids 40/80/160/320), where the error is resolvable (`>1e-6` at `n=40`) | `≥ 2.5` | Simpson is 4; trapezoid is 2, so the floor separates them |
| C3 | coefficient identities `Cp·Cm = Cb`, `∇ = Cb·L·B·T`, `A_W = Cw·L·B`, `BM = I_T/∇` | `< 1e-9` | hull-independent: any implementation must satisfy them exactly |
| C4 | **mutation gate**: every planted defect must be rejected | 6/6 | report 1133's device (physically plausible synthetic control) — criteria that accept a broken implementation are worthless |
| C5 | external candidate over held-out hulls, platform band | `< 3%` | platform acceptance standard |

Planted mutants (`geometry/mutants.py`), each rejected in the run:

| mutant | defect | caught by | evidence |
|---|---|---|---|
| `depth_in_cb` | `Cb = ∇/(L·B·D)` | C1 on `wigley_D=2T` | 50 % error in `Cb` |
| `trapezoid` | second-order quadrature | C1 on `wigley_D=T` | `KB` 1.2e-4 |
| `cw_equals_cb` | `Cw := Cb` (copy-paste) | C1 on `wigley_D=2T` | `A_W` 22 % |
| `bm_longitudinal` | `BM` from the longitudinal moment | C1 on `box_barge` | `BM` 2400 % |
| `kb_half_draft` | folk `KB = T/2` for every hull | C1 on `wigley_D=T` | `KB` 33 % |
| `cp_from_cw` | `Cp = Cb/Cw` | C1 on `wigley_D=2T` | `Cp` 83 % |

Note on the `KB = T/2` mutant: it is exactly right for a box barge and **33 % wrong** for a
full-depth Wigley — the folk formula is a hull-specific accident, not a law.

## 5. Results (self-audit, `n_x = 160`, `n_z = 80`)

| case | worst metric | rel. error vs closed form | converged order | verdict |
|---|---|---|---|---|
| `box_barge` | `I_T` | 2.2e-16 | exact (round-off) | PASS |
| `wigley_D=T` | `LCB` | 1.3e-15 | exact | PASS |
| `wigley_D=2T` | `BM` | 1.4e-08 | 4.00 | PASS |
| `sineform_D=1.5T` | `BM` | 8.3e-09 | 4.00 | PASS |

C1 `PASS` (worst 1.4e-8) · C2 `PASS` (order 4.00 vs floor 2.5) · C3 `PASS` (worst 1.3e-16) ·
C4 `PASS` (6/6 mutants rejected) → **verdict PASS**.

Figures: `artifacts/verifier/fig_geometry_convergence.png` (log–log error with the `−4`
reference slope), `fig_geometry_errors.png` (per-metric error vs both bands),
`fig_geometry_hulls.png` (body plan + design waterline of the three families).

## 6. Candidate mode — how the debt actually gets closed

```
python3 tools/verifier/tp_ver_geometry.py --candidate <impl.py> --candidate-id <id> \
        --output artifacts/verifier/<id>.json
```

Protocol (identical to `tools/verifier/tp_ver_resistance.py`): the verifier pushes
`{"specification": {L,B,T,D,x[],z[],y[][]}}` on stdin, the candidate answers
`{"result": {VOL,Cb,Cm,Cp,A_W,Cw,I_T,KB,BM,LCB,LCF}}` on stdout. The exam set is six hulls
from the three analytic families with seeded random `L, B, T, D`
(`L ∈ [80,320] m`, `B/L ∈ [0.10,0.18]`, `T/L ∈ [0.035,0.06]`, `D/T ∈ [1.2,1.8]`), so a
candidate cannot hard-code the parameters it was developed against. Tests pin both
directions: an independent **trapezoid** implementation (a different quadrature from the
reference Simpson, so a genuinely independent candidate) passes **6/6, worst relative error
5.6e-5** — inside the 3 % band even though it is 3000× worse than the reference gate; a
constant-`Cb` MOCK fails 0/6; a crashing candidate is reported as an error rather than
scored. Measured run: `artifacts/verifier/geometry_candidate_trapezoid.json`.

## 7. Limits — stated explicitly

* **`geometry.*` capability levels did not change, and now there is direct evidence why.**
  The three candidate paths (`/api/v1/geometry/hydro-curves`, `…/hydro_curves`,
  `/api/v1/calculations/hydrostatics`) were probed against the live platform and all return
  **HTTP 404** while `/health`-level service is up — i.e. the capability is **NOT_SUPPORTED**
  at the HTTP layer, not merely unverified. (`:8001` was additionally unresponsive for part of
  the session, hence the timeout-tolerant probe.) No candidate ⇒ no verdict; the pilot supplies
  the missing measuring instrument, not a retroactive blessing. When such an endpoint appears,
  C5 adjudicates it in one command.
* **No experiment or towing-tank reference is involved**, and none is claimed. The baselines
  are exact for their *definitions*; a real ship hull is not a Wigley. A candidate passing C5
  proves correct numerical hydrostatics, not agreement with a model test.
* **Only symmetric, single-hull, zero-heel, zero-trim, `z ∈ [0,T]` forms** are covered.
  Appendages, openings, trim/heel, and non-symmetric hulls are out of scope and would each
  need their own baseline (a trimmed case changes `S(x)` and `A_W` definitionally).
* Coefficients are single-precision blind beyond round-off: the gate is a *code* check, not
  a tolerance study of the printed endpoint output (`3–4` significant digits on `:8001` was
  the limiting factor in report 1133; the same caveat applies to any endpoint adjudication).

## 8. Reproduction

```
python3 tools/verifier/tp_ver_geometry.py --self-audit --figures \
        --output artifacts/verifier/geometry_pilot_report.json     # PASS, 4 cases, 6/6 mutants
python3 -m pytest tools/verifier/tests/test_geometry_pilot.py -q   # 22 passed
```

Tests cover: pinned analytic values (incl. both Wigley limits and the `D < T` rejection),
integrator-vs-baseline agreement on all four cases, Simpson order = 4, trapezoid order = 2,
identities to round-off, negative half-breadth rejection, full mutation gate, held-out set
shape/symmetry/`Cb` range, candidate PASS/FAIL/CRASH paths, and the CLI exit codes.