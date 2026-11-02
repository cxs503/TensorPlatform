"""Machine-adjudicated intact-stability gate for the ship-performance platform (:8001).

Runs three independent criteria families on a rectangular pontoon, where the
reference values are exact (see ``refs.py``):

  S1  GM accuracy          |GM_platform - GM_analytic| / GM_analytic  <= 3 %
  S2  KM decomposition     GM_platform - GM_analytic - T/2  ->  root-cause test
                           (if this residual is ~0 the platform double-counts KB)
  S3  GZ curve             max |GZ_platform - GZ_analytic| over the wall-sided
                           range (phi <= deck-edge angle) <= 0.05 m, plus the
                           physical bound on the vanishing angle (<= 90 deg for a
                           pontoon whose deck is 2T above the keel)
  S4  internal identity    GZ + KG sin(phi) - KN  ~ 0  (self-consistency only,
                           *not* evidence of correctness)

Usage:  python3 stability_gate.py            # writes artifacts/ + figs/
"""
from __future__ import annotations

import json
import math
import os
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import requests  # noqa: E402

import refs  # noqa: E402

BASE = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001/api/v1")
HERE = Path(__file__).resolve().parent
OUT = HERE / "artifacts"
FIG = HERE / "figs"
KG = 3.0
TOL_GM = 0.03      # 3 % - accuracy standard for key quantities
TOL_GZ_ABS = 0.05  # m
TOL_HYP = 0.01     # m, root-cause residual


def post(path, body, timeout=25):
    try:
        r = requests.post(f"{BASE}/{path}", json=body, timeout=timeout)
        return r.status_code, r.json()
    except Exception as exc:  # pragma: no cover
        return -1, repr(exc)


def body(L, B, T, D=None):
    return {"L": L, "B": B, "T": T, "D": D if D else 2 * T,
            "displacement": L * B * T, "Cb": 1.0, "Cm": 1.0, "KG": KG}


def main() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    FIG.mkdir(parents=True, exist_ok=True)

    # ---- sweep: (L, B, T) covering B/T = 1.25 .. 7.5 -----------------------
    cases = [(100.0, B, T) for B in (10.0, 20.0, 30.0) for T in (4.0, 8.0, 12.0)]
    rows = []
    for L, B, T in cases:
        code, data = post("stability/evaluate", body(L, B, T))
        a = refs.pontoon_analytic(L, B, T, KG)
        if code != 200 or not isinstance(data, dict):
            rows.append({"L": L, "B": B, "T": T, "status": code, "error": str(data)[:200]})
            continue
        GM_p = data.get("GM")
        rows.append({
            "L": L, "B": B, "T": T, "status": code,
            "GM_platform": GM_p, "GM_analytic": a["GM"],
            "GM_rel_err": refs.rel_error(GM_p, a["GM"]) if GM_p is not None else None,
            "KM_analytic": a["KM"],
            "KM_implied": (GM_p + KG) if GM_p is not None else None,
            "KB_duplicate_residual_m": (GM_p - a["GM"] - T / 2.0) if GM_p is not None else None,
            "deck_edge_deg": a["deck_edge_deg"],
            "GZ_max_platform": data.get("GZ_max"),
            "vanishing_angle_platform": data.get("vanishing_angle"),
        })

    ok = [r for r in rows if r.get("GM_platform") is not None]
    hyp_res = [abs(r["KB_duplicate_residual_m"]) for r in ok]

    # ---- reference case: full GZ curve ------------------------------------
    L, B, T = 100.0, 20.0, 8.0
    a = refs.pontoon_analytic(L, B, T, KG)
    ref_curve = refs.gz_curve(L, B, T, KG, step=1.0)
    code, gz = post("stability/gz-curve", body(L, B, T))
    gz_rows = []
    if code == 200 and isinstance(gz, dict):
        angles = gz.get("angles") or []
        gzv = gz.get("gz_values") or []
        knv = gz.get("kn_values") or []
        for i, phi in enumerate(angles):
            plat = gzv[i] if i < len(gzv) else None
            kn = knv[i] if i < len(knv) else None
            if plat is None:
                continue
            valid = phi <= a["deck_edge_deg"] + 1e-9
            ana = refs.gz_wall_sided(phi, a["GM"], a["BM"]) if valid else None
            gz_rows.append({
                "phi_deg": phi, "GZ_platform": plat,
                "GZ_analytic": ana,
                "abs_err": abs(plat - ana) if ana is not None else None,
                "valid": valid,
                "KN_identity_abs_err": (abs(plat + KG * math.sin(math.radians(phi)) - kn)
                                        if kn is not None else None),
            })
        area_plat = gz.get("area_0_30")
    else:
        area_plat = None
    valid_err = [r["abs_err"] for r in gz_rows if r["valid"] and r["abs_err"] is not None]
    kn_err = [r["KN_identity_abs_err"] for r in gz_rows if r["KN_identity_abs_err"] is not None]

    # ---- verdicts ----------------------------------------------------------
    gm_errs = [abs(r["GM_rel_err"]) for r in ok if r["GM_rel_err"] is not None]
    verdict = {
        "S1_GM_accuracy_3pct": {
            "pass": bool(gm_errs) and max(gm_errs) <= TOL_GM,
            "max_abs_rel_err": max(gm_errs) if gm_errs else None,
            "worst_case": max(ok, key=lambda r: abs(r["GM_rel_err"])) if gm_errs else None,
            "criterion": "max |GM_err|/GM_ref <= 3%",
        },
        "S2_KM_double_count_KB": {
            "pass": bool(hyp_res) and max(hyp_res) <= TOL_HYP,
            "max_abs_residual_m": max(hyp_res) if hyp_res else None,
            "median_abs_residual_m": statistics.median(hyp_res) if hyp_res else None,
            "hypothesis": "GM_platform = GM_analytic + T/2  <=>  KM adds KB a second time",
        },
        "S3_GZ_curve": {
            "pass": bool(valid_err) and max(valid_err) <= TOL_GZ_ABS,
            "max_abs_err_m": max(valid_err) if valid_err else None,
            "vanishing_angle_physical": all(
                (r.get("vanishing_angle_platform") or 0) <= 90.0 for r in ok) if ok else None,
            "vanishing_angles_deg": [r.get("vanishing_angle_platform") for r in ok],
            "criterion": "max |dGZ| <= 0.05 m for phi <= deck-edge; vanishing angle <= 90 deg",
        },
        "S4_KN_identity": {
            "pass": bool(kn_err) and max(kn_err) <= 1e-3,
            "max_abs_err_m": max(kn_err) if kn_err else None,
            "note": "self-consistency only - NOT evidence of geometric correctness",
        },
    }
    area_ref = refs.area_under(ref_curve, 30.0)
    res = {
        "api_base": BASE,
        "reference_case": {"L": L, "B": B, "T": T, "KG": KG, **a},
        "sweep": rows,
        "gz_rows": gz_rows,
        "area_0_30_platform": area_plat,
        "area_0_30_reference_m_rad": area_ref,
        "verdict": verdict,
        "figure": "figs/stability_gate.png",
    }

    # ---- figure -----------------------------------------------------------
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.4))
    ax = axes[0]
    ax.plot([r["phi_deg"] for r in ref_curve], [r["GZ"] for r in ref_curve],
            "k-", lw=2, label="analytic (wall-sided, exact)")
    ax.plot([r["phi_deg"] for r in gz_rows], [r["GZ_platform"] for r in gz_rows],
            "o--", ms=3, color="tab:red", label="platform /stability/gz-curve")
    ax.axvline(a["deck_edge_deg"], color="gray", ls=":", lw=1)
    ax.text(a["deck_edge_deg"] + 1, 0.15, "deck edge %.1f$^\\circ$" % a["deck_edge_deg"],
            fontsize=8, color="gray")
    ax.set_xlabel("heel angle $\\varphi$ [deg]"), ax.set_ylabel("GZ [m]")
    ax.set_title("Pontoon L=100 B=20 T=8 m, KG=3 m")
    ax.grid(alpha=0.3), ax.legend(fontsize=8)

    ax = axes[1]
    ax.axhline(0.0, color="k", lw=0.8)
    ax.plot([r["T"] / r["B"] for r in ok],
            [r["GM_rel_err"] * 100 for r in ok], "s-", color="tab:red",
            label="measured GM error")
    ax.plot(sorted(r["T"] / r["B"] for r in ok),
            sorted([0.5 * r["T"] / (r["GM_analytic"]) * 100 for r in ok]),
            "k:", lw=1.5, label="predicted by double-count: $T/2$")
    ax.axhline(3.0, color="tab:green", ls="--", lw=1, label="3 % acceptance")
    ax.set_xlabel("T/B"), ax.set_ylabel("GM error [%]")
    ax.set_title("GM error vs T/B (9 cases)")
    ax.grid(alpha=0.3), ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG / "stability_gate.png", dpi=150)

    (OUT / "stability_gate.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))

    print("== S1 GM accuracy ==", verdict["S1_GM_accuracy_3pct"]["pass"],
          "max |err| = %.2f%%" % (100 * verdict["S1_GM_accuracy_3pct"]["max_abs_rel_err"] or 0))
    for k, v in verdict.items():
        print(f"{k:28s} pass={v['pass']}  " +
              str({kk: vv for kk, vv in v.items() if kk not in ('pass', 'worst_case', 'criterion')})[:120])
    print("area_0_30 platform=%s ref=%.4f m.rad" % (area_plat, area_ref))
    return res


if __name__ == "__main__":
    main()