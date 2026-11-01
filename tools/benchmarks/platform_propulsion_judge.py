"""Live-platform propulsion judges (m1413).

Pure, side-effect-free verdict functions.  They take *already fetched* platform
payloads and return structured verdicts, so they are unit-testable without a
running ship service, and the same function is used by
``tools/benchmarks/verify_propulsion.py`` against the live API.

Why these judges
----------------
``propeller.open_water`` is graded REAL by ``audit_capabilities`` while the
worktree holds no propulsion benchmark at all.  Absolute values from a
literature KT-KQ table are not available offline, so the judges below use only
literature-free, definitional/physical laws, which are nevertheless decisive:

  J1  open-water efficiency identity      eta_o = J K_T / (2 pi K_Q)   (exact)
  J2  momentum-theory ceiling             eta_o <= eta_ideal(C_T)     (bound)
  J3  physicality                         eta_o < 1, eta_i < 1
  J4  B-series structure                  KQ/KT must vary with (J, P/D, Z, Ae/Ao);
                                          a constant ratio means the regression
                                          is a placeholder, not a regression
  J5  efficiency saturation               eta_o must peak then decay in J,
                                          not saturate at a constant value
  J6  propulsion chain                    eta_D = eta_o eta_H eta_R and eta_D < 1
  J7  hull/loading coupling               output must respond to displacement
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List

import propulsion_anchors as pa

TOL_IDENTITY = 0.03        # 3% acceptance on definitional identities
TOL_CONST = 1e-9           # "exactly constant"


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def judge_open_water_case(case_id: str, J: float, KT: float, KQ: float,
                          eta0_reported: float) -> Dict:
    """J1/J2/J3 on a single open-water point."""
    implied = pa.open_water_efficiency(J, KT, KQ)
    CT = 8.0 * KT / (math.pi * J * J)
    eta_i = pa.ideal_efficiency(CT)
    ident_err = abs(implied - eta0_reported) / abs(eta0_reported) if eta0_reported else float("inf")
    return {
        "case_id": case_id,
        "J": J, "KT": KT, "KQ": KQ,
        "eta_reported": eta0_reported,
        "eta_implied": implied,
        "CT": CT,
        "eta_ideal": eta_i,
        "identity_error": ident_err,
        "J1_identity_ok": ident_err <= TOL_IDENTITY,
        "J2_bound_reported_ok": eta0_reported <= eta_i + 1e-12,
        "J2_bound_implied_ok": implied <= eta_i + 1e-12,
        "J3_physical": (eta0_reported < 1.0) and (implied < 1.0),
        "failures": [n for n, ok in (
            ("J1 identity", ident_err <= TOL_IDENTITY),
            ("J2 bound(reported)", eta0_reported <= eta_i + 1e-12),
            ("J2 bound(implied)", implied <= eta_i + 1e-12),
            ("J3 physical", (eta0_reported < 1.0) and (implied < 1.0)),
        ) if not ok],
    }


def judge_b_series_structure(rows: List[Dict]) -> Dict:
    """J4/J5 over a set of B-series evaluations (each has J, P_D, Z, Ae_Ao, KT, KQ, eta0)."""
    rows = [r for r in rows if r.get("KT") and r.get("KQ")]
    ratios = [r["KQ"] / r["KT"] for r in rows]
    spread = (max(ratios) - min(ratios)) if ratios else 0.0
    j4_ok = spread > 1e-6   # a real regression cannot keep KQ/KT perfectly fixed
    etas = [r["eta0"] for r in rows]
    # J5: within a fixed-geometry J sweep, eta0 must not be pinned at its max value
    jsweep = sorted([r for r in rows if r.get("_sweep") == "J"], key=lambda r: r["J"])
    sat = []
    if len(jsweep) >= 3:
        top = max(r["eta0"] for r in jsweep)
        sat = [r["J"] for r in jsweep if abs(r["eta0"] - top) <= TOL_CONST]
    # a genuine open-water curve peaks at an *interior* J and decays afterwards;
    # a plateau of >=2 points at the maximum value, or a maximum at the last
    # point, betrays a clamp rather than a physical curve.
    interior_peak = True
    if len(jsweep) >= 3:
        etas_j = [r["eta0"] for r in jsweep]
        interior_peak = etas_j.index(max(etas_j)) < len(etas_j) - 1
    j5_ok = len(sat) <= 1 and interior_peak
    return {
        "n_points": len(rows),
        "kq_over_kt": sorted(set(round(x, 6) for x in ratios)),
        "kq_over_kt_spread": spread,
        "eta0_values": sorted(set(round(e, 6) for e in etas)),
        "eta0_saturated_at": sat,
        "J4_structure_ok": j4_ok,
        "J5_no_saturation_ok": j5_ok,
        "failures": [n for n, ok in (("J4 KQ/KT constant", j4_ok),
                                     ("J5 eta0 saturation", j5_ok)) if not ok],
    }


def judge_propulsion_chain(res: Dict) -> Dict:
    """J6/J7 on ``/calculations/propulsion`` output."""
    eta_o = _f(res.get("open_water_eff"))
    eta_H = _f(res.get("hull_eff"))
    eta_R = _f(res.get("relative_rot_eff"))
    eta_D = _f(res.get("propulsive_eff"))
    chain = eta_o * eta_H * eta_R if all(map(math.isfinite, (eta_o, eta_H, eta_R))) else float("nan")
    chain_err = abs(chain - eta_D) / abs(eta_D) if eta_D else float("inf")
    P_D = _f(res.get("delivered_power"))
    Q = _f(res.get("torque"))
    return {
        "eta_o": eta_o, "eta_H": eta_H, "eta_R": eta_R, "eta_D": eta_D,
        "chain_product": chain,
        "chain_error": chain_err,
        "J6_chain_identity_ok": chain_err <= TOL_IDENTITY,
        "eta_D_below_unity": eta_D < 1.0,
        "eta_o_below_unity": eta_o < 1.0,
        "torque_positive": Q > 0.0 and P_D > 0.0,
        "failures": [n for n, ok in (
            ("J6 eta_D < 1", eta_D < 1.0),
            ("J6 eta_o < 1", eta_o < 1.0),
            ("J6 chain identity", chain_err <= TOL_IDENTITY),
        ) if not ok],
    }


def judge_displacement_coupling(pairs: Iterable[Dict]) -> Dict:
    """J7: two runs differing only in displacement must not be bit-identical."""
    pairs = list(pairs)
    same = [p for p in pairs if p["a"] == p["b"]]
    return {
        "n_pairs": len(pairs),
        "identical_pairs": len(same),
        "J7_coupling_ok": not same,
        "failures": ["J7 displacement has no effect"] if same else [],
    }


def overall_verdict(cases: List[Dict], structure: Dict, chain: Dict,
                    coupling: Dict) -> Dict:
    """Roll up to an evidence grade for the propulsion capability."""
    failures: List[str] = []
    for c in cases:
        failures += ["%s: %s" % (c["case_id"], f) for f in c["failures"]]
    for block in (structure, chain, coupling):
        failures += list(block.get("failures", []))
    grade = "REAL" if not failures else "MOCK"
    return {
        "evidence_grade": grade,
        "claimed_grade": "REAL",
        "n_failures": len(failures),
        "failures": failures,
        "recommendation": (
            "downgrade propeller.open_water and calculations.propulsion to MOCK "
            "pending repair" if failures else "keep REAL"),
    }