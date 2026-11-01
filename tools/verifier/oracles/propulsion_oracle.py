"""Analytic oracle for marine-propeller open-water / propulsive-coefficient verification.

Design rule (why this module embeds no fitted coefficients)
----------------------------------------------------------
An oracle that re-uses the same regression it audits cannot detect fabrication:
two copies of the same wrong formula agree perfectly.  Therefore every check
below is built from one of three coefficient-free strata:

  (a) EXACT kinematics / definitions
        J   = V_A / (n D)
        K_T = T / (rho n^2 D^4)
        K_Q = Q / (rho n^2 D^5)
        eta_0 = J K_T / (2 pi K_Q)          (open-water efficiency, definition)

  (b) RIGOROUS physics bounds (Froude momentum / actuator-disk theory)
        C_T = T / (1/2 rho A_0 V_A^2) = 8 K_T / (pi J^2)      (A_0 = pi D^2/4)
        eta_ideal = 2 / (1 + sqrt(1 + C_T))
        eta_0 <= eta_ideal   (ideal efficiency is an upper bound for a single
                              propulsor in uniform inflow; equality only in the
                              zero-loss limit)
        eta_0 -> 0 as J -> 0 (no advance speed => zero useful power)
        eta_D = P_E / P_D <= 1

  (c) PUBLISHED ENVELOPES for merchant Wageningen-B propellers, used only as
      generous plausibility bounds (never as point comparisons):
        - open-water efficiency of merchant B-series propellers peaks near
          0.70-0.75 and never reaches 0.85 (Carlton, *Marine Propellers and
          Propulsion*, open-water diagram family)
        - zero-thrust advance ratio for P/D ~ 1.0 lies near 0.85 (0.55-1.10
          used here as a +-25% band)
        - K_T at J ~ 0.6 lies in 0.05-0.45 for pitch ratios 0.6-1.4

Strata (a) and (b) are the load-bearing criteria; (c) is corroborative and is
always reported with its band, so a reader can re-tune it.

Units: SI (N, N*m, m, s^-1, kg/m^3).  rho = 1025 kg/m^3 (sea water, ITTC).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

RHO_SEAWATER = 1025.0  # kg/m^3
KNOT = 0.514444        # m/s

# ----------------------------------------------------------------------------
# Stratum (a): exact definitions
# ----------------------------------------------------------------------------


def advance_ratio(va: float, n: float, d: float) -> float:
    """J = V_A / (n D)."""
    if n <= 0 or d <= 0:
        raise ValueError("n and D must be positive")
    return va / (n * d)


def thrust_coefficient(t: float, n: float, d: float, rho: float = RHO_SEAWATER) -> float:
    """K_T = T / (rho n^2 D^4)."""
    return t / (rho * n * n * d ** 4)


def torque_coefficient(q: float, n: float, d: float, rho: float = RHO_SEAWATER) -> float:
    """K_Q = Q / (rho n^2 D^5)."""
    return q / (rho * n * n * d ** 5)


def open_water_efficiency(j: float, kt: float, kq: float) -> float:
    """eta_0 = J K_T / (2 pi K_Q)  (exact identity; 0 when K_Q -> 0)."""
    if kq == 0.0:
        return 0.0
    return j * kt / (2.0 * math.pi * kq)


# ----------------------------------------------------------------------------
# Stratum (b): rigorous bounds
# ----------------------------------------------------------------------------


def thrust_loading(j: float, kt: float) -> float:
    """C_T = 8 K_T / (pi J^2)  (actuator-disk thrust loading, A_0 = pi D^2/4)."""
    if j <= 0:
        raise ValueError("thrust loading undefined at J=0")
    return 8.0 * kt / (math.pi * j * j)


def ideal_efficiency(ct: float) -> float:
    """Froude momentum-theory ideal efficiency eta_i = 2 / (1 + sqrt(1 + C_T))."""
    if ct <= -1.0:
        raise ValueError("C_T must exceed -1")
    return 2.0 / (1.0 + math.sqrt(1.0 + ct))


# ----------------------------------------------------------------------------
# Constraints (the held-out exam for the propulsion domain)
# ----------------------------------------------------------------------------


@dataclass
class ConstraintResult:
    cid: str
    title: str
    stratum: str          # "exact" | "bound" | "envelope"
    passed: bool
    measured: dict
    criterion: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.cid,
            "title": self.title,
            "stratum": self.stratum,
            "passed": self.passed,
            "criterion": self.criterion,
            "measured": self.measured,
            "detail": self.detail,
        }


def _sweep_rows(series: dict, pd: str) -> list[dict]:
    return [r for r in series["rows"] if abs(r["P_D"] - float(pd)) < 1e-9]


def constraint_eta_zero_advance(series: dict) -> ConstraintResult:
    """eta_0 must vanish at J=0 (no advance speed => zero useful power)."""
    worst = []
    for pd in ("0.8", "1.0", "1.2"):
        rows = _sweep_rows(series, pd)
        r0 = [r for r in rows if abs(r["J"]) < 1e-12]
        if not r0:
            continue
        worst.append({"P_D": float(pd), "eta0_at_J0": r0[0]["eta0"]})
    ok = bool(worst) and all(abs(w["eta0_at_J0"]) <= 1e-2 for w in worst)
    return ConstraintResult(
        cid="CP-01",
        title="eta_0(J=0) = 0",
        stratum="bound",
        passed=ok,
        measured={"samples": worst},
        criterion="|eta_0(J=0)| <= 0.01 for every pitch ratio",
        detail="Zero advance speed means zero useful power, so open-water efficiency must be 0.",
    )


def constraint_eta_below_unity(series: dict) -> ConstraintResult:
    """eta_0 < 1 everywhere (thermodynamic/energetic limit)."""
    bad = []
    n_pts = 0
    for pd in ("0.8", "1.0", "1.2"):
        for r in _sweep_rows(series, pd):
            if r["J"] <= 0:
                continue
            n_pts += 1
            if r["eta0"] >= 1.0:
                bad.append({"P_D": r["P_D"], "J": r["J"], "eta0": r["eta0"]})
    return ConstraintResult(
        cid="CP-02",
        title="eta_0 < 1 for all J > 0",
        stratum="bound",
        passed=not bad,
        measured={"points_checked": n_pts, "violations": bad[:6], "n_violations": len(bad)},
        criterion="eta_0 < 1.0 at every tested J",
        detail="Open-water efficiency above unity would mean the propeller delivers more "
               "power than it absorbs.",
    )


def constraint_momentum_bound(series: dict, margin: float = 1e-6) -> ConstraintResult:
    """eta_0 <= eta_ideal(C_T) — Froude momentum-theory upper bound."""
    viol, n_pts, worst = [], 0, None
    for pd in ("0.8", "1.0", "1.2"):
        for r in _sweep_rows(series, pd):
            if r["J"] <= 0 or r["KT"] <= 0:
                continue
            n_pts += 1
            ct = thrust_loading(r["J"], r["KT"])
            eta_i = ideal_efficiency(ct)
            excess = r["eta0"] - eta_i
            if worst is None or excess > worst["excess"]:
                worst = {"P_D": r["P_D"], "J": r["J"], "KT": r["KT"], "eta0": r["eta0"],
                         "C_T": ct, "eta_ideal": eta_i, "excess": excess}
            if excess > margin:
                viol.append({"P_D": r["P_D"], "J": r["J"], "eta0": r["eta0"],
                             "eta_ideal": eta_i, "excess": excess})
    return ConstraintResult(
        cid="CP-03",
        title="eta_0 <= eta_ideal (Froude momentum bound)",
        stratum="bound",
        passed=not viol,
        measured={"points_checked": n_pts, "n_violations": len(viol),
                  "violations": viol[:6], "worst_excess": worst},
        criterion="eta_0 - 2/(1+sqrt(1+C_T)) <= 1e-6 at every tested J",
        detail="Ideal actuator-disk efficiency eta_i = 2/(1+sqrt(1+C_T)) is an upper bound; "
               "a propeller cannot beat its own momentum-theory limit.",
    )


def constraint_eta_identity(series: dict, tol: float = 1e-6) -> ConstraintResult:
    """eta_0 must satisfy the exact definition eta_0 = J K_T / (2 pi K_Q)."""
    worst, n_pts = None, 0
    for pd in ("0.8", "1.0", "1.2"):
        for r in _sweep_rows(series, pd):
            if r["J"] <= 0 or r["KQ"] == 0:
                continue
            n_pts += 1
            ident = open_water_efficiency(r["J"], r["KT"], r["KQ"])
            dev = abs(r["eta0"] - ident) / max(abs(r["eta0"]), 1e-12)
            if worst is None or dev > worst["rel_dev"]:
                worst = {"P_D": r["P_D"], "J": r["J"], "eta0": r["eta0"],
                         "identity_value": ident, "rel_dev": dev}
    ok = worst is not None and worst["rel_dev"] <= tol
    return ConstraintResult(
        cid="CP-04",
        title="eta_0 = J K_T /(2 pi K_Q) (definition identity)",
        stratum="exact",
        passed=ok,
        measured={"points_checked": n_pts, "worst": worst, "tol": tol},
        criterion=f"max relative deviation <= {tol:g}",
        detail="Internal consistency of the three reported outputs.",
    )


def constraint_pitch_sensitivity(series: dict, min_spread: float = 0.02) -> ConstraintResult:
    """eta_0 must respond to pitch ratio P/D (exact invariance => fabricated curve)."""
    per_j = {}
    for j_key in ("0.2", "0.3", "0.4", "0.5"):
        vals = {}
        for pd in ("0.8", "1.0", "1.2"):
            for r in _sweep_rows(series, pd):
                if abs(r["J"] - float(j_key)) < 1e-9:
                    vals[float(pd)] = r["eta0"]
        if len(vals) == 3:
            spread = (max(vals.values()) - min(vals.values())) / max(max(vals.values()), 1e-12)
            per_j[j_key] = {"eta0_by_PD": vals, "rel_spread": spread}
    ok = bool(per_j) and all(v["rel_spread"] >= min_spread for v in per_j.values())
    return ConstraintResult(
        cid="CP-05",
        title="eta_0 responds to pitch ratio P/D",
        stratum="bound",
        passed=ok,
        measured={"per_J": per_j, "min_required_rel_spread": min_spread},
        criterion="relative spread of eta_0 across P/D in {0.8,1.0,1.2} >= 2% at J=0.2..0.5",
        detail="Open-water efficiency is a function of pitch ratio; bit-identical eta_0 across "
               "three pitch ratios proves the value does not come from a B-series regression.",
    )


def constraint_no_plateau(series: dict, plateau_tol: float = 1e-9, max_run: int = 1) -> ConstraintResult:
    """No artificial saturation plateau in eta_0(J)."""
    worst_run, worst_pd = 0, None
    for pd in ("0.8", "1.0", "1.2"):
        rows = sorted(_sweep_rows(series, pd), key=lambda r: r["J"])
        run = 0
        for a, b in zip(rows, rows[1:]):
            if abs(b["eta0"] - a["eta0"]) <= plateau_tol:
                run += 1
                if run > worst_run:
                    worst_run, worst_pd = run, {"P_D": float(pd), "J_from": a["J"], "J_to": b["J"]}
            else:
                run = 0
    return ConstraintResult(
        cid="CP-06",
        title="no saturation plateau in eta_0(J)",
        stratum="bound",
        passed=worst_run <= max_run,
        measured={"longest_repeated_run": worst_run, "at": worst_pd, "tol": plateau_tol},
        criterion="at most 1 consecutive |delta eta_0| <= 1e-9 (i.e. no clamping artifact)",
        detail="Open-water efficiency has a single interior maximum and then falls; an exact "
               "plateau indicates a min()/max() clamp, not physics.",
    )


def constraint_zero_thrust_advance(series: dict, pd: str = "1.0",
                                   band: tuple = (0.55, 1.10)) -> ConstraintResult:
    """Zero-thrust advance ratio J_0 for P/D=1.0 must stay in the published band."""
    rows = sorted(_sweep_rows(series, pd), key=lambda r: r["J"])
    j0, kind = None, "no_crossing"
    for a, b in zip(rows, rows[1:]):
        if a["KT"] > 0 >= b["KT"]:
            # linear interpolation of the KT zero crossing
            frac = a["KT"] / (a["KT"] - b["KT"])
            j0, kind = a["J"] + frac * (b["J"] - a["J"]), "interpolated"
            break
    if j0 is None and len(rows) >= 2:
        # no zero thrust inside the swept range: extrapolate the last segment
        a, b = rows[-2], rows[-1]
        if b["KT"] < a["KT"]:
            frac = a["KT"] / (a["KT"] - b["KT"])
            j0, kind = a["J"] + frac * (b["J"] - a["J"]), "extrapolated"
    ok = j0 is not None and band[0] <= j0 <= band[1]
    return ConstraintResult(
        cid="CP-07",
        title="zero-thrust advance ratio J_0 within published band",
        stratum="envelope",
        passed=ok,
        measured={"J0": j0, "J0_kind": kind, "band": list(band), "P_D": float(pd)},
        criterion=f"J_0 in [{band[0]}, {band[1]}] for P/D=1.0 (B4-70 open-water diagrams, +-25%)",
        detail="Published B4-70 open-water diagrams place J_0 near 0.85 for P/D=1.0; a much "
               "larger J_0 means the K_T(J) slope is fabricated (e.g. linear extrapolation).",
    )


def constraint_kq_kt_decoupled(series: dict, min_var: float = 0.02) -> ConstraintResult:
    """K_Q/K_T must vary with J (constant ratio => eta_0 proportional to J)."""
    per_pd = {}
    for pd in ("0.8", "1.0", "1.2"):
        ratios = [r["KQ"] / r["KT"] for r in _sweep_rows(series, pd)
                  if r["J"] > 0 and r["KT"] != 0]
        if len(ratios) >= 3:
            var = (max(ratios) - min(ratios)) / max(max(ratios), 1e-12)
            per_pd[pd] = {"KQ_over_KT_min": min(ratios), "KQ_over_KT_max": max(ratios),
                          "rel_variation": var}
    ok = bool(per_pd) and all(v["rel_variation"] >= min_var for v in per_pd.values())
    return ConstraintResult(
        cid="CP-08",
        title="K_Q/K_T varies with J",
        stratum="bound",
        passed=ok,
        measured={"per_PD": per_pd, "min_required_rel_variation": min_var},
        criterion="relative variation of K_Q/K_T over J in (0,1] >= 2%",
        detail="eta_0 = J K_T/(2 pi K_Q); a constant K_Q/K_T forces eta_0 proportional to J, "
               "which contradicts eta_0(J=0)=0 together with an interior maximum.",
    )


def constraint_delivered_power_identity(endo: dict, tol: float = 1e-3) -> ConstraintResult:
    """P_D = 2 pi n Q (delivered-power definition)."""
    case = endo["case"]
    n = case["prop_rpm"] / 60.0
    pd_calc = 2.0 * math.pi * n * endo["response"]["torque"]
    pd_rep = endo["response"]["delivered_power"]
    dev = abs(pd_rep - pd_calc) / max(abs(pd_calc), 1e-12)
    return ConstraintResult(
        cid="CP-09",
        title="delivered_power = 2 pi n Q",
        stratum="exact",
        passed=dev <= tol,
        measured={"reported_W": pd_rep, "identity_W": pd_calc, "rel_dev": dev, "tol": tol},
        criterion=f"relative deviation <= {tol:g}",
        detail="Delivered power is the shaft power absorbed by the propeller.",
    )


def constraint_propulsive_identity(endo: dict, tol: float = 1e-3) -> ConstraintResult:
    """eta_D = eta_0 eta_H eta_R (quasi-propulsive chain)."""
    r = endo["response"]
    chain = r["open_water_eff"] * r["hull_eff"] * r["relative_rot_eff"]
    dev = abs(r["propulsive_eff"] - chain) / max(abs(chain), 1e-12)
    return ConstraintResult(
        cid="CP-10",
        title="eta_D = eta_0 * eta_H * eta_R",
        stratum="exact",
        passed=dev <= tol,
        measured={"reported": r["propulsive_eff"], "identity": chain, "rel_dev": dev},
        criterion=f"relative deviation <= {tol:g}",
        detail="Internal consistency of the efficiency chain.",
    )


def constraint_propulsive_below_unity(endo: dict) -> ConstraintResult:
    """eta_D = P_E/P_D <= 1 (no propulsion system delivers more than it absorbs)."""
    r = endo["response"]
    return ConstraintResult(
        cid="CP-11",
        title="propulsive efficiency eta_D < 1",
        stratum="bound",
        passed=r["propulsive_eff"] < 1.0,
        measured={"propulsive_eff": r["propulsive_eff"], "open_water_eff": r["open_water_eff"]},
        criterion="eta_D < 1.0",
        detail="eta_D = P_E/P_D with P_E = T V is a power ratio of a passive system.",
    )


def constraint_tow_power_consistency(endo: dict, tol: float = 1e-3) -> ConstraintResult:
    """P_E = T V must not exceed P_D."""
    case, r = endo["case"], endo["response"]
    v = case["speed"] * KNOT
    pe = r["thrust"] * v
    pd_ = r["delivered_power"]
    return ConstraintResult(
        cid="CP-12",
        title="tow power P_E = T V <= P_D",
        stratum="bound",
        passed=pe <= pd_ * (1.0 + tol),
        measured={"P_E_W": pe, "P_D_W": pd_, "ratio": pe / max(pd_, 1e-12)},
        criterion="T V / P_D <= 1",
        detail="Effective (tow) power cannot exceed delivered power for a propeller.",
    )


def constraint_thrust_coefficient_envelope(endo: dict,
                                           band: tuple = (0.05, 0.45)) -> ConstraintResult:
    """Implied K_T (from reported thrust, n, D) must sit in the B-series envelope."""
    case, r = endo["case"], endo["response"]
    n = case["prop_rpm"] / 60.0
    d = case["prop_diameter"]
    v = case["speed"] * KNOT
    j = advance_ratio(v, n, d)
    kt = thrust_coefficient(r["thrust"], n, d)
    kq = torque_coefficient(r["torque"], n, d)
    ok = band[0] <= kt <= band[1]
    return ConstraintResult(
        cid="CP-13",
        title="implied K_T at J=%.2f inside B-series envelope" % j,
        stratum="envelope",
        passed=ok,
        measured={"J": j, "KT_implied": kt, "KQ_implied": kq, "band": list(band)},
        criterion=f"K_T_implied in [{band[0]}, {band[1]}] (pitch ratios 0.6-1.4)",
        detail="Thrust implied by the reported thrust/n/D is K_T = T/(rho n^2 D^4); merchant "
               "propellers at J~0.6 sit far above 0.05.",
    )


def constraint_kt_curvature(series: dict, min_abs_curvature: float = 0.02) -> ConstraintResult:
    """K_T(J) must show quadratic curvature, as every B-series polynomial does."""
    per_pd, ok_all = {}, True
    for pd in ("0.8", "1.0", "1.2"):
        rows = sorted(_sweep_rows(series, pd), key=lambda r: r["J"])
        pts = [(r["J"], r["KT"]) for r in rows if 0.0 <= r["J"] <= 0.8]
        curv = []
        for (j0, k0), (j1, k1), (j2, k2) in zip(pts, pts[1:], pts[2:]):
            if abs((j2 - j1) - (j1 - j0)) < 1e-12:
                h = j1 - j0
                curv.append(abs((k2 - 2 * k1 + k0) / (h * h)))
        if curv:
            v = max(curv)
            per_pd[pd] = {"max_abs_second_derivative": v,
                          "n_samples": len(curv),
                          "is_linear": v < min_abs_curvature}
            ok_all = ok_all and v >= min_abs_curvature
    return ConstraintResult(
        cid="CP-14",
        title="K_T(J) carries quadratic curvature",
        stratum="bound",
        passed=ok_all,
        measured={"per_PD": per_pd, "min_required_abs_curvature": min_abs_curvature},
        criterion="max |d^2 K_T/dJ^2| >= 0.02 over J<=0.8",
        detail="B-series K_T(J) are quadratic polynomials; exactly zero curvature over the whole "
               "J range means a straight line, i.e. not the cited regression.",
    )


def constraint_geometry_multipliers(geom_sweep: dict, min_variation: float = 0.01) -> ConstraintResult:
    """Geometry multipliers must not be J-invariant (i.e. not hand-rolled constants).

    In the B-series polynomial every geometry term (Z, A_E/A_O) enters with its own
    J-dependence, so the ratio K_T(J, geom)/K_T(J, reference) changes with J at the
    percent level.  A ratio that is constant to 1e-3 or better across J = 0.3/0.5/0.7
    means the value was assembled as ``K_T = f(J) * g(P/D) * h(Z) * k(A_E/A_O)``.
    """
    keys = set()
    for samples in geom_sweep.values():
        for smp in samples:
            keys.add(f"P_D={smp['P_D']},Z={smp['Z']},Ae/Ao={smp['Ae_Ao']}")
    table, variations = {}, {}
    for j_key, samples in sorted(geom_sweep.items(), key=lambda kv: float(kv[0])):
        base = [s for s in samples if abs(s["P_D"] - 1.0) < 1e-9
                and s["Z"] == 4 and abs(s["Ae_Ao"] - 0.70) < 1e-9]
        if not base:
            return ConstraintResult(
                cid="CP-15", title="geometry multipliers vary with J", stratum="bound",
                passed=False, measured={"error": "reference geometry missing"},
                criterion="reference geometry (P/D=1, Z=4, Ae/Ao=0.70) required")
        kt_base = base[0]["KT"]
        table[float(j_key)] = {f"P_D={s['P_D']},Z={s['Z']},Ae/Ao={s['Ae_Ao']}": s["KT"] / kt_base
                               for s in samples}
    fabricated = []
    for k in sorted(keys):
        vals = [table[j][k] for j in table if k in table[j]]
        if len(vals) < 2:
            continue
        var = (max(vals) - min(vals)) / max(abs(max(vals)), 1e-12)
        variations[k] = {"rel_variation_over_J": var, "values": vals}
        if var < min_variation:
            fabricated.append(k)
    return ConstraintResult(
        cid="CP-15",
        title="geometry multipliers vary with J (not hand-rolled constants)",
        stratum="bound",
        passed=not fabricated,
        measured={"ratio_table": table, "variations": variations,
                  "J_invariant_geometries": fabricated,
                  "min_required_rel_variation": min_variation},
        criterion="for every non-reference geometry, the K_T ratio varies by >= 1% over J=0.3/0.5/0.7",
        detail="Bit-constant geometry multipliers prove K_T was built as a product f(J)*g(P/D)*h(Z)*k(Ae/Ao) "
               "rather than evaluated from the cited B-series regression.",
    )


ALL_CONSTRAINTS = [
    constraint_eta_zero_advance,
    constraint_eta_below_unity,
    constraint_momentum_bound,
    constraint_eta_identity,
    constraint_pitch_sensitivity,
    constraint_no_plateau,
    constraint_zero_thrust_advance,
    constraint_kq_kt_decoupled,
    constraint_kt_curvature,
]


def evaluate_series(series: dict, geom_sweep: dict | None = None) -> list[ConstraintResult]:
    """Run every series-level (open-water) constraint."""
    out = [fn(series) for fn in ALL_CONSTRAINTS]
    if geom_sweep:
        out.append(constraint_geometry_multipliers(geom_sweep))
    return out


ENDO_CONSTRAINTS = [
    constraint_delivered_power_identity,
    constraint_propulsive_identity,
    constraint_propulsive_below_unity,
    constraint_tow_power_consistency,
    constraint_thrust_coefficient_envelope,
]


def evaluate_end_to_end(endo: dict) -> list[ConstraintResult]:
    """Run every end-to-end (whole-propulsion) constraint."""
    return [fn(endo) for fn in ENDO_CONSTRAINTS]


def summarize(results: list[ConstraintResult]) -> dict:
    """Aggregate constraint results into a verdict block."""
    by_stratum = {}
    for r in results:
        s = by_stratum.setdefault(r.stratum, {"passed": 0, "failed": 0})
        s["passed" if r.passed else "failed"] += 1
    hard = [r for r in results if r.stratum in ("exact", "bound")]
    return {
        "n_constraints": len(results),
        "n_passed": sum(1 for r in results if r.passed),
        "n_failed": sum(1 for r in results if not r.passed),
        "failed_ids": [r.cid for r in results if not r.passed],
        "by_stratum": by_stratum,
        "hard_criteria_failed": [r.cid for r in hard if not r.passed],
        "verdict": "pass" if all(r.passed for r in results) else "fail",
    }