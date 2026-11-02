"""Seakeeping verification audit: live endpoint (:8001) vs repository STF kernel.

Two independent verdicts are produced.

V1  LIVE endpoint  ``POST /api/v1/seakeeping/rao``
    Degeneracy tests that need no model at all: are the three degree-of-freedom
    arrays distinct, and does the answer react to the inputs that must change the
    physics (heading, speed, wave frequency, block coefficient)?

V2  REPOSITORY kernel ``backend/api/seakeeping_physics.py`` (imported offline)
    Analytic anchor suite A1-A9 of ``references_seakeeping``: dispersion, FK
    long-wave limits, high-frequency exponent, heading/excitation structure,
    encounter-frequency identity, natural-frequency closure, cross-endpoint
    closure, geometry gate, roll peak location.

Both verdicts and the live-vs-repository divergence are written to
``artifacts/audit_seakeeping.json`` and plotted to ``figs/``.

Usage:  python3 audit_seakeeping.py            # SHIP_API_BASE overrides :8001
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import requests  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import references_seakeeping as ref  # noqa: E402

OUT = HERE / "artifacts"
FIG = HERE / "figs"
OUT.mkdir(parents=True, exist_ok=True)
FIG.mkdir(parents=True, exist_ok=True)

BASE = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001/api/v1")
REPO = Path(__file__).resolve().parents[3]          # TensorPlatform worktree root
TOL = 1e-3                                          # 0.1 %: absorbs g = 9.81 vs 9.80665

#: Platform-rule-consistent reference hull: displacement closes Cb L B T rho.
HULL = dict(L=100.0, B=10.0, T=6.25, D=10.0, Cb=0.60, KG=7.0, GM=1.50, speed=0.0)
HULL["displacement"] = HULL["Cb"] * HULL["L"] * HULL["B"] * HULL["T"] * ref.RHO / 1000.0


def post(path, body, timeout=90):
    try:
        r = requests.post(f"{BASE}/{path}", json=body, timeout=timeout)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text[:400]
    except Exception as exc:  # pragma: no cover
        return -1, repr(exc)


# ---------------------------------------------------------------------------
# V1  live endpoint degeneracy
# ---------------------------------------------------------------------------
PERTURBATIONS = {
    "base": {"heading": 180.0, "speed": 0.0, "wave_frequency": 0.5, "Cb": 0.60},
    "heading_90": {"heading": 90.0},
    "speed_10kn": {"speed": 10.0},
    "omega_1p2": {"wave_frequency": 1.2},
    "Cb_0p80": {"Cb": 0.80},
}


def audit_live(res):
    curves, rec = {}, {"requests": {}, "status": {}}
    for label, over in PERTURBATIONS.items():
        body = dict(HULL)
        body["displacement"] = over.get("Cb", HULL["Cb"]) * HULL["L"] * HULL["B"] * HULL["T"] * ref.RHO / 1000.0
        body.update(over)
        code, data = post("seakeeping/rao", body)
        rec["status"][label] = code
        if code == 200 and isinstance(data, dict) and "heave_rao" in data:
            curves[label] = {k: data[k] for k in ("frequencies", "heave_rao", "pitch_rao", "roll_rao")
                             if k in data}
            rec["requests"][label] = {"keys": sorted(data.keys()), "n_points": len(data["frequencies"])}
        else:
            rec["requests"][label] = {"error": str(data)[:200]}
    rec["n_ok"] = len(curves)
    if "base" not in curves:
        res["V1_live"] = rec
        return curves
    base = curves["base"]
    rec["dof_heave_vs_pitch"] = ref.dof_degeneracy(base["heave_rao"], base["pitch_rao"])
    rec["dof_heave_vs_roll"] = ref.dof_degeneracy(base["heave_rao"], base["roll_rao"])
    rec["dof_pitch_vs_roll"] = ref.dof_degeneracy(base["pitch_rao"], base["roll_rao"])
    rec["response_keys"] = rec["requests"]["base"]["keys"]
    for dof in ("heave_rao", "pitch_rao", "roll_rao"):
        payloads = {k: v[dof] for k, v in curves.items() if dof in v}
        rec[f"input_sensitivity_{dof}"] = ref.input_degeneracy(payloads)
    rec["phase_columns_present"] = "heave_phase_deg" in rec["requests"]["base"]["keys"]
    rec["method_string_present"] = "method" in rec["requests"]["base"]["keys"]
    res["V1_live"] = rec
    return curves


# ---------------------------------------------------------------------------
# V2  repository kernel analytic anchors
# ---------------------------------------------------------------------------
def load_kernel():
    sys.path.insert(0, str(REPO / "backend" / "api"))
    import seakeeping_physics as sp  # noqa: E402

    return sp


def audit_repo(res, sp):
    rec = {"anchors": {}, "kernel_G": sp.G, "grid": list(np.round(sp.FREQ_GRID_DEFAULT, 6))}
    ship = sp.ship_from_request(dict(HULL))
    body = dict(HULL)
    freqs = list(sp.FREQ_GRID_DEFAULT)

    curve = sp.rao_curves(ship, heading_deg=180.0, speed_kn=0.0, freqs=freqs)
    nat = sp.natural_periods(ship)
    w = np.asarray(curve["frequencies"], float)
    hv = np.asarray(curve["heave_rao"], float)
    pt = np.asarray(curve["pitch_rao"], float)
    rl = np.asarray(curve["roll_rao"], float)

    # A1 dispersion / encounter frequency (identity, head seas at rest)
    we_kernel = np.asarray(curve["encounter_frequencies"], float)
    we_ref = np.array([ref.encounter_omega(x, 0.0, 180.0) for x in w])
    rec["anchors"]["A1_dispersion"] = {
        "max_rel_err": float(np.max(np.abs((we_kernel - we_ref) / we_ref))),
        "tolerance": 1e-9, "pass": bool(np.max(np.abs((we_kernel - we_ref) / we_ref)) < 1e-9),
        "note": "omega_e = omega - k U cos(beta) with k = omega^2/g, U = 0",
    }

    # A5 encounter frequency rises in head seas at speed
    c10 = sp.rao_curves(ship, heading_deg=180.0, speed_kn=10.0, freqs=freqs)
    we10 = np.asarray(c10["encounter_frequencies"], float)
    we10_ref = np.array([ref.encounter_omega(x, 10.0 * ref.KNOT, 180.0) for x in w])
    head_raise = float(np.min(we10 - w))
    rec["anchors"]["A5_encounter_head_seas"] = {
        "max_rel_err": float(np.max(np.abs((we10 - we10_ref) / we10_ref))),
        "min_omega_e_minus_omega_rad_s": head_raise,
        "pass": bool(np.max(np.abs((we10 - we10_ref) / we10_ref)) < 1e-9 and head_raise > 0.0),
        "note": "kernel uses abs(): the following-sea branch (omega_e <= 0) is folded, not flagged",
    }

    # A2 long-wave limits (k L << 1 end of the grid)
    i0 = int(np.argmin(w))
    c90 = sp.rao_curves(ship, heading_deg=90.0, speed_kn=0.0, freqs=freqs)
    lim = {
        "omega": float(w[i0]), "kL": float(ref.deep_water_k(w[i0]) * HULL["L"]),
        "heave_rao": float(hv[i0]), "pitch_rao": float(pt[i0]),
        "roll_rao_beam": float(c90["roll_rao"][i0]),
        "roll_rao_head": float(rl[i0]),
    }
    lim["heave_rel_err"] = ref.rel_err(lim["heave_rao"], 1.0)
    lim["pitch_rel_err"] = ref.rel_err(lim["pitch_rao"], 1.0)
    lim["roll_beam_rel_err"] = ref.rel_err(lim["roll_rao_beam"], 1.0)
    lim["pass"] = bool(all(abs(lim[k]) < 0.05 for k in
                           ("heave_rel_err", "pitch_rel_err", "roll_beam_rel_err")))
    lim["tolerance"] = 0.05
    lim["note"] = ("FK long-wave limit: heave -> 1 m/m, pitch -> 1 rad/(ka), roll -> sin(beta) rad/(ka); "
                   "kL at the grid floor is finite, hence the 5 % band")
    rec["anchors"]["A2_longwave_limit"] = lim

    # A3 retired: no clean algebraic high-frequency window (documented, not asserted)
    hi = w > 1.5
    rec["anchors_retired"] = {
        "A3_hf_decay": {
            "why": ("with the FK Smith factor exp(-kT) the excitation decays by exp(-kT) ~ exp(-5.7) "
                    "at 3 rad/s and the coupled heave-pitch terms re-inject energy, so the omega^-2 "
                    "reactive asymptote has no accessible window on the 0.05-3.0 rad/s grid"),
            "observed_loglog_slope_heave": ref.loglog_slope(w[hi], hv[hi]),
            "kT_at_grid_top": float(ref.deep_water_k(float(w.max())) * HULL["T"]),
            "verdict": "WITHHELD (no machine-checkable anchor; not counted as pass or fail)",
        }
    }

    # A4 heading structure: heave excitation has no beta factor, roll excitation ~ sin(beta)
    rec["anchors"]["A4_heading_structure"] = {
        "heave_max_rel_gap_180_vs_90": float(np.max(np.abs(hv - np.asarray(c90["heave_rao"], float))
                                                    / np.max(hv))),
        "roll_head_seas_max_rad_per_ka": float(np.max(rl)),
        "roll_beam_seas_max_rad_per_ka": float(np.max(c90["roll_rao"])),
        "pass": bool(np.max(rl) < 1e-9 and np.max(c90["roll_rao"]) > 0.1),
        "note": "f4 is proportional to sin(beta) => roll must vanish identically at 180 deg",
    }

    # A6 natural-frequency closure from the kernel's own reported added mass / stability
    mass = HULL["displacement"] * 1000.0
    aw = sp.sectional_geometry(HULL["L"], HULL["B"], HULL["T"], HULL["Cb"])["Aw"]
    t3_ref = ref.heave_natural_period(mass, nat["A33"], aw)
    t4_ref = ref.roll_natural_period(HULL["GM"], nat["k_xx_eff"])
    rec["anchors"]["A6_natural_period_closure"] = {
        "T3_kernel": nat["heave"], "T3_recomputed": t3_ref,
        "T3_rel_err": ref.rel_err(nat["heave"], t3_ref),
        "T4_kernel": nat["roll"], "T4_recomputed": t4_ref,
        "T4_rel_err": ref.rel_err(nat["roll"], t4_ref),
        "A33_over_mass": nat["A33"] / mass,
        "Aw_m2": aw, "k_xx_eff": nat["k_xx_eff"],
        "pass": bool(abs(ref.rel_err(nat["heave"], t3_ref)) < TOL
                     and abs(ref.rel_err(nat["roll"], t4_ref)) < TOL),
        "note": "T3 = 2pi sqrt((m+A33)/(rho g Aw)); T4 = 2pi k_xx / sqrt(g GM), k_xx in metres",
    }

    # A7 cross-endpoint closure: rao_curves vs motion_amplitudes at the same omega
    w_test = 0.8
    mo = sp.motion_amplitudes(ship, w_test, 180.0, 0.0, 1.0)
    row = sp.solve_rao(ship, w_test, 180.0, 0.0, 1.0)
    rec["anchors"]["A7_cross_endpoint"] = {
        "heave_curve": row["heave_rao"], "heave_motion": mo["heave_rao"],
        "heave_rel_err": ref.rel_err(mo["heave_rao"], row["heave_rao"]),
        "roll_head_seas": row["roll_rao"],
        "pass": bool(abs(ref.rel_err(mo["heave_rao"], row["heave_rao"])) < 1e-9
                     and row["roll_rao"] < 1e-9),
        "note": "/seakeeping/rao and /seakeeping/motion share solve_rao, so they must agree exactly",
    }

    # A8 geometry gate
    vol_in = HULL["displacement"] * 1000.0 / ref.RHO
    vol_geo = HULL["Cb"] * HULL["L"] * HULL["B"] * HULL["T"]
    rec["anchors"]["A8_geometry_gate"] = {
        "nabla_input_m3": vol_in, "Cb_L_B_T_m3": vol_geo,
        "rel_err": ref.rel_err(vol_geo, vol_in),
        "sectional_nabla_m3": sp.sectional_geometry(HULL["L"], HULL["B"], HULL["T"], HULL["Cb"])["nabla"],
        "pass": bool(abs(ref.rel_err(vol_geo, vol_in)) < 1e-3),
        "note": "platform rule: Cb/L/B/T must close before any resistance/RAO number is usable",
    }

    # A9 roll peak at the natural roll frequency
    w4 = ref.roll_peak_omega(HULL["GM"], nat["k_xx_eff"])
    rl_beam = np.asarray(c90["roll_rao"], float)
    peak_w = ref.peak_location(w, rl_beam)
    rec["anchors"]["A9_roll_peak"] = {
        "omega4_analytic": w4, "peak_omega_grid": peak_w,
        "grid_rel_err": ref.rel_err(peak_w, w4),
        "roll_peak_rad_per_ka": float(np.max(rl_beam)),
        "pass": bool(abs(ref.rel_err(peak_w, w4)) < 0.15),
        "note": "peak located on the kernel grid (40 log points, 0.05-3.0 rad/s); tolerance = one grid box",
    }

    # A10 roll peak magnification 1/(2 zeta) at the SDOF damped natural frequency
    zeta = float(sp.ZETA_ROLL)
    mag_ref = ref.roll_peak_magnification(zeta)
    rec["anchors"]["A10_roll_peak_magnification"] = {
        "zeta": zeta, "magnification_reference": mag_ref,
        "peak_measured_rad_per_ka": float(np.max(rl_beam)),
        "rel_err": ref.rel_err(float(np.max(rl_beam)), mag_ref),
        "pass": bool(abs(ref.rel_err(float(np.max(rl_beam)), mag_ref)) < 0.05),
        "note": "closed form 1/(2 zeta) = 6.25 for zeta = 0.08; finite-grid peak is a slight underestimate",
    }

    rec["n_pass"] = sum(1 for a in rec["anchors"].values() if a.get("pass"))
    rec["n_anchors"] = len(rec["anchors"])
    rec["curve_head_seas"] = {"omega": w.tolist(), "heave": hv.tolist(),
                              "pitch": pt.tolist(), "roll": rl.tolist()}
    rec["curve_beam_seas"] = {"omega": w.tolist(),
                              "roll": np.asarray(c90["roll_rao"], float).tolist()}
    res["V2_repo"] = rec
    return rec


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------
def make_figures(res, live_curves):
    paths = []
    repo = res.get("V2_repo") or {}
    ch = repo.get("curve_head_seas") or {}
    live = live_curves.get("base") or {}

    fig, ax = plt.subplots(1, 2, figsize=(12, 4.2))
    if ch:
        ax[0].semilogy(ch["omega"], ch["heave"], "o-", ms=3, label="repo kernel heave (m/m)")
        ax[0].semilogy(ch["omega"], ch["pitch"], "s-", ms=3, label="repo kernel pitch (rad/ka)")
    if live.get("frequencies"):
        ax[0].semilogy(live["frequencies"], live["heave_rao"], "k--", lw=1, label="LIVE :8001 heave")
    ax[0].set_xlabel(r"$\omega$ [rad/s]")
    ax[0].set_ylabel("RAO")
    ax[0].set_title("Heave/pitch RAO: repository kernel vs live endpoint")
    ax[0].grid(alpha=0.3, which="both")
    ax[0].legend(fontsize=8)

    if ch:
        ax[1].semilogy(ch["omega"], np.clip(ch["roll"], 1e-12, None), "^-", ms=3,
                       label="repo kernel roll, head seas (180°)")
        cb = repo.get("curve_beam_seas") or {}
        if cb:
            ax[1].semilogy(cb["omega"], cb["roll"], "v-", ms=3, label="repo kernel roll, beam seas (90°)")
    if live.get("frequencies"):
        ax[1].semilogy(live["frequencies"], live["roll_rao"], "k--", lw=1, label="LIVE :8001 roll")
    ax[1].set_xlabel(r"$\omega$ [rad/s]")
    ax[1].set_ylabel("roll RAO [rad/(ka)]")
    ax[1].set_title("Roll: sin(beta) excitation => identically 0 in head seas")
    ax[1].grid(alpha=0.3, which="both")
    ax[1].legend(fontsize=8)
    fig.tight_layout()
    p = FIG / "fig1_rao_repo_vs_live.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    paths.append(p)

    # anchor error bars
    anchors = repo.get("anchors") or {}
    labels, errs = [], []
    for key in ("A1_dispersion", "A5_encounter_head_seas", "A6_natural_period_closure"):
        a = anchors.get(key) or {}
        e = a.get("max_rel_err", a.get("T3_rel_err"))
        if e is not None:
            labels.append(key.split("_")[0] + " identity")
            errs.append(abs(e))
    a2 = anchors.get("A2_longwave_limit") or {}
    if a2:
        labels.append("A2 long-wave limit")
        errs.append(max(abs(a2["heave_rel_err"]), abs(a2["pitch_rel_err"]), abs(a2["roll_beam_rel_err"])))
    a3 = anchors.get("A3_hf_decay") or {}
    if a3:
        labels.append("A3 HF exponent")
        errs.append(abs(a3["heave_loglog_slope"] + 2.0) / 2.0)
    a9 = anchors.get("A9_roll_peak") or {}
    if a9:
        labels.append("A9 roll peak $\\omega_4$")
        errs.append(abs(a9["grid_rel_err"]))
    v1 = res.get("V1_live") or {}
    dd = v1.get("dof_heave_vs_pitch") or {}
    if dd:
        labels.append("V1 live heave==pitch")
        errs.append(max(dd["max_rel_gap"], 1e-12))

    fig, ax = plt.subplots(figsize=(8.5, 4.2))
    x = np.arange(len(labels))
    colors = ["tab:red" if lab.startswith("V1") else "tab:blue" for lab in labels]
    ax.bar(x, np.clip(errs, 1e-16, 1.0), color=colors)
    ax.set_yscale("log")
    ax.axhline(1e-3, color="green", ls="--", lw=1, label="0.1 % tolerance band")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=25, ha="right", fontsize=8)
    ax.set_ylabel("relative deviation from reference")
    ax.set_title("Verification anchors: repository kernel (blue) vs live degeneracy (red)")
    ax.grid(alpha=0.3, axis="y", which="both")
    ax.legend(fontsize=8)
    fig.tight_layout()
    p = FIG / "fig2_anchor_errors.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    paths.append(p)
    return paths


def main():
    res = {"api_base": BASE, "hull": HULL, "sections": {}}
    live_curves = {}
    try:
        live_curves = audit_live(res["sections"])
    except Exception as exc:  # pragma: no cover
        import traceback
        res["sections"]["V1_live"] = {"error": repr(exc), "tb": traceback.format_exc()[-800:]}
    try:
        sp = load_kernel()
        audit_repo(res["sections"], sp)
    except Exception as exc:  # pragma: no cover
        import traceback
        res["sections"]["V2_repo"] = {"error": repr(exc), "tb": traceback.format_exc()[-1200:]}
    res["figures"] = [str(p.relative_to(HERE)) for p in make_figures(res["sections"], live_curves)]
    (OUT / "audit_seakeeping.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))

    v1 = res["sections"].get("V1_live") or {}
    v2 = res["sections"].get("V2_repo") or {}
    print("V1 live status:", v1.get("status"))
    print("V1 keys:", v1.get("response_keys"), "| n_ok", v1.get("n_ok"))
    if v1.get("dof_heave_vs_pitch"):
        d = v1["dof_heave_vs_pitch"]
        print("V1 heave vs pitch: max_rel_gap=%.3e differing=%d" % (d["max_rel_gap"], d["n_differing"]))
        for dof in ("heave_rao", "pitch_rao", "roll_rao"):
            s = v1.get(f"input_sensitivity_{dof}") or {}
            print("   %-9s sensitive_to=%s invariant_to=%s" % (dof, s.get("sensitive_to"), s.get("invariant_to")))
    print("V2 anchors: %s/%s pass" % (v2.get("n_pass"), v2.get("n_anchors")))
    for k, a in (v2.get("anchors") or {}).items():
        for kk in ("max_rel_err", "heave_rel_err", "T3_rel_err", "grid_rel_err"):
            if kk in a:
                print("   %-26s %s=%+.3e pass=%s" % (k, kk, a[kk], a.get("pass")))
                break
        else:
            if "heave_loglog_slope" in a:
                print("   %-26s slope=%+.3f pass=%s" % (k, a["heave_loglog_slope"], a.get("pass")))
            else:
                print("   %-26s pass=%s" % (k, a.get("pass")))
    if v2.get("error"):
        print("V2 ERROR", v2["error"], v2.get("tb"))
    print("figures:", res["figures"])


if __name__ == "__main__":
    main()