"""Audit the ship platform intact-stability endpoint against an exact analytic baseline.

Verification debt QR-INTACT.  Read-only w.r.t. the ship platform: this script only
POSTs to ``/api/v1/calculations/stability`` and compares the returned numbers with
:mod:`intact_stability_baseline` (exact box-barge GZ), writing a JSON artifact and
figures under ``artifacts/verify_intact_stability/<run_id>/``.

Judgement criteria (fixed before the run, see docs/reports/QR-INTACT-*.md):
  C1  KB / BM / GM of a box barge (Cb=1) reproduce the exact values within 2%.
  C2  GZ(phi) reproduces the exact barge GZ within 3% at every heel angle up to the
      downflooding angle (the only range where an open-top barge has a righting curve).
  C3  IMO area criteria (0-30, 30-40, 0-40 m*rad) reproduce the exact values within 5%.
  C4  The endpoint must respond to freeboard (depth D): a near-zero-freeboard barge has
      a righting curve that terminates within a few degrees, so GZmax / vanishing angle
      must change when D changes at fixed B, T, KG.
  C5  The IMO verdicts must be falsifiable: a hull whose exact curve fails a criterion
      must not be reported as passing it.

Usage:  python3 tools/verify/verify_intact_stability.py [--base-url URL] [--out DIR]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import intact_stability_baseline as bl  # noqa: E402

DEFAULT_BASE = os.getenv("SHIP_PLATFORM_URL", "http://127.0.0.1:8001")

# Geometry presets (all Cb=1.0: a rectangular barge is the only hull for which an
# exact closed-form GZ exists, which is precisely why it is the audit vehicle).
CASES = {
    "barge_flush_deck": {"length": 100.0, "beam": 20.0, "depth": 10.0, "draft": 6.0, "cb": 1.0,
                         "kg": 5.0, "displacement": 12000.0},
    "barge_low_freeboard": {"length": 100.0, "beam": 20.0, "depth": 6.3, "draft": 6.0, "cb": 1.0,
                            "kg": 5.0, "displacement": 12000.0},
}
FREEBOARD_SWEEP = [6.3, 7.0, 8.0, 10.0, 14.0, 20.0]
CB_SWEEP = [0.60, 0.70, 0.80, 0.85]


# ---------------------------------------------------------------------------
# API access
# ---------------------------------------------------------------------------
def api_stability(base: str, payload: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
    """POST the stability payload; returns parsed JSON or {'error': ...}."""
    req = urllib.request.Request(
        f"{base.rstrip('/')}/api/v1/calculations/stability",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"status": r.status, "body": json.loads(r.read())}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "error": e.read().decode()[:400]}
    except Exception as e:  # pragma: no cover - network dependent
        return {"status": 0, "error": str(e)}


def reload(path: str) -> Dict[str, Any]:
    return json.loads(Path(path).read_text())


# ---------------------------------------------------------------------------
# Attributed platform formula (proven, not assumed: see test_api_formula_attribution)
# ---------------------------------------------------------------------------
def platform_curve(gm: float, bm: float, angles: List[float]) -> List[float]:
    """GZ curve implied by the audited endpoint: sin*phi*(GM+0.5*BM*sin^2) + 40deg taper."""
    out = []
    for a in angles:
        p = math.radians(a)
        gz = (gm + 0.5 * bm * math.sin(p) ** 2) * math.sin(p)
        if a > 40:
            gz *= (60.0 - a) / 20.0
        out.append(max(0.0, gz))
    return out


def morrish_kb(T: float, cb: float, cw: float) -> float:
    """Morrish estimate KB = T - (1/3)(T/2 + nabla/Aw); nabla/Aw = cb*T/cw."""
    return T - (1.0 / 3.0) * (T / 2.0 + cb * T / cw)


# ---------------------------------------------------------------------------
# Case audit
# ---------------------------------------------------------------------------
def audit_case(base: str, name: str, geo: Dict[str, Any]) -> Dict[str, Any]:
    B, D, T, KG = geo["beam"], geo["depth"], geo["draft"], geo["kg"]
    api = api_stability(base, geo)
    ref_open = bl.barge_baseline(B, D, T, KG, open_top=True)
    ref_closed = bl.barge_baseline(B, D, T, KG, open_top=False)

    rec: Dict[str, Any] = {"case": name, "geometry": geo, "api_status": api.get("status")}
    if "body" not in api:
        rec["api_error"] = api.get("error")
        return rec
    body = api["body"]
    rec["api"] = body

    # C1: hydrostatics
    rec["hydrostatics"] = {
        "kb": {"api": body["kb"], "exact": bl.box_kb(T),
               "err_pct": _pct(body["kb"], bl.box_kb(T))},
        "bm": {"api": body["bm"], "exact": bl.box_bm(B, T),
               "err_pct": _pct(body["bm"], bl.box_bm(B, T))},
        "gm": {"api": body["gm"], "exact": bl.box_gm(B, T, KG),
               "err_pct": _pct(body["gm"], bl.box_gm(B, T, KG))},
    }

    # C2: GZ curve (platform curve reconstructed from the parameters it reports)
    angles = [float(a) for a in range(0, 61, 5)]
    plat = platform_curve(body["gm"], body["bm"], angles)
    exact_closed = [bl.box_gz(B, D, T, KG, a) for a in angles]
    phi_f = ref_open["flooding_angle_deg"]
    per_angle = []
    for a, gp, ge in zip(angles, plat, exact_closed):
        per_angle.append({
            "phi_deg": a, "platform": round(gp, 4), "exact_closed_box": round(ge, 4),
            "usable": a <= phi_f + 1e-9,
            "err_pct": (_pct(gp, ge) if abs(ge) > 1e-9 else (0.0 if abs(gp) < 1e-9 else None)),
        })
    rec["gz_curve"] = per_angle
    rec["usable_up_to_deg"] = phi_f

    # C3: areas + verdicts
    areas_plat = bl.imo_areas(angles, plat)
    rec["areas"] = {
        k: {"api_field": body.get(k), "reconstructed": round(areas_plat[k], 5),
            "exact_open_top": round(ref_open["areas"][k], 5),
            "exact_closed": round(ref_closed["areas"][k], 5),
            "err_pct_vs_exact_closed": _pct(areas_plat[k], ref_closed["areas"][k])}
        for k in ("area_0_30", "area_30_40", "area_0_40")
    }

    # C5: verdict comparison, key by key
    api_imo = body.get("imo_criteria", {})
    rec["verdicts"] = {
        "api": api_imo,
        "exact_open_top": ref_open["imo"],
        "exact_closed": ref_closed["imo"],
        "false_pass_vs_open_top": [k for k, v in ref_open["imo"].items()
                                   if (not v) and _lookup_api_verdict(api_imo, k)],
    }
    rec["gz_max"] = {"api": body["gz_max"], "exact_open_top": round(ref_open["gz_max"], 4),
                     "exact_closed": round(ref_closed["gz_max"], 4),
                     "err_pct_vs_exact_open_top": _pct(body["gz_max"], ref_open["gz_max"])}
    rec["vanishing_angle"] = {"api": body["vanishing_angle"],
                              "exact_open_top": round(ref_open["vanishing_angle"], 2),
                              "exact_closed": round(ref_closed["vanishing_angle"], 2)}
    return rec


def _pct(a: float, b: float) -> float:
    if abs(b) < 1e-12:
        return 0.0 if abs(a) < 1e-12 else float("inf")
    return round(abs(a - b) / abs(b) * 100.0, 3)


def _lookup_api_verdict(api_imo: Dict[str, bool], key: str) -> bool:
    """Match the human-readable API keys against baseline keys."""
    norm = lambda s: "".join(ch for ch in s.lower() if ch.isalnum())
    target = norm(key)
    for k, v in api_imo.items():
        nk = norm(k)
        if nk.startswith(target[:10]) or target[:10] in nk:
            return bool(v)
    return False


# ---------------------------------------------------------------------------
# Sensitivity sweeps (C4)
# ---------------------------------------------------------------------------
def freeboard_sweep(base: str) -> List[Dict[str, Any]]:
    rows = []
    for D in FREEBOARD_SWEEP:
        geo = {"length": 100.0, "beam": 20.0, "depth": D, "draft": 6.0, "cb": 1.0,
               "kg": 5.0, "displacement": 12000.0}
        api = api_stability(base, geo)
        ref = bl.barge_baseline(20.0, D, 6.0, 5.0, open_top=True)
        b = api.get("body", {})
        rows.append({
            "depth": D, "freeboard": round(D - 6.0, 2),
            "api_gz_max": b.get("gz_max"), "api_gz_max_angle": b.get("gz_max_angle"),
            "api_vanishing": b.get("vanishing_angle"),
            "api_area_0_40": b.get("area_0_40"),
            "api_verdicts_all_pass": all(b.get("imo_criteria", {}).values()) if b else None,
            "exact_gz_max": round(ref["gz_max"], 4),
            "exact_vanishing": round(ref["vanishing_angle"], 2),
            "exact_area_0_40": round(ref["areas"]["area_0_40"], 5),
            "exact_flooding_angle": round(ref["flooding_angle_deg"], 2),
        })
    return rows


def kb_consistency(base: str) -> List[Dict[str, Any]]:
    """Cb sweep: endpoint KB vs literature (Morrish) and vs the platform's own IMO module."""
    rows = []
    T = 10.0
    for cb in CB_SWEEP:
        geo = {"length": 200.0, "beam": 32.0, "depth": 20.0, "draft": T, "cb": cb,
               "kg": 12.0, "displacement": 200.0 * 32.0 * T * cb}
        api = api_stability(base, geo)
        b = api.get("body", {})
        cw = 0.75 + 0.25 * cb
        kb_api = b.get("kb")
        kb_morrish = morrish_kb(T, cb, cw)
        kb_imo_mod = T * (0.5 + 0.05 * cb)
        rows.append({
            "cb": cb, "cw": round(cw, 4),
            "api_kb": kb_api, "api_kb_over_T": round(kb_api / T, 5) if kb_api else None,
            "morrish_kb": round(kb_morrish, 4),
            "imo_module_kb": round(kb_imo_mod, 4),
            "err_vs_morrish_pct": _pct(kb_api, kb_morrish) if kb_api else None,
            "err_vs_own_imo_module_pct": _pct(kb_api, kb_imo_mod) if kb_api else None,
        })
    return rows


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def make_figures(out: Path, cases: Dict[str, Any], sweep: List[Dict[str, Any]]) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    made = []
    A = cases["barge_flush_deck"]
    if "api" in A:
        geo = A["geometry"]
        B, D, T, KG = geo["beam"], geo["depth"], geo["draft"], geo["kg"]
        angles = [float(a) for a in range(0, 65, 5)]
        plat = platform_curve(A["api"]["gm"], A["api"]["bm"], angles)
        exact_c = [bl.box_gz(B, D, T, KG, a) for a in angles]
        ref = bl.barge_baseline(B, D, T, KG, open_top=True)
        wall = [bl.wall_sided_gz(a, bl.box_gm(B, T, KG), bl.box_bm(B, T))
                if a <= ref["wall_sided_valid_limit_deg"] + 1e-9 else None for a in angles]

        fig, ax = plt.subplots(figsize=(7.5, 4.6))
        ax.plot(angles, plat, "o-", label="platform endpoint (attributed formula)", lw=1.8)
        ax.plot(angles, exact_c, "s-", label="exact box GZ (closed box, polygon)", lw=1.8)
        ax.plot([a for a, w in zip(angles, wall) if w is not None],
                [w for w in wall if w is not None], "k--", lw=1.2,
                label="wall-sided closed form (valid range)")
        ax.axvline(ref["flooding_angle_deg"], color="crimson", ls=":", lw=1.4,
                   label=f"downflooding {ref['flooding_angle_deg']:.1f} deg")
        ax.set_xlabel("heel angle (deg)"); ax.set_ylabel("GZ (m)")
        ax.set_title("Intact stability GZ: platform vs exact barge baseline\n"
                     f"B={B} D={D} T={T} KG={KG} (Cb=1)")
        ax.grid(alpha=.3); ax.legend(fontsize=8)
        p1 = out / "fig1_gz_curves.png"; fig.tight_layout(); fig.savefig(p1, dpi=140); plt.close(fig)
        made.append(p1.name)

        fig, ax = plt.subplots(figsize=(7.5, 3.8))
        rows = [r for r in A["gz_curve"] if r["phi_deg"] > 0]
        ax.bar([r["phi_deg"] for r in rows],
               [r["err_pct"] if r["err_pct"] is not None else 0.0 for r in rows],
               width=3.2, color="#c0392b")
        ax.axhline(3.0, color="k", ls="--", lw=1, label="3% acceptance")
        ax.set_xlabel("heel angle (deg)"); ax.set_ylabel("|platform - exact| / exact (%)")
        ax.set_title("C2 GZ deviation against exact closed-box baseline")
        ax.grid(alpha=.3, axis="y"); ax.legend(fontsize=8)
        p2 = out / "fig2_gz_error.png"; fig.tight_layout(); fig.savefig(p2, dpi=140); plt.close(fig)
        made.append(p2.name)

    if sweep:
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.6, 3.9))
        fb = [r["freeboard"] for r in sweep]
        ax1.plot(fb, [r["api_gz_max"] for r in sweep], "o-", label="platform GZmax")
        ax1.plot(fb, [r["exact_gz_max"] for r in sweep], "s--", label="exact GZmax (open top)")
        ax1.set_xlabel("freeboard D-T (m)"); ax1.set_ylabel("GZmax (m)")
        ax1.set_title("C4a freeboard response of GZmax"); ax1.grid(alpha=.3); ax1.legend(fontsize=8)
        ax2.plot(fb, [r["api_vanishing"] for r in sweep], "o-", label="platform vanishing angle")
        ax2.plot(fb, [r["exact_vanishing"] for r in sweep], "s--", label="exact curve end angle")
        ax2.set_xlabel("freeboard D-T (m)"); ax2.set_ylabel("angle (deg)")
        ax2.set_title("C4b freeboard response of curve end angle")
        ax2.grid(alpha=.3); ax2.legend(fontsize=8)
        p3 = out / "fig3_freeboard_sensitivity.png"; fig.tight_layout(); fig.savefig(p3, dpi=140)
        plt.close(fig)
        made.append(p3.name)
    return made


# ---------------------------------------------------------------------------
def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    run_id = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    repo = Path(__file__).resolve().parents[2]
    out = Path(args.out) if args.out else repo / "artifacts" / "verify_intact_stability" / run_id
    out.mkdir(parents=True, exist_ok=True)

    cases = {name: audit_case(args.base_url, name, geo) for name, geo in CASES.items()}
    probe = api_stability(args.base_url, CASES["barge_flush_deck"])
    if "body" not in probe:
        result = {"run_id": run_id, "base_url": args.base_url,
                  "api_reachable": False, "error": probe.get("error"), "cases": cases}
        (out / "report.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print(json.dumps({"api_reachable": False, "error": probe.get("error"), "out": str(out)},
                         ensure_ascii=False))
        return 2

    sweep = freeboard_sweep(args.base_url)
    kb_rows = kb_consistency(args.base_url)
    figs = make_figures(out, cases, sweep)

    # criteria scoreboard
    A = cases["barge_flush_deck"]
    c1 = max(abs(A["hydrostatics"]["gm"]["err_pct"]), abs(A["hydrostatics"]["kb"]["err_pct"]))
    usable = [r for r in A["gz_curve"] if r["usable"] and r["phi_deg"] > 0 and r["err_pct"] is not None]
    c2 = max((r["err_pct"] for r in usable), default=0.0)
    c3 = max(abs(v["err_pct_vs_exact_closed"]) for v in A["areas"].values())
    c4_invariant = (len({(r["api_gz_max"], r["api_vanishing"], r["api_area_0_40"]) for r in sweep}) == 1)
    c5_false_pass = bool(A["verdicts"]["false_pass_vs_open_top"])

    result = {
        "run_id": run_id,
        "base_url": args.base_url,
        "api_reachable": True,
        "criteria": {
            "C1_hydrostatics_err_pct": round(c1, 3), "C1_pass": c1 <= 2.0,
            "C2_max_gz_err_pct_usable": round(c2, 3), "C2_pass": c2 <= 3.0,
            "C3_max_area_err_pct": round(c3, 3), "C3_pass": c3 <= 5.0,
            "C4_platform_invariant_to_freeboard": c4_invariant, "C4_pass": not c4_invariant,
            "C5_false_pass_verdicts": A["verdicts"]["false_pass_vs_open_top"],
            "C5_pass": not c5_false_pass,
        },
        "cases": cases,
        "freeboard_sweep": sweep,
        "kb_consistency": kb_rows,
        "figures": figs,
    }
    (out / "report.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(json.dumps({"run_id": run_id, "out": str(out), "criteria": result["criteria"],
                      "figures": figs}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())