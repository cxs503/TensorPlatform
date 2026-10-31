# W3-N08 — Limitations & evidence grading

Mission: *wind-current-ice coupled manoeuvrability and drift-force correction*.
Package: `backend/physics/ship_env_maneuvering/`.
Artifacts: `artifacts/m795/validation.json`, `docs/m795/REPORT.md`.

## 1 What is REAL

| item | evidence |
|---|---|
| Wind load $F=\tfrac12\rho_{air}V_R^2 C(\psi)A$ with angle-dependent coefficients | implemented, 4-angle cross-check vs an independent projected-area formulation (max 6.82 %) |
| Beam-wind pressure anchor | reproduces IMO 2008 IS Code `P = 0.504 V^2` within 3.75 % |
| Current drift load | implemented, 3-speed cross-check vs strip cross-flow + ITTC-57 (max 6.55 %); the reference uses a Reynolds-dependent friction line, so the speed dependence is *not* shared by construction |
| MMG 3-DOF manoeuvring core (hull derivatives, $K_T(J)$, rudder normal force, flow straightening) | RK4 time integration, time-step converged (0.00 % between dt = 0.5 s and 0.25 s), matches an independent damped-Newton steady-state equilibrium solve within 6.67 % |
| Turning-circle / zigzag metrics | inside the engineering envelope for a VLCC ($D_T/L$ = 2.9, advance/L = 3.3, transfer/L = 1.4; IMO Res. A.751 limits satisfied) |
| Ice resistance | Riska-type and Lindqvist-type decompositions, scaling laws ($R\propto h^2$, $\propto\sigma_f$, linear in $B$, monotone in $V$ and $\mu$) covered by unit tests |
| Coupling | environment loads appear additively in the MMG right-hand side; superposition verified numerically by unit test |

## 2 What is WITHHELD (and why)

1. **"Wind load vs OCIMF 1994 guide tables < 10 %"** — the guide tables are not
   machine-readable offline and general network access is blocked (only a PyPI
   mirror responds). Substitute evidence: independent projected-area model and
   the published IMO IS Code beam pressure. The coefficient *shape* between the
   anchor points is literature-shaped, not table-verified.
2. **"Current load vs OCIMF current curves < 10 %"** — same reason; substituted
   by strip cross-flow + ITTC-57.
3. **"Turning diameter vs ice-basin manoeuvring trials < 10 %"** — no ice-basin /
   EFD manoeuvring data exist in the worktree. This is the criterion the mission
   asks for; it **cannot be honestly claimed**. The package provides instead
   code-to-code verification (equilibrium solve, $\Delta t$ refinement) and an
   explicit sensitivity band: two independently derived coefficient sets give
   tactical diameters differing by **30.8 %**. That number is the honest
   uncertainty of an uncalibrated MMG prediction and is the reason the absolute
   accuracy claim is withheld.
4. **"Ice resistance matching the original Riska (1989) / Lindqvist (1989)
   coefficients"** — the papers are unavailable offline. Coefficients are chosen
   inside the published engineering ranges and the two independent
   decompositions differ by 36 % (mean), reported as a model-uncertainty band.

## 3 Modelling notes / known approximations

* **Rudder inflow regularisation.** $u_R = u(1-w_R)\sqrt{1+8K_T/(\pi J^2)}$
  diverges as $J\to0$ (heavily loaded propeller). Without a cap, the simulated
  turn keeps tightening until the ship's speed collapses (observed: diameter
  error of two orders of magnitude). The implementation caps the amplification
  factor at `RUDDER_RACE_MAX = 2.0`. This is an engineering closure, documented
  in code, and it is what makes the steady turning diameter land at
  $D_T/L = 3.08$ instead of an unphysical limit cycle.
* **Numerical floor** $U_{ND} = \max(U_w, 1\ \text{m/s})$ for the
  non-dimensionalisation: below 1 m/s the quadratic non-dimensional form is not
  meaningful, while the physical force scale still uses the true $U_w^2$.
* **Current** enters kinematically (forces from the velocity through water); the
  explicit drift-force model of `loads.current_load` is retained for static
  checks and is the quantity validated in criterion (b). Manoeuvring metrics are
  evaluated in the **water frame** (current advection removed), otherwise a
  ground-track diameter is meaningless in a current field.
* **Ice** acts opposite to the instantaneous velocity vector plus an engineering
  yaw-damping moment from the channel; no ice-floe impact model, no ice-channel
  breadth effect, no ice-class structural limits.
* **Applicability**: single-screw single-rudder, $\delta\le35^\circ$,
  $C_b\approx0.81$, $Fn\approx0.14$, ice thickness 0.2–1.8 m, wind up to 25 m/s,
  sea water 15 °C.