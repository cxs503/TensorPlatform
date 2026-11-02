#!/usr/bin/env python3
"""Cross-implementation audit of the platform's level-ice resistance models.

Three implementations in this repository all claim the same physics:

  A  backend/services/ice_resistance_uq.py  ``_lindqvist_raw`` / ``lindqvist_ice_resistance``
     (Lindqvist-form decomposition + one-point per-ship calibration factor)
  B  optimizer/ice_class/objectives.py       ``evaluate_ice_resistance``
     ("Lindqvist 1989" heuristic used by the ice-class NSGA-II design optimiser)
  C  backend/services/polar/resistance.py    ``ice_components``
     (first-principles decomposition; production path of the polar voyage optimiser)
  R  backend/services/ice_canon.py           ``ice_components``
     (reference re-derivation appended by this audit; analytic anchors only)

The audit does not treat agreement between implementations as truth.  It measures,
for each implementation, whether the scaling laws that follow from the
derivations hold:

  K1  total h-exponent must lie in [1.00, 2.00]  (mixture of h^2 / h^1.25 / h^1)
  K3  submersion h-exponent == 1.00 +/- 0.02 and dR/dT > 0
  K4  ice strength must be an input:            dR/dsigma_c > 0
  K5  friction must increase resistance:        dR/dmu > 0
  K6  speed must increase resistance:           dR/dV > 0
  K7  beam must increase resistance:            dR/dB > 0
and, term-wise where a breakdown is exposed (C, R):
  K1t crushing h^2, K2t breaking h^1.25, K3t submersion h^1, K2b breaking ~ B^1

Two "validation by construction" risks are quantified:
  C1  a one-point calibration factor makes the nominal residual exactly zero
  C2  that factor is a rigid scale and cannot repair an exponent error

Outputs: outputs/ice_crosscheck/ice_crosscheck.json + four PNG figures.
Run:  python3 docs/ice_crosscheck/audit_ice_resistance.py
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Callable, Dict, List

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "backend"))
sys.path.insert(0, REPO)

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from optimizer.ice_class import objectives as OPT  # noqa: E402
from services import ice_canon as CANON  # noqa: E402
from services import ice_resistance_uq as UQ  # noqa: E402
from services.polar import resistance as POLAR  # noqa: E402
from services.polar.ships import ShipGeometry  # noqa: E402

OUT = os.path.join(REPO, "outputs", "ice_crosscheck")
os.makedirs(OUT, exist_ok=True)

# ---------------------------------------------------------------------------
# Common test hull (a PC6-ish general cargo ship) and reference conditions
# ---------------------------------------------------------------------------
BASE: Dict[str, float] = {
    "L": 150.0, "B": 23.0, "T": 8.5, "Cb": 0.70,
    "alpha_deg": 30.0, "alpha_stem_deg": 20.0,
    "h": 0.8, "sigma": 1.5e6, "mu": 0.10, "v": 2.0,
}

H_GRID = [0.20, 0.30, 0.40, 0.60, 0.80, 1.00, 1.20, 1.50]
SIGMA_GRID = [0.5e6, 1.0e6, 1.5e6, 2.0e6, 2.5e6]
MU_GRID = [0.02, 0.05, 0.10, 0.15, 0.25]
V_GRID = [0.0, 0.5, 1.0, 2.0, 3.0]
B_GRID = [14.0, 18.0, 23.0, 28.0, 34.0]
T_GRID = [5.0, 6.5, 8.5, 10.5, 12.0]


def state(**over: float) -> Dict[str, float]:
    s = dict(BASE)
    s.update(over)
    return s


def polar_ship(st: Dict[str, float]) -> ShipGeometry:
    return ShipGeometry(
        key="audit", name="Audit hull", category="general_cargo",
        length_m=st["L"], beam_m=st["B"], draft_m=st["T"], cb=st["Cb"],
        dwt_t=14000.0, design_speed_kn=14.0, brake_power_kw=8000.0,
        sfoc_g_kwh=180.0, eta_propulsive=0.65,
        bow_half_angle_deg=st["alpha_deg"], ice_class="1A", h_max_m=1.2,
        mu_hull_ice=st["mu"],
    )


# ---------------------------------------------------------------------------
# Adapters: one signature per implementation, force in newton
# ---------------------------------------------------------------------------
def total_a(st: Dict[str, float]) -> float:
    """A raw (calibration_factor = 1): ice_resistance_uq total [N]."""
    ship = UQ.ShipIceParams(name="audit", length_m=st["L"], beam_m=st["B"],
                            draft_m=st["T"], cb=st["Cb"],
                            bow_angle_deg=st["alpha_deg"],
                            displacement_t=14000.0, calibration_factor=1.0)
    return 1e3 * UQ._lindqvist_raw(ship, st["h"], st["sigma"] / 1e6, st["mu"], st["v"])


def total_b(st: Dict[str, float]) -> float:
    """B: optimizer heuristic total [N].  sigma_c and mu are not inputs."""
    kn = OPT.evaluate_ice_resistance(
        L=st["L"], B=st["B"], T=st["T"], Cb=st["Cb"],
        alpha_bow=st["alpha_deg"], alpha_stem=st["alpha_stem_deg"],
        h_ice=st["h"], V_kn=st["v"] / 0.5144)
    return 1e3 * kn


def total_c(st: Dict[str, float]) -> float:
    """C: polar production first-principles total [N]."""
    return POLAR.ice_components(polar_ship(st), st["v"], st["h"], st["sigma"] / 1e6)["total"]


def total_r(st: Dict[str, float]) -> float:
    """R: reference decomposition [N]."""
    return CANON.ice_components(
        length_m=st["L"], beam_m=st["B"], draft_m=st["T"], cb=st["Cb"],
        bow_angle_deg=st["alpha_deg"], h_m=st["h"], sigma_c_pa=st["sigma"],
        mu=st["mu"], v_ms=st["v"]).total_n


def terms_c(st: Dict[str, float]) -> Dict[str, float]:
    return POLAR.ice_components(polar_ship(st), st["v"], st["h"], st["sigma"] / 1e6)


def terms_r(st: Dict[str, float]) -> Dict[str, float]:
    c = CANON.ice_components(
        length_m=st["L"], beam_m=st["B"], draft_m=st["T"], cb=st["Cb"],
        bow_angle_deg=st["alpha_deg"], h_m=st["h"], sigma_c_pa=st["sigma"],
        mu=st["mu"], v_ms=st["v"])
    return {"crushing": c.crushing_n, "breaking": c.breaking_n,
            "submersion": c.submersion_n, "total": c.total_n}


IMPLS: Dict[str, Callable[[Dict[str, float]], float]] = {
    "A_ice_resistance_uq_raw": total_a,
    "B_optimizer_objectives": total_b,
    "C_polar_production": total_c,
    "R_reference_canon": total_r,
}


# ---------------------------------------------------------------------------
# Numerics
# ---------------------------------------------------------------------------
def loglog_slope(fn: Callable[[float], float], values: List[float]) -> float:
    xs = np.log(np.asarray(values, dtype=float))
    ys = np.log(np.asarray([max(fn(v), 1e-30) for v in values], dtype=float))
    return float(np.polyfit(xs, ys, 1)[0])


def deriv(fn: Callable[[Dict[str, float]], float], knob: str, x0: float, dx: float) -> float:
    a = fn(state(**{knob: x0 + dx}))
    b = fn(state(**{knob: x0 - dx}))
    return (a - b) / (2.0 * dx)


def exponent(fn: Callable[[Dict[str, float]], float], knob: str, grid: List[float]) -> float:
    return loglog_slope(lambda value: fn(state(**{knob: value})), grid)


# ---------------------------------------------------------------------------
# Criteria
# ---------------------------------------------------------------------------
def aggregate_criteria() -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []

    def add(impl: str, crit: str, measured, target: str, ok: bool, note: str = "") -> None:
        rows.append({"impl": impl, "criterion": crit, "measured": measured,
                     "target": target, "pass": bool(ok), "note": note})

    for name, fn in IMPLS.items():
        e_h = exponent(fn, "h", H_GRID)
        add(name, "K1 total h-exponent in [1.00, 2.00]", round(e_h, 4), "[1,2]",
            1.0 <= e_h <= 2.0, "mixture of h^2, h^1.25, h^1 terms")

        # isolate the submersion component: sigma_c -> 0 kills crushing+breaking
        sub_fn = lambda st: fn({**st, "sigma": 1e-6, "v": 0.0})  # noqa: E731
        e_h_sub = exponent(sub_fn, "h", H_GRID)
        add(name, "K3 submersion h-exponent", round(e_h_sub, 4), "1.00+/-0.02",
            abs(e_h_sub - 1.0) <= 0.02, "isolated by sigma_c->0, V->0")

        d_t = deriv(sub_fn, "T", BASE["T"], 1e-2)
        add(name, "K3 submersion dR/dT", f"{d_t:.4g} N/m", "> 0", d_t > 1e-6,
            "draft must raise the submerged bow area")

        d_sigma = deriv(fn, "sigma", BASE["sigma"], 1e4)
        add(name, "K4 dR/dsigma_c", f"{d_sigma:.4g} N/Pa", "> 0", d_sigma > 0,
            "ice strength must be an input")

        d_mu = deriv(fn, "mu", BASE["mu"], 1e-4)
        add(name, "K5 dR/dmu", f"{d_mu:.4g} N/-", "> 0", d_mu > 0)

        d_v = deriv(fn, "v", BASE["v"], 1e-4)
        add(name, "K6 dR/dV", f"{d_v:.4g} N/(m/s)", "> 0", d_v > 0)

        d_b = deriv(fn, "B", BASE["B"], 1e-3)
        add(name, "K7 dR/dB", f"{d_b:.4g} N/m", "> 0", d_b > 0)

        # K8: the classical decomposition puts the whole speed dependence in the
        # submersion (added-mass) component, 1 + C_v*V/sqrt(g*h).  At h = 0.8 m
        # and C_v = 0.5 that is +13.2 % going from V = 2 to 3 m/s *on that
        # component*.  It is measured on the sigma_c -> 0 isolant, because on the
        # total the crushing term dominates and masks it (see report note).
        s2 = fn({**state(v=2.0), "sigma": 1e-6})
        s3 = fn({**state(v=3.0), "sigma": 1e-6})
        speed_resp = s3 / s2 - 1.0
        add(name, "K8 submersion speed response V:2->3 m/s", round(speed_resp, 4),
            "0.06 .. 0.20 (analytic 0.132)", 0.06 <= speed_resp <= 0.20,
            "added-mass term (1 + C_v V/sqrt(g h)), C_v = 0.5")

        # K9: magnitude envelope against the reference decomposition
        ratio = fn(state()) / total_r(state())
        add(name, "K9 total / reference at audit point", round(ratio, 4), "[0.33, 3.0]",
            1.0 / 3.0 <= ratio <= 3.0, "order-of-magnitude sanity band")
    return rows


def term_criteria() -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    for name, tf in (("C_polar_production", terms_c), ("R_reference_canon", terms_r)):
        for term, target, tol in (("crushing", 2.0, 0.03), ("breaking", 1.25, 0.03),
                                  ("submersion", 1.0, 0.02)):
            # the submersion term is isolated at V = 0: both implementations fold
            # the added-mass factor (1 + C_v V/sqrt(g h)) into it, which carries
            # an h^-0.5 of its own and must not be counted as the buoyancy power.
            v0 = 0.0 if term == "submersion" else BASE["v"]
            s = exponent(lambda st: tf({**st, "v": v0})[term], "h", H_GRID)
            out.append({"impl": name, "term": term, "knob": "h", "measured": round(s, 4),
                        "analytic": target, "rel_err": round(abs(s - target) / target, 5),
                        "pass": abs(s - target) <= tol * target})
        s_b = exponent(lambda st: tf(st)["breaking"], "B", B_GRID)
        out.append({"impl": name, "term": "breaking", "knob": "B", "measured": round(s_b, 4),
                    "analytic": 1.0, "rel_err": round(abs(s_b - 1.0), 5),
                    "pass": abs(s_b - 1.0) <= 0.03})
    return out


# ---------------------------------------------------------------------------
# Absolute level at the in-repo full-scale trial references
# ---------------------------------------------------------------------------
def absolute_comparison() -> Dict[str, object]:
    trials: Dict[str, object] = {}
    for ship_name, rec in UQ.SHIP_TRIAL_DATA.items():
        p = rec["params"]
        st = state(L=p.length_m, B=p.beam_m, T=p.draft_m, Cb=p.cb,
                   alpha_deg=p.bow_angle_deg, h=rec["nominal_ice_thickness_m"],
                   sigma=rec["nominal_ice_strength_MPa"] * 1e6,
                   mu=rec["nominal_friction_coeff"], v=rec["nominal_speed_ms"])
        cal = p.calibration_factor
        raw = total_a(st)
        c = total_c(st)
        r = total_r(st)
        b = total_b(st)
        trial_kn = rec["mean_resistance_kN"]
        trials[ship_name] = {
            "h_m": st["h"], "sigma_mpa": st["sigma"] / 1e6, "mu": st["mu"], "v_ms": st["v"],
            "trial_mean_kN": trial_kn,
            "A_calibration_factor": round(cal, 4),
            "A_raw_kN": round(raw / 1e3, 1),
            "A_calibrated_kN": round(raw / 1e3 * cal, 1),
            "B_optimizer_kN": round(b / 1e3, 1),
            "C_polar_kN": round(c / 1e3, 1),
            "R_canon_kN": round(r / 1e3, 1),
            "C_vs_trial_rel": round(c / 1e3 / trial_kn - 1.0, 4),
            "R_vs_trial_rel": round(r / 1e3 / trial_kn - 1.0, 4),
            "B_over_C": round(b / c, 4),
            "A_raw_over_C": round(raw / c, 4),
        }
    return trials


def calibration_analysis() -> Dict[str, object]:
    rows: Dict[str, object] = {}
    for ship_name, rec in UQ.SHIP_TRIAL_DATA.items():
        p = rec["params"]
        h0, s0, m0, v0 = (rec["nominal_ice_thickness_m"], rec["nominal_ice_strength_MPa"],
                          rec["nominal_friction_coeff"], rec["nominal_speed_ms"])
        R_ref = rec["mean_resistance_kN"]
        cal = p.calibration_factor

        def model(h: float, v: float) -> float:
            # UQ._lindqvist_raw already returns kN
            return UQ._lindqvist_raw(p, h, s0, m0, v) * cal

        nominal = model(h0, v0)
        off = {}
        for label, h, v in (("h=0.5h_nom", 0.5 * h0, v0), ("h=1.5h_nom", 1.5 * h0, v0),
                            ("V=0.5V_nom", h0, 0.5 * v0), ("V=1.5V_nom", h0, 1.5 * v0)):
            val = model(h, v)
            # the calibrated model's own scaling is compared with the analytic
            # first-order expectation (h scaling of the mixture, V linear)
            off[label] = {"A_kN": round(val, 1),
                          "ratio_to_nominal": round(val / nominal, 4)}
        rows[ship_name] = {
            "nominal_residual_rel": round(nominal / R_ref - 1.0, 12),
            "calibration_factor": round(cal, 4),
            "calibration_is_rigid_scale": True,
            "off_nominal": off,
        }
    return rows


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def make_figures(trials: Dict[str, object], term_rows: List[Dict[str, object]]) -> List[str]:
    figs: List[str] = []

    fig, ax = plt.subplots(figsize=(7.2, 5.0))
    for name, fn in IMPLS.items():
        ys = [fn(state(h=h)) / 1e3 for h in H_GRID]
        ax.loglog(H_GRID, ys, "o-", lw=1.8, ms=4, label=name)
    ax.set_xlabel("level-ice thickness $h$ [m]")
    ax.set_ylabel("level-ice resistance [kN]")
    ax.set_title("Level-ice resistance on a common hull\n"
                 f"L={BASE['L']:.0f} m, B={BASE['B']:.0f} m, T={BASE['T']:.1f} m, "
                 f"$\\sigma_c$={BASE['sigma']/1e6:.1f} MPa, V={BASE['v']} m/s")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=8)
    f = os.path.join(OUT, "fig1_total_vs_h.png")
    fig.tight_layout(); fig.savefig(f, dpi=150); plt.close(fig); figs.append(f)

    fig, ax = plt.subplots(figsize=(8.0, 4.4))
    labels = [f"{r['impl'].split('_')[0]}\n{r['term']}~{r['knob']}" for r in term_rows]
    x = np.arange(len(labels))
    ax.bar(x - 0.2, [r["measured"] for r in term_rows], 0.4, label="measured slope",
           color="#3b6ea5")
    ax.bar(x + 0.2, [r["analytic"] for r in term_rows], 0.4, label="analytic anchor",
           color="#c8a45c", alpha=0.85)
    ax.set_xticks(x); ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=7)
    ax.set_ylabel("log-log slope")
    ax.set_title("Term-wise scaling exponents vs analytic anchors (C and R)")
    ax.legend(fontsize=8); ax.grid(True, axis="y", alpha=0.3)
    f = os.path.join(OUT, "fig2_exponents.png")
    fig.tight_layout(); fig.savefig(f, dpi=150); plt.close(fig); figs.append(f)

    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.2), sharey=True)
    hg = [0.3, 0.6, 0.9, 1.2, 1.5]
    for ax, (which, tf) in zip(axes, (("C_polar_production", terms_c),
                                      ("R_reference_canon", terms_r))):
        comp = [tf(state(h=h)) for h in hg]
        ax.stackplot(hg, [c["crushing"] / 1e3 for c in comp],
                     [c["breaking"] / 1e3 for c in comp],
                     [c["submersion"] / 1e3 for c in comp],
                     labels=["crushing", "breaking", "submersion"],
                     colors=["#3b6ea5", "#c8a45c", "#7a9e6b"], alpha=0.9)
        ax.set_title(which, fontsize=9); ax.set_xlabel("$h$ [m]"); ax.grid(True, alpha=0.25)
    axes[0].set_ylabel("resistance [kN]"); axes[0].legend(fontsize=8, loc="upper left")
    f = os.path.join(OUT, "fig3_components.png")
    fig.tight_layout(); fig.savefig(f, dpi=150); plt.close(fig); figs.append(f)

    fig, ax = plt.subplots(figsize=(8.0, 4.4))
    names = list(trials)
    keys = [("trial_mean_kN", "trial mean", "#222222"),
            ("A_calibrated_kN", "A calibrated", "#b5651d"),
            ("A_raw_kN", "A raw", "#e0a060"),
            ("B_optimizer_kN", "B optimizer", "#8c3b3b"),
            ("C_polar_kN", "C polar", "#3b6ea5"),
            ("R_canon_kN", "R reference", "#7a9e6b")]
    w = 0.14
    x = np.arange(len(names))
    for i, (k, lab, col) in enumerate(keys):
        ax.bar(x + (i - 2.5) * w, [trials[n][k] for n in names], w, label=lab, color=col)
    ax.set_yscale("log"); ax.set_xticks(x); ax.set_xticklabels(names)
    ax.set_ylabel("resistance [kN] (log scale)")
    ax.set_title("Absolute level at the in-repo full-scale trial references")
    ax.legend(fontsize=8, ncol=3); ax.grid(True, axis="y", which="both", alpha=0.25)
    f = os.path.join(OUT, "fig4_absolute.png")
    fig.tight_layout(); fig.savefig(f, dpi=150); plt.close(fig); figs.append(f)
    return figs


def main() -> int:
    agg = aggregate_criteria()
    terms = term_criteria()
    trials = absolute_comparison()
    cal = calibration_analysis()
    figs = make_figures(trials, terms)

    payload = {"hull": {k: BASE[k] for k in ("L", "B", "T", "Cb", "alpha_deg")},
               "reference_conditions": {"h": BASE["h"], "sigma_MPa": BASE["sigma"] / 1e6,
                                        "mu": BASE["mu"], "V": BASE["v"]},
               "aggregate_criteria": agg, "term_criteria": terms,
               "absolute": trials, "calibration": cal,
               "figures": [os.path.basename(f) for f in figs]}
    path = os.path.join(OUT, "ice_crosscheck.json")
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)

    n_all = len(agg) + len(terms)
    fails = [r for r in agg if not r["pass"]] + [r for r in terms if not r["pass"]]
    print(f"criteria: {n_all}   FAIL: {len(fails)}")
    for r in fails:
        print("  FAIL", r["impl"], r.get("criterion") or f"{r['term']}~{r['knob']}",
              r.get("measured"))
    print("json ->", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())