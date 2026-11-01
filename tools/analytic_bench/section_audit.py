#!/usr/bin/env python3
"""Live adjudication of the platform's intact-stability endpoints on real Cb.

Until now the analytic benchmark (m1532) could only judge box barges, so for
every realistic block coefficient the platform verdict was WITHHELD.  With the
sloped-bilge family (``reference.SlopedBilgeHull``) the same analytic/numeric
machinery now has a reference for ``Cb < 1``, which is what this script
exercises against the running ship platform:

  * ``KB`` -- three independent references (trapezoid closed form, shoelace
    centroid of the immersed section, and the m1473 kernel formula);
  * ``BM`` and ``GM`` -- ``B^2/(12 Cb T)`` and ``KB + BM - KG``;
  * ``GZ`` at a heel grid -- judged only inside the wall-sided validity domain,
    because beyond it the shipped closed form is not merely inaccurate but
    unphysical.

Outputs (artifacts/analytic_bench/)::

    section_audit.json     machine-readable verdicts (all numbers come from here)
    section_audit.md       per-case tables
    fig_section_audit.png  GZ curves + KB error growth with Cb

Usage: python3 tools/analytic_bench/section_audit.py [--api http://127.0.0.1:8001]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.analytic_bench import reference as R  # noqa: E402

OUT = ROOT / "artifacts" / "analytic_bench"
TOL_OK, TOL_BAD = 3.0, 15.0

CASES = [
    R.SlopedBilgeHull(160.0, 26.0, 9.0, 16.0, 8.5, cb=0.90, bilge_rise_frac=0.25,
                      name="tanker-like Cb0.90"),
    R.SlopedBilgeHull(180.0, 28.0, 10.0, 18.0, 9.0, cb=0.80, bilge_rise_frac=0.45,
                      name="bulk-like Cb0.80"),
    R.SlopedBilgeHull(120.0, 20.0, 7.0, 12.0, 6.5, cb=0.70, bilge_rise_frac=0.62,
                      name="cargo-like Cb0.70"),
]


def post(base: str, path: str, body: Dict[str, Any], timeout: float = 60.0) -> Dict[str, Any]:
    url = f"{base.rstrip('/')}/api/v1/{path.lstrip('/')}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def verdict(err: Optional[float], defined: bool = True) -> str:
    if err is None or not math.isfinite(err):
        return "WITHHELD"
    if not defined:
        return "WITHHELD"
    a = abs(err)
    return "PASS" if a <= TOL_OK else ("FAIL" if a >= TOL_BAD else "ALERT")


def cmp(ref: float, calc: Optional[float], defined: bool = True) -> Dict[str, Any]:
    if calc is None:
        return {"ref": round(ref, 4), "calc": None, "err_pct": None, "verdict": "WITHHELD"}
    err = (float(calc) - float(ref)) / ref * 100.0 if ref else float("nan")
    return {"ref": round(ref, 4), "calc": round(float(calc), 4),
            "err_pct": round(err, 3), "verdict": verdict(err, defined)}


def audit(base: str, hull: R.SlopedBilgeHull) -> Dict[str, Any]:
    body_a = dict(length=hull.length, beam=hull.beam, draft=hull.draft,
                  displacement=hull.displacement_t, cb=hull.cb, kg=hull.kg)
    body_b = dict(L=hull.length, B=hull.beam, T=hull.draft, D=hull.depth,
                  displacement=hull.displacement_t, Cb=hull.cb, Cm=hull.cb, KG=hull.kg)
    # geometry priors: a prismatic section has Cw = 1, Cp = 1 and Cm = Cb, so the
    # full-breadth waterplane behind BM = B^2/(12 Cb T) is consistent with the body
    # actually sent (checked in tests/test_stability_section_benchmark.py).
    rec: Dict[str, Any] = {"case": hull.name, "cb": hull.cb,
                           "bilge_rise_frac": hull.bilge_rise_frac,
                           "geometry": {"L": hull.length, "B": hull.beam,
                                        "T": hull.draft, "D": hull.depth, "KG": hull.kg},
                           "reference": {"KB": round(hull.kb, 4), "BM": round(hull.bm, 4),
                                         "GM": round(hull.gm, 4),
                                         "wall_sided_limit_deg": round(hull.wall_sided_limit_deg, 2),
                                         "KB_shoelace": round(hull.kb_numeric(), 4)},
                           "errors": {}}
    try:
        a = post(base, "calculations/stability", body_a)
        b = post(base, "stability/gz-curve", body_b)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
        return rec

    rec["platform_A_calculations_stability"] = {k: a.get(k) for k in
                                                ("kb", "bm", "gm", "displacement")}
    rec["errors"]["KB"] = cmp(hull.kb, a.get("kb"))
    rec["errors"]["BM"] = cmp(hull.bm, a.get("bm"))
    rec["errors"]["GM"] = cmp(hull.gm, a.get("gm"))

    angles: List[float] = b["angles"]
    gz: List[float] = b["gz_values"]
    limit = hull.wall_sided_limit_deg
    rows = []
    for phi, gz_c in zip(angles, gz):
        gz_r = hull.gz_numeric(phi)
        defined = phi <= limit + 1e-9
        err = (gz_c - gz_r) / gz_r * 100.0 if abs(gz_r) > 1e-9 else None
        rows.append({"heel_deg": phi, "calc": round(gz_c, 4), "ref": round(gz_r, 4),
                     "err_pct": None if err is None else round(err, 3),
                     "in_domain": defined,
                     "verdict": verdict(err, defined)})
    judged = [r for r in rows if r["in_domain"] and r["err_pct"] is not None]
    rec["gz_points"] = rows
    rec["gz_summary"] = {
        "n_judged": len(judged),
        "max_abs_err_pct": round(max((abs(r["err_pct"]) for r in judged), default=0.0), 3),
        "verdict_at_each_angle": {str(r["heel_deg"]): r["verdict"] for r in judged},
    }
    # effective GM implied by the 5 deg GZ point (platform inlet consistency)
    if len(angles) > 1 and gz[1] > 0:
        rec["errors"]["GM_from_gz5"] = cmp(
            hull.gm, gz[1] / math.sin(math.radians(angles[1])))
    return rec


def figure(cases: List[Dict[str, Any]], swing: List[Dict[str, float]], path: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.5, 4.6))
    hull = CASES[1]
    angles = [r["heel_deg"] for r in cases[1]["gz_points"]]
    ax1.plot(angles, [r["ref"] for r in cases[1]["gz_points"]], "k-", lw=2,
             label="independent numeric (polygon clip)")
    ax1.plot(angles, [r["calc"] for r in cases[1]["gz_points"]], "r--o", ms=3,
             label="platform /stability/gz-curve")
    lim = hull.wall_sided_limit_deg
    ax1.axvspan(0, lim, color="green", alpha=0.10)
    ax1.axvline(lim, color="green", ls=":", lw=1)
    ax1.text(lim / 2, ax1.get_ylim()[1] * 0.96, " wall-sided\n validity",
             fontsize=8, color="darkgreen", va="top")
    ax1.set_xlabel("heel angle [deg]")
    ax1.set_ylabel("GZ [m]")
    ax1.set_title(f"GZ, realistic hull {hull.name}\n(judged only inside validity domain)")
    ax1.legend(fontsize=8, loc="upper left")
    ax1.grid(alpha=0.3)

    cbs = [r["cb"] for r in swing]
    ax2.plot(cbs, [r["err_platform_pct"] for r in swing], "r^-",
             label="platform  KB = T(1-0.5(1-Cb))")
    ax2.plot(cbs, [r["err_kernel_pct"] for r in swing], "gs-",
             label="in-repo kernel KB = T(1+1/(2Cb))/3")
    ax2.axhline(3.0, color="grey", ls="--", lw=1)
    ax2.axhline(-3.0, color="grey", ls="--", lw=1)
    ax2.set_xlabel("block coefficient Cb")
    ax2.set_ylabel("KB error vs analytic [%]")
    ax2.set_title("KB: platform approximation vs corrected kernel")
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return True


def kb_swing(length=100.0, beam=20.0, draft=8.0) -> List[Dict[str, float]]:
    """KB error of the platform formula and of the in-repo kernel over Cb."""
    rows = []
    for i in range(11):
        cb = 0.55 + i * 0.045
        ref = R.section_kb(draft, beam, cb, 1.0)
        plat = draft * (1.0 - 0.5 * (1.0 - cb))
        ker = draft * (1.0 + 1.0 / (2.0 * cb)) / 3.0
        rows.append({"cb": round(cb, 3), "kb_ref": round(ref, 4),
                     "err_platform_pct": round((plat - ref) / ref * 100.0, 2),
                     "err_kernel_pct": round((ker - ref) / ref * 100.0, 2)})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001"))
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    cases = [audit(args.api, hull) for hull in CASES]
    swing = kb_swing()
    payload = {"api": args.api, "cases": cases, "kb_swing": swing,
               "tolerance": {"pass_pct": TOL_OK, "fail_pct": TOL_BAD}}
    (out / "section_audit.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                            encoding="utf-8")

    lines = ["# Real-hull intact-stability adjudication (sloped-bilge reference)", "",
             f"API: `{args.api}`  |  PASS <= {TOL_OK} %  |  FAIL >= {TOL_BAD} %", ""]
    for c in cases:
        lines.append(f"## {c['case']} (Cb={c['cb']}, bilge rise "
                     f"{c['bilge_rise_frac']}T, validity limit "
                     f"{c['reference']['wall_sided_limit_deg']} deg)")
        if "error" in c:
            lines += [f"platform unreachable: {c['error']}", ""]
            continue
        for k, v in c["errors"].items():
            lines.append(f"- {k}: ref {v['ref']} / calc {v['calc']} -> "
                         f"{v['err_pct']} % ({v['verdict']})")
        g = c["gz_summary"]
        lines.append(f"- GZ in-domain: {g['n_judged']} points, max |err| "
                     f"{g['max_abs_err_pct']} %")
        lines.append("")
    (out / "section_audit.md").write_text("\n".join(lines), encoding="utf-8")
    ok = figure(cases, swing, out / "fig_section_audit.png")
    print(json.dumps({"cases": [c["case"] for c in cases],
                      "figure": ok,
                      "kb_errors": {c["case"]: c["errors"]["KB"]["err_pct"] for c in cases
                                    if "errors" in c and "KB" in c["errors"]}},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())