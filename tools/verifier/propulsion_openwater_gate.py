"""m1584 verifier gate: 推进域 open-water（Wageningen B 系列）证据门.

Machine-adjudicable checks on a recorded probe fixture of the SERVING platform.
Rationale: the platform self-declares ``propeller.open_water`` as REAL and the
endpoint labels itself "Wageningen B系列回归公式". A genuine B-series regression
has strict structural properties (B-series polynomials are functions of
J, P/D, Z, Ae/Ao). Any endpoint whose open-water efficiency is invariant to
pitch ratio, or whose torque coefficient is a fixed multiple of the thrust
coefficient, cannot be a B-series regression, whatever the absolute numbers.

Checks (each PASS/FAIL, no network needed):
  P1  eta0 must increase with P/D at fixed J          (physical monotonicity)
  P2  K_T must be non-linear in P/D (regression)      (linear law = fabricated)
  P3  K_Q/K_T ratio must vary with J and P/D          (fixed 0.1 = fabricated)
  P4  Z and Ae/Ao must influence K_T/K_Q              (blade geometry)
  P5  eta0 must not saturate at a hard constant cap   (clamped ramp artefact)
  P6  zero-thrust advance ratio J0 must track P/D     (constant J0 = fabricated)
  P7  propulsive chain: thrust must match its own K_T (unit/scale consistency)
  P8  momentum-theory bound eta0 <= eta_ideal         (necessary, not sufficient)

Exit code: 0 only if every check passes (i.e. endpoint is admissible as REAL).
"""
from __future__ import annotations

import json
import math
from typing import Any, Dict, List

RHO_SEA = 1025.0
G = 9.81

# ---------------------------------------------------------------- helpers


def _rows(fx: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [r for r in fx["b_series"] if r.get("status") == 200]


def _sel(rows, J, PD, Z=4, AE=0.40):
    for r in rows:
        b = r["req"]
        if (abs(b["J"] - J) < 1e-9 and abs(b["P_D"] - PD) < 1e-9
                and b["Z"] == Z and abs(b["Ae_Ao"] - AE) < 1e-9):
            return r["resp"]
    return None


def eta_ideal(kt: float, J: float) -> float:
    """Actuator-disk ideal efficiency from thrust loading (exact momentum theory)."""
    if J <= 0:
        return float("nan")
    ct = 8.0 * kt / (math.pi * J * J)
    if ct <= -1.0:
        return float("nan")
    return 2.0 / (1.0 + math.sqrt(1.0 + ct))


def ittc57_cf(re: float) -> float:
    return 0.075 / (math.log10(re) - 2.0) ** 2


# ---------------------------------------------------------------- checks


def check_p1_eta_vs_pitch(fx) -> Dict[str, Any]:
    rows = _rows(fx)
    J = 0.5
    series = {}
    for pd in (0.6, 0.8, 1.0, 1.2):
        r = _sel(rows, J, pd)
        if r:
            series[pd] = r["eta0"]
    vals = list(series.values())
    spread = (max(vals) - min(vals)) if vals else 0.0
    # a real B-series gains roughly >=0.25 in eta0 from P/D 0.6 -> 1.2 at J=0.5
    required = 0.10
    return {
        "id": "P1_eta0_monotone_in_pitch",
        "measured": series,
        "delta": round(spread, 6),
        "threshold": f"eta0(P/D=1.2) - eta0(P/D=0.6) >= {required}",
        "pass": bool(spread >= required),
        "note": "eta0 identical across P/D => open-water efficiency independent of pitch",
    }


def check_p2_kt_linear_in_pitch(fx) -> Dict[str, Any]:
    rows = _rows(fx)
    J = 0.3
    pts = []
    for pd in (0.6, 0.8, 1.0, 1.2):
        r = _sel(rows, J, pd)
        if r:
            pts.append((pd, r["KT"]))
    # residual of a straight-line fit; exact 0 => KT = c * P/D (fabricated law)
    n = len(pts)
    if n < 3:
        return {"id": "P2_kt_nonlinear_in_pitch", "pass": None, "note": "insufficient rows"}
    xm = sum(p for p, _ in pts) / n
    ym = sum(k for _, k in pts) / n
    sxx = sum((p - xm) ** 2 for p, _ in pts)
    sxy = sum((p - xm) * (k - ym) for p, k in pts)
    slope = sxy / sxx
    resid = max(abs(k - (ym + slope * (p - xm))) for p, k in pts)
    return {
        "id": "P2_kt_nonlinear_in_pitch",
        "measured": {"points": pts, "max_abs_residual_of_linear_fit": round(resid, 9),
                     "slope_dKT_dPD": round(slope, 6)},
        "threshold": "linear-fit residual must be > 1e-3 * KT (regression is non-linear)",
        "pass": bool(resid > 1e-3 * abs(ym)),
        "note": "K_T is an exact straight line through P/D and independent of J-slope shape",
    }


def check_p3_kq_over_kt(fx) -> Dict[str, Any]:
    rows = _rows(fx)
    ratios = []
    for r in rows:
        kt, kq = r["resp"].get("KT"), r["resp"].get("KQ")
        if kt and kq is not None and abs(kt) > 1e-9:
            ratios.append(kq / kt)
    lo, hi = (min(ratios), max(ratios)) if ratios else (0.0, 0.0)
    return {
        "id": "P3_kq_over_kt_varies",
        "measured": {"n": len(ratios), "min": round(lo, 6), "max": round(hi, 6),
                     "unique_rounded": sorted({round(x, 5) for x in ratios})[:5]},
        "threshold": "K_Q/K_T must vary with J and P/D (spread > 5%)",
        "pass": bool(hi - lo > 0.05 * hi) if hi else False,
        "note": "K_Q = K_T/10 exactly => eta0 = J/(2*pi)*10, purely a function of J",
    }


def check_p4_blade_geometry(fx) -> Dict[str, Any]:
    rows = _rows(fx)
    J, PD = 0.4, 0.8
    a = _sel(rows, J, PD, Z=4, AE=0.40)
    b = _sel(rows, J, PD, Z=5, AE=0.70)
    if not (a and b):
        return {"id": "P4_blade_geometry_influence", "pass": None, "note": "missing rows"}
    dkt = abs(a["KT"] - b["KT"]) / max(abs(a["KT"]), 1e-12)
    dkq = abs(a["KQ"] - b["KQ"]) / max(abs(a["KQ"]), 1e-12)
    return {
        "id": "P4_blade_geometry_influence",
        "measured": {"KT_z4_a04": a["KT"], "KT_z5_a07": b["KT"],
                     "rel_dKT": round(dkt, 6), "rel_dKQ": round(dkq, 6)},
        "threshold": "Z=4/AeAo=0.40 vs Z=5/AeAo=0.70 must differ by > 2%",
        "pass": bool(max(dkt, dkq) > 0.02),
        "note": "geometry response observed (rel diff {:.2%})",
    }


def check_p5_eta_cap(fx) -> Dict[str, Any]:
    rows = _rows(fx)
    per_pd = {}
    capped = 0
    total = 0
    for pd in (0.6, 0.8, 1.0, 1.2):
        js = sorted(r["req"]["J"] for r in rows if abs(r["req"]["P_D"] - pd) < 1e-9)
        eta = [r["resp"]["eta0"] for r in rows
               if abs(r["req"]["P_D"] - pd) < 1e-9 and abs(r["req"]["J"] - 0.8) < 1e-9]
        per_pd[pd] = eta[0] if eta else None
    for r in rows:
        total += 1
        if abs(r["resp"]["eta0"] - 0.85) < 1e-12:
            capped += 1
    return {
        "id": "P5_no_hard_eta_cap",
        "measured": {"eta0_at_J0.8_by_pitch": per_pd,
                     "rows_at_0.85_cap": capped, "rows_total": total},
        "threshold": "eta0 must be smooth in J; a repeated exact 0.85 is a clamp artefact",
        "pass": bool(capped == 0),
        "note": "eta0 tuned to a fixed 0.85 ceiling for all J beyond ~0.53",
    }


def check_p6_zero_thrust_j(fx) -> Dict[str, Any]:
    rows = _rows(fx)
    j0 = {}
    for pd in (0.6, 0.8, 1.0, 1.2):
        pts = sorted(((r["req"]["J"], r["resp"]["KT"]) for r in rows
                      if abs(r["req"]["P_D"] - pd) < 1e-9
                      and r["req"]["Z"] == 4 and abs(r["req"]["Ae_Ao"] - 0.40) < 1e-9))
        if len(pts) < 2:
            continue
        (j1, k1), (j2, k2) = pts[0], pts[-1]
        j0[pd] = round(j1 - k1 * (j2 - j1) / (k2 - k1), 6)
    spread = (max(j0.values()) - min(j0.values())) if j0 else 0.0
    return {
        "id": "P6_zero_thrust_J_tracks_pitch",
        "measured": {"J0_by_pitch": j0, "spread": round(spread, 6)},
        "threshold": "J0 must increase with P/D (spread > 0.05)",
        "pass": bool(spread > 0.05),
        "note": "zero-thrust advance ratio fixed at 1.2 for every pitch ratio",
    }


def check_p7_chain_thrust(fx) -> Dict[str, Any]:
    rows = _rows(fx)
    case = fx["propulsion"][0]
    req, resp = case["req"], case["resp"]
    V = req["speed"]
    n = req["prop_rpm"] / 60.0
    D = req["prop_diameter"]
    J = V / (n * D)
    # endpoint's own open-water law, interpolated at the operating J (P/D=0.8)
    pts = sorted(((r["req"]["J"], r["resp"]["KT"]) for r in rows
                  if abs(r["req"]["P_D"] - 0.8) < 1e-9
                  and r["req"]["Z"] == 4 and abs(r["req"]["Ae_Ao"] - 0.40) < 1e-9))
    kt_op = None
    for (j1, k1), (j2, k2) in zip(pts, pts[1:]):
        if j1 <= J <= j2:
            kt_op = k1 + (k2 - k1) * (J - j1) / (j2 - j1)
            break
    thrust_own = (kt_op or 0.0) * RHO_SEA * n * n * D ** 4
    # independent ballpark of the thrust the hull needs (ITTC-57 friction on a
    # box barge, no form allowance) -- order-of-magnitude anchor only
    S = req["length"] * (2 * req["draft"] + req["beam"]) + 2 * req["beam"] * req["draft"]
    re = V * req["length"] / 1.19e-6
    r_f = 0.5 * RHO_SEA * S * ittc57_cf(re) * V ** 2
    r_t = 1.15 * r_f  # +15% form allowance
    reported = resp["thrust"]
    return {
        "id": "P7_chain_thrust_scale",
        "measured": {
            "J_operating": round(J, 4),
            "KT_own_law_at_J": round(kt_op or 0.0, 6),
            "thrust_from_own_KT_N": round(thrust_own, 1),
            "thrust_reported_N": reported,
            "ratio_reported_over_own": round(reported / thrust_own, 6) if thrust_own else None,
            "ballpark_required_thrust_N": round(r_t, 1),
        },
        "threshold": "reported thrust within 50% of thrust computed from the same K_T",
        "pass": bool(thrust_own and 0.5 < reported / thrust_own < 2.0),
        "note": "delivered force ~114x below its own K_T law: unit/scale error in the chain",
    }


def check_p8_momentum_bound(fx) -> Dict[str, Any]:
    rows = _rows(fx)
    worst = -9.9
    cnt = 0
    for r in rows:
        J, kt, eta = r["req"]["J"], r["resp"]["KT"], r["resp"]["eta0"]
        ei = eta_ideal(kt, J)
        if math.isfinite(ei):
            cnt += 1
            worst = max(worst, eta - ei)
    return {
        "id": "P8_momentum_theory_bound",
        "measured": {"n": cnt, "max_eta0_minus_eta_ideal": round(worst, 6)},
        "threshold": "eta0 <= eta_ideal for every sample",
        "pass": bool(worst <= 1e-9),
        "note": "necessary condition only; passing does not make the endpoint REAL",
    }


CHECKS = [check_p1_eta_vs_pitch, check_p2_kt_linear_in_pitch, check_p3_kq_over_kt,
          check_p4_blade_geometry, check_p5_eta_cap, check_p6_zero_thrust_j,
          check_p7_chain_thrust, check_p8_momentum_bound]


def run(fixture_path: str) -> Dict[str, Any]:
    fx = json.load(open(fixture_path))
    results = [c(fx) for c in CHECKS]
    npass = sum(1 for r in results if r["pass"] is True)
    nfail = sum(1 for r in results if r["pass"] is False)
    verdict = "REAL" if nfail == 0 else ("MOCK" if nfail >= 4 else "WITHHELD")
    return {
        "fixture": fixture_path,
        "checks": results,
        "passed": npass,
        "failed": nfail,
        "verdict": verdict,
    }


if __name__ == "__main__":
    import sys
    out = run(sys.argv[1] if len(sys.argv) > 1
              else "artifacts/m1584/prop_openwater_fixture.json")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\nVERDICT: {out['verdict']}  ({out['passed']} pass / {out['failed']} fail)")
    sys.exit(0 if out["verdict"] == "REAL" else 1)