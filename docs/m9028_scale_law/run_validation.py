"""m9028 / p234w2-n02: scale-effect & extrapolation similarity validation driver.

Produces ``results.json`` and four figures in this directory.  Everything is
computed, nothing is transcribed: the JSON is the only source of the numbers
quoted in the report.

Validation design (honest bookkeeping)
-------------------------------------
The reference full-scale resistance is built from *three independent closures*
so that the extrapolation is not graded against itself:

    R_TS_ref = (1+k) q_S S_S C_FS^Schoenherr + R_wS^Michell + q_S S_S C_A

* ``C_FS^Schoenherr`` -- ATTC-1947 implicit line (independent of ITTC-1957,
  which the extrapolation uses on the ship side);
* ``R_wS^Michell`` -- thin-ship wave resistance of the *same* Wigley hull at
  full scale, from ``docs/verification_debt/wigley_michell`` (two independent
  quadrature routes there);
* ``C_A`` -- ITTC-1978 correlation allowance (0 in the base case; swept).

The "towing tank" data are synthetic: the model total resistance is generated
from the *transition-aware* friction line (``low_re_friction_cf``) plus the
Michell wave resistance of the geometrically similar model hull, with a
deterministic +-0.3 % "measurement" perturbation.  For the classical 100 m
Wigley the model Reynolds numbers are > 1e6 so this reduces to the fully
turbulent plate line; for the 10 m USV the three models live inside the
transition band, which is the whole point of the exercise.

Reference friction line for the *ship* is Schoenherr while the extrapolation
uses ITTC-1957 -- the ~1 % residual that this closure difference produces is
the non-circular part of the error budget.
"""

from __future__ import annotations

import base64
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (ROOT / "backend", ROOT / "docs" / "verification_debt" / "wigley_michell"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from api.domain import scale_effect as se  # noqa: E402
from michell_wigley import Wigley, michell_rw_converged, wetted_surface  # noqa: E402

LAMBDAS = {"1/10": 0.10, "1/25": 0.04, "1/50": 0.02}
FORM_FACTOR_K = 0.10
TEMP_C = 15.0
SALINITY = 35.0
NOISE = 0.003  # +-0.3 % synthetic tank measurement uncertainty

CASES = {
    "classical_wigley_L100": {"L_S": 100.0, "fr": [0.20, 0.25, 0.30, 0.35, 0.40, 0.45]},
    "usv_wigley_L10": {"L_S": 10.0, "fr": [0.20, 0.25, 0.30, 0.35, 0.40, 0.45]},
}
CHECK_FR = [0.25, 0.40]

RHO_S = se.density_water(TEMP_C, SALINITY)      # sea water, ship
NU_S = se.kinematic_viscosity(TEMP_C, SALINITY)
RHO_M = se.density_water(TEMP_C, 0.0)           # towing tank / lake, fresh water
NU_M = se.kinematic_viscosity(TEMP_C, 0.0)


def wigley_hull(L: float, rho: float) -> Wigley:
    """Same Wigley family (L/B = 10, B/T = 1.6) at length L."""
    return Wigley(L=L, B=L / 10.0, T=L / 10.0 / 1.6, rho=rho)


_SURF_CACHE: dict = {}


def hull_surface(hull: Wigley) -> float:
    key = round(hull.L, 9)
    if key not in _SURF_CACHE:
        _SURF_CACHE[key] = wetted_surface(hull, n_x=800, n_z=400)
    return _SURF_CACHE[key]


def michell(hull: Wigley, velocity: float, n_theta: int = 80):
    """Richardson-extrapolated Michell wave resistance and its refinement increment."""
    rw, delta = michell_rw_converged(hull, velocity, n_theta=n_theta, ratio=2)
    return rw, delta


def make_state(hull: Wigley, velocity: float, rho: float, nu: float) -> se.ScaleState:
    return se.ScaleState(
        length=hull.L,
        velocity=velocity,
        density=rho,
        viscosity=nu,
        wetted_surface=hull_surface(hull),
    )


def deterministic_noise(tag: str) -> float:
    """Deterministic +-NOISE perturbation (no hidden RNG state across runs)."""
    digest = sum(ord(c) * (i + 1) for i, c in enumerate(tag))
    return 1.0 + NOISE * math.sin(0.7 * digest)


def reference_ship_resistance(
    ship: se.ScaleState, rw_ship: float, c_a: float, k: float
) -> float:
    """Independent reference: Schoenherr friction + Michell wave + C_A."""
    cf_schoenherr = se.schoenherr_cf(ship.reynolds)
    q_s = ship.dynamic_pressure * ship.wetted_surface
    return (1.0 + k) * q_s * cf_schoenherr + rw_ship + q_s * c_a


def run_case(name: str, spec: dict) -> dict:
    hull_s = wigley_hull(spec["L_S"], RHO_S)
    s_s = hull_surface(hull_s)
    rows = []
    case = {
        "case": name,
        "L_ship": hull_s.L,
        "B_ship": hull_s.B,
        "T_ship": hull_s.T,
        "S_ship": s_s,
        "cb_analytic": hull_s.cb,
        "volume_analytic": hull_s.volume,
        "rho_ship": RHO_S,
        "nu_ship": NU_S,
        "rho_model": RHO_M,
        "nu_model": NU_M,
        "form_factor_k": FORM_FACTOR_K,
        "rows": rows,
    }
    for fr in spec["fr"]:
        v_s = fr * math.sqrt(se.G * hull_s.L)
        ship = make_state(hull_s, v_s, RHO_S, NU_S)
        rw_s, rw_s_delta = michell(hull_s, v_s)
        c_a = 0.0
        r_ref = reference_ship_resistance(ship, rw_s, c_a, FORM_FACTOR_K)
        r_ref_schoenherr_only = r_ref
        # closure-sensitivity band: same reference but with ITTC-1957 friction
        q_s = ship.dynamic_pressure * ship.wetted_surface
        r_ref_ittc = (
            (1.0 + FORM_FACTOR_K) * q_s * se.ittc1957_cf(ship.reynolds)
            + rw_s
            + q_s * c_a
        )
        row = {
            "fr": fr,
            "v_ship": v_s,
            "re_ship": ship.reynolds,
            "r_wave_ship": rw_s,
            "r_wave_ship_refinement_increment": rw_s_delta,
            "cw_ship": rw_s / q_s,
            "r_ref_schoenherr": r_ref,
            "r_ref_ittc1957": r_ref_ittc,
            "cf_ship_schoenherr": se.schoenherr_cf(ship.reynolds),
            "cf_ship_ittc1957": se.ittc1957_cf(ship.reynolds),
            "scales": [],
        }
        for lname, lam in LAMBDAS.items():
            hull_m = wigley_hull(spec["L_S"] * lam, RHO_M)
            v_m = se.froude_velocity(v_s, lam)
            model = make_state(hull_m, v_m, RHO_M, NU_M)
            rw_m, rw_m_delta = michell(hull_m, v_m)
            cf_m_true = se.low_re_friction_cf(model.reynolds)
            cf_m_turb = se.prandtl_schlichting_cf(model.reynolds)
            q_m = model.dynamic_pressure * model.wetted_surface
            noise = deterministic_noise(f"{name}:{lname}:{fr}")
            r_tm = ((1.0 + FORM_FACTOR_K) * q_m * cf_m_true + rw_m) * noise
            r_tm_tripped = ((1.0 + FORM_FACTOR_K) * q_m * cf_m_turb + rw_m) * noise

            variants = {}
            # (i) platform baseline: ITTC-1957 correlation line on both sides
            #     (the classical ITTC-78 convention, blind to the model Re)
            variants["ittc57_both_sides"] = se.extrapolate_ittc78(
                r_tm, model, ship,
                cf_line_ship="ittc1957", cf_line_model="ittc1957",
                form_factor_k=FORM_FACTOR_K, c_a=c_a,
            )
            # (ii) Re-threshold switch: physical line below Re_M = 1e6
            variants["auto_switch"] = se.extrapolate_ittc78(
                r_tm, model, ship,
                cf_line_ship="ittc1957", cf_line_model="auto",
                form_factor_k=FORM_FACTOR_K, c_a=c_a,
            )
            # (iii) RECOMMENDED: physical (transition-aware / P-S) model-side
            #       closure at every model scale, ITTC-1957 on the ship side
            variants["physical_model_line"] = se.extrapolate_ittc78(
                r_tm, model, ship,
                cf_line_ship="ittc1957", cf_line_model="low_re",
                form_factor_k=FORM_FACTOR_K, c_a=c_a,
            )
            # (iv) mission-literal Delta C_F (+ sign) -- negative control
            variants["delta_cf_mission_sign"] = se.extrapolate_delta_cf(
                r_tm, model, ship,
                cf_line_ship="ittc1957", cf_line_model="ittc1957",
                c_a=c_a, delta_cf_sign=+1,
            )
            # (v) turbulence-stimulated tank data (sensitivity: the correction
            #     must NOT be applied when the model really is fully turbulent)
            variants["tripped_physical_line"] = se.extrapolate_ittc78(
                r_tm_tripped, model, ship,
                cf_line_ship="ittc1957", cf_line_model="low_re",
                form_factor_k=FORM_FACTOR_K, c_a=c_a,
            )
            variants["tripped_ittc57_both"] = se.extrapolate_ittc78(
                r_tm_tripped, model, ship,
                cf_line_ship="ittc1957", cf_line_model="ittc1957",
                form_factor_k=FORM_FACTOR_K, c_a=c_a,
            )
            variants["tripped_ps_model_line"] = se.extrapolate_ittc78(
                r_tm_tripped, model, ship,
                cf_line_ship="ittc1957", cf_line_model="prandtl_schlichting",
                form_factor_k=FORM_FACTOR_K, c_a=c_a,
            )

            entry = {
                "lambda": lam,
                "L_model": hull_m.L,
                "v_model": v_m,
                "re_model": model.reynolds,
                "s_ratio": model.wetted_surface / s_s,
                "r_wave_model": rw_m,
                "r_wave_model_refinement_increment": rw_m_delta,
                "ratic_rw": rw_s / rw_m,
                "cf_model_true_transition_aware": cf_m_true,
                "cf_model_fully_turbulent": cf_m_turb,
                "cf_model_ittc1957": se.ittc1957_cf(model.reynolds),
                "intermittency": se.transition_intermittency(model.reynolds),
                "noise_factor": noise,
                "r_tm": r_tm,
                "r_tm_tripped": r_tm_tripped,
                "variants": {},
            }
            for key, ex in variants.items():
                err_pct = 100.0 * (ex.r_ts - r_ref) / r_ref
                entry["variants"][key] = {
                    "r_ts": ex.r_ts,
                    "err_pct_vs_schoenherr_ref": err_pct,
                    "err_pct_vs_ittc1957_ref": 100.0 * (ex.r_ts - r_ref_ittc) / r_ref_ittc,
                    "cf_model": ex.cf_model,
                    "cf_ship": ex.cf_ship,
                }
            row["scales"].append(entry)
        rows.append(row)
    return case


def spread_block(case: dict, fr: float, variant: str) -> dict:
    row = next(r for r in case["rows"] if abs(r["fr"] - fr) < 1e-12)
    res = {f"{s['lambda']:.3f}": s["variants"][variant]["r_ts"] for s in row["scales"]}
    out = se.self_consistency_spread(res)
    outs = se.self_consistency_spread(
        {k: v for k, v in res.items()}
    )
    return {"values": res, **outs}


def cf_line_tables() -> dict:
    handbook_re = {1e5: 8.333e-3, 1e6: 4.688e-3, 1e7: 3.000e-3, 1e8: 2.083e-3, 1e9: 1.531e-3}
    rows = []
    for re_val, printed in sorted(handbook_re.items()):
        cf = se.ittc1957_cf(re_val)
        rows.append(
            {
                "re": re_val,
                "ittc1957": cf,
                "handbook_4sig": printed,
                "err_pct": 100.0 * (cf - printed) / printed,
                "schoenherr": se.schoenherr_cf(re_val),
                "schoenherr_vs_ittc57_pct": 100.0 * (se.schoenherr_cf(re_val) - cf) / cf,
            }
        )
    low_re = []
    for re_val in (5e4, 1e5, 2e5, 3e5, 5e5, 7e5, 1e6, 2e6, 1e7):
        low_re.append(
            {
                "re": re_val,
                "blasius_laminar": se.blasius_cf(re_val),
                "schlichting_plate": se.schlichting_plate_cf(re_val),
                "prandtl_schlichting": se.prandtl_schlichting_cf(re_val),
                "low_re_transition_aware": se.low_re_friction_cf(re_val),
                "ittc1957": se.ittc1957_cf(re_val),
                "intermittency": se.transition_intermittency(re_val),
            }
        )
    return {"handbook": rows, "low_re": low_re}


def asymptotic_block() -> dict:
    grid = np.logspace(4, 30, 60)
    cfs = [se.ittc1957_cf(r) for r in grid]
    mono = all(cfs[i] > cfs[i + 1] for i in range(len(cfs) - 1))
    return {
        "re": [float(r) for r in grid],
        "cf": cfs,
        "strictly_decreasing": mono,
        "cf_re1e12": se.ittc1957_cf(1e12),
        "cf_re1e30": se.ittc1957_cf(1e30),
        "limit_loglog_slope": (
            (math.log(se.ittc1957_cf(1e30)) - math.log(se.ittc1957_cf(1e12)))
            / (math.log(1e30) - math.log(1e12))
        ),
    }


def identity_block() -> dict:
    ship = make_state(wigley_hull(100.0, RHO_S), 6.26, RHO_S, NU_S)
    model = make_state(wigley_hull(10.0, RHO_M), 1.98, RHO_M, NU_M)
    r_tm = 120.0
    a = se.extrapolate_ittc78(r_tm, model, ship, cf_line_ship="ittc1957",
                              form_factor_k=0.0, c_a=0.0)
    b = se.extrapolate_delta_cf(r_tm, model, ship, cf_line_ship="ittc1957",
                                c_a=0.0, delta_cf_sign=-1)
    c = se.extrapolate_delta_cf(r_tm, model, ship, cf_line_ship="ittc1957",
                                c_a=0.0, delta_cf_sign=+1)
    return {
        "r_ts_ittc1978_k0_ca0": a.r_ts,
        "r_ts_delta_cf_physics_sign": b.r_ts,
        "rel_diff_physics": abs(a.r_ts - b.r_ts) / a.r_ts,
        "r_ts_delta_cf_mission_literal": c.r_ts,
        "rel_diff_mission_vs_ittc1978": abs(c.r_ts - a.r_ts) / a.r_ts,
    }


def ca_sweep_block() -> dict:
    """C_A is additive and cancels between reference and extrapolation."""
    hull_s = wigley_hull(10.0, RHO_S)
    v_s = 0.25 * math.sqrt(se.G * hull_s.L)
    ship = make_state(hull_s, v_s, RHO_S, NU_S)
    hull_m = wigley_hull(0.4, RHO_M)
    v_m = se.froude_velocity(v_s, 0.04)
    model = make_state(hull_m, v_m, RHO_M, NU_M)
    rw_s, _ = michell(hull_s, v_s)
    rw_m, _ = michell(hull_m, v_m)
    q_m = model.dynamic_pressure * model.wetted_surface
    r_tm = (1.0 + FORM_FACTOR_K) * q_m * se.low_re_friction_cf(model.reynolds) + rw_m
    rows = []
    for c_a in (0.0, 5.0e-4, 1.0e-3):
        ref = reference_ship_resistance(ship, rw_s, c_a, FORM_FACTOR_K)
        ex = se.extrapolate_ittc78(r_tm, model, ship, cf_line_ship="ittc1957",
                                   cf_line_model="low_re",
                                   form_factor_k=FORM_FACTOR_K, c_a=c_a)
        q_s = ship.dynamic_pressure * ship.wetted_surface
        rows.append({
            "c_a": c_a,
            "r_ref": ref,
            "r_ts": ex.r_ts,
            "r_a_term": ex.r_a,
            "r_a_expected": q_s * c_a,
            "err_pct": 100.0 * (ex.r_ts - ref) / ref,
        })
    return {"rows": rows,
            "err_pct_spread": max(r["err_pct"] for r in rows) - min(r["err_pct"] for r in rows)}


def _propeller_case(name: str, *, diameter: float, chord: float, tc: float, p_d: float,
                    z: float, kt: float, kq: float, n_ship: float, v_a_ship: float,
                    lambdas=LAMBDAS) -> dict:
    rows = []
    for lname, lam in lambdas.items():
        n_model = n_ship / math.sqrt(lam)          # Froude: n ~ lambda^-1/2
        v_a_model = v_a_ship * math.sqrt(lam)      # V_A ~ lambda^1/2
        # the model propeller is geometrically scaled: chord and D shrink by lambda
        re_m = se.propeller_re_co(chord * lam, v_a_model, n_model, diameter * lam, NU_M)
        re_s = se.propeller_re_co(chord, v_a_ship, n_ship, diameter, NU_S)
        out = se.propeller_scale_effect(
            kt, kq,
            chord=chord, thickness_ratio=tc, pitch_ratio=p_d, n_blades=z,
            diameter=diameter, re_co_model=re_m, re_co_ship=re_s,
            advance_ratio=0.7,
        )
        out_none = se.propeller_scale_effect(
            kt, kq,
            chord=chord, thickness_ratio=tc, pitch_ratio=p_d, n_blades=z,
            diameter=diameter, re_co_model=re_m, re_co_ship=re_s,
            advance_ratio=0.7, torque_model="none",
        )
        rows.append({
            "scale": lname, "lambda": lam, "n_model": n_model, "v_a_model": v_a_model,
            "chord_model": chord * lam, "diameter_model": diameter * lam,
            "re_co_model": re_m, "re_co_ship": re_s,
            **out,
            "eta_rel_change_upper_bound": out_none["eta_rel_change"],
        })
    return {
        "name": name,
        "geometry": {"D": diameter, "c_075": chord, "t_c": tc, "P_D": p_d,
                     "Z": z, "n_ship": n_ship, "V_A_ship": v_a_ship, "K_T": kt, "K_Q": kq},
        "rows": rows,
    }


def propeller_block() -> dict:
    """ITTC-1978 open-water scale correction along lambda, two hull classes."""
    ship = _propeller_case(
        "large_ship_propeller", diameter=6.0, chord=1.5, tc=0.05, p_d=0.95, z=5.0,
        kt=0.25, kq=0.035, n_ship=6.0, v_a_ship=8.0,
    )
    usv = _propeller_case(
        "usv_propeller", diameter=0.25, chord=0.05, tc=0.05, p_d=1.0, z=4.0,
        kt=0.25, kq=0.035, n_ship=25.0, v_a_ship=2.4,
    )
    return {
        "re_co_valid_band": [se.SECTION_DRAG_RE_MIN, se.SECTION_DRAG_RECOMMENDED_MIN],
        "large_ship_propeller": ship,
        "usv_propeller": usv,
        "usv_all_scales_in_range": all(r["cd_model_in_range"] for r in usv["rows"]),
        "ship_all_scales_in_range": all(r["cd_model_in_range"] for r in ship["rows"]),
    }


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------

def figure_cf_lines(case_usv: dict, path: Path) -> str:
    re_grid = np.logspace(4, 9.5, 400)
    fig, ax = plt.subplots(figsize=(7.2, 5.0))
    ax.loglog(re_grid, [se.ittc1957_cf(r) for r in re_grid], "k-", lw=2.0, label="ITTC-1957 correlation line")
    ax.loglog(re_grid, [se.schoenherr_cf(r) for r in re_grid], "b--", lw=1.6, label="Schoenherr / ATTC-1947")
    ax.loglog(re_grid, [se.prandtl_schlichting_cf(r) for r in re_grid], "g-.", lw=1.6, label="Prandtl-Schlichting 0.455/(logRe)^2.58")
    ax.loglog(re_grid, [se.schlichting_plate_cf(r) for r in re_grid], "m:", lw=1.6, label="Schlichting w/ transition (Re_c=5e5)")
    ax.loglog(re_grid, [se.low_re_friction_cf(r) for r in re_grid], "r-", lw=2.2, label="transition-aware composite (this work)")
    ax.loglog(re_grid, [se.blasius_cf(r) for r in re_grid], "c:", lw=1.2, label="Blasius laminar 1.328/sqrt(Re)")
    ax.axvspan(3e5, 1e6, color="orange", alpha=0.10, label="transition band 3e5-1e6")
    row = next(r for r in case_usv["rows"] if abs(r["fr"] - 0.25) < 1e-12)
    for s in row["scales"]:
        ax.axvline(s["re_model"], color="0.4", ls=":", lw=1.0)
        ax.annotate(f"model 1/{round(1/s['lambda'])}", (s["re_model"], 7e-3),
                    rotation=90, fontsize=7, color="0.35", va="top")
    ax.axvline(row["re_ship"], color="k", ls="--", lw=1.0)
    ax.annotate("ship (10 m USV)", (row["re_ship"], 7e-3), rotation=90, fontsize=7, va="top")
    ax.set_xlabel("Re = V L / nu")
    ax.set_ylabel("C_F")
    ax.set_title("Friction lines and the three model Reynolds numbers (Fr = 0.25)")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=7.5, loc="lower left")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path.name


def figure_ts_vs_fr(case: dict, path: Path, title: str) -> str:
    fig, ax = plt.subplots(figsize=(7.2, 5.0))
    frs = [r["fr"] for r in case["rows"]]
    ax.plot(frs, [r["r_ref_schoenherr"] for r in case["rows"]], "k-", lw=2.2,
            label="reference (Schoenherr friction + Michell wave)")
    ax.plot(frs, [r["r_wave_ship"] for r in case["rows"]],
            "0.5", ls=":", lw=1.2, label="wave part (Michell)")
    styles = {"0.100": ("o", "tab:blue", "1/10"), "0.040": ("s", "tab:green", "1/25"), "0.020": ("^", "tab:red", "1/50")}
    for key, (marker, color, lab) in styles.items():
        ys = []
        for r in case["rows"]:
            s = next(s for s in r["scales"] if f"{s['lambda']:.3f}" == key)
            ys.append(s["variants"]["physical_model_line"]["r_ts"])
        ax.plot(frs, ys, marker, color=color, ms=6, mfc="none", lw=0, label=f"extrapolated from lambda={lab} (recommended path)")
    ax.set_xlabel("Froude number Fn")
    ax.set_ylabel("full-scale total resistance R_TS [N]")
    ax.set_title(title)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7.5)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path.name


def figure_error_vs_lambda(cases: dict, path: Path) -> str:
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.6), sharey=True)
    variants = [("ittc57_both_sides", "baseline: ITTC-57 both sides", "tab:red"),
                ("auto_switch", "switch at Re_M<1e6", "tab:blue"),
                ("physical_model_line", "recommended: physical model line", "tab:green"),
                ("tripped_ittc57_both", "tripped data, ITTC-57 both", "tab:orange"),
                ("delta_cf_mission_sign", "mission-literal +DeltaC_F", "tab:purple")]
    for ax, (cname, case) in zip(axes, cases.items()):
        frs = CHECK_FR
        x = np.arange(len(frs))
        width = 0.2
        for i, (key, lab, color) in enumerate(variants):
            vals = []
            for fr in frs:
                r = next(r for r in case["rows"] if abs(r["fr"] - fr) < 1e-12)
                errs = [s["variants"][key]["err_pct_vs_schoenherr_ref"] for s in r["scales"]]
                vals.append(max(abs(e) for e in errs))
            ax.bar(x + (i - 1.5) * width, vals, width, label=lab, color=color, alpha=0.85)
        ax.set_xticks(x)
        ax.set_xticklabels([f"Fn={fr}" for fr in frs])
        ax.axhline(5.0, color="k", ls="--", lw=1.0)
        ax.text(0.02, 5.4, "5 % acceptance", fontsize=8)
        ax.set_yscale("log")
        ax.set_title(f"{cname} (worst of lambda=1/10,1/25,1/50)")
        ax.set_ylabel("|error| vs reference [%]")
        ax.grid(alpha=0.3, axis="y")
    axes[0].legend(fontsize=7.5)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path.name


def figure_propeller(prop: dict, path: Path) -> str:
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.4))
    for ax, key, title in zip(
        axes,
        ("large_ship_propeller", "usv_propeller"),
        ("large ship propeller (D = 6.0 m, n = 6 rps) -- in range",
         "10 m USV propeller (D = 0.25 m, n = 25 rps) -- OUT_OF_RANGE"),
    ):
        blk = prop[key]
        rows = blk["rows"]
        lams = [1.0 / r["lambda"] for r in rows]
        ax.plot(lams, [r["delta_cd"] * 1e3 for r in rows], "o-", color="tab:blue",
                label="Delta C_D x1e3")
        ax.plot(lams, [-r["delta_kt"] * 1e4 for r in rows], "s-", color="tab:red",
                label="-Delta K_T x1e4")
        ax.plot(lams, [r["eta_rel_change_upper_bound"] * 100 for r in rows], "^-",
                color="tab:green", label="Delta eta_0 upper bound [%]")
        for r, x in zip(rows, lams):
            if not r["cd_model_in_range"]:
                ax.axvspan(x * 1.12, x * 0.88, color="red", alpha=0.12)
                ax.annotate("Re_co,M out\nof band", (x, 0.0), fontsize=7, color="tab:red",
                            ha="center", va="bottom")
        ax.set_xscale("log")
        ax.set_xlabel("model scale 1/lambda")
        ax.set_ylabel("quantity (scaled as labelled)")
        ax.set_title(title, fontsize=9)
        ax.grid(True, which="both", alpha=0.3)
        ax.legend(fontsize=7.5)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path.name


def b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def main() -> int:
    figdir = HERE / "figures"
    figdir.mkdir(parents=True, exist_ok=True)
    cases = {name: run_case(name, spec) for name, spec in CASES.items()}

    summary = {}
    for name, case in cases.items():
        per_variant = {}
        for variant in ("ittc57_both_sides", "auto_switch", "physical_model_line",
                        "delta_cf_mission_sign", "tripped_physical_line",
                        "tripped_ittc57_both", "tripped_ps_model_line"):
            errs_abs = []
            errs_signed = []
            for r in case["rows"]:
                for s in r["scales"]:
                    e = s["variants"][variant]["err_pct_vs_schoenherr_ref"]
                    errs_abs.append(abs(e))
                    errs_signed.append(e)
            spreads = {f"Fn={fr}": spread_block(case, fr, variant) for fr in CHECK_FR}
            per_variant[variant] = {
                "max_abs_err_pct": max(errs_abs),
                "mean_abs_err_pct": sum(errs_abs) / len(errs_abs),
                "max_abs_err_pct_check_fr": max(
                    abs(s["variants"][variant]["err_pct_vs_schoenherr_ref"])
                    for r in case["rows"] if r["fr"] in CHECK_FR for s in r["scales"]
                ),
                "signed_err_pct_check_fr": [
                    s["variants"][variant]["err_pct_vs_schoenherr_ref"]
                    for r in case["rows"] if r["fr"] in CHECK_FR for s in r["scales"]
                ],
                "spread_check_fr": spreads,
                "max_spread_pct_check_fr": max(v["spread_pct"] for v in spreads.values()),
            }
        summary[name] = per_variant

    results = {
        "mission": "m9028",
        "node": "p234w2-n02-scale-law-maturity",
        "environment": {
            "temp_c": TEMP_C, "salinity_ppt": SALINITY,
            "rho_ship": RHO_S, "nu_ship": NU_S, "rho_model": RHO_M, "nu_model": NU_M,
            "form_factor_k": FORM_FACTOR_K, "measurement_noise": NOISE,
            "lambdas": LAMBDAS,
        },
        "cf_lines": cf_line_tables(),
        "asymptotic": asymptotic_block(),
        "identity_ittc78_vs_delta_cf": identity_block(),
        "correlation_allowance_sweep": ca_sweep_block(),
        "propeller": propeller_block(),
        "cases": cases,
        "summary": summary,
    }

    figs = []
    figs.append(figure_cf_lines(cases["usv_wigley_L10"], figdir / "fig1_cf_lines.png"))
    figs.append(figure_ts_vs_fr(cases["usv_wigley_L10"], figdir / "fig2_usv_RTS.png",
                               "10 m USV Wigley: extrapolated R_TS vs reference"))
    figs.append(figure_ts_vs_fr(cases["classical_wigley_L100"], figdir / "fig2b_L100_RTS.png",
                               "100 m Wigley: extrapolated R_TS vs reference"))
    figs.append(figure_error_vs_lambda(cases, figdir / "fig3_error_vs_variant.png"))
    figs.append(figure_propeller(results["propeller"], figdir / "fig4_propeller_scaling.png"))
    results["figures"] = figs

    (HERE / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(json.dumps({k: {kk: round(vv["max_abs_err_pct"], 3) if isinstance(vv, dict) and "max_abs_err_pct" in vv else vv
                          for kk, vv in v.items()} for k, v in summary.items()}, indent=2))
    print("figures:", figs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())