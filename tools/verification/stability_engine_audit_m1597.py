#!/usr/bin/env python3
"""Verification-debt audit: platform stability engine vs exact box hydrostatics.

Mission m1597 (self-driven, data seat).  Read-only w.r.t. the engine: this
script only *calls* ``POST /api/v1/calculations/stability`` and compares the
answer with :mod:`services.box_hydrostatics` (exact reference, no empirical
coefficient).

Audited engine (as deployed behind :8001 on 2026-11-01) lives in the
``ship-performance-platform`` checkout, ``backend/api/calculations.py``:

    calculate_kb:  KB = T·(1 − Cb/3)
    calculate_bm:  Cwp = 0.75 + 0.25·Cb ; BM = (Cwp²·B³/12)/(B·T·Cb)
    calculate_gz:  φ<15°  GZ = GM·sinφ
                   φ≥15°  GZ = (GM + ½·BM·sin²φ)·sinφ
    vanishing:     scan 0..60° on a 5° grid, default 60 (grid cap)
    areas:         trapezoid on the 5° grid

A frozen transcription of those formulas (``engine_gz`` & friends below) lets
the audit quantify the engine's own curve, including quantities the endpoint
does not expose (per-angle GZ, the 15° branch jump).

Outputs (all under artifacts/m1597/):
    stability_audit.json      machine-readable table
    stability_audit.md        markdown table (pasted into the report)
    gz_curves.png             exact vs engine GZ curves (+ error panel)
    kb_gm_error.png           KB / GM / GZmax error bars per benchmark case

Usage::

    python3 tools/verification/stability_engine_audit_m1597.py \
        [--api http://127.0.0.1:8001/api/v1] [--no-api]
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
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services import box_hydrostatics as ref  # noqa: E402
from services.stability_engine_audit_snapshot import (  # noqa: E402
    AUDITED_SOURCE, engine_bm, engine_curve, engine_gz, engine_kb)

OUT_DIR = REPO / "artifacts" / "m1597"

# ---------------------------------------------------------------------------
# Benchmark matrix: rectangular barges (Cb = 1) with closed-form hydrostatics
# ---------------------------------------------------------------------------
CASES: List[Dict] = [
    dict(name="A_std_barge",   L=60.0, B=12.0, T=3.0, D=6.0, KG=4.7,
         note="B=12 T=3 D=6 freeboard 3 m; exact GM=0.80 m"),
    dict(name="B_stiff",       L=60.0, B=12.0, T=3.0, D=6.0, KG=4.0,
         note="same hull, lower KG → exact GM=1.50 m"),
    dict(name="C_tender",      L=60.0, B=12.0, T=3.0, D=6.0, KG=5.2,
         note="same hull, exact GM=0.30 m (IMO min 0.15 m)"),
    dict(name="D_shallow",     L=40.0, B=18.0, T=2.0, D=3.5, KG=3.0,
         note="wide shallow barge: bilge emergence at 12.5°, deck at 9.5°"),
    dict(name="E_decky",       L=80.0, B=14.0, T=4.0, D=9.0, KG=6.0,
         note="tall-sided barge: wall-sided regime up to 29.7°"),
]


# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------
def call_api(base: str, case: Dict, timeout: float = 10.0) -> Optional[Dict]:
    # Deployed StabilityInput (as audited): length/beam/draft/displacement/cb/kg
    # (+ optional gm).  Note it carries NO depth/freeboard field, so deck-edge
    # immersion is not representable at all.
    payload = {
        "length": case["L"], "beam": case["B"], "draft": case["T"],
        "displacement": case["L"] * case["B"] * case["T"] * 1.025,
        "cb": 1.0, "kg": case["KG"],
    }
    req = urllib.request.Request(
        base.rstrip("/") + "/calculations/stability",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as fh:
            return json.loads(fh.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"  ! API unreachable for {case['name']}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------
def rel_err(value: Optional[float], truth: float) -> Optional[float]:
    if value is None:
        return None
    if abs(truth) < 1e-12:
        return None
    return (value - truth) / truth


def audit_case(base: Optional[str], case: Dict) -> Dict:
    hyd = ref.box_hydrostatics(case["L"], case["B"], case["T"])
    kb_x, bm_x = hyd["kb_m"], hyd["bm_m"]
    gm_x = kb_x + bm_x - case["KG"]
    bilge, deck = ref.wallsided_regime_limits_deg(case["B"], case["T"], case["D"])
    limit = min(bilge, deck)

    # exact curve on a 0.25° grid (Simpson areas, curve maximum)
    angles = [0.25 * i for i in range(0, 4 * 121)]
    gz_x = ref.gz_curve(angles, case["B"], case["T"], case["D"], case["KG"])
    gz_max_x = max(gz_x)
    gz_max_angle_x = angles[gz_x.index(gz_max_x)]
    vanish_x = ref.vanishing_angle_deg(case["B"], case["T"], case["D"], case["KG"])
    a0_30 = ref.gz_area(angles, gz_x, 0.0, 30.0)
    a30_40 = ref.gz_area(angles, gz_x, 30.0, 40.0)
    a0_40 = a0_30 + a30_40

    # wall-sided cross-check of the reference inside its validity window
    ws_check = max(abs(ref.box_gz(a, case["B"], case["T"], case["D"], case["KG"])
                       - ref.wallsided_gz(a, gm_x, bm_x))
                   for a in [0.5 * i for i in range(1, int(2 * limit) + 1)])

    eng = engine_curve(case["KG"], engine_kb(case["T"], 1.0), engine_bm(case["B"], case["T"], 1.0))

    api = call_api(base, case) if base else None

    def pick(key, fallback=None):
        return api.get(key, fallback) if api else None

    row = {
        "case": case["name"], "geometry": {k: case[k] for k in ("L", "B", "T", "D", "KG")},
        "note": case["note"],
        "wallsided_limit_deg": round(limit, 4),
        "reference_check_wallsided_identity_max_abs_m": ws_check,
        "reference": {"kb": kb_x, "bm": bm_x, "gm": gm_x,
                      "gz_max": round(gz_max_x, 6), "gz_max_angle": gz_max_angle_x,
                      "vanishing_angle": round(vanish_x, 3),
                      "area_0_30": round(a0_30, 6), "area_30_40": round(a30_40, 6),
                      "area_0_40": round(a0_40, 6)},
        "engine_replica": {k: (round(v, 6) if isinstance(v, float) else v)
                           for k, v in eng.items() if k not in ("angles", "gz", "gz_raw")},
        "api": api,
        "errors_vs_reference": {},
    }
    pairs = {
        "kb": (pick("kb"), kb_x),
        "bm": (pick("bm"), bm_x),
        "gm": (pick("gm"), gm_x),
        "gz_max": (pick("gz_max"), gz_max_x),
        "area_0_30": (pick("area_0_30"), a0_30),
        "area_30_40": (pick("area_30_40"), a30_40),
        "area_0_40": (pick("area_0_40"), a0_40),
    }
    for key, (val, truth) in pairs.items():
        e = rel_err(val, truth)
        row["errors_vs_reference"][key] = None if e is None else round(e, 6)
    if api:
        row["vanishing_angle_delta_deg"] = round(api.get("vanishing_angle", 0.0) - vanish_x, 3)
        row["gz_max_angle_delta_deg"] = round(api.get("gz_max_angle", 0.0) - gz_max_angle_x, 3)

    # IMO verdicts: engine vs exact curve (IS Code 2008 Part A, 6 criteria)
    def verdicts(gm, gz_max, gz_angle, ar30, ar40, ar040, vangle):
        return {
            "GM>=0.15": gm >= 0.15,
            "GZmax>=0.20": gz_max >= 0.20,
            "angle_GZmax>=25": gz_angle >= 25.0,
            "A30_40>=0.03": ar30 >= 0.03,
            "A0_40>=0.055": ar040 >= 0.055,
            "vanishing>=55": vangle >= 55.0,
        }

    v_ref = verdicts(gm_x, gz_max_x, gz_max_angle_x, a30_40, a0_40, a0_40, vanish_x)
    row["imo_reference"] = v_ref
    if api:
        # The deployed criteria dict uses human-readable keys; both dicts are in
        # IS-Code order (GM, GZmax, heel@GZmax, A30-40, A0-40, vanishing).
        crit = api.get("imo_criteria") or {}
        row["imo_engine"] = crit
        eng_vals = list(crit.values())
        row["imo_flips"] = {
            k: (eng_vals[i] if i < len(eng_vals) else None, v_ref[k])
            for i, k in enumerate(v_ref)
            if i < len(eng_vals) and bool(eng_vals[i]) != bool(v_ref[k])
        }
    row["all_pass_reference"] = all(v_ref.values())
    if api:
        row["all_pass_engine"] = all(bool(v) for v in (api.get("imo_criteria") or {}).values())
    return row


def engine_formula_diagnostics() -> Dict:
    """Quantify the engine's formula-level defects, independent of the API."""
    out: Dict = {}
    # (1) KB at the box limit: exact T/2, engine T(1-Cb/3)
    out["kb_box_limit"] = {
        "Cb": 1.0,
        "engine_over_T": engine_kb(1.0, 1.0),
        "exact_over_T": 0.5,
        "rel_error": (engine_kb(1.0, 1.0) - 0.5) / 0.5,
    }
    # (2) BM: engine uses Cwp² where waterplane inertia carries Cwp¹
    B, T = 12.0, 3.0
    rows = []
    for cb in (0.7, 0.8, 0.9, 1.0):
        cwp = 0.75 + 0.25 * cb
        bm_engine = engine_bm(B, T, cb)
        bm_ref = cwp * B ** 2 / (12.0 * T * cb)
        rows.append({"Cb": cb, "Cwp": cwp, "bm_engine": round(bm_engine, 5),
                     "bm_ref": round(bm_ref, 5),
                     "rel_error": round((bm_engine - bm_ref) / bm_ref, 5)})
    out["bm_cwp_exponent"] = rows
    # (3) GZ branch inconsistency at 15° and sin² vs tan² growth
    gm, bm = 0.8, 4.0
    jump = engine_gz(15.0, gm, bm) - engine_gz(14.999, gm, bm)
    out["gz_branch_jump_at_15deg_m"] = round(jump, 6)
    rows = []
    for a in (20.0, 30.0, 40.0, 50.0):
        e = engine_gz(a, gm, bm)
        t = ref.wallsided_gz(a, gm, bm)
        rows.append({"heel_deg": a, "engine_gz": round(e, 5), "wallsided_gz": round(t, 5),
                     "rel_error": round((e - t) / t, 5)})
    out["gz_profile_vs_wallsided"] = rows
    # (4) vanishing angle: 0..60° grid cap ⇒ any GM>0 barge reports 60°
    rows = []
    for kg in (5.6, 5.8, 6.0):  # GM = 5.5 - KG → 5.5-5.6 <0 excluded; use positive GMs
        pass
    for kg in (4.0, 4.7, 5.2, 5.3):
        eng = engine_curve(kg, engine_kb(T, 1.0), engine_bm(B, T, 1.0))
        rows.append({"KG": kg, "engine_gm": round(eng["gm"], 4),
                     "engine_vanishing": eng["vanishing_angle"]})
    out["vanishing_censoring"] = rows
    return out


def _pct(err) -> str:
    return "n/a" if err is None else f"{100.0 * err:+.1f}%"


def _num(val, unit: str = "") -> str:
    return "n/a" if val is None else f"{val:.4g}{unit}"


def write_markdown(rows: List[Dict], diag: Dict, api_used: bool) -> str:
    lines = ["# Stability engine audit vs exact box hydrostatics (mission m1597)", ""]
    lines.append(f"API queried: **{'yes' if api_used else 'no (offline mode)'}**")
    lines.append("")
    lines.append("| case | GM exact [m] | GM engine [m] | err GM | KB exact [m] | KB engine [m] | err KB "
                 "| GZmax exact [m] | GZmax engine [m] | err GZmax | vanishing exact | vanishing engine |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        a = r["api"] or {}
        e = r["errors_vs_reference"]
        lines.append(
            f"| {r['case']} | {r['reference']['gm']:.3f} | {_num(a.get('gm'))} | {_pct(e['gm'])} "
            f"| {r['reference']['kb']:.3f} | {_num(a.get('kb'))} | {_pct(e['kb'])} "
            f"| {r['reference']['gz_max']:.3f} | {_num(a.get('gz_max'))} | {_pct(e['gz_max'])} "
            f"| {r['reference']['vanishing_angle']:.2f} deg | {_num(a.get('vanishing_angle'), ' deg')} |")
    lines.append("")
    lines.append("| case | A(0-30) exact | A(0-30) engine | err | A(30-40) exact | A(30-40) engine | err | A(0-40) exact | A(0-40) engine | err |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        a = r["api"] or {}
        e = r["errors_vs_reference"]
        lines.append(
            f"| {r['case']} | {r['reference']['area_0_30']:.4f} | {_num(a.get('area_0_30'))} | {_pct(e['area_0_30'])} "
            f"| {r['reference']['area_30_40']:.4f} | {_num(a.get('area_30_40'))} | {_pct(e['area_30_40'])} "
            f"| {r['reference']['area_0_40']:.4f} | {_num(a.get('area_0_40'))} | {_pct(e['area_0_40'])} |")
    lines.append("")
    lines.append("## IMO verdict comparison (engine vs exact curve)")
    lines.append("")
    for r in rows:
        if not r.get("imo_engine"):
            continue
        flips = r.get("imo_flips") or {}
        verdict = "identical" if not flips else ", ".join(
            f"{k}: engine={v[0]} exact={v[1]}" for k, v in flips.items())
        lines.append(f"* **{r['case']}** - {verdict}")
    lines.append("")
    lines.append("## Formula-level diagnostics (no API needed)")
    lines.append("")
    d1 = diag["kb_box_limit"]
    lines.append(f"* KB at the box limit (Cb=1): engine `KB/T = {d1['engine_over_T']:.4f}` vs exact `0.5` "
                 f"-> **{100 * d1['rel_error']:+.1f}%**")
    lines.append(f"* GZ branch jump at 15 deg: **{diag['gz_branch_jump_at_15deg_m']:.4f} m**")
    for row in diag["gz_profile_vs_wallsided"]:
        lines.append(f"  * phi={row['heel_deg']:.0f} deg: engine {row['engine_gz']:.4f} m vs wall-sided "
                     f"{row['wallsided_gz']:.4f} m ({100 * row['rel_error']:+.1f}%)")
    lines.append("* BM waterplane-inertia exponent (engine `Cwp^2` vs `Cwp^1`):")
    for row in diag["bm_cwp_exponent"]:
        lines.append(f"  * Cb={row['Cb']}: BM engine {row['bm_engine']:.4f} m vs Cwp^1 reference "
                     f"{row['bm_ref']:.4f} m ({100 * row['rel_error']:+.1f}%)")
    lines.append("* Vanishing-angle censoring (0-60 deg grid, default 60 deg):")
    for row in diag["vanishing_censoring"]:
        lines.append(f"  * KG={row['KG']}: GM={row['engine_gm']:.2f} m -> engine vanishing "
                     f"{row['engine_vanishing']:.0f} deg")
    lines.append("")
    return "\n".join(lines)


def make_figures(rows: List[Dict]) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    made = []

    # Figure 1: GZ curves (exact vs engine) + error panel for case A and D
    fig, axes = plt.subplots(2, 2, figsize=(11, 8))
    for ax, row in zip(axes[0], [rows[0], rows[3]]):
        c = row["geometry"]
        angles = [0.5 * i for i in range(0, 181)]
        gz_x = ref.gz_curve(angles, c["B"], c["T"], c["D"], c["KG"])
        eng = engine_curve(c["KG"], engine_kb(c["T"], 1.0), engine_bm(c["B"], c["T"], 1.0))
        ax.plot(angles, gz_x, "k-", lw=2, label="exact box hydrostatics (reference)")
        ax.plot(eng["angles"], eng["gz"], "o--", color="tab:red", ms=4,
                label="platform engine (as deployed)")
        ax.axvline(row["wallsided_limit_deg"], color="tab:blue", ls=":", lw=1.2,
                   label=f"wall-sided limit {row['wallsided_limit_deg']:.1f}°")
        ax.axvline(55.0, color="0.6", ls="-.", lw=1, label="IMO vanishing limit 55°")
        ax.set_xlabel("heel angle φ [deg]")
        ax.set_ylabel("GZ [m]")
        ax.set_title(f"{row['case']}: B={c['B']:.0f} T={c['T']:.0f} D={c['D']:.0f} KG={c['KG']:.1f}")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7)
    for ax, row in zip(axes[1], [rows[0], rows[3]]):
        c = row["geometry"]
        angles = [0.5 * i for i in range(0, 181)]
        gz_x = ref.gz_curve(angles, c["B"], c["T"], c["D"], c["KG"])
        eng = engine_curve(c["KG"], engine_kb(c["T"], 1.0), engine_bm(c["B"], c["T"], 1.0))
        e_gz = [engine_gz(a, eng["gm"], engine_bm(c["B"], c["T"], 1.0)) for a in angles]
        err = [100.0 * (e - t) / t if abs(t) > 1e-9 else 0.0 for e, t in zip(e_gz, gz_x)]
        ax.plot(angles, err, "r-", lw=1.6)
        ax.axhline(0, color="k", lw=0.8)
        ax.axhline(15, color="0.6", ls=":", lw=1, label="±15% acceptance band")
        ax.axhline(-15, color="0.6", ls=":", lw=1)
        ax.set_xlabel("heel angle φ [deg]")
        ax.set_ylabel("GZ error [%]")
        ax.set_title(f"{row['case']}: engine GZ error vs reference")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7)
    fig.suptitle("m1597 — platform stability engine vs exact box hydrostatics", fontsize=11)
    fig.tight_layout()
    p = OUT_DIR / "gz_curves.png"
    fig.savefig(p, dpi=140)
    plt.close(fig)
    made.append(str(p.relative_to(REPO)))

    # Figure 2: bar chart of key quantity errors across cases
    keys = ["kb", "gm", "gz_max", "area_0_40"]
    fig, ax = plt.subplots(figsize=(9, 4.6))
    width = 0.8 / len(keys)
    xs = range(len(rows))
    for j, key in enumerate(keys):
        vals = [100.0 * (r["errors_vs_reference"].get(key) or 0.0) for r in rows]
        ax.bar([x + j * width for x in xs], vals, width=width, label=key)
    ax.axhline(0, color="k", lw=0.8)
    ax.axhspan(-15, 15, color="tab:green", alpha=0.08, label="±15% band")
    ax.set_xticks([x + 1.5 * width for x in xs])
    ax.set_xticklabels([r["case"] for r in rows], fontsize=8)
    ax.set_ylabel("relative error vs reference [%]")
    ax.set_title("m1597 — stability engine error by quantity and case")
    ax.legend(fontsize=7)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    p = OUT_DIR / "kb_gm_error.png"
    fig.savefig(p, dpi=140)
    plt.close(fig)
    made.append(str(p.relative_to(REPO)))
    return made


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001/api/v1"))
    ap.add_argument("--no-api", action="store_true")
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    base = None if args.no_api else args.api
    rows = []
    for case in CASES:
        print(f"[audit] {case['name']} ...")
        rows.append(audit_case(base, case))
    api_used = any(r["api"] for r in rows)
    diag = engine_formula_diagnostics()
    figs = make_figures(rows)

    payload = {"mission": "m1597", "api_base": base, "api_reachable": api_used,
               "audited_engine": AUDITED_SOURCE,
               "cases": rows, "formula_diagnostics": diag, "figures": figs}
    (OUT_DIR / "stability_audit.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "stability_audit.md").write_text(
        write_markdown(rows, diag, api_used), encoding="utf-8")
    print(f"[audit] wrote {OUT_DIR/'stability_audit.json'}")
    if api_used:
        for r in rows:
            e = r["errors_vs_reference"]
            print(f"  {r['case']}: err GM={e['gm']}, KB={e['kb']}, GZmax={e['gz_max']}, "
                  f"vanish Δ={r.get('vanishing_angle_delta_deg')}°, flips={r.get('imo_flips')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())