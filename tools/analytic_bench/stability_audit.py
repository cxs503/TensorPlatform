#!/usr/bin/env python3
"""Audit the platform's intact-stability (GZ) capabilities against exact
analytic references for wall-sided box barges.

Run::

    python -m tools.analytic_bench.stability_audit [--api http://127.0.0.1:8001]

Outputs (all under ``artifacts/analytic_bench/``):
  * ``stability_audit.json``   machine-readable measurements + verdicts
  * ``stability_audit.md``     result tables
  * ``fig_stability_audit.png`` GZ curves, error-vs-angle, factor errors

Tolerance ladder (project accuracy standard): 3 % = "verified", 15 % =
"unacceptable".  Everything in between is reported as an alert.

Scope note (honest limitation): the reference is a *watertight* box barge, so
GZ is only physical up to the first opening.  For these hulls that is the
deck-edge immersion angle, which for a full-form barge is low
(26-38 deg).  Pointwise GZ comparisons are therefore only *judged* inside
``phi <= wall_sided_limit_deg``; beyond it the verdict is WITHHELD (the box
would already be flooding, and the platform applies its own ad-hoc deck-angle
and "60-deg taper" corrections whose physical basis cannot be tested with an
analytic reference).  IMO area criteria are reported on the sub-grid that lies
inside the validated window.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import reference as R

OUT_DIR = Path(__file__).resolve().parents[2] / "artifacts" / "analytic_bench"
TOL_OK = 3.0
TOL_BAD = 15.0

CASES = [
    R.BoxBarge(length=100.0, beam=20.0, draft=8.0, depth=14.0, kg=6.0, name="box-B20T8"),
    R.BoxBarge(length=80.0, beam=24.0, draft=6.0, depth=12.0, kg=5.0, name="box-B24T6"),
    R.BoxBarge(length=120.0, beam=18.0, draft=7.0, depth=15.0, kg=6.8, name="box-B18T7"),
]

ANGLES_A = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0, 50.0, 55.0, 60.0]


def post(base: str, path: str, body: Dict[str, Any], timeout: float = 60.0) -> Dict[str, Any]:
    url = f"{base.rstrip('/')}/api/v1/{path.lstrip('/')}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def verdict(err_pct: Optional[float], defined: bool = True) -> str:
    if err_pct is None or not defined:
        return "WITHHELD"
    a = abs(err_pct)
    if not math.isfinite(a):
        return "WITHHELD"
    return "PASS" if a <= TOL_OK else ("FAIL" if a >= TOL_BAD else "ALERT")


def _cmp(ref: float, calc: Optional[float], defined: bool = True) -> Dict[str, Any]:
    if calc is None:
        return {"ref": round(float(ref), 4), "calc": None, "err_pct": None, "verdict": "WITHHELD"}
    e = R.relative_error_pct(float(calc), float(ref))
    return {"ref": round(float(ref), 4), "calc": calc, "err_pct": round(e, 3),
            "verdict": verdict(e, defined)}


def audit_case(base: str, h: R.BoxBarge) -> Dict[str, Any]:
    body_a = dict(length=h.length, beam=h.beam, draft=h.draft,
                  displacement=h.displacement_t, cb=1.0, kg=h.kg)
    body_b = dict(L=h.length, B=h.beam, T=h.draft, D=h.depth,
                  displacement=h.displacement_t, Cb=1.0, Cm=1.0, KG=h.kg)

    a = post(base, "calculations/stability", body_a)
    b = post(base, "stability/gz-curve", body_b)
    c = post(base, "stability/evaluate", body_b)
    limit = h.wall_sided_limit_deg

    # ---------------- hydrostatic factors (always analytically defined) ----
    factors: Dict[str, Any] = {
        "KB": _cmp(h.kb, a.get("kb")),
        "BM": _cmp(h.bm, a.get("bm")),
        "GM": _cmp(h.gm, a.get("gm")),
        "GM_endpoint_evaluate": _cmp(h.gm, c.get("GM")),
    }
    # effective small-angle GM implied by the gz-curve grid (5 deg point)
    angles_b: List[float] = b["angles"]
    gz_b: List[float] = b["gz_values"]
    if len(gz_b) > 2 and gz_b[1] > 0:
        factors["GM_endpoint_gzcurve"] = _cmp(h.gm, round(gz_b[1] / math.sin(math.radians(angles_b[1])), 4))

    # ---------------- pointwise GZ ---------------------------------------
    ref_b = [h.gz_numeric(p) for p in angles_b]
    pointwise = []
    judged = []
    for p, gc, gr in zip(angles_b, gz_b, ref_b):
        defined = p <= limit + 1e-9
        e = R.relative_error_pct(gc, gr)
        row = {"angle_deg": p, "calc": round(gc, 4), "ref": round(gr, 4),
               "err_pct": round(e, 3), "reference_defined": defined,
               "verdict": verdict(e, defined)}
        pointwise.append(row)
        if defined and math.isfinite(e):
            judged.append(row)

    window = {
        "window_deg": [0.0, max(r["angle_deg"] for r in judged) if judged else 0.0],
        "n_points": len(judged),
        "max_abs_err_pct": round(max((abs(r["err_pct"]) for r in judged), default=0.0), 3),
        "mean_abs_err_pct": round(sum(abs(r["err_pct"]) for r in judged) / max(1, len(judged)), 3),
        "n_fail_15pct": sum(1 for r in judged if r["verdict"] == "FAIL"),
        "n_pass_3pct": sum(1 for r in judged if r["verdict"] == "PASS"),
    }

    # ---------------- area / scalar descriptors ---------------------------
    win_angles = [r["angle_deg"] for r in judged]
    win_calc = [r["calc"] for r in judged]
    win_ref = [r["ref"] for r in judged]
    end = win_angles[-1] if win_angles else 0.0
    scalars: Dict[str, Any] = {}
    if len(win_angles) >= 2:
        scalars[f"area_0_{end:.0f}_gzcurve"] = _cmp(
            R.trapezoid_area(win_angles, win_ref, 0.0, end),
            R.trapezoid_area(win_angles, win_calc, 0.0, end))
    # endpoint A scalars: only judged when the reported extremum lies inside
    # the validated window (otherwise it depends on the post-flooding region).
    a_max_angle = a.get("gz_max_angle")
    scalar_specs = [
        ("gz_max", R.curve_summary(ANGLES_A, [h.gz_numeric(p) for p in ANGLES_A])["gz_max"],
         a.get("gz_max"), a_max_angle is not None and float(a_max_angle) <= limit + 1e-9),
        ("gz_max_angle", R.curve_summary(ANGLES_A, [h.gz_numeric(p) for p in ANGLES_A])["gz_max_angle"],
         a_max_angle, a_max_angle is not None and float(a_max_angle) <= limit + 1e-9),
    ]
    for key in ("area_0_30", "area_0_40"):
        scalar_specs.append((key, None, a.get(key), limit >= float(key.split("_")[-1])))
    for key, ref, got, defined in scalar_specs:
        if got is None:
            continue
        if ref is None:  # area over a window that may extend past the limit
            hi = float(key.split("_")[-1])
            ref = R.trapezoid_area(ANGLES_A, [h.gz_numeric(p) for p in ANGLES_A], 0.0, hi)
        scalars[key] = _cmp(ref, got, defined and ref is not None)

    # curve descriptors of endpoint C (published guesses, not integrations)
    evaluate = {"GM": c.get("GM"), "GZ_max": c.get("GZ_max"), "GZ_angle": c.get("GZ_angle"),
                "vanishing_angle": c.get("vanishing_angle")}

    return {
        "case": h.name,
        "hull": {"L": h.length, "B": h.beam, "T": h.draft, "D": h.depth,
                 "KG": h.kg, "Cb": 1.0, "displacement_t": round(h.displacement_t, 1)},
        "analytic": {"KB": round(h.kb, 4), "BM": round(h.bm, 4), "GM": round(h.gm, 4),
                     "phi_deck_deg": round(h.phi_deck_deg, 2),
                     "phi_bilge_deg": round(h.phi_bilge_deg, 2),
                     "wall_sided_limit_deg": round(limit, 2)},
        "endpoint_A_calculations_stability": {"factors_subset": {k: factors[k] for k in ("KB", "BM", "GM")},
                                              "scalars": scalars},
        "endpoint_B_stability_gz_curve": {"factors": {k: factors[k] for k in
                                                      ("GM_endpoint_gzcurve", "GM_endpoint_evaluate")},
                                          "pointwise": pointwise, "window_summary": window,
                                          "method": b.get("method")},
        "endpoint_C_stability_evaluate_raw": evaluate,
        "_judged": judged,
        "_ref_b": ref_b,
        "_angles_b": angles_b,
        "_gz_b": gz_b,
    }


def friction_control(base: str) -> Dict[str, Any]:
    """PASS control: ITTC-57 Cf is an exact identity the platform must match."""
    h = CASES[0]
    body = dict(length=h.length, beam=h.beam, draft=h.draft,
                displacement=h.displacement_t, speed=12.0, cb=1.0)
    d = post(base, "calculations/resistance", body)
    cf_ref = R.ittc57_cf(d["reynolds_number"])
    e = R.relative_error_pct(d["cf"], cf_ref)
    s = h.wetted_surface
    es = R.relative_error_pct(d["wetted_surface"], s)
    return {"quantity": "ITTC-57 Cf", "re": d["reynolds_number"], "cf_calc": d["cf"],
            "cf_ref": round(cf_ref, 6), "err_pct": round(e, 4), "verdict": verdict(e),
            "wetted_surface": {"calc": d["wetted_surface"], "ref_box_exact": round(s, 2),
                               "err_pct": round(es, 3), "verdict": verdict(es),
                               "note": "platform uses the Holtrop 1984 approximation, "
                                       "so a few % against the exact box area is expected"}}


def _md_table(rows: List[Dict[str, Any]], cols: List[str]) -> List[str]:
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001"))
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    results = [audit_case(args.api, h) for h in CASES]
    ctrl = friction_control(args.api)
    payload = {
        "harness": "tools/analytic_bench/stability_audit.py",
        "api": args.api,
        "tolerance": {"pass_pct": TOL_OK, "fail_pct": TOL_BAD},
        "scope": "wall-sided box barge (Cb=Cm=Cw=1), GZ judged only inside the "
                 "deck-immersion / bilge-emergence window",
        "friction_control": ctrl,
        "cases": [{k: v for k, v in c.items() if not k.startswith("_")} for c in results],
    }
    (out / "stability_audit.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    lines = ["# Intact-stability analytic audit — wall-sided box barge (exact reference)", "",
             f"API `{args.api}` · PASS <= {TOL_OK}% · FAIL >= {TOL_BAD}% · GZ judged only inside the "
             "deck-immersion / bilge-emergence window", "",
             "## PASS control — ITTC-57 friction line (same platform, same harness)", ""]
    lines += _md_table([{"quantity": "Cf", "calc": ctrl["cf_calc"], "ref": ctrl["cf_ref"],
                         "err_pct": f"{ctrl['err_pct']:+.4f}", "verdict": ctrl["verdict"]},
                        {"quantity": "S [m2]", "calc": ctrl["wetted_surface"]["calc"],
                         "ref": ctrl["wetted_surface"]["ref_box_exact"],
                         "err_pct": f"{ctrl['wetted_surface']['err_pct']:+.3f}",
                         "verdict": ctrl["wetted_surface"]["verdict"]}],
                       ["quantity", "calc", "ref", "err_pct", "verdict"])
    for c in payload["cases"]:
        an = c["analytic"]
        lines += ["", f"## Case {c['case']} — L={c['hull']['L']} B={c['hull']['B']} "
                      f"T={c['hull']['T']} D={c['hull']['D']} KG={c['hull']['KG']} Cb=1", "",
                  f"analytic KB={an['KB']} BM={an['BM']} GM={an['GM']} m · deck immersion "
                  f"{an['phi_deck_deg']}deg · bilge emergence {an['phi_bilge_deg']}deg · judged window "
                  f"<= {an['wall_sided_limit_deg']}deg", "",
                  "### Hydrostatic factors (`/calculations/stability`)", ""]
        rows = [{"factor": k, "ref": v["ref"], "calc": v["calc"], "err_pct": v["err_pct"],
                 "verdict": v["verdict"]}
                for k, v in c["endpoint_A_calculations_stability"]["factors_subset"].items()]
        rows.append({"factor": "GM (`/stability/gz-curve`)",
                     "ref": c["endpoint_B_stability_gz_curve"]["factors"]["GM_endpoint_gzcurve"]["ref"],
                     "calc": c["endpoint_B_stability_gz_curve"]["factors"]["GM_endpoint_gzcurve"]["calc"],
                     "err_pct": c["endpoint_B_stability_gz_curve"]["factors"]["GM_endpoint_gzcurve"]["err_pct"],
                     "verdict": c["endpoint_B_stability_gz_curve"]["factors"]["GM_endpoint_gzcurve"]["verdict"]})
        rows.append({"factor": "GM (`/stability/evaluate`)",
                     "ref": c["endpoint_B_stability_gz_curve"]["factors"]["GM_endpoint_evaluate"]["ref"],
                     "calc": c["endpoint_B_stability_gz_curve"]["factors"]["GM_endpoint_evaluate"]["calc"],
                     "err_pct": c["endpoint_B_stability_gz_curve"]["factors"]["GM_endpoint_evaluate"]["err_pct"],
                     "verdict": c["endpoint_B_stability_gz_curve"]["factors"]["GM_endpoint_evaluate"]["verdict"]})
        lines += _md_table(rows, ["factor", "ref", "calc", "err_pct", "verdict"])
        ws = c["endpoint_B_stability_gz_curve"]["window_summary"]
        lines += ["", f"### GZ pointwise (`/stability/gz-curve`) — judged window 0-{ws['window_deg'][1]:.0f}deg",
                  f"n={ws['n_points']} · max|err|={ws['max_abs_err_pct']}% · mean|err|="
                  f"{ws['mean_abs_err_pct']}% · PASS(3%)={ws['n_pass_3pct']} · FAIL(15%)={ws['n_fail_15pct']}", ""]
        lines += _md_table([{"angle": r["angle_deg"], "ref": r["ref"], "calc": r["calc"],
                             "err_pct": f"{r['err_pct']:+.2f}", "defined": r["reference_defined"],
                             "verdict": r["verdict"]}
                            for r in c["endpoint_B_stability_gz_curve"]["pointwise"]],
                           ["angle", "ref", "calc", "err_pct", "defined", "verdict"])
        lines += ["", "### Curve scalars", ""]
        lines += _md_table([{"scalar": k, "ref": v["ref"], "calc": v["calc"],
                             "err_pct": v["err_pct"], "verdict": v["verdict"]}
                            for k, v in c["endpoint_A_calculations_stability"]["scalars"].items()],
                           ["scalar", "ref", "calc", "err_pct", "verdict"])
    (out / "stability_audit.md").write_text("\n".join(lines))

    # ---------------- figure ----------------
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    c1 = results[0]
    fig, ax = plt.subplots(1, 3, figsize=(16.5, 4.6))
    ab, gb, rb = c1["_angles_b"], c1["_gz_b"], c1["_ref_b"]
    lim = c1["analytic"]["wall_sided_limit_deg"]
    ax[0].axvspan(lim, 90, color="0.9", label="beyond deck immersion (withheld)")
    ax[0].plot(ab, rb, "k-", lw=2.4, label="hermetic wedge-integration reference")
    ax[0].plot(ab, gb, "o--", color="#c0392b", ms=3.5, label="POST /stability/gz-curve")
    ax[0].axvline(lim, color="gray", ls=":", label=f"wall-sided limit {lim:.1f} deg")
    ax[0].set_xlabel("heel angle [deg]"); ax[0].set_ylabel("GZ [m]")
    ax[0].set_title(f"GZ curve — {c1['case']} (Cb=1, KG={c1['hull']['KG']} m)")
    ax[0].legend(fontsize=7); ax[0].grid(alpha=.3)

    errs = [r["err_pct"] for r in c1["endpoint_B_stability_gz_curve"]["pointwise"]]
    ax[1].axvspan(lim, 90, color="0.9")
    ax[1].plot(ab, errs, "o-", color="#c0392b", label="/stability/gz-curve")
    ax[1].axhline(0, color="k", lw=.8)
    for y, col, ls, lab in ((3, "green", "--", "+-3% tolerance"), (15, "red", ":", "+-15% unacceptable")):
        ax[1].axhline(y, color=col, ls=ls, lw=.9, label=lab)
        ax[1].axhline(-y, color=col, ls=ls, lw=.9)
    ax[1].axvline(lim, color="gray", ls=":")
    ax[1].set_xlabel("heel angle [deg]"); ax[1].set_ylabel("GZ relative error [%]")
    ax[1].set_title("GZ error vs analytic reference"); ax[1].legend(fontsize=7); ax[1].grid(alpha=.3)

    keys = ["KB", "BM", "GM"]
    xpos = np.arange(len(keys)); w = 0.26
    for i, c in enumerate(results):
        vals = [c["endpoint_A_calculations_stability"]["factors_subset"][k]["err_pct"] for k in keys]
        ax[2].bar(xpos + (i - 1) * w, vals, w, label=c["case"])
    ax[2].axhline(0, color="k", lw=.8)
    ax[2].axhline(3, color="green", ls="--", lw=.9); ax[2].axhline(-3, color="green", ls="--", lw=.9)
    ax[2].set_xticks(xpos); ax[2].set_xticklabels(keys)
    ax[2].set_ylabel("relative error [%]")
    ax[2].set_title("/calculations/stability factors vs exact box-barge values")
    ax[2].legend(fontsize=7); ax[2].grid(alpha=.3, axis="y")
    fig.suptitle("TensorPlatform m1532 — intact-stability analytic benchmark audit", fontsize=11)
    fig.tight_layout()
    fig.savefig(out / "fig_stability_audit.png", dpi=140)
    plt.close(fig)

    print(f"[ok] artifacts -> {out}")
    print(f"[control] ITTC-57 Cf err {ctrl['err_pct']:+.4f}% -> {ctrl['verdict']}")
    for c in payload["cases"]:
        f = c["endpoint_A_calculations_stability"]["factors_subset"]
        ws = c["endpoint_B_stability_gz_curve"]["window_summary"]
        print(f"[{c['case']}] KB {f['KB']['err_pct']:+.2f}% BM {f['BM']['err_pct']:+.2f}% "
              f"GM {f['GM']['err_pct']:+.2f}% | GZ max|err| {ws['max_abs_err_pct']:.2f}% "
              f"({ws['n_fail_15pct']}/{ws['n_points']} FAIL) | "
              f"GM(gzcurve) {c['endpoint_B_stability_gz_curve']['factors']['GM_endpoint_gzcurve']['err_pct']:+.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())