#!/usr/bin/env python3
"""Resistance validation-chain audit (verification debt, mission 1452).

The platform exposes `/calculations/benchmark_validate`, which compares the
Holtrop/ITTC-57 resistance calculation against `backend/data/benchmark_reference.json`
and reports a pass/fail against a 15 % error threshold.  That verdict is only as
good as the reference numbers, so this tool audits the reference *dataset itself*
against three physical invariants that any honest resistance record must satisfy:

    I1  Froude number      Fn   = V / sqrt(g L)                      (dimensional)
    I2  Force consistency  Rt   = 0.5 rho S V^2 Ct                    (definition of Ct)
    I3  Wetted-surface     S/(L B) within [0.6, 3.0] for surface ships,
                           S/(pi D L) within [0.5, 1.05] for bodies of revolution

plus a plausibility band on the dimensionless coefficient itself:

    I4  Ct / Cf(Re) in [1.1, 3.0]   (viscous floor from ITTC-57, wave/form on top)

Optional (`--live-url`): cross-check a running resistance service against the
same invariants, so a deployed implementation cannot drift away from the
worktree code without being caught.

Usage
-----
    python3 tools/audit_resistance_validation.py                    # offline audit
    python3 tools/audit_resistance_validation.py --live-url http://127.0.0.1:8001/api/v1
    python3 tools/audit_resistance_validation.py --json artifacts/.../latest.json

Exit code 0 when every case satisfies all invariants, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

G = 9.81
RHO_FRESH = 998.0     # kg/m^3, fresh water at 20 C
RHO_SEA = 1025.0      # kg/m^3, sea water at 15 C
NU_FRESH = 1.004e-6   # m^2/s, ITTC-57 fresh water at 20 C
NU_SEA = 1.19e-6      # m^2/s, sea water at 15 C

# Tolerances: reference tables are quoted to 3-4 significant digits.
TOL_FN = 0.01         # absolute, Fn
TOL_RT = 0.02         # relative, Rt
S_LB_BAND = (0.6, 3.0)
S_AXIS_BAND = (0.5, 1.05)
CT_CF_BAND = (1.1, 3.0)


def ittc57_cf(re: float) -> float:
    """ITTC-1957 model-ship correlation line."""
    return 0.075 / (math.log10(re) - 2.0) ** 2


def audit_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """Audit one dataset case against invariants I1-I4."""
    geom = case.get("geometry", {})
    length = geom.get("length_m")
    speed = case.get("speed_ms")
    ct = case.get("experimental_ct")
    rt = case.get("experimental_rt_n")
    fn_reported = case.get("froude_number")

    checks: List[Dict[str, Any]] = []

    # ---- I1: Froude number -------------------------------------------------
    fn_calc = speed / math.sqrt(G * length) if (speed and length) else None
    if fn_calc is not None and fn_reported is not None:
        delta = abs(fn_reported - fn_calc)
        checks.append({
            "invariant": "I1_froude",
            "reported": fn_reported,
            "recomputed": round(fn_calc, 4),
            "delta": round(delta, 4),
            "ok": delta <= TOL_FN,
            "detail": f"Fn = V/sqrt(gL) = {speed}/sqrt(9.81*{length})",
        })

    # ---- I2/I3: force + wetted surface ------------------------------------
    s_doc = geom.get("wetted_surface_m2")
    rho = RHO_SEA if (length or 0) > 50 else RHO_FRESH
    nu = NU_SEA if (length or 0) > 50 else NU_FRESH

    if s_doc and rt and ct and speed:
        rt_calc = 0.5 * rho * s_doc * speed ** 2 * ct
        rel = abs(rt - rt_calc) / rt_calc
        checks.append({
            "invariant": "I2_force",
            "reported_rt_n": rt,
            "rt_from_ct_n": round(rt_calc, 2),
            "reported_over_expected": round(rt / rt_calc, 3),
            "rel_err": round(rel, 4),
            "ok": rel <= TOL_RT,
            "detail": "Rt = 0.5*rho*S*V^2*Ct with documented S",
        })
    elif rt and ct and speed:
        # No documented wetted surface: invert the definition and judge the
        # implied area's plausibility instead.
        s_imp = rt / (0.5 * rho * speed ** 2 * ct)
        beam = geom.get("beam_m") or geom.get("diameter_m")
        draft = geom.get("draft_m") or geom.get("diameter_m")
        axisym = "diameter_m" in geom
        if axisym:
            ref = math.pi * geom["diameter_m"] * length
            band = S_AXIS_BAND
            basis = "S/(pi*D*L)"
        else:
            ref = length * beam
            band = S_LB_BAND
            basis = "S/(L*B)"
        ratio = s_imp / ref
        checks.append({
            "invariant": "I3_implied_wetted_surface",
            "implied_s_m2": round(s_imp, 3),
            "reference_scale_m2": round(ref, 3),
            "basis": basis,
            "ratio": round(ratio, 3),
            "band": list(band),
            "ok": band[0] <= ratio <= band[1],
            "detail": "implied S from Rt = 0.5 rho S V^2 Ct, compared with hull scale",
        })
        del draft

    # ---- I4: coefficient plausibility -------------------------------------
    if speed and length and ct:
        re = speed * length / nu
        cf = ittc57_cf(re)
        ratio = ct / cf
        checks.append({
            "invariant": "I4_ct_over_cf",
            "re": round(re, 1),
            "cf_ittc57": round(cf, 6),
            "ratio": round(ratio, 3),
            "band": list(CT_CF_BAND),
            "ok": CT_CF_BAND[0] <= ratio <= CT_CF_BAND[1],
            "detail": "Ct must exceed the ITTC-57 flat-plate floor by a form+wave margin",
        })

    # ---- I5: block coefficient vs displacement ----------------------------
    disp = geom.get("displacement_kg") or geom.get("displacement_t")
    cb_rep = geom.get("cb")
    beam = geom.get("beam_m")
    draft = geom.get("draft_m")
    if disp and cb_rep and beam and draft and length:
        mass = disp * 1000.0 if geom.get("displacement_t") else disp
        rho_i5 = RHO_SEA if length > 50 else RHO_FRESH
        cb_geom = (mass / rho_i5) / (length * beam * draft)
        checks.append({
            "invariant": "I5_cb_vs_displacement",
            "cb_reported": cb_rep,
            "cb_from_geometry": round(cb_geom, 4),
            "delta": round(abs(cb_rep - cb_geom), 4),
            "ok": abs(cb_rep - cb_geom) <= 0.02,
            "detail": "Cb = (mass/rho)/(L*B*T) must reproduce the tabulated Cb",
        })

    failed = [c["invariant"] for c in checks if not c["ok"]]
    return {
        "case_id": case["case_id"],
        "ship_type": case.get("ship_type"),
        "speed_ms": speed,
        "checks": checks,
        "failed_invariants": failed,
        "verdict": "CONSISTENT" if not failed else "INCONSISTENT",
    }


def audit_live(url: str, timeout: float = 10.0) -> Dict[str, Any]:
    """Cross-check a running resistance service against I1/I2 and force scaling."""
    payload = {
        "ship_type": "SUBOFF", "speed": 3.0, "length": 4.356,
        "beam": 0.508, "draft": 0.508, "displacement": 0.508, "cb": 0.542,
    }
    req = urllib.request.Request(
        f"{url.rstrip('/')}/calculations/resistance",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        return {"url": url, "reachable": False, "error": str(exc)}

    V, L, D, cb = 3.0, 4.356, 0.508, 0.542
    checks: List[Dict[str, Any]] = []
    re_exp = V * L / NU_FRESH
    fn_exp = V / math.sqrt(G * L)

    if "reynolds_number" in body:
        got = body["reynolds_number"]
        checks.append({
            "invariant": "L1_reynolds",
            "got": got, "expected": round(re_exp, 1),
            "ratio": round(got / re_exp, 4),
            "ok": abs(got / re_exp - 1) <= TOL_RT,
        })
    if "froude_number" in body:
        got = body["froude_number"]
        checks.append({
            "invariant": "L2_froude",
            "got": got, "expected": round(fn_exp, 4),
            "delta": round(abs(got - fn_exp), 4),
            "ok": abs(got - fn_exp) <= TOL_FN,
        })
    # Force closure uses whatever wetted surface the service itself reports.
    s = body.get("wetted_surface") or body.get("wetted_surface_m2")
    ct = body.get("ct")
    rt = body.get("rt") or body.get("total_resistance_n")
    if s and ct and rt:
        rt_exp = 0.5 * RHO_FRESH * s * V ** 2 * ct
        checks.append({
            "invariant": "L3_force_closure",
            "rt_reported_n": rt, "rt_from_ct_n": round(rt_exp, 3),
            "reported_over_expected": round(rt / rt_exp, 5),
            "ok": abs(rt / rt_exp - 1) <= TOL_RT,
        })
    return {
        "url": url, "reachable": True, "response": body, "checks": checks,
        "failed_invariants": [c["invariant"] for c in checks if not c["ok"]],
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", default="backend/data/benchmark_reference.json")
    ap.add_argument("--live-url", default=None,
                    help="e.g. http://127.0.0.1:8001/api/v1 to audit a deployed service")
    ap.add_argument("--json", default=None, help="write the audit as JSON here")
    args = ap.parse_args(argv)

    data = json.loads(Path(args.dataset).read_text())
    cases = data["cases"]
    results = [audit_case(c) for c in cases]

    print(f"# resistance validation-chain audit — {args.dataset} ({len(cases)} cases)\n")
    print(f"{'case_id':14s} {'verdict':13s} failed invariants")
    print("-" * 72)
    for r in results:
        print(f"{r['case_id']:14s} {r['verdict']:13s} {', '.join(r['failed_invariants']) or '-'}")

    inconsist = [r for r in results if r["verdict"] != "CONSISTENT"]
    print(f"\ncases failing >=1 invariant: {len(inconsist)}/{len(cases)}")

    live = audit_live(args.live_url) if args.live_url else None
    if live:
        print(f"\n# live service audit — {live['url']}")
        if not live["reachable"]:
            print(f"unreachable: {live['error']}")
        else:
            for c in live["checks"]:
                print(f"  {c['invariant']:18s} ok={c['ok']}  "
                      f"{ {k: v for k, v in c.items() if k not in ('invariant', 'ok')} }")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dataset": args.dataset,
            "n_cases": len(cases),
            "n_inconsistent": len(inconsist),
            "cases": results,
            "live": live,
        }
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"\nwrote {out}")

    return 0 if not inconsist else 1


if __name__ == "__main__":
    sys.exit(main())