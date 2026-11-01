"""Independent criteria for auditing the platform structure endpoints.

Design contract
---------------
* A criterion is a *pure function* of recorded (input, output) pairs.  It never
  touches the network, so the exact same code runs against the live endpoint and
  against the analytic positive-control implementation (see `reference_candidate`).
* Every criterion carries a published reference ([1]..[5] of `baselines`) and an
  explicit numeric tolerance.  No criterion encodes knowledge of the platform
  source code; all of them follow from classical plate theory, classification-society
  rule formulas, or dimensional analysis.
* `decisive=True` marks the criteria whose failure alone is sufficient to downgrade
  an endpoint to MOCK: they are either exact algebraic identities, exact scaling
  laws, or unit/dimension checks, i.e. quantities a physically correct
  implementation reproduces to round-off.
* `circular=True` marks a criterion whose baseline the positive control shares by
  construction; it is reported but excluded from the control's pass requirement.

Verdict scale for an endpoint
-----------------------------
MOCK        at least one decisive criterion fails with an error above the platform's
            15 % acceptance threshold
WITHHELD    no decisive failure, but some criterion could not be evaluated
REAL        every criterion evaluates and passes
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from . import baselines as B

TOL = 0.03            # 3 % -- platform-level acceptance for closed-form identities
PLATFORM_TOL = 0.15   # 15 % -- platform's own "unacceptable error" threshold
PHYSICAL_UTIL_MAX = 1.0


@dataclass(frozen=True)
class Record:
    """One endpoint call: `case` is a stable identifier, `inputs`/`output` the JSON."""

    case: str
    endpoint: str
    inputs: dict
    output: dict


@dataclass(frozen=True)
class Result:
    cid: str
    endpoint: str
    statement: str
    reference: str
    tolerance: float
    decisive: bool
    circular: bool
    verdict: str            # PASS | FAIL | WITHHELD
    measured: float
    unit: str
    detail: dict = field(default_factory=dict)


def _get(out: dict, key: str, default: float = float("nan")) -> float:
    v = out.get(key, default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def _rel_err(measured: float, expected: float) -> float:
    if expected == 0:
        return abs(measured - expected)
    return abs(measured - expected) / abs(expected)


def _pick(records: Sequence[Record], case: str) -> Record | None:
    for r in records:
        if r.case == case:
            return r
    return None


def _ok(cond: bool) -> str:
    return "PASS" if cond else "FAIL"


# =============================================================== plate buckling
EP_PB = "/fea/plate-buckling"


def _pb_theory_identity(recs: Sequence[Record]) -> tuple[str, float, str, dict]:
    """sigma_cr must equal k(a/b) pi^2 D / (b^2 t) exactly (round-off only). [1]"""
    grid = ["pb_r0.4", "pb_r1.0", "pb_r1.5", "pb_r2.5", "pb_r4.0"]
    worst, detail = 0.0, {}
    n = 0
    for case in grid:
        r = _pick(recs, case)
        if r is None:
            continue
        n += 1
        i = r.inputs
        th = B.plate_buckling_stress(i["a"], i["b"], i["t"], i.get("E", 210000.0),
                                     i.get("nu", 0.3))
        m = _get(r.output, "critical_stress")
        e = _rel_err(m, th["sigma_cr"])
        detail[case] = {"measured": m, "expected": round(th["sigma_cr"], 4),
                        "k": round(th["k"], 4), "rel_err": round(e, 5)}
        worst = max(worst, e)
    if n == 0:
        return "WITHHELD", float("nan"), "rel", {"reason": "no grid records"}
    return _ok(worst <= TOL), worst, "rel", detail


def _pb_aspect_response(recs: Sequence[Record]) -> tuple[str, float, str, dict]:
    """The a/b dependence must survive: sigma(r=0.4)/sigma(r=4.0) = k(0.4)/k(4.0). [1]"""
    lo, hi = _pick(recs, "pb_r0.4"), _pick(recs, "pb_r4.0")
    if lo is None or hi is None:
        return "WITHHELD", float("nan"), "rel", {"reason": "grid incomplete"}
    k_lo, _ = B.plate_buckling_coefficient(0.48 / 1.2)
    k_hi, _ = B.plate_buckling_coefficient(4.8 / 1.2)
    expected = k_lo / k_hi
    s_lo, s_hi = _get(lo.output, "critical_stress"), _get(hi.output, "critical_stress")
    if not (s_hi > 0):
        return "FAIL", float("inf"), "rel", {"s_lo": s_lo, "s_hi": s_hi}
    measured = s_lo / s_hi
    e = _rel_err(measured, expected)
    return _ok(e <= TOL), e, "rel", {"measured_ratio": round(measured, 5),
                                     "expected_ratio": round(expected, 5),
                                     "sigma_r0.4": s_lo, "sigma_r4.0": s_hi}


def _pb_scaling(case_num: str, case_den: str, expected_ratio: float,
                why: str) -> Callable[[Sequence[Record]], tuple[str, float, str, dict]]:
    def _f(recs: Sequence[Record]) -> tuple[str, float, str, dict]:
        num, den = _pick(recs, case_num), _pick(recs, case_den)
        if num is None or den is None:
            return "WITHHELD", float("nan"), "rel", {"reason": "cases missing"}
        s_num, s_den = _get(num.output, "critical_stress"), _get(den.output, "critical_stress")
        if not (s_den > 0 and s_num > 0):
            return "FAIL", float("inf"), "rel", {"num": s_num, "den": s_den}
        measured = s_num / s_den
        e = _rel_err(measured, expected_ratio)
        return _ok(e <= TOL), e, "rel", {"measured_ratio": round(measured, 5),
                                         "expected_ratio": expected_ratio,
                                         "why": why}
    return _f


def _pb_halfwave_argmin(recs: Sequence[Record]) -> tuple[str, float, str, dict]:
    """Reported half-wave count must be the argmin m of the k(m) family. [1]"""
    grid = ["pb_r0.4", "pb_r1.0", "pb_r1.5", "pb_r2.5", "pb_r4.0"]
    detail, bad, n = {}, 0, 0
    for case in grid:
        r = _pick(recs, case)
        if r is None:
            continue
        n += 1
        i = r.inputs
        _, m = B.plate_buckling_coefficient(i["a"] / i["b"])
        raw = _get(r.output, "half_waves")
        if not (raw == raw):          # NaN: field missing or call failed
            n -= 1
            detail[case] = {"measured": None, "expected": m, "missing": True}
            continue
        got = int(raw)
        detail[case] = {"measured": got, "expected": m}
        bad += int(got != m)
    if n == 0:
        return "WITHHELD", float("nan"), "count", {"reason": "no records"}
    return _ok(bad == 0), float(bad), "count", {"mismatches": bad, "of": n, "cases": detail}


def _pb_edge_force(recs: Sequence[Record]) -> tuple[str, float, str, dict]:
    """`critical_load` must be a positive edge force proportional to sigma_cr * b * t."""
    vals, detail = [], {}
    for r in recs:
        sc = _get(r.output, "critical_stress")
        load = _get(r.output, "critical_load", 0.0)
        if not (sc > 0):
            continue
        force_kN = sc * r.inputs["b"] * r.inputs["t"] / 1.0e3
        vals.append(load)
        detail[r.case] = {"critical_load": load, "edge_force_kN": round(force_kN, 6),
                          "ratio": (load / force_kN) if force_kN else None}
    if not vals:
        return "WITHHELD", float("nan"), "ratio", {"reason": "no records"}
    if max(abs(v) for v in vals) == 0.0:
        return "FAIL", 0.0, "ratio", {
            "reason": "critical_load identically zero in every sample: an edge force "
                      "cannot vanish while sigma_cr > 0 (dimension/return bug)",
            "n_zero": len(vals), "cases": detail}
    nonzero = sorted({v for v in vals if v})
    if all(abs(v - round(v, 2)) < 1e-12 for v in vals) and nonzero:
        return "FAIL", float(len([v for v in vals if not v])), "count_zero", {
            "reason": "critical_load is quantised to 2 decimal places of a kN-scale "
                      "quantity (smallest non-zero value {} kN), so the edge force "
                      "collapses to 0 in {}/{} samples: the field is not usable at "
                      "plate-panel magnitudes".format(nonzero[0],
                                                      len([v for v in vals if not v]), len(vals)),
            "smallest_nonzero": nonzero[0], "cases": detail}
    ratios = [d["ratio"] for d in detail.values() if d["ratio"] is not None]
    spread = (max(ratios) - min(ratios)) / (sum(ratios) / len(ratios)) if ratios else float("inf")
    return _ok(spread <= TOL), spread, "rel_spread", {"cases": detail}


def _pb_margin_definition(recs: Sequence[Record]) -> tuple[str, float, str, dict]:
    """`safety_margin` must be the classical margin sigma_y/sigma_cr - 1. [1]"""
    grid = ["pb_r0.4", "pb_r1.0", "pb_r1.5", "pb_r2.5", "pb_r4.0"]
    worst, detail, n = 0.0, {}, 0
    for case in grid:
        r = _pick(recs, case)
        if r is None:
            continue
        n += 1
        sc = _get(r.output, "critical_stress")
        sy = float(r.inputs.get("yield_stress", 235.0))
        if not (sc > 0):
            continue
        expected = sy / sc - 1.0
        m = _get(r.output, "safety_margin")
        e = _rel_err(m, expected)
        detail[case] = {"measured": m, "expected": round(expected, 5),
                        "sigma_cr_over_sigma_y": round(sc / sy, 6), "rel_err": round(e, 5)}
        worst = max(worst, e)
    if n == 0:
        return "WITHHELD", float("nan"), "rel", {"reason": "no records"}
    return _ok(worst <= TOL), worst, "rel", detail


# ============================================================ /structure/analyze
EP_SA = "/structure/analyze"


def _stress_mpa_implied(out: dict) -> tuple[float, str]:
    """Resolve the unit of `max_stress` by physical admissibility.

    A structural normal stress in MPa cannot exceed ~10^3 MPa for any metal, and a
    stress in Pa below 10^5 Pa (0.1 MPa) is negligible for a 230 m ship.  Exactly one
    interpretation survives for a ship-sized hull; we report which one and why.
    """
    raw = _get(out, "max_stress")
    if raw <= 0:
        return raw, "non-positive"
    if raw <= 1.0e3:
        return raw, "MPa (value admissible as MPa)"
    if 1.0e5 <= raw <= 1.0e9:
        return raw / 1.0e6, "Pa (value admissible only as Pa)"
    return raw, "unresolvable"


def _sa_unit_consistency(recs: Sequence[Record]) -> tuple[str, float, str, dict]:
    """utilization must equal sigma_MPa / sigma_allow_MPa with one common unit."""
    worst, detail, n = 0.0, {}, 0
    for r in recs:
        o = r.output
        u = _get(o, "utilization")
        allow = _get(o, "allowable_stress")
        if not (allow > 0) or not (u > 0):
            continue
        n += 1
        sigma_mpa, unit = _stress_mpa_implied(o)
        allow_mpa = allow / 1.0e6 if allow > 1.0e3 else allow  # allowable: MPa or Pa
        u_correct = sigma_mpa / allow_mpa
        e = _rel_err(u, u_correct)
        detail[r.case] = {"utilization": u, "sigma_MPa_implied": round(sigma_mpa, 6),
                          "sigma_unit": unit, "allowable_MPa": allow_mpa,
                          "utilization_consistent": round(u_correct, 6),
                          "rel_err": round(e, 3)}
        worst = max(worst, e)
    if n == 0:
        return "WITHHELD", float("nan"), "rel", {"reason": "no records"}
    return _ok(worst <= TOL), worst, "rel", detail


def _sa_stress_band(recs: Sequence[Record]) -> tuple[str, float, str, dict]:
    """Hull girder stress vs the IACS S11/S7 rule baseline, factor-3 plausibility band."""
    detail, worst, n = {}, 0.0, 0
    for r in recs:
        i = r.inputs
        ref = B.hull_girder_stress_rule(i["L"], i["B"], i["Cb"])
        sig, unit = _stress_mpa_implied(r.output)
        s_ref = max(ref["sigma_hog_MPa"], ref["sigma_sag_MPa"])
        if not (sig > 0) or not (s_ref > 0):
            continue
        n += 1
        ratio = sig / s_ref
        e = abs(math.log10(ratio)) if ratio > 0 else float("inf")
        outside = not (1.0 / 3.0 <= ratio <= 3.0)
        worst = max(worst, e)
        detail[r.case] = {"sigma_MPa": round(sig, 6), "sigma_unit": unit,
                          "sigma_ref_MPa": round(s_ref, 3),
                          "W_min_m3": round(ref["W_min_m3"], 3),
                          "ratio": round(ratio, 6), "outside_band": outside}
    if n == 0:
        return "WITHHELD", float("nan"), "log10_ratio", {"reason": "no records"}
    verdict = "FAIL" if any(d["outside_band"] for d in detail.values()) else "PASS"
    return verdict, worst, "log10_ratio", detail


def _sa_geometry_sensitivity(recs: Sequence[Record]) -> tuple[str, float, str, dict]:
    """Stress must respond to the section properties supplied in the request.

    A hull girder stress computed from L,B,T,D,displacement,Cb,plate_thickness cannot
    be bit-identical for a 150 m and a 320 m hull, nor for 12 mm and 30 mm plating.
    """
    vals = {}
    for r in recs:
        vals[r.case] = _get(r.output, "max_stress")
    if len(vals) < 2:
        return "WITHHELD", float("nan"), "distinct", {"reason": "need >=2 samples"}
    distinct = len(set(vals.values()))
    detail = {"values": {k: v for k, v in vals.items()},
              "distinct": distinct, "n": len(vals)}
    if distinct == 1:
        return "FAIL", 0.0, "distinct", {
            **detail,
            "reason": "max_stress is bit-identical across hulls whose L differs by "
                      "113 m and whose plating thickness differs by 2.5x: the value "
                      "is not computed from the section properties"}
    # ratio of extremes must be >= 10 % for such a large change in scantlings
    lo, hi = min(vals.values()), max(vals.values())
    spread = (hi - lo) / hi if hi else 0.0
    return _ok(spread >= 0.10), spread, "rel_spread", detail


def _sa_fatigue_degenerate(recs: Sequence[Record]) -> tuple[str, float, str, dict]:
    """A fatigue life of a loaded structure must be finite and strictly positive."""
    detail, bad = {}, 0
    for r in recs:
        life = _get(r.output, "fatigue_life", 0.0)
        detail[r.case] = life
        bad += int(not (life > 0))
    if not detail:
        return "WITHHELD", float("nan"), "years", {"reason": "no records"}
    return _ok(bad == 0), float(bad), "count", {"non_positive": bad, "of": len(detail),
                                                "values": detail}


def _sa_status_consistency(recs: Sequence[Record]) -> tuple[str, float, str, dict]:
    """The status label must follow the reported utilization (>1 -> needs check)."""
    needs = {"需校核", "不合格", "NEEDS_CHECK", "fail", "FAIL"}
    detail, bad = {}, 0
    for r in recs:
        u = _get(r.output, "utilization")
        status = str(r.output.get("status", ""))
        expect_flag = u > PHYSICAL_UTIL_MAX
        got_flag = status.strip() in needs
        detail[r.case] = {"utilization": u, "status": status,
                          "label_matches_bound": got_flag == expect_flag}
        bad += int(got_flag != expect_flag)
    if not detail:
        return "WITHHELD", float("nan"), "count", {"reason": "no records"}
    return _ok(bad == 0), float(bad), "count", {"labelled": len(detail), "mismatch": bad,
                                                "cases": detail}


# ============================================================== /structure/plate
EP_SP = "/structure/plate"


def _sp_panel_theory(recs: Sequence[Record]) -> tuple[str, float, str, dict]:
    """Panel critical stress vs k=4 plate formula, steelmanned by a yield cap. [1]"""
    detail, worst, n = {}, 0.0, 0
    for r in recs:
        i = r.inputs
        th = B.plate_buckling_stress(a=i["frame_spacing"], b=i["frame_spacing"],
                                     t=i["plate_thickness"])
        sy = float(i.get("yield_stress", 235.0))
        capped = min(th["sigma_cr"], sy)          # most favourable admissible reading
        m = _get(r.output, "critical_stress")
        e = _rel_err(m, capped)
        detail[r.case] = {"measured": m, "theory_MPa": round(th["sigma_cr"], 3),
                          "theory_with_yield_cap_MPa": round(capped, 3),
                          "yield_cap_active": th["sigma_cr"] > sy,
                          "rel_err_vs_steelmanned": round(e, 5)}
        worst = max(worst, e)
        n += 1
    if n == 0:
        return "WITHHELD", float("nan"), "rel", {"reason": "no records"}
    return _ok(worst <= TOL), worst, "rel", detail


def _sp_thickness_sensitivity(recs: Sequence[Record]) -> tuple[str, float, str, dict]:
    """Doubling the plating thickness must raise the panel strength as t^2 -> 4x. [1]

    Both the theoretical and the yield-capped readings are computed, and the expected
    ratio is taken from the *steelmanned* (capped) theory so that a legitimate
    yield-capped implementation is not punished for the cap.
    """
    lo, hi = _pick(recs, "sp_t6"), _pick(recs, "sp_t12")
    if lo is None or hi is None:
        return "WITHHELD", float("nan"), "rel", {"reason": "cases missing"}
    t_lo = float(lo.inputs["plate_thickness"])
    t_hi = float(hi.inputs["plate_thickness"])

    def capped(inp: dict) -> float:
        th = B.plate_buckling_stress(a=inp["frame_spacing"], b=inp["frame_spacing"],
                                     t=inp["plate_thickness"])
        return min(th["sigma_cr"], float(inp.get("yield_stress", 235.0)))

    expected = capped(hi.inputs) / capped(lo.inputs) if capped(lo.inputs) > 0 else float("nan")
    s_lo, s_hi = _get(lo.output, "critical_stress"), _get(hi.output, "critical_stress")
    if not (s_lo > 0 and s_hi > 0):
        return "FAIL", float("inf"), "rel", {"s_lo": s_lo, "s_hi": s_hi}
    measured = s_hi / s_lo
    e = _rel_err(measured, expected)
    return _ok(e <= TOL), e, "rel", {"measured_ratio": round(measured, 5),
                                     "expected_ratio": round(expected, 5),
                                     "t_mm": [t_lo * 1e3, t_hi * 1e3]}


def _sp_factor_definition(recs: Sequence[Record]) -> tuple[str, float, str, dict]:
    """safety_factor must be a consistent ratio of the reported pair, matching status."""
    detail, worst, n = {}, 0.0, 0
    for r in recs:
        crit = _get(r.output, "critical_stress")
        act = _get(r.output, "actual_stress")
        sf = _get(r.output, "safety_factor")
        status = str(r.output.get("status", ""))
        if not (crit > 0 and act > 0 and sf > 0):
            detail[r.case] = {"skipped": True}
            continue
        n += 1
        # accept either convention, but it must be one of them exactly
        cands = {"crit/act": crit / act, "act/crit": act / crit}
        best_name, best_val = min(cands.items(), key=lambda kv: _rel_err(sf, kv[1]))
        e = _rel_err(sf, best_val)
        consist = (status.strip() in {"需加强", "FAIL", "fail"}) == (act > crit)
        detail[r.case] = {"safety_factor": sf, "convention": best_name,
                          "expected": round(best_val, 6), "rel_err": round(e, 5),
                          "status_matches_pair": consist}
        worst = max(worst, 0.0 if consist else 1.0)
        worst = max(worst, e)
    if n == 0:
        return "WITHHELD", float("nan"), "rel", {"reason": "no records"}
    return _ok(worst <= TOL), worst, "rel", detail


# ============================================================ /structure/fatigue
EP_SF = "/structure/fatigue"


def _sf_range_from_geometry(recs: Sequence[Record]) -> tuple[str, float, str, dict]:
    """The fatigue stress range must be derived from hull geometry, not fixed."""
    vals = {r.case: _get(r.output, "stress_range") for r in recs}
    distinct = len(set(vals.values()))
    if len(vals) < 2:
        return "WITHHELD", float("nan"), "distinct", {"reason": "need >=2 samples"}
    if distinct == 1:
        return "FAIL", 0.0, "distinct", {
            "values": vals,
            "reason": "stress_range is bit-identical across hulls with L = 150/230/320 m "
                      "and Cb = 0.65/0.82: no stress-transfer model is applied"}
    lo, hi = min(vals.values()), max(vals.values())
    spread = (hi - lo) / hi if hi else 0.0
    return _ok(spread >= 0.05), spread, "rel_spread", {"values": vals}


def _sf_curve_consistency(recs: Sequence[Record]) -> tuple[str, float, str, dict]:
    """Life must be reproducible from the endpoint's own range and named S-N curve [4]."""
    lo, hi = B.plausible_wave_cycle_band()
    detail, outside, n = {}, 0, 0
    for r in recs:
        o = r.output
        S = _get(o, "stress_range")
        life = _get(o, "calculated_life")
        curve_name = str(o.get("s_n_curve", ""))
        curve = B.DNV_C if "C" in curve_name.upper() else B.DNV_D
        if not (S > 0 and life > 0):
            continue
        n += 1
        N = curve.cycles(S)
        implied = N / life
        out = not (lo <= implied <= hi)
        outside += int(out)
        detail[r.case] = {"stress_range": S, "life_years": life, "curve": curve.name,
                          "N_allowable": round(N, 1),
                          "implied_cycles_per_year": round(implied, 1),
                          "plausible_band": [round(lo, 1), round(hi, 1)],
                          "outside_band": out}
    if n == 0:
        return "WITHHELD", float("nan"), "count", {"reason": "no records"}
    worst = max(d["implied_cycles_per_year"] for d in detail.values())
    verdict = "FAIL" if outside else "PASS"
    return verdict, worst, "cycles_per_year", {"outside": outside, "of": n,
                                               "cases": detail}


def _sf_slope(recs: Sequence[Record]) -> tuple[str, float, str, dict]:
    """The life ratio between two stress ranges must follow the DNV-RP-C203 C curve. [4]

    Comparing two ranges that sit on opposite sides of the 1e7-cycle knee is legitimate
    once the *expected* ratio is taken from the curve itself (a raw log-log exponent
    would mix the m = 3 and m = 5 branches).  The measured quantity is therefore the
    relative deviation of the observed life ratio from the curve ratio, and the apparent
    exponent is reported alongside for information.
    """
    pts = [(_get(r.output, "stress_range"), _get(r.output, "calculated_life"))
           for r in recs]
    pts = [(S, L) for S, L in pts if S > 0 and L > 0]
    uniq = sorted({round(S, 6) for S, _ in pts})
    if len(uniq) < 2:
        return "WITHHELD", float("nan"), "exponent", {
            "distinct_stress_ranges": uniq,
            "reason": "the endpoint emits a single stress range for every hull, so the "
                      "S-N relation is not identifiable: the fatigue model cannot be "
                      "verified from outside (WITHHELD, not PASS)"}
    by_S = {}
    for S, L in pts:
        by_S.setdefault(round(S, 6), L)
    Ss = sorted(by_S)
    S1, S2 = Ss[0], Ss[-1]
    L1, L2 = by_S[S1], by_S[S2]
    ep = B.DNV_C
    expected = ep.cycles(S1) / ep.cycles(S2)
    measured_ratio = L1 / L2
    err = _rel_err(measured_ratio, expected)
    slope = math.log10(L2 / L1) / math.log10(S2 / S1)
    tol = 0.10      # 3-significant-figure output already costs ~1 %
    return _ok(err <= tol), err, "rel", {
        "S_range": [S1, S2], "life_years": [L1, L2],
        "measured_life_ratio": round(measured_ratio, 5),
        "expected_life_ratio": round(expected, 5),
        "apparent_log_log_exponent": round(slope, 4),
        "note": "expected ratio from DNV-RP-C203 C curve over the same two ranges"}


def _sf_status_consistency(recs: Sequence[Record]) -> tuple[str, float, str, dict]:
    """status must be 合格 iff calculated_life >= design_life."""
    ok_set = {"合格", "OK", "ok", "pass"}
    detail, bad = {}, 0
    for r in recs:
        o = r.output
        life = _get(o, "calculated_life")
        design = _get(o, "design_life")
        status = str(o.get("status", ""))
        expect = life >= design
        got = status.strip() in ok_set
        detail[r.case] = {"calculated_life": life, "design_life": design,
                          "status": status, "consistent": expect == got}
        bad += int(expect != got)
    if not detail:
        return "WITHHELD", float("nan"), "count", {"reason": "no records"}
    return _ok(bad == 0), float(bad), "count", {"mismatch": bad, "of": len(detail),
                                                "cases": detail}


def _sa_plating_response(recs: Sequence[Record]) -> tuple[str, float, str, dict]:
    """max_stress must respond to plate_thickness (sigma = M/W, W grows with plating)."""
    lo, hi = _pick(recs, "sa_t12"), _pick(recs, "sa_t30")
    if lo is None or hi is None:
        return "WITHHELD", float("nan"), "rel", {"reason": "cases missing"}
    s_lo, s_hi = _get(lo.output, "max_stress"), _get(hi.output, "max_stress")
    if not (s_lo > 0 and s_hi > 0):
        return "FAIL", float("inf"), "rel", {"s_lo": s_lo, "s_hi": s_hi}
    change = abs(s_hi - s_lo) / s_lo
    ok = change >= 0.20
    return _ok(ok), change, "rel_change", {
        "sigma_t12": s_lo, "sigma_t30": s_hi, "relative_change": round(change, 6),
        "t_mm": [float(lo.inputs["plate_thickness"]) * 1e3,
                 float(hi.inputs["plate_thickness"]) * 1e3],
        "why": "a 2.5x change of the plating thickness changes the midship section "
               "modulus, hence sigma = M/W at fixed wave moment, by far more than 20 %"}


def _sf_range_not_from_yield(recs: Sequence[Record]) -> tuple[str, float, str, dict]:
    """At fixed yield stress the fatigue range must still follow hull geometry."""
    groups: dict[float, dict] = {}
    for r in recs:
        sy = float(r.inputs.get("yield_stress", 235.0))
        groups.setdefault(sy, {})[r.case] = _get(r.output, "stress_range")
    detail, worst, tested = {}, 0.0, 0
    for sy, vals in groups.items():
        if len(vals) < 2:
            continue
        tested += 1
        lo, hi = min(vals.values()), max(vals.values())
        spread = (hi - lo) / hi if hi else 0.0
        worst = max(worst, spread)
        detail[f"sigma_y={sy:g}"] = {"stress_range": vals,
                                     "rel_spread": round(spread, 6)}
    if tested == 0:
        return "WITHHELD", float("nan"), "rel_spread", {"reason": "no fixed-yield group"}
    if all(d["rel_spread"] == 0.0 for d in detail.values()):
        ranges = {v for d in detail.values() for v in d["stress_range"].values()}
        sy_vals = {float(s.split("=")[1]): list(d["stress_range"].values())[0]
                   for s, d in detail.items()}
        ratio = {sy: round(rng / sy, 6) for sy, rng in sy_vals.items()}
        return "FAIL", 0.0, "rel_spread", {
            **detail,
            "reason": "stress_range is constant inside every fixed-yield group and "
                      "scales exactly with yield stress (range/sigma_y = {}), i.e. it "
                      "is a fraction of the yield stress rather than a computed "
                      "wave-induced stress range".format(ratio)}
    return _ok(worst >= 0.05), worst, "rel_spread", detail


# ------------------------------------------------------------------- registry

CRITERIA: list[tuple] = [
    # (cid, endpoint, statement, reference, tolerance, decisive, circular, fn)
    ("PB1", EP_PB,
     "sigma_cr equals the Timoshenko thin-plate solution k(a/b) pi^2 D /(b^2 t)",
     "Timoshenko & Gere 1961 Eq.9-3", TOL, True, False, _pb_theory_identity),
    ("PB2", EP_PB,
     "the a/b dependence survives: sigma(0.4)/sigma(4.0) = k(0.4)/k(4.0) = 2.10",
     "Timoshenko & Gere 1961 Eq.9-3", TOL, True, False, _pb_aspect_response),
    ("PB3", EP_PB,
     "sigma_cr scales as t^2 when the plating thickness doubles",
     "D ~ t^3 in Eq.9-3 => sigma_cr ~ t^2", TOL, True, False,
     _pb_scaling("pb_t_hi", "pb_t_lo", 4.0, "sigma_cr ~ t^2")),
    ("PB4", EP_PB,
     "sigma_cr scales as b^-2 when plate and span are both doubled",
     "sigma_cr = k pi^2 D/(b^2 t)", TOL, True, False,
     _pb_scaling("pb_b_double", "pb_r2.5", 0.25, "sigma_cr ~ 1/b^2")),
    ("PB5", EP_PB,
     "sigma_cr scales linearly with E",
     "D = E t^3/(12(1-nu^2))", TOL, True, False,
     _pb_scaling("pb_E_half", "pb_r2.5", 0.5, "sigma_cr ~ E")),
    ("PB6", EP_PB,
     "the reported half-wave count is the argmin m of the k(m) family",
     "Timoshenko & Gere 1961 Eq.9-3", 0.0, False, False, _pb_halfwave_argmin),
    ("PB7", EP_PB,
     "critical_load is a positive edge force proportional to sigma_cr * b * t",
     "dimensional analysis (stress x width x thickness)", TOL, True, False,
     _pb_edge_force),
    ("PB8", EP_PB,
     "safety_margin = sigma_y / sigma_cr - 1 (classical structural margin)",
     "Timoshenko & Gere 1961 (margin convention)", TOL, False, False,
     _pb_margin_definition),

    ("SA1", EP_SA,
     "utilization = sigma/sigma_allow evaluated in one common unit",
     "dimensional analysis", TOL, True, False, _sa_unit_consistency),
    ("SA2", EP_SA,
     "hull girder stress within a factor 3 of the IACS S11+S7 rule baseline",
     "IACS UR S11 5.2.1 / S7.1.1", 3.0, True, False, _sa_stress_band),
    ("SA3", EP_SA,
     "max_stress responds to the section properties in the request",
     "physics of hull girder bending (sigma = M/W)", 0.10, True, False,
     _sa_geometry_sensitivity),
    ("SA4", EP_SA,
     "fatigue_life is finite and strictly positive for a loaded structure",
     "Palmgren-Miner damage accumulation", 0.0, False, False,
     _sa_fatigue_degenerate),
    ("SA6", EP_SA,
     "max_stress responds to plate_thickness 12 mm -> 30 mm (sigma = M/W)",
     "hull girder beam theory", 0.20, True, False, _sa_plating_response),
    ("SA5", EP_SA,
     "the status label follows the reported utilization (>1 => needs check)",
     "internal consistency", 0.0, False, False, _sa_status_consistency),

    ("SP1", EP_SP,
     "panel critical stress matches k=4 plate buckling (steelmanned by a yield cap)",
     "Timoshenko & Gere 1961 Eq.9-3 + yield criterion", TOL, True, False,
     _sp_panel_theory),
    ("SP2", EP_SP,
     "critical stress scales as t^2 when plating goes 6 mm -> 12 mm (span fixed)",
     "sigma_cr ~ t^2", TOL, True, False, _sp_thickness_sensitivity),
    ("SP3", EP_SP,
     "safety_factor is a consistent ratio of the reported pair and agrees with status",
     "internal consistency", TOL, False, False, _sp_factor_definition),

    ("SF1", EP_SF,
     "the fatigue stress range is derived from hull geometry, not a constant",
     "stress-transfer physics", 0.05, True, False, _sf_range_from_geometry),
    ("SF2", EP_SF,
     "life reproducible from the endpoint's own range and its named DNV S-N curve",
     "DNV-RP-C203 (2016) Table 2-1 + Miner", 0.0, True, False,
     _sf_curve_consistency),
    ("SF3", EP_SF,
     "life ratio between two stress ranges follows the named DNV S-N curve",
     "DNV-RP-C203 (2016) Table 2-1", 0.10, False, False, _sf_slope),
    ("SF5", EP_SF,
     "at fixed yield stress the fatigue range still follows hull geometry",
     "stress-transfer physics", 0.05, True, False, _sf_range_not_from_yield),
    ("SF4", EP_SF,
     "status is 合格 iff calculated_life >= design_life",
     "internal consistency", 0.0, False, False, _sf_status_consistency),
]


def evaluate(records: Iterable[Record]) -> list[Result]:
    """Run every criterion whose endpoint appears in `records`."""
    recs = list(records)
    by_ep: dict[str, list[Record]] = {}
    for r in recs:
        by_ep.setdefault(r.endpoint, []).append(r)
    results: list[Result] = []
    for (cid, ep, statement, reference, tol, decisive, circular, fn) in CRITERIA:
        if ep not in by_ep:
            continue
        verdict, measured, unit, detail = fn(by_ep[ep])
        results.append(Result(cid=cid, endpoint=ep, statement=statement,
                              reference=reference, tolerance=tol, decisive=decisive,
                              circular=circular, verdict=verdict,
                              measured=float(measured), unit=unit, detail=detail))
    return results


def grade(results: Sequence[Result]) -> dict[str, dict]:
    """Aggregate criteria into a per-endpoint evidence grade."""
    out: dict[str, dict] = {}
    for r in results:
        g = out.setdefault(r.endpoint, {"PASS": 0, "FAIL": 0, "WITHHELD": 0,
                                        "decisive_fail": [], "failed": [],
                                        "withheld": []})
        g[r.verdict] += 1
        if r.verdict == "FAIL":
            g["failed"].append(r.cid)
            if r.decisive:
                g["decisive_fail"].append(r.cid)
        elif r.verdict == "WITHHELD":
            g["withheld"].append(r.cid)
    for ep, g in out.items():
        if g["decisive_fail"]:
            g["grade"] = "MOCK"
        elif g["FAIL"] or g["withheld"]:
            g["grade"] = "WITHHELD"
        else:
            g["grade"] = "REAL"
    return out