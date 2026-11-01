"""Analytic baselines and validity-domain checks for ship resistance auditing.

Purpose (platform validation-debt track): give an *independent*, paper-traceable
reference for the ship-performance platform's ``POST /api/v1/calculations/resistance``
endpoint so that the capability grade (REAL / MOCK / WITHHELD) can be decided by
machine-computable evidence instead of by self-report.

Design constraints
------------------
* stdlib only (no numpy/scipy) -> runs anywhere, cheap (fits "lightweight" capacity rule).
* every formula cited with its source so a reviewer can re-derive it on paper.
* no platform code is imported: the baseline must be independent of the subject
  under test, otherwise it only proves self-consistency.

References
----------
[ITTC57]  ITTC Recommended Procedure 7.5-02-03-01.4, 1978/2011 revision:
          Cf = 0.075 / (log10(Re) - 2)^2                     (turbulent flat plate)
[HOLTROP] Holtrop, J. & Mennen, G.G.J. (1982) "An approximate power prediction
          method", ISP 29(335); Holtrop (1984) "A statistical re-analysis of
          resistance and propulsion data", ISP 31(363).
          Frictional form:  Rf = 0.5 rho V^2 S Cf (1+k)
          Wetted surface:   S = L(2T+B) sqrt(Cm) (0.453 + 0.4425 Cb - 0.2862 Cm
                                - 0.003467 B/T + 0.3696 Cwp) + 2.38 Abt/Cb
          Validity: conventional displacement hulls, 0.55 < Cb < 0.85,
                    0.15 < Fn < 0.45  (statistical fit domain of the regression)
[GROVES]  Groves, Huang & Chang (1989) DTRC-89/... : DARPA SUBOFF bare hull
          L = 4.356 m, D = 0.508 m, wetted area 5.42 m^2  (as cited in the repo
          benchmark dataset, source Roddy 1990 DTRC-90/012).
[WAVE]    For a deeply submerged body of revolution there is no free surface
          deformation -> no wave-making resistance; the only non-viscous
          component in the classical decomposition is the form effect, which is
          absorbed into (1+k).  Therefore Ct -> (1+k) Cf and Cr -> 0 as the
          submergence/D ratio grows (submarine resistance literature; e.g.
          SUBOFF analyses use Cts = (1+k) Cfs + Caa).
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, List, Mapping, Optional

G = 9.81
KNOT_MS = 0.514444          # 1 kn = 0.514444 m/s (exact definition 1852/3600)
RHO_DEFAULT = 1025.0        # kg/m^3  (platform default, sea water)
NU_DEFAULT = 1.19e-6        # m^2/s   (platform default)

# --- validity domains -------------------------------------------------------
HOLTROP_CB_RANGE = (0.55, 0.85)
HOLTROP_FN_RANGE = (0.15, 0.45)


# ---------------------------------------------------------------------------
# Kinematics
# ---------------------------------------------------------------------------
def knots_to_ms(v_kn: float) -> float:
    return v_kn * KNOT_MS


def ms_to_knots(v_ms: float) -> float:
    return v_ms / KNOT_MS


def reynolds(v_ms: float, length: float, nu: float = NU_DEFAULT) -> float:
    """Re = V L / nu  (V in m/s)."""
    return v_ms * length / nu


def froude(v_ms: float, length: float, g: float = G) -> float:
    """Fn = V / sqrt(g L)."""
    return v_ms / math.sqrt(g * length)


# ---------------------------------------------------------------------------
# Resistance coefficients
# ---------------------------------------------------------------------------
def ittc57_cf(re: float) -> float:
    """ITTC-1957 turbulent flat-plate friction coefficient [ITTC57]."""
    if re <= 0:
        raise ValueError("Re must be positive")
    return 0.075 / (math.log10(re) - 2.0) ** 2


def submerged_ct_baseline(cf: float, k_form: float = 0.10) -> float:
    """Baseline total coefficient for a deeply submerged body: Ct = (1+k) Cf [WAVE]."""
    return (1.0 + k_form) * cf


def frictional_resistance(rho: float, v_ms: float, s: float, cf: float) -> float:
    """Rf = 0.5 rho V^2 S Cf  [N]."""
    return 0.5 * rho * v_ms ** 2 * s * cf


# ---------------------------------------------------------------------------
# Holtrop wetted surface (as implemented by the platform, replicated here so the
# geometry can be audited without touching the server)
# ---------------------------------------------------------------------------
def holtrop_wetted_surface(L: float, B: float, T: float, Cb: float,
                           cm: Optional[float] = None,
                           cwp: Optional[float] = None,
                           abt: float = 0.0) -> float:
    """Holtrop wetted-surface regression [HOLTROP], with Abt = 0 (no bulb)."""
    if cm is None:
        cm = 0.95 + 0.05 * Cb
    if cwp is None:
        cwp = 0.75 + 0.25 * Cb
    core = (0.453 + 0.4425 * Cb - 0.2862 * cm
            - 0.003467 * (B / T) + 0.3696 * cwp)
    return L * (2.0 * T + B) * math.sqrt(cm) * core + 2.38 * abt / Cb


# ---------------------------------------------------------------------------
# Verdicts / checks (pure functions -> unit-testable offline)
# ---------------------------------------------------------------------------
def in_range(x: float, lo_hi: Iterable[float]) -> bool:
    lo, hi = lo_hi
    return lo <= x <= hi


def cr_cf_ratio(cf: float, cr: float) -> float:
    """Residual/frictional ratio. For conventional forms at Fn<0.30 this sits
    around 0.10-0.60; >>1 means the "residual" law dominates the viscous part,
    which is physically inconsistent with the wave-resistance hump picture."""
    return cr / cf if cf else float("inf")


def check_inputs_physically_valid(ship_type: str, cb: float, fn: float) -> Dict[str, object]:
    """Is the *request* inside the Holtrop validity domain [HOLTROP]?"""
    cb_ok = in_range(cb, HOLTROP_CB_RANGE)
    fn_ok = in_range(fn, HOLTROP_FN_RANGE)
    return {
        "ship_type": ship_type,
        "cb": cb,
        "froude": round(fn, 5),
        "cb_in_holtrop_domain": cb_ok,
        "fn_in_holtrop_domain": fn_ok,
        "holtrop_applicable": bool(cb_ok and fn_ok),
    }


def check_arithmetic_identity(out: Mapping[str, float]) -> Dict[str, object]:
    """The endpoint must satisfy Ct = Cf + Cr + dCf and RT = 0.5 rho V^2 S Ct / 1000."""
    cf = out["cf"]
    cr = out["cr"]
    ct = out["ct"]
    dcf = out.get("delta_cf", 0.0004)
    ct_rebuilt = cf + cr + dcf
    rel = abs(ct_rebuilt - ct) / ct if ct else float("inf")
    return {
        "ct_reported": ct,
        "ct_rebuilt_cf_plus_cr_plus_dcf": round(ct_rebuilt, 6),
        "rel_error": round(rel, 6),
        "passed": rel <= 0.01,
    }


def check_surface_response(out: Mapping[str, float], v_ms: float, rho: float,
                           s: float, tol_rt: float = 0.02,
                           tol_pe: float = 0.02) -> Dict[str, object]:
    """RT[kN] = 0.5 rho V^2 S Ct / 1000  and  PE[kW] = RT * V.

    ``tol_rt`` / ``tol_pe`` must account for the endpoint's output quantisation
    (RT and PE are rounded to 1e-2), otherwise a 0.06 kN answer looks like a 7%
    failure when it is only a rounding artefact.  Both reference values are
    recomputed from the *unrounded* chain, so PE is not polluted by rounded RT.
    """
    rt_expected = 0.5 * rho * v_ms ** 2 * s * out["ct"] / 1000.0
    rt = out["rt"]
    rel_rt = abs(rt_expected - rt) / rt if rt else float("inf")
    pe_expected = rt_expected * v_ms
    rel_pe = abs(pe_expected - out["pe"]) / out["pe"] if out.get("pe") else float("inf")
    return {
        "rt_reported_kn": rt,
        "rt_recomputed_kn": round(rt_expected, 5),
        "rt_rel_error": round(rel_rt, 5),
        "pe_reported_kw": out.get("pe"),
        "pe_recomputed_kw": round(pe_expected, 5),
        "pe_rel_error": round(rel_pe, 5),
        "tolerance_rt": round(tol_rt, 5),
        "tolerance_pe": round(tol_pe, 5),
        "passed": bool(rel_rt <= tol_rt and rel_pe <= tol_pe),
    }


def check_submerged_wave_resistance(ship_type: str, out: Mapping[str, float],
                                    tolerance_ratio: float = 0.25) -> Dict[str, object]:
    """Submerged body: residual (wave) resistance must be a small fraction of Cf.

    The platform has no submergence / free-surface flag, so a submarine is pushed
    through the surface-ship residual-resistance law.  Anything above
    ``tolerance_ratio`` x Cf is flagged as a category error, not a calibration issue.
    """
    ratio = cr_cf_ratio(out["cf"], out["cr"])
    return {
        "ship_type": ship_type,
        "cr_cf_ratio": round(ratio, 3),
        "threshold": tolerance_ratio,
        "wave_component_plausible": bool(ratio <= tolerance_ratio),
        "verdict": ("consistent with deeply submerged body" if ratio <= tolerance_ratio
                    else "CATEGORY ERROR: wave/residual term is %.2fx the viscous term "
                         "for a body with no free surface" % ratio),
    }


def check_wetted_surface(s_calc: float, s_ref: float) -> Dict[str, object]:
    """Geometry-first rule: the wetted area must agree with the reference hull."""
    err = abs(s_calc - s_ref) / s_ref
    return {
        "s_calc_m2": round(s_calc, 3),
        "s_ref_m2": s_ref,
        "rel_error": round(err, 4),
        "passed": bool(err <= 0.05),
    }


def check_decomposition_share(out: Mapping[str, float]) -> Dict[str, float]:
    """Share of the total resistance carried by the residual term."""
    ct = out["ct"]
    return {
        "friction_share": round(out["cf"] / ct, 4),
        "residual_share": round(out["cr"] / ct, 4),
    }


# ---------------------------------------------------------------------------
# Benchmark-dataset integrity (the repo's own reference table)
# ---------------------------------------------------------------------------
def case_internal_consistency(case: Mapping[str, object],
                              rho: float = RHO_DEFAULT,
                              tol: float = 0.15) -> Dict[str, object]:
    """Cross-check a benchmark case: Ct vs Rt vs wetted area vs speed.

    Ct and the reported resistance must describe the same force:
        Rt = 0.5 rho S V^2 Ct
    A dataset that fails this identity cannot be used to grade *any* resistance
    implementation, because the "reference" is self-contradictory.
    """
    cid = case["case_id"]
    v = float(case["speed_ms"])
    ct = float(case["experimental_ct"])
    rt = float(case["experimental_rt_n"])
    geo = case.get("geometry") or {}
    s = geo.get("wetted_surface_m2")
    if not s:
        return {"case_id": cid, "checked": False, "reason": "no wetted_surface_m2 in case geometry"}
    rt_implied = 0.5 * rho * s * v ** 2 * ct
    rel = abs(rt_implied - rt) / rt
    return {
        "case_id": cid,
        "ship_type": case["ship_type"],
        "checked": True,
        "rt_reported_n": rt,
        "rt_implied_by_ct_n": round(rt_implied, 3),
        "ratio_reported_over_implied": round(rt / rt_implied, 4) if rt_implied else None,
        "rel_error": round(rel, 4),
        "consistent": bool(rel <= tol),
    }


def dataset_monotonicity(cases: List[Mapping[str, object]], ship_type: str) -> Dict[str, object]:
    """For a deeply submerged body Ct must *decrease* with speed (Re up -> Cf down).

    A Ct that rises with speed cannot come from a friction-dominated experiment on
    a submerged body; it signals hand-set / synthetic reference values.
    """
    sub = sorted([c for c in cases if c["ship_type"] == ship_type],
                 key=lambda c: float(c["speed_ms"]))
    cts = [float(c["experimental_ct"]) for c in sub]
    speeds = [float(c["speed_ms"]) for c in sub]
    decreasing = all(cts[i + 1] < cts[i] for i in range(len(cts) - 1))
    return {
        "ship_type": ship_type,
        "speeds_ms": speeds,
        "ct": cts,
        "monotonically_decreasing": decreasing,
        "verdict": ("consistent with friction-dominated submerged experiment" if decreasing
                    else "NON-PHYSICAL: Ct increases with speed "
                         "(Re grows -> Cf must fall)"),
    }