#!/usr/bin/env python3
"""Audit the ship platform's structural domain against independent references.

Reads the recorded probe artifacts (no live API call: the audit is reproducible
offline) and emits

  artifacts/structure_validation.json   every check with numbers + verdict
  artifacts/structure_tables.md         markdown tables for the report
  artifacts/figures/*.png               figures for the report

Usage: python3 tools/validation/validate_structure.py
"""
from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import structure_reference as ref  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ART = os.path.join(ROOT, "artifacts")
FIG = os.path.join(ART, "figures")
TOL = 3.0  # % : the platform's stated acceptability threshold


def load(name):
    with open(os.path.join(ART, name)) as f:
        return json.load(f)


def mk(check_id, group, quantity, computed, reference, unit="MPa", tol=TOL, note=""):
    err = None
    if computed is not None and reference is not None and reference != 0:
        err = (computed - reference) / reference * 100.0
    verdict = "PASS" if (err is not None and abs(err) <= tol) else "FAIL"
    return {"id": check_id, "group": group, "quantity": quantity,
            "computed": computed, "reference": reference, "unit": unit,
            "rel_err_pct": None if err is None else round(err, 2),
            "tol_pct": tol, "verdict": verdict, "note": note}


# ---------------------------------------------------------------------------
# A. /api/v1/plate-stability/plate
# ---------------------------------------------------------------------------
def audit_plate_stability(probe):
    checks, rows = [], []
    E, nu, sy = 206e9, 0.3, 235.0

    for r in probe["plate"]:
        i, ec = r["input"], r.get("edge_condition")
        if r.get("critical_stress_a") is None:
            continue
        alpha = i["a"] / i["b"]
        b, t = i["b"], i["t"]
        k_served = r["critical_stress_a"] / ref.sigma_cr(1.0, E, t, b, nu)
        k_ref_ssss = ref.k_uniaxial_x(alpha)
        k_ref_clamped = ref.K_CLAMPED
        kb_ref = ref.k_uniaxial_y(alpha)
        kb_served = (r["critical_stress_b"] / ref.sigma_cr(1.0, E, t, b, nu)
                     if r.get("critical_stress_b") is not None else None)
        ks_ref = ref.k_shear(alpha)
        ks_served = (r["critical_shear"] / ref.sigma_cr(1.0, E, t, b, nu)
                     if r.get("critical_shear") is not None else None)
        beta = ref.plate_slenderness(b, t, sy, E / 1e6)
        ult_ref = ref.faulkner_ultimate(sy, beta)
        rows.append({
            "a_over_b": round(alpha, 4), "t_mm": round(t * 1000, 2),
            "edge_condition_input": ec,
            "k_served": round(k_served, 4), "k_ref_ssss": round(k_ref_ssss, 4),
            "k_ref_clamped": k_ref_clamped,
            "k_b_served": None if kb_served is None else round(kb_served, 4),
            "k_b_ref": round(kb_ref, 4),
            "k_s_served": None if ks_served is None else round(ks_served, 4),
            "k_s_ref": round(ks_ref, 4),
            "sigma_cr_a": r.get("critical_stress_a"),
            "tau_cr": r.get("critical_shear"),
            "ult_served": r.get("ultimate_strength"), "ult_ref_faulkner": round(ult_ref, 2),
            "beta": round(beta, 3),
            "SF_a_served": r.get("safety_factor_a"),
            "SF_b_served": r.get("safety_factor_b"),
            "tag": r.get("tag"),
            "axial_load_a": i.get("axial_load_a"),
        })

    # core check: simply supported square panel
    sq = [x for x in rows if x["a_over_b"] == 1.0 and x["t_mm"] == 15.0
          and x["tag"] is None and x["edge_condition_input"] in ("SSSS", None)]
    if sq:
        checks.append(mk("P1", "plate-stability", "sigma_cr,a (SSSS, a/b=1, t=15mm)",
                         sq[0]["sigma_cr_a"],
                         ref.sigma_cr(ref.k_uniaxial_x(1.0), E, 0.015, 2.4, nu),
                         note="Bryan/Timoshenko k=4.0"))
    # SSSS k(alpha) -- token "SSSS"
    ssss = [x for x in rows if x["edge_condition_input"] == "SSSS"]
    worst = None
    for x in ssss:
        err = abs(x["k_served"] - x["k_ref_ssss"]) / x["k_ref_ssss"] * 100
        if worst is None or err > worst[0]:
            worst = (err, x)
    if worst:
        checks.append(mk("P2", "plate-stability",
                         "k(alpha) simply supported (token 'SSSS'), worst of sweep",
                         worst[1]["k_served"], worst[1]["k_ref_ssss"], unit="-",
                         note=f"worst case a/b={worst[1]['a_over_b']}; served k is floored at 4.0, "
                              f"the exact minimum is 6.25 for a/b=0.5"))
    # same physics, different token?
    by_ab = {}
    for x in rows:
        by_ab.setdefault(x["a_over_b"], {})[x["edge_condition_input"]] = x["k_served"]
    mismatch = [(ab, d["SSSS"], d["simply_supported"]) for ab, d in by_ab.items()
                if "SSSS" in d and "simply_supported" in d
                and abs(d["SSSS"] - d["simply_supported"]) / d["simply_supported"] > 0.01]
    if mismatch:
        ab, k1, k2 = mismatch[0]
        checks.append({"id": "P2b", "group": "plate-stability",
                       "quantity": "same panel, token 'SSSS' vs 'simply_supported'",
                       "computed": k1, "reference": k2, "unit": "-",
                       "rel_err_pct": round((k1 - k2) / k2 * 100, 2), "tol_pct": TOL,
                       "verdict": "FAIL",
                       "note": f"a/b={ab}: SSSS->{k1}, simply_supported->{k2}; "
                               f"identical edge conditions must give identical k"})
    # clamped square
    cl = [x for x in rows if x["edge_condition_input"] == "clamped" and x["a_over_b"] == 1.0]
    if cl:
        checks.append(mk("P3", "plate-stability", "k clamped edges, a/b=1",
                         cl[0]["k_served"], ref.K_CLAMPED, unit="-",
                         note="DNV-RP-C201 Table 6-1 k=6.97"))
    # token recognition: CCCC identical to SSSS?
    cc = [x for x in rows if x["edge_condition_input"] == "CCCC"]
    if cc:
        same = all(abs(x["k_served"] - x["k_ref_ssss"]) < 1e-6 for x in cc)
        checks.append(mk("P4", "plate-stability",
                         "edge_condition='CCCC' recognised (k should be ~6.97)",
                         cc[0]["k_served"], ref.K_CLAMPED, unit="-",
                         note="identical to SSSS for all a/b -> token silently ignored"
                              if same else "CCCC handled"))
    # transverse buckling coefficient vs variational bound
    kb = [x for x in rows if x["k_b_served"] is not None]
    worst_b = min(kb, key=lambda x: x["k_b_served"] / x["k_b_ref"])
    checks.append(mk("P5", "plate-stability", "k_b (transverse compression), worst of sweep",
                     worst_b["k_b_served"], worst_b["k_b_ref"], unit="-",
                     note=f"a/b={worst_b['a_over_b']}; bound k_b>=1.0 violated if k<1"))
    # shear (restricted to a/b >= 1 where the 5.34 + 4/alpha^2 convention is unambiguous)
    kb_s = [x for x in kb if x["a_over_b"] >= 1.0] or kb
    worst_s = max(kb_s, key=lambda x: abs(x["k_s_served"] - x["k_s_ref"]) / x["k_s_ref"])
    checks.append(mk("P6", "plate-stability", "k_s shear, worst of sweep",
                     worst_s["k_s_served"], worst_s["k_s_ref"], unit="-",
                     note=f"a/b={worst_s['a_over_b']}, k_s=5.34+4/alpha^2"))
    # ultimate strength vs Faulkner
    worst_u = max(rows, key=lambda x: abs(x["ult_served"] - x["ult_ref_faulkner"]))
    checks.append(mk("P7", "plate-stability",
                     "ultimate strength vs Faulkner (worst of sweep)",
                     worst_u["ult_served"], worst_u["ult_ref_faulkner"],
                     note=f"t={worst_u['t_mm']}mm beta={worst_u['beta']}; "
                          f"served == sigma_cr in every row"))
    # safety factor cap consistency
    zero = [x for x in rows if x["tag"] is None and (x["axial_load_a"] in (0.0, None))
            and x["edge_condition_input"] in ("SSSS",) ]
    loaded = [x for x in rows if x["tag"] == "load_convention" and x["axial_load_a"] == 1.0]
    if zero and loaded:
        checks.append(mk("P8", "plate-stability",
                         "safety factor at zero load (should be inf, capped?)",
                         zero[0]["SF_a_served"], None, unit="-", tol=0.0,
                         note=f"zero load -> {zero[0]['SF_a_served']} (cap 10); "
                              f"1 Pa load -> {loaded[0]['SF_a_served']:.0f} (uncapped) -> "
                              f"cap applied inconsistently"))
        checks[-1]["verdict"] = "FAIL"
    return checks, rows


# ---------------------------------------------------------------------------
# B. /api/v1/fatigue/analyze
# ---------------------------------------------------------------------------
def audit_fatigue(probe):
    checks = []
    curves = {c["id"]: c for c in probe["fatigue_curves"]["curves"]}
    by = {}
    for r in probe["fatigue"]:
        if "S" in r and r.get("N"):
            by.setdefault(r["sn_curve"], []).append((r["S"], r["N"]))

    extracted = {}
    for cid, pts in by.items():
        pts.sort()
        c3 = [N * S ** 3 for S, N in pts]
        c5 = [N * S ** 5 for S, N in pts]
        # m=3 branch = the longest run of constant N*S^3
        best_val, best_run = None, []
        run, prev = [], None
        for (S, N), v in zip(pts, c3):
            if prev is not None and abs(v - prev) / prev < 1e-6:
                run.append((S, N, v))
            else:
                run = [(S, N, v)]
            if len(run) > len(best_run):
                best_run, best_val = list(run), v
            prev = v
        c3_branch = best_val if best_run else None
        # m=5 branch = constant N*S^5 at low stress
        best5, run5, prev = None, [], None
        for (S, N), v in zip(pts, c5):
            if prev is not None and abs(v - prev) / prev < 1e-6:
                run5.append((S, N, v))
            else:
                run5 = [(S, N, v)]
            if len(run5) > 0 and (best5 is None or len(run5) > len(best5[0])):
                best5 = (list(run5), v)
            prev = v
        c5_branch = best5[1] if best5 else None
        knee_n = knee_s = None
        if c3_branch and c5_branch:
            knee_s = math.sqrt(c5_branch / c3_branch)
            knee_n = c3_branch / knee_s ** 3
        extracted[cid] = {
            "sc_published": curves.get(cid, {}).get("Sc"),
            "m_published": curves.get(cid, {}).get("m"),
            "C_m3": c3_branch, "C_m5": c5_branch,
            "knee_cycles": knee_n, "knee_stress_MPa": knee_s,
            "n_points": len(pts),
            "c3_branch_stress": [p[0] for p in best_run] if best_run else [],
            "served_at_sc": None,
            "implied_stress_at_2e6": None,
            "life_at_sc_cycles": None,
        }
        if c3_branch:
            extracted[cid]["implied_stress_at_2e6"] = (c3_branch / 2e6) ** (1 / 3)
            sc = curves.get(cid, {}).get("Sc")
            if sc:
                extracted[cid]["life_at_sc_cycles"] = c3_branch / sc ** 3

    # F1: internal contradiction -- curve used vs published reference stress
    d = extracted.get("BS-D", {})
    checks.append(mk("F1", "fatigue",
                     "stress range at 2e6 cycles implied by served curve, BS-D",
                     round(d["implied_stress_at_2e6"], 2) if d.get("implied_stress_at_2e6") else None,
                     d.get("sc_published"), unit="MPa",
                     note="served m=3 branch gives N*dsigma^3 = %.4e (log a=%.3f); "
                          "published Sc=53.4 MPa @2e6 implies 3.05e11"
                          % (d.get("C_m3") or 0, math.log10(d["C_m3"]) if d.get("C_m3") else 0)))
    # F2: life at the published reference stress vs the standard's 2e6
    checks.append(mk("F2", "fatigue", "life at published Sc (BS-D), cycles",
                     round(d["life_at_sc_cycles"]) if d.get("life_at_sc_cycles") else None,
                     2e6, unit="cycles", tol=TOL,
                     note="published anchor states 2e6 cycles"))
    # F3/F4: external standards
    ss = [70.0, 80.0, 100.0, 120.0]
    served = {S: d["C_m3"] / S ** 3 for S in ss} if d.get("C_m3") else {}
    worst_bs = max(ss, key=lambda S: abs(served[S] / ref.bs_d_cycles(S) - 1))
    checks.append(mk("F3", "fatigue", f"life at dsigma={worst_bs:.0f} MPa vs BS 7608 class D",
                     round(served[worst_bs]), round(ref.bs_d_cycles(worst_bs)),
                     unit="cycles", note="BS 7608 D: 53.4 MPa @2e6, m=3, knee 1e7"))
    low = [S for S in (30.0, 40.0, 50.0) if S in
           {p[0] for p in by.get("BS-D", [])}]
    if low:
        S = max(low)
        served_low = dict(by["BS-D"])[S]
        checks.append(mk("F4", "fatigue", f"life at dsigma={S:.0f} MPa vs DNV-RP-C203 D (m=5 branch)",
                         round(served_low), round(ref.dnv_d_cycles(S)), unit="cycles",
                         note="below knee -> platform optimistic"))
    # F5: knee continuity
    if d.get("knee_cycles"):
        # stress where the served m=3 branch reaches the 1e7-cycle switch point
        s_switch = (d["C_m3"] / 1e7) ** (1 / 3)
        jump = (d["C_m5"] / s_switch ** 5) / 1e7
        checks.append(mk("F5", "fatigue", "knee position, cycles (served curve)",
                         round(d["knee_cycles"]),
                         ref.DNV_KNEE_CYCLES, unit="cycles",
                         note="DNV/BS both place the knee at 1e7"))
        checks.append(mk("F5b", "fatigue", "life jump at the served S-N knee", round(jump, 3), 1.0,
                         unit="x", tol=TOL / 100,
                         note=f"at dsigma={s_switch:.2f} MPa the m=3 branch gives 1.0e7 cycles but "
                              f"the m=5 branch gives {jump*1e7:.3e}; curve is discontinuous"))
    # F6: Miner internal consistency on the reported demo case
    demo = {"stress_ranges": [50.0, 80.0, 100.0], "cycles": [1_000_000, 500_000, 100_000]}
    if "BS-D" in by:
        tbl = dict(by["BS-D"])
        # use the served lives for those stress levels where available
        dsum = 0.0
        valid = True
        for S, n in zip(demo["stress_ranges"], demo["cycles"]):
            if S in tbl:
                dsum += n / tbl[S]
            else:
                valid = False
        if valid:
            checks.append(mk("F6", "fatigue", "Miner sum recomputed from served lives",
                             round(dsum, 6), 0.291661, unit="-", tol=1e-3,
                             note="served total_damage of the demo case"))

    # anomalous branch switching on other classes
    anomalies = []
    for cid, e in extracted.items():
        if cid == "BS-D":
            continue
        c3 = [N * S ** 3 for S, N in sorted(by[cid])]
        uniq = len({round(v / 1e9, 4) for v in c3 if v / c3[-1] > 1e-3})
        if uniq > 1:
            anomalies.append({"curve": cid, "distinct_C_m3_local": uniq,
                              "C_m3_tail": e["C_m3"]})
    return checks, extracted, anomalies


# ---------------------------------------------------------------------------
# C. /api/v1/structure/plate and /api/v1/structure/analyze
# ---------------------------------------------------------------------------
def audit_structure_endpoints(probe):
    checks = []
    E, nu = 206e9, 0.3
    plate_rows = []
    for r in probe["plate"]:
        resp = r.get("resp") or {}
        t = r["input"]["plate_thickness"]
        fs = r["input"]["frame_spacing"]
        sy = r["input"]["yield_stress"]
        # span deduced from the (t/span)^2 scaling: b_eff = 3 * frame_spacing
        b_eff = 3.0 * fs
        ref_cr = ref.sigma_cr(4.0, E, t, b_eff, nu)
        plate_rows.append({
            "var": r["var"], "value": r["value"], "t_mm": t * 1000,
            "frame_spacing": fs, "sigma_y": sy,
            "critical_stress": resp.get("critical_stress"),
            "actual_stress": resp.get("actual_stress"),
            "safety_factor": resp.get("safety_factor"),
            "ref_sigma_cr_b_eff": round(ref_cr, 2),
            "b_eff_assumed": b_eff,
            "actual_over_yield": (resp.get("actual_stress") / sy) if sy else None,
        })
    t_rows = [x for x in plate_rows if x["var"] == "plate_thickness"]
    if t_rows:
        mid = [x for x in t_rows if abs(x["t_mm"] - 15) < 0.5] or t_rows
        checks.append(mk("S1", "structure/plate",
                         "buckling capacity of the panel (b_eff=3*frame_spacing deduced)",
                         mid[0]["critical_stress"], mid[0]["ref_sigma_cr_b_eff"],
                         note="Bryan k=4; span deduced from the (t/s)^2 scaling"))
    ratios = [x["actual_over_yield"] for x in plate_rows if x["actual_over_yield"]]
    if ratios:
        checks.append(mk("S2", "structure/plate",
                         "applied stress / yield stress (should vary with load, not be constant)",
                         round(sum(ratios) / len(ratios), 4), None, unit="-", tol=0.0,
                         note="actual_stress = 0.5*sigma_y for every input -> applied load is a "
                              "hard-coded fraction of yield, so the SF grades slenderness only"))
        checks[-1]["verdict"] = "FAIL"

    an_rows = []
    for r in probe["analyze"]:
        resp = r.get("resp") or {}
        an_rows.append({
            "var": r["var"], "value": r["value"],
            "max_stress": resp.get("max_stress"),
            "allowable_stress": resp.get("allowable_stress"),
            "utilization": resp.get("utilization"),
            "fatigue_life": resp.get("fatigue_life"),
            "status": resp.get("status"),
            "elapsed_s": r.get("elapsed_s"),
            "utilization_ratio_check": (resp.get("max_stress") / resp.get("allowable_stress")
                                        if resp.get("allowable_stress") else None),
        })
    if an_rows:
        a = an_rows[0]
        checks.append(mk("S3", "structure/analyze",
                         "utilization vs max_stress/allowable_stress (Pa vs MPa)",
                         a["utilization"], round(a["utilization_ratio_check"], 3), unit="-",
                         note="served utilization equals the raw ratio -> stress in Pa divided by "
                              "allowable in MPa, off by 1e6"))
        tys = [x for x in an_rows if x["var"] == "T"]
        if tys:
            vals = [x["max_stress"] for x in tys if x["max_stress"]]
            identical = len(set(round(v, 3) for v in vals)) == 1
            checks.append(mk("S4", "structure/analyze",
                             "max_stress sensitivity to depth T (T=8..16 m)",
                             len(set(round(v, 3) for v in vals)), len(vals), unit="distinct",
                             tol=0.0,
                             note="identical for every T -> hull-girder section modulus does not "
                                  "depend on depth in this path" if identical else "T used"))
            checks[-1]["verdict"] = "FAIL" if identical else "PASS"
        fl = [x["fatigue_life"] for x in an_rows]
        if fl and all((v or 0) == 0 for v in fl):
            checks.append({"id": "S5", "group": "structure/analyze",
                           "quantity": "fatigue_life returned to the caller", "computed": 0.0,
                           "reference": None, "unit": "years", "rel_err_pct": None,
                           "tol_pct": 0.0, "verdict": "FAIL",
                           "note": "always 0.0 -> fatigue is not coupled into the strength path"})
    return checks, plate_rows, an_rows


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------
def make_figures(plate_rows, extracted, fatigue_probe, an_rows, outdir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    os.makedirs(outdir, exist_ok=True)
    made = []

    # Fig 1: buckling coefficient vs aspect ratio
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    alphas = np.linspace(0.4, 3.5, 200)
    ax[0].plot(alphas, [ref.k_uniaxial_x(a) for a in alphas], "k-", lw=2, label="reference (exact, SSSS)")
    ssss = [x for x in plate_rows if x["edge_condition_input"] in ("SSSS", "simply_supported")]
    cccc = [x for x in plate_rows if x["edge_condition_input"] == "CCCC"]
    cl = [x for x in plate_rows if x["edge_condition_input"] == "clamped"]
    ax[0].plot([x["a_over_b"] for x in ssss], [x["k_served"] for x in ssss], "o", ms=7,
               label="served, SSSS / simply_supported")
    ax[0].plot([x["a_over_b"] for x in cccc], [x["k_served"] for x in cccc], "s", ms=7,
               mfc="none", label="served, 'CCCC' token")
    ax[0].plot([x["a_over_b"] for x in cl], [x["k_served"] for x in cl], "^", ms=7,
               label="served, 'clamped'")
    ax[0].axhline(ref.K_CLAMPED, ls="--", c="grey", label="clamped reference 6.97")
    ax[0].set_xlabel("aspect ratio a/b"); ax[0].set_ylabel("k  (sigma_cr = k pi^2 D / b^2)")
    ax[0].set_title("Uniaxial buckling coefficient, uniaxial load along a")
    ax[0].legend(fontsize=7); ax[0].grid(alpha=0.3)

    kb = [x for x in plate_rows if x["k_b_served"] is not None]
    ax[1].plot(alphas, [ref.k_uniaxial_y(a) for a in alphas], "k-", lw=2, label="reference (exact)")
    ax[1].axhline(1.0, ls=":", c="r", label="variational lower bound k=1")
    ax[1].plot([x["a_over_b"] for x in kb], [x["k_b_served"] for x in kb], "o", ms=7,
               label="served, transverse")
    ax[1].set_xlabel("aspect ratio a/b"); ax[1].set_ylabel("k_b")
    ax[1].set_title("Transverse-compression coefficient (served falls below bound)")
    ax[1].legend(fontsize=7); ax[1].grid(alpha=0.3)
    fig.tight_layout(); p = os.path.join(outdir, "fig1_plate_buckling_coefficients.png")
    fig.savefig(p, dpi=130); plt.close(fig); made.append(p)

    # Fig 2: ultimate strength vs Faulkner
    fig, ax = plt.subplots(figsize=(6.2, 4.4))
    ts = [10, 12, 15, 18, 20, 25, 30]
    rows = [x for x in plate_rows if x["t_mm"] in ts and x["tag"] is None]
    betas, ult_served, ult_ref = [], [], []
    for x in sorted(rows, key=lambda r: r["t_mm"]):
        beta = ref.plate_slenderness(2.4, x["t_mm"] / 1000.0, 235.0, 206000.0)
        betas.append(beta); ult_served.append(x["sigma_cr_a"])
        ult_ref.append(ref.faulkner_ultimate(235.0, beta))
    ax.plot(betas, ult_ref, "k-o", label="Faulkner (1975) reference")
    ax.plot(betas, ult_served, "r-s", label="served ultimate_strength (= sigma_cr)")
    ax.set_xlabel("plate slenderness beta = (b/t) sqrt(sigma_y/E)")
    ax.set_ylabel("ultimate compressive stress [MPa]")
    ax.set_title("Post-buckling reserve is not modelled")
    ax.legend(); ax.grid(alpha=0.3)
    fig.tight_layout(); p = os.path.join(outdir, "fig2_ultimate_strength.png")
    fig.savefig(p, dpi=130); plt.close(fig); made.append(p)

    # Fig 3: S-N curves
    fig, ax = plt.subplots(figsize=(6.6, 4.6))
    by = {}
    for r in fatigue_probe["fatigue"]:
        if "S" in r and r.get("N"):
            by.setdefault(r["sn_curve"], []).append((r["S"], r["N"]))
    for cid, pts in sorted(by.items()):
        pts.sort()
        ax.loglog([p[0] for p in pts], [p[1] for p in pts], "o-", ms=3.5, lw=1.2,
                  label=f"served {cid}")
    ss = np.logspace(math.log10(20), math.log10(200), 200)
    ax.loglog(ss, [ref.bs_d_cycles(s) for s in ss], "k--", lw=2,
              label="BS 7608 class D (53.4 MPa @2e6)")
    ax.loglog(ss, [ref.dnv_d_cycles(s) for s in ss], "k:", lw=2,
              label="DNV-RP-C203 curve D (log a=12.164)")
    ax.set_xlabel("stress range [MPa]"); ax.set_ylabel("endurance N [cycles]")
    ax.set_title("Served S-N curves vs standards (class D highlighted)")
    ax.legend(fontsize=7); ax.grid(alpha=0.3, which="both")
    fig.tight_layout(); p = os.path.join(outdir, "fig3_sn_curves.png")
    fig.savefig(p, dpi=130); plt.close(fig); made.append(p)

    # Fig 4: fatigue life ratio served/standard
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    d = extracted.get("BS-D", {})
    if d.get("C_m3"):
        ss = np.logspace(math.log10(25), math.log10(160), 120)
        served = []
        for s in ss:
            n3 = d["C_m3"] / s ** 3
            served.append(n3 if n3 <= 1e7 else d["C_m5"] / s ** 5)
        ax[0].semilogx(ss, np.array(served) / np.array([ref.bs_d_cycles(s) for s in ss]),
                       "r-", label="served / BS 7608 D")
        ax[0].semilogx(ss, np.array(served) / np.array([ref.dnv_d_cycles(s) for s in ss]),
                       "b-", label="served / DNV C203 D")
        ax[0].axhline(1.0, c="k", ls=":"); ax[0].axhline(1.15, c="grey", ls="--", lw=0.8)
        ax[0].set_xlabel("stress range [MPa]"); ax[0].set_ylabel("life ratio served / reference")
        ax[0].set_title("Fatigue life ratio (rule: <1.15 acceptable)")
        ax[0].legend(fontsize=8); ax[0].grid(alpha=0.3, which="both")
    util = [x for x in an_rows if x["utilization"]]
    if util:
        labels = [f"{x['var']}={x['value']:g}" for x in util]
        ax[1].barh(labels, [x["utilization"] for x in util], color="indianred")
        ax[1].axvline(1.0, c="k", ls="--", label="utilization = 1")
        ax[1].set_xscale("log"); ax[1].set_xlabel("served utilization (log)")
        ax[1].set_title("/structure/analyze utilization: Pa/MPa mix")
        ax[1].legend(fontsize=8); ax[1].tick_params(labelsize=6)
    fig.tight_layout(); p = os.path.join(outdir, "fig4_life_ratio_and_utilization.png")
    fig.savefig(p, dpi=130); plt.close(fig); made.append(p)
    return made


# ---------------------------------------------------------------------------
def md_table(rows, cols, headers=None):
    headers = headers or cols
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(cols)) + "|"]
    for r in rows:
        cells = []
        for c in cols:
            v = r.get(c)
            if isinstance(v, float):
                cells.append(f"{v:.4g}" if abs(v) < 1e4 else f"{v:.3e}")
            else:
                cells.append("" if v is None else str(v))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def main():
    p1 = load("probe_struct_fatigue.json")
    p2 = load("probe_structure_endpoints.json")

    c1, plate_rows = audit_plate_stability(p1)
    c2, extracted, anomalies = audit_fatigue(p1)
    c3, plate2, an_rows = audit_structure_endpoints(p2)
    checks = c1 + c2 + c3

    figs = make_figures(plate_rows, extracted, p1, an_rows, FIG)

    summary = {}
    for g in ("plate-stability", "fatigue", "structure/plate", "structure/analyze"):
        sel = [c for c in checks if c["group"] == g]
        summary[g] = {"n": len(sel), "pass": sum(c["verdict"] == "PASS" for c in sel),
                      "fail": sum(c["verdict"] == "FAIL" for c in sel)}

    out = {"generated": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
           "checks": checks, "summary": summary,
           "plate_sweep": plate_rows, "sn_extracted": extracted,
           "anomalous_classes": anomalies,
           "structure_plate_sweep": plate2, "structure_analyze_sweep": an_rows,
           "figures": [os.path.relpath(f, ROOT) for f in figs]}
    with open(os.path.join(ART, "structure_validation.json"), "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    # ---- markdown tables ----
    tbl = []
    tbl.append("### Table 1 — closed-form check register\n")
    tbl.append(md_table(checks, ["id", "group", "quantity", "computed", "reference",
                                 "unit", "rel_err_pct", "tol_pct", "verdict"],
                        ["ID", "group", "quantity", "served", "reference", "unit",
                         "err %", "tol %", "verdict"]))
    tbl.append("\n\n### Table 2 — plate-stability sweep (served vs exact)\n")
    tbl.append(md_table(plate_rows, ["a_over_b", "t_mm", "edge_condition_input", "k_served",
                                     "k_ref_ssss", "k_b_served", "k_b_ref", "k_s_served",
                                     "k_s_ref", "sigma_cr_a", "tau_cr", "ult_served",
                                     "ult_ref_faulkner", "beta"],
                        ["a/b", "t mm", "edge token", "k served", "k ref(SSSS)", "k_b served",
                         "k_b ref", "k_s served", "k_s ref", "sigma_cr,a", "tau_cr",
                         "ult served", "ult Faulkner", "beta"]))
    tbl.append("\n\n### Table 3 — S-N parameters extracted from the served response\n")
    rows3 = []
    for cid, e in sorted(extracted.items()):
        rows3.append({"curve": cid, "Sc_published": e["sc_published"],
                      "C_m3": e["C_m3"], "log10_C_m3": (math.log10(e["C_m3"])
                                                        if e["C_m3"] else None),
                      "implied_dsigma_at_2e6": e["implied_stress_at_2e6"],
                      "life_at_Sc": e["life_at_sc_cycles"],
                      "knee_cycles": e["knee_cycles"], "knee_stress": e["knee_stress_MPa"]})
    tbl.append(md_table(rows3, ["curve", "Sc_published", "C_m3", "log10_C_m3",
                                "implied_dsigma_at_2e6", "life_at_Sc", "knee_cycles",
                                "knee_stress"],
                        ["curve", "Sc (pub)", "C = N·S^3", "log10 C", "sigma@2e6 implied",
                         "N at published Sc", "N knee", "sigma knee"]))
    tbl.append("\n\n### Table 4 — /structure/plate OAT sweep\n")
    tbl.append(md_table(plate2, ["var", "value", "t_mm", "frame_spacing", "sigma_y",
                                 "critical_stress", "ref_sigma_cr_b_eff", "actual_stress",
                                 "actual_over_yield", "safety_factor"],
                        ["varied", "value", "t mm", "s m", "sigma_y", "sigma_cr served",
                         "sigma_cr ref", "applied", "applied/yield", "SF"]))
    tbl.append("\n\n### Table 5 — /structure/analyze OAT sweep\n")
    tbl.append(md_table(an_rows, ["var", "value", "max_stress", "allowable_stress",
                                  "utilization", "utilization_ratio_check", "fatigue_life",
                                  "status"],
                        ["varied", "value", "max_stress", "allowable", "utilization",
                         "ratio max/allow", "fatigue_life", "status"]))
    with open(os.path.join(ART, "structure_tables.md"), "w") as f:
        f.write("\n".join(tbl) + "\n")

    for k, v in summary.items():
        print(f"{k:20s} checks={v['n']:2d} pass={v['pass']:2d} fail={v['fail']:2d}")
    print("figures:", [os.path.basename(f) for f in figs])
    print("anomalous classes:", anomalies)


if __name__ == "__main__":
    main()